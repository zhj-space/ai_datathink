#!/usr/bin/env python3
"""作业图级血缘上报（D1，2026-09-19）：把 Flink SQL 作业定义解析成 source→sink 边，注册到 OpenMetadata。

为什么是"自研上报"（与 ADR-A7、ADR 体系的关系）：
  * ADR-A7 要求"**作业图级血缘**（作业启动/变更时上报，非事件级）"，**未限定必须由 OpenLineage 上报**；
  * 实测：Flink 1.20.1 + Flink SQL 下 OpenLineage 无法工作（1.x 不支持 SQL；2.x 所需 SPI 在 1.20.1 不存在），
    且 `flink-cdc` **未实现** FLIP-314 的 `LineageVertexProvider`（源码树 0 命中）⇒ 换个 Flink 版本也拿不到；
  * 故 D1 由**自研侧（GA）在作业上报时**把边写进 OM。**这是机制变更，属人工决策已批准（2026-09-19 选 D1）。**

D1 范围（简版）：
  * 只连**表/主题级边**（table→table、table→topic 等），用 `LineageDetails.source=PipelineLineage`
    并把作业 SQL 存进 `sqlQuery`，便于事后追溯；
  * **不建作业实体**（那是 D2：先建 Custom Pipeline service 让血缘图上出现"作业节点"）；
  * 不含周期对账任务（D1 先做"上报 + 留痕"，对账见后续）。

准确性纪律（ADR-A7「血缘定准确率验收」的直接落地）：
  * 这是**文本解析**，不是 SQL 引擎：只处理它认识的结构（`CREATE TABLE … WITH (…)` + `INSERT INTO … SELECT … FROM …`）；
  * 认识不了就**报错，不猜**（宁缺勿错）：`errors` 非空时调用方**必须拒绝上报**；
  * 表名 → OM FQN 的映射由**显式 mapping** 给出（service 名、默认 schema），缺项即报错——**映射错=血缘错**。
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

OM_BASE = os.getenv("GA_OM_BASE", os.getenv("OM_BASE", "http://openmetadata-server:8585/api/v1"))
ENVF = "/data/ai-governance/.env"
DEFAULT_SCHEMA = os.getenv("GA_OM_DEFAULT_SCHEMA", "public")
LINEAGE_SOURCE = "PipelineLineage"           # OM LineageDetails.source 枚举内取值


def env(key: str) -> str:
    if os.environ.get(key):
        return os.environ[key]
    try:
        with open(ENVF, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


# ---------------------------------------------------------------- 1) 解析 Flink SQL
_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[^\s(]+)\s*\(.*?\)\s*"
    r"WITH\s*\((?P<opts>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_OPT_RE = re.compile(r"'(?P<k>[^']+)'\s*=\s*'(?P<v>[^']*)'")
# Flink 窗口 TVF：FROM TABLE(TUMBLE(TABLE <name>, DESCRIPTOR(ts), INTERVAL '5' SECOND))
# —— 若不特判，`(` 会被当成子查询而误杀**合法的**窗口聚合作业（实测踩坑）。
_WINDOW_TVF_RE = re.compile(
    r"TABLE\s*\(\s*(?:TUMBLE|HOP|CUMULATE)\s*\(\s*TABLE\s+(?P<name>[^\s,)]+)",
    re.IGNORECASE,
)
_JDBC_DB_RE = re.compile(r"//[^/]+/(?P<db>[A-Za-z0-9_\-]+)")
_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+(?P<target>[^\s(;]+)\s+SELECT\s+(?P<select>.*?)\bFROM\s+"
    r"(?P<from>.*?)(?:\bWHERE\b|\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)
_STOP_KEYWORDS = ("where", "group", "order", "limit", "having", "union")


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def _clean(ident: str) -> str:
    return ident.strip().strip("`").strip('"')


def parse_job(sql_text: str) -> dict:
    """把一段 Flink SQL 解析成表定义与 source→sink 边。**不确定即记 errors，不猜。**"""
    sql = _strip_comments(sql_text or "")
    errors: list[str] = []
    tables: dict[str, dict] = {}

    for m in _CREATE_RE.finditer(sql):
        name = _clean(m.group("name"))
        opts = {o.group("k").strip().lower(): o.group("v").strip() for o in _OPT_RE.finditer(m.group("opts"))}
        if not opts:
            errors.append(f"表 {name} 的 WITH 选项解析为空（D1 只支持 'k'='v' 形式）")
            continue
        tables[name] = {"connector": opts.get("connector", "").lower(), "options": opts}

    edges: list[dict] = []
    for m in _INSERT_RE.finditer(sql):
        target = _clean(m.group("target"))
        select_part = m.group("select")
        # 只拦**嵌套 SELECT**（子查询）；普通函数调用（COUNT(*) 等）是合法的
        if re.search(r"\bSELECT\b", select_part, re.IGNORECASE):
            errors.append(f"INSERT INTO {target} 的 SELECT 含子查询，D1 不解析（请简化或人工核对）")
            continue
        if target not in tables:
            errors.append(f"INSERT INTO {target}：目标表未在本文本中定义")
            continue
        raw_from = m.group("from")
        win = _WINDOW_TVF_RE.search(raw_from)
        if win:
            sources = [_clean(win.group("name"))]
        elif "(" in raw_from:
            errors.append(f"INSERT INTO {target} 的 FROM 含子查询，D1 不解析")
            continue
        else:
            # 去掉 JOIN 关键字后按逗号取标识符（D1 只做表级边，不区分 JOIN 类型）
            src_text = re.sub(r"\b(LEFT|RIGHT|FULL|INNER|CROSS)?\s*JOIN\b", ",", raw_from, flags=re.IGNORECASE)
            src_text = re.split(r"\b" + r"\b|\b".join(_STOP_KEYWORDS) + r"\b", src_text, maxsplit=1, flags=re.IGNORECASE)[0]
            sources = [_clean(s) for s in src_text.split(",") if s.strip()]
        if not sources:
            errors.append(f"INSERT INTO {target}：未能从 FROM 子句取到源表")
            continue
        for s in sources:
            if s not in tables:
                errors.append(f"INSERT INTO {target}：源表 {s} 未在本文本中定义")
                continue
            edges.append({"from": s, "to": target})

    if not tables:
        errors.append("未解析到任何 CREATE TABLE … WITH (…) 定义")
    if not edges and tables and not errors:
        errors.append("未解析到任何 INSERT INTO … SELECT … FROM … 语句（无法形成边）")
    return {"tables": tables, "edges": edges, "errors": errors}


# ---------------------------------------------------------------- 2) 映射到 OM 实体
def resolve_ref(name: str, tdef: dict, mapping: dict) -> tuple[dict | None, str | None]:
    """Flink 表定义 → OM 实体引用 {type, fqn}。缺项一律返回错误（不猜）。"""
    connector = (tdef.get("connector") or "").lower()
    opts = tdef.get("options") or {}

    if "kafka" in connector:
        topic = opts.get("topic")
        svc = mapping.get("topic_service")
        if not topic:
            return None, f"表 {name}：Kafka 连接器缺少 'topic' 选项"
        if not svc:
            return None, f"表 {name}：缺少 mapping.topic_service（OM 里消息服务名）"
        return {"type": "topic", "fqn": f"{svc}.{topic}"}, None

    if "jdbc" in connector or "cdc" in connector:
        table = opts.get("table-name")
        database = opts.get("database-name") or opts.get("dbname")
        if not database:
            # JDBC sink 常只给 url（例：jdbc:postgresql://pgbouncer:6432/app_db）⇒ 从 url 取库名
            m2 = _JDBC_DB_RE.search(opts.get("url") or "")
            if m2:
                database = m2.group("db")
        schema = opts.get("schema-name") or mapping.get("default_schema") or DEFAULT_SCHEMA
        svc = mapping.get("table_service")
        if not table:
            return None, f"表 {name}：JDBC/CDC 连接器缺少 'table-name' 选项"
        if not database:
            return None, f"表 {name}：JDBC/CDC 连接器缺少 'database-name'，且 url 里取不到库名"
        if not svc:
            return None, f"表 {name}：缺少 mapping.table_service（OM 里数据库服务名）"
        return {"type": "table", "fqn": f"{svc}.{database}.{schema}.{table}"}, None

    return None, f"表 {name}：不认识的 connector={connector!r}（D1 仅支持 kafka / jdbc / *-cdc）"


def build_plan(sql_text: str, mapping: dict) -> dict:
    """产出期望边集合；errors 非空 ⇒ 调用方必须拒绝上报。"""
    parsed = parse_job(sql_text)
    errors = list(parsed["errors"])
    edges: list[dict] = []
    for e in parsed["edges"]:
        f_ref, f_err = resolve_ref(e["from"], parsed["tables"][e["from"]], mapping)
        t_ref, t_err = resolve_ref(e["to"], parsed["tables"][e["to"]], mapping)
        if f_err:
            errors.append(f_err)
        if t_err:
            errors.append(t_err)
        if f_err or t_err:
            continue
        edges.append({
            "from": {"type": f_ref["type"], "fqn": f_ref["fqn"], "flink_table": e["from"]},
            "to": {"type": t_ref["type"], "fqn": t_ref["fqn"], "flink_table": e["to"]},
        })
    return {"edges": edges, "tables": parsed["tables"], "errors": errors}


def edge_key(e: dict) -> tuple:
    """同时支持两种形态：解析结果（嵌套 from/to）与库内行（扁平 from_type/…）。"""
    if "from" in e:
        return (e["from"]["type"], e["from"]["fqn"], e["to"]["type"], e["to"]["fqn"])
    return (e["from_type"], e["from_fqn"], e["to_type"], e["to_fqn"])


def plan_diff(desired: list[dict], current: list[dict]) -> dict:
    """desired = 本次解析结果；current = 库里该 job 的现存边（status != removed）。"""
    d = {edge_key(e): e for e in desired}
    c = {edge_key(e): e for e in current}
    return {
        "add": [d[k] for k in sorted(set(d) - set(c))],
        "keep": [d[k] for k in sorted(set(d) & set(c))],
        "remove": [c[k] for k in sorted(set(c) - set(d))],
    }


# ---------------------------------------------------------------- 3) OM 客户端
def om_login() -> str:
    pw = env("OPENMETADATA_ADMIN_PASSWORD")
    body = json.dumps({"email": "admin@open-metadata.org",
                       "password": base64.b64encode(pw.encode()).decode()}).encode()
    req = urllib.request.Request(OM_BASE + "/users/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"{}").get("accessToken", "")


class OmClient:
    """只做与血缘相关的三个动作：连边 / 删边 / 读边。"""

    def __init__(self, token: str | None = None):
        self.token = token or om_login()

    def _req(self, method: str, path: str, body: dict | None = None) -> tuple[int, str]:
        data = json.dumps(body or {}).encode() if body is not None else None
        req = urllib.request.Request(OM_BASE + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    @staticmethod
    def _fqn_path(fqn: str) -> str:
        return urllib.parse.quote(fqn, safe=".")

    def add_edge(self, f_type: str, f_fqn: str, t_type: str, t_fqn: str, sql_query: str) -> dict:
        path = "/lineage/{0}/name/{1}/{2}/name/{3}".format(
            f_type, self._fqn_path(f_fqn), t_type, self._fqn_path(t_fqn))
        details = {"source": LINEAGE_SOURCE}
        if sql_query:
            details["sqlQuery"] = sql_query
        status, text = self._req("PUT", path, details)
        return {"ok": 200 <= status < 300, "status": status, "response": text[:400]}

    def delete_edge(self, f_type: str, f_fqn: str, t_type: str, t_fqn: str) -> dict:
        path = "/lineage/{0}/name/{1}/{2}/name/{3}".format(
            f_type, self._fqn_path(f_fqn), t_type, self._fqn_path(t_fqn))
        status, text = self._req("DELETE", path, None)
        return {"ok": 200 <= status < 300, "status": status, "response": text[:400]}

    def get_lineage(self, entity_type: str, fqn: str) -> dict:
        path = "/lineage/{0}/name/{1}".format(entity_type, self._fqn_path(fqn))
        status, text = self._req("GET", path, None)
        return {"status": status, "body": text[:2000]}

    def edge_exists(self, f_type: str, f_fqn: str, t_type: str, t_fqn: str) -> bool:
        """查单条边是否存在（OM 专门提供了 getLineageEdge 端点，比拉全量下游再比对可靠）。

        ⚠️ 实测（2026-09-19）：**存在时返回 `{"edge":{...}}`**——边信息在 `edge` 键里，
        不是顶层 `toEntity`。早期按顶层判 ⇒ 永远 False ⇒ 对账会**误报漂移并每次"修复"**。

        ⚠️ 只用于**判断我们自己上报的边**；**绝不据此删除边**——OM 里还有 sqllineage 等
        离线链路的血缘，删错就是第二套真理源的问题（ADR-A7 口径）。
        """
        path = "/lineage/getLineageEdge/{0}/name/{1}/{2}/name/{3}".format(
            f_type, self._fqn_path(f_fqn), t_type, self._fqn_path(t_fqn))
        status, text = self._req("GET", path, None)
        if status == 200:
            try:
                if json.loads(text).get("edge"):
                    return True
            except Exception:  # noqa: BLE001
                pass
        # 兜底（防端点行为差异）：目标实体必须同时出现在 downstreamEdges 与 nodes 里，且 FQN 吻合
        st, text = self._req("GET", "/lineage/{0}/name/{1}".format(f_type, self._fqn_path(f_fqn)), None)
        if st != 200:
            return False
        try:
            d = json.loads(text)
        except Exception:  # noqa: BLE001
            return False
        ids = {e.get("toEntity") for e in (d.get("downstreamEdges") or []) if isinstance(e, dict)}
        for n in (d.get("nodes") or []):
            if isinstance(n, dict) and n.get("id") in ids and n.get("fullyQualifiedName") == t_fqn:
                return True
        return False

    def list_downstream(self, f_type: str, f_fqn: str) -> list:
        """列出某实体的下游边（用于**发现意外边**；只报告，不删除）。"""
        st, text = self._req("GET", "/lineage/{0}/name/{1}".format(f_type, self._fqn_path(f_fqn)), None)
        if st != 200:
            return []
        try:
            body = json.loads(text)
        except Exception:  # noqa: BLE001
            return []
        return body.get("downstreamEdges") or []
