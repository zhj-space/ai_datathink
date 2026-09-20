#!/usr/bin/env python3
"""Governance Agent 首版（S3-6）：告警落库 + 开放 API + 审批网关。

依据：《AI数据治理平台_单机版_S3-6治理Agent接口规格.md》v1.0
边界（ADR-A3，**不得越界**）：
  * 只做**事后审计 + 主动告警**，**不做事前拦截**（审批只记录，不阻断任何在线请求）；
  * 不做在线 PII 脱敏（Presidio 仅离线批量）；
  * 血缘/元数据以 OpenMetadata 为唯一权威源，本服务不建第二套；
  * `/chat`（场景 D 问答）属 S4（Ollama + RAG），首版**明确降级**，不伪造答案。

依赖：psycopg2-binary（必需）；confluent-kafka（可选——缺失时服务仍可运行，
      仅告警消费不可用，`/ready` 会如实报告 kafka=false）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg2
import psycopg2.extras

try:                      # S4-2：RAG 模块（缺依赖时不阻断服务，/chat 走降级）
    import rag            # noqa: F401
except Exception:         # noqa: BLE001
    rag = None

try:                      # D1：作业图级血缘上报（纯 stdlib，正常不会失败）
    import lineage
except Exception:         # noqa: BLE001
    lineage = None

# ---------------------------------------------------------------- 配置
PORT = int(os.getenv("GA_PORT", "8080"))
BIND = os.getenv("GA_BIND", "0.0.0.0")

DB_CONF = {
    "host": os.getenv("GA_DB_HOST", "pgbouncer"),          # 硬约束②：只经 PgBouncer，不直连 5432
    "port": int(os.getenv("GA_DB_PORT", "6432")),
    "dbname": os.getenv("GA_DB_NAME", "governance_agent"),
    "user": os.getenv("GA_DB_USER", "ga_writer"),
    "password": os.getenv("GA_DB_PASSWORD", ""),
}

KAFKA_BOOTSTRAP = os.getenv("GA_KAFKA_BOOTSTRAP", "kafka:9092")
KAFKA_TOPIC = os.getenv("GA_KAFKA_TOPIC", "governance.alerts")
KAFKA_GROUP = os.getenv("GA_KAFKA_GROUP", "governance-agent-poc")

# Alertmanager webhook 共享令牌（2026-09-19）：非空时 /api/v1/alerts/ingest 必须带
# `Authorization: Bearer <token>`。POC 期该端点仅容器网内可达（宿主只发布回环 8085），
# 加令牌是为了"网内也不裸奔"——若后续接 IM/正式通道，鉴权方式按当时设计复核。
GA_INGEST_TOKEN = os.getenv("GA_INGEST_TOKEN", "")

# 规则化增强（首版）：规则名 → 处置建议模板。LLM 增强属 S4，届时写同一字段。
ADVICE = {
    "PgReplicationSlotWatermarkHigh": "复制槽水位达兜底值 70%：检查 CDC 作业是否停机/背压；必要时按「作业下线 checklist」处置该 slot（不得随手删除）。",
    "PgReplicationSlotNoSafetyCap": "源库未设 max_slot_wal_keep_size：请 DBA 按《…S3-1门禁④材料包.md》§5.3 落值，否则停机期间可能撑爆源库磁盘。",
    "PgReplicationSlotInvalidated": "复制槽已失效（wal_status=lost）：续接窗口用尽，须评估 ADR-A2 第 4 件「重快照预案」。",
    "PgReplicationSlotBeyondCap": "复制槽超出兜底值（extended）：WAL 仍保留但已越界，须立即人工介入。",
    "PgReplicationSlotInactiveHoldingWal": "槽无消费者却持有 WAL：确认是否为「作业已停但 slot 未删」。",
    "HostFilesystemUsageHigh": "磁盘使用率超 85%：按 0.3 降级声明，100G 单盘打满会同时打穿 PG/OpenSearch/Kafka。",
    "HostCpuUsageHigh": "宿主机 CPU 超 80%（S1-7 SLO）：核对是否有 Flink 作业/批处理叠加。",
    "HostMemoryUsageHigh": "宿主机内存超 85%（S1-7 SLO）：核对容器内存占用，必要时人工干预。",
    # Flink 作业 / Checkpoint（2026-09-19 新增；对应 `alerting/poc-alerts.yml` 的 flink-jobs 组 5 条）
    "FlinkMetricsScrapeFailed": "Flink 指标抓取失败 ⇒ **作业健康不可观测**（不是没有异常）。先查 JM/TM 的 9249 监听与容器网络（backend-net），必要时重启 Flink 容器。",
    "FlinkRegisteredJobNotRunning": "**登记了作业但 Flink 上没有 RUNNING 作业** ⇒ 作业掉了或没起来。查 JM REST `/jobs/overview`（**按 state=RUNNING 过滤，勿用 jobs[0]**）与 TM 日志；确认后按 `submit_flink_job.sh` 重新提交。",
    "FlinkJobRestarted": "作业发生重启：查 Checkpoint 失败原因、TM 内存/磁盘与下游连接；**反复重启**须停作业排查，不得只看它自己恢复。",
    "FlinkCheckpointFailed": "**Checkpoint 失败 = 恢复能力受损**（ADR-A2 第 3 件与位点续接的前提）。查 `state.checkpoints.dir` 可写性、TM 磁盘余量与背压。",
    "FlinkCheckpointStalled": "Checkpoint 停摆（配了 Checkpoint 却长时间无一完成）：查背压、Checkpoint 超时设置与存储目录；**持续停摆须人工介入**。",
    # Ollama 运行面（2026-09-19 新增；对应 `alerting/poc-alerts.yml` 的 ollama-runtime 组 7 条）
    "OllamaApiDown": "Ollama API 不可达 ⇒ 场景 D 问答与 RAG 向量化中断。先查容器状态与回环 11434，再看是否被 cgroup OOM kill（见 OllamaContainerOomKilled）。",
    "OllamaContainerCpuSaturated": "Ollama 容器 CPU 长期 >80% 自身限额：S4-1 要求它**不得拖慢实时告警链路**。查是否有并发推理或长时间未卸载的模型；必要时人工降档/限流，**不得由 Agent 自改限额**。",
    "OllamaContainerMemoryHigh": "Ollama 容器内存接近 cgroup 上限（含可回收页缓存）：持续贴近会被 OOM kill。用 memory_anon/file 两个序列区分「真占用」与页缓存，再决定是否降档或调上限。",
    "OllamaContainerOomKilled": "Ollama 被 cgroup OOM kill ⇒ 推理进程重启、场景 D/RAG 中断。按 S4-1 §4 评估换更小/量化模型或触发 GPU 评估（**人工决策**）。",
    "OllamaModelNotUnloading": "模型卸载期限 >1h：容器配了 OLLAMA_KEEP_ALIVE=10m，说明有调用方传了更大的 keep_alive（含 -1 常驻）⇒ 长期占内存/CPU。查 RAG/门户的调用参数。",
    "OllamaProbeStale": "Ollama 运行面采集器停摆 ⇒ **运行面不可观测**（上面的告警也不会触发）。查 /data/ai-governance/scripts/ollama_probe.sh、cron 与 node_exporter textfile 目录。",
    "OllamaRuntimeMetricsMissing": "Ollama 运行面指标整体缺失（textfile 被拒收或未生成）⇒ 运行面告警全部静默失效。查 `ai_governance_ollama_runtime.prom` 是否存在且每行合法。",
    # 通用容器运行面（2026-09-19 新增；对应 `alerting/poc-alerts.yml` 的 container-runtime 组 6 条）
    "ContainerMetricsMissing": "容器运行面指标整体缺失 ⇒ 容器级 CPU/内存/OOM 全部不可观测（cAdvisor 在本环境本就取不到逐容器指标）。查 `ai_governance_containers.prom` 与 `container_probe.sh`。",
    "ContainerProbeStale": "容器运行面采集器停摆（cron `/etc/cron.d/ai-governance-containers`，每分钟）⇒ 容器级不可观测。查脚本、cron 与 node_exporter textfile 目录。",
    "ContainerSeriesIncomplete": "采集器清点的容器数多于实际采到的内存序列 ⇒ **有容器没采到**（容器刚起、cgroup 不可读）。这属「看着在跑其实没数据」的静默失效，须查 `docker ps` 与 cgroup 路径。",
    "ContainerOomKilled": "**容器被 cgroup OOM kill**（累计计数，容器重启后清零）⇒ 进程被杀、功能中断，属单机无冗余环境的高危事件。先看 memory_anon/file 区分真占用与页缓存，再由**人工**决定调限额或降负载（不得由 Agent 自改限额）。",
    "ContainerMemoryNearLimit": "容器内存 >85% 自身限额：与 S1-7 的 85% 口径一致。先用 memory_anon_bytes（真占用）与 memory_file_bytes（可回收缓存）判读是否真的在涨；持续贴近上限会 OOM kill。",
    "ContainerCpuSaturated": "容器持续 >90% 自身 CPU 限额 30m：单机无冗余，会拖慢同机其它组件（实时告警链路优先）。先确认是预期批处理（备份/ETL/压测）还是异常自旋。",
}

STATE = {"kafka": False, "kafka_error": "", "last_consume_at": None, "consumed": 0}
STATE_LOCK = threading.Lock()


# ---------------------------------------------------------------- 数据库
def db_conn():
    conn = psycopg2.connect(**DB_CONF)
    conn.autocommit = True
    return conn


def db_ok() -> tuple[bool, str]:
    try:
        with db_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc.__class__.__name__}: {exc}"


def audit(cur, actor: str, action: str, entity_type: str, entity_ref: str, detail: dict) -> None:
    cur.execute(
        "INSERT INTO ga.audit_log (actor, action, entity_type, entity_ref, detail) "
        "VALUES (%s, %s, %s, %s, %s)",
        (actor, action, entity_type, entity_ref, json.dumps(detail, ensure_ascii=False)),
    )


# ---------------------------------------------------------------- 告警消费
def store_alert(raw: bytes, source: str | None = None) -> bool:
    """幂等落库；返回是否新写入。source 为空时沿用表默认值（governance.alerts）。"""
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
        if not isinstance(payload, dict):
            payload = {"value": payload}
    except Exception:  # noqa: BLE001
        payload = {"raw": raw.decode("utf-8", "replace")}

    key = str(payload.get("id") or payload.get("alert_key") or
              hashlib.sha256(raw).hexdigest())
    name = str(payload.get("alertname") or payload.get("rule") or "unknown")
    severity = str(payload.get("severity") or payload.get("level") or "unknown").lower()
    advice = ADVICE.get(name)

    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ga.alert_event (alert_key, alert_name, severity, source, payload, "
            "enhanced_summary, enhanced_at) VALUES (%s, %s, %s, %s, %s, %s, "
            "CASE WHEN %s IS NULL THEN NULL ELSE now() END) "
            "ON CONFLICT (alert_key) DO NOTHING RETURNING id",
            (key, name, severity, source or "governance.alerts",
             json.dumps(payload, ensure_ascii=False), advice, advice),
        )
        row = cur.fetchone()
    return row is not None


def ingest_alertmanager(payload: dict) -> dict:
    """Alertmanager webhook → 逐条展开 → 复用 store_alert 幂等落库。

    为什么需要：Alertmanager 默认只在 UI 里留存（本环境 receiver 曾为空），
    告警既不落库也不可审计。本端点把 AM 的告警汇入 ga.alert_event，
    **不引入新组件、不改变 AM 的判定逻辑**（只多一个接收端）。

    幂等键 = `alertmanager:<fingerprint>:<status>`
      * 同一条告警的重复投递（repeat_interval 到点重发）**不会产生重复行**；
      * `firing` 与 `resolved` 各占一行，便于事后还原"何时起、何时恢复"。
    """
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        return {"received": 0, "stored": 0, "duplicates": 0,
                "note": "payload 不含 alerts 数组（可能为空通知）"}
    group_key = payload.get("groupKey") or ""
    top_status = str(payload.get("status") or "")
    stored = 0
    for item in alerts:
        if not isinstance(item, dict):
            continue
        labels = item.get("labels") or {}
        fp = item.get("fingerprint") or hashlib.sha256(
            json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        st = str(item.get("status") or top_status or "unknown")
        rec = {
            # store_alert 优先取 "id" 作为幂等键
            "id": f"alertmanager:{fp}:{st}",
            "alertname": labels.get("alertname", "unknown"),
            "severity": labels.get("severity", "unknown"),
            "status": st,
            "labels": labels,
            "annotations": item.get("annotations") or {},
            "startsAt": item.get("startsAt"),
            "endsAt": item.get("endsAt"),
            "generatorURL": item.get("generatorURL"),
            "groupKey": group_key,
            "receiver": payload.get("receiver"),
            "alertmanager_version": payload.get("version"),
        }
        if store_alert(json.dumps(rec, ensure_ascii=False).encode("utf-8"),
                       source="alertmanager"):
            stored += 1
    return {"received": len(alerts), "stored": stored,
            "duplicates": len(alerts) - stored}


def consume_loop() -> None:
    try:
        from confluent_kafka import Consumer  # 可选依赖
    except Exception as exc:  # noqa: BLE001
        with STATE_LOCK:
            STATE["kafka"] = False
            STATE["kafka_error"] = f"confluent_kafka 不可用：{exc.__class__.__name__}"
        return

    consumer = Consumer({
        "bootstrap.servers": KAFKA_BOOTSTRAP,
        "group.id": KAFKA_GROUP,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
    })
    try:
        consumer.subscribe([KAFKA_TOPIC])
        with STATE_LOCK:
            STATE["kafka"] = True
            STATE["kafka_error"] = ""
    except Exception as exc:  # noqa: BLE001
        with STATE_LOCK:
            STATE["kafka"] = False
            STATE["kafka_error"] = f"subscribe 失败：{exc}"
        return

    while True:
        try:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                with STATE_LOCK:
                    STATE["kafka_error"] = str(msg.error())
                continue
            store_alert(msg.value() or b"{}")
            with STATE_LOCK:
                STATE["kafka"] = True
                STATE["kafka_error"] = ""
                STATE["last_consume_at"] = datetime.now(timezone.utc).isoformat()
                STATE["consumed"] += 1
        except Exception as exc:  # noqa: BLE001
            with STATE_LOCK:
                STATE["kafka_error"] = f"{exc.__class__.__name__}: {exc}"
            time.sleep(2)


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "governance-agent/0.1.0"

    # --- 工具 ---
    def _json(self, code: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _text(self, code: int, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _err(self, code: int, err_code: str, message: str) -> None:
        self._json(code, {"error": {"code": err_code, "message": message}})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"请求体不是合法 JSON：{exc}") from exc

    def log_message(self, fmt, *args):  # 静音 access log（避免刷屏；需要时改这里）
        pass

    def _write_authorized(self) -> bool:
        """写端点的统一鉴权（告警汇入 / 血缘上报）：GA_INGEST_TOKEN 非空即强制校验。"""
        if not GA_INGEST_TOKEN:
            return True
        return (self.headers.get("Authorization") or "") == f"Bearer {GA_INGEST_TOKEN}"

    # --- GET ---
    def do_GET(self):  # noqa: N802
        path, _, query = self.path.partition("?")
        params = {}
        for kv in query.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                params[k] = v

        if path == "/health":
            # 轻量：不依赖下游（门户以 200 判定"Agent 就绪"）
            return self._json(200, {"status": "ok", "service": "governance-agent",
                                    "version": "0.1.0", "scope": "S3-6 首版"})
        if path == "/ready":
            ok, err = db_ok()
            with STATE_LOCK:
                kafka, kerr, consumed = STATE["kafka"], STATE["kafka_error"], STATE["consumed"]
            return self._json(200 if ok else 503,
                              {"db": ok, "db_error": err, "kafka": kafka,
                               "kafka_error": kerr, "consumed_alerts": consumed})
        if path == "/api/v1/alerts":
            return self._list_alerts(params)
        m = re.fullmatch(r"/api/v1/alerts/(\d+)", path)
        if m:
            return self._get_alert(int(m.group(1)))
        if path == "/api/v1/approvals":
            return self._list_approvals(params)
        if path == "/api/v1/audit":
            return self._list_audit(params)
        if path == "/api/v1/lineage/report":
            return self._list_lineage(params)
        return self._err(404, "not_found", f"未知路径：{path}")

    # --- POST ---
    def do_POST(self):  # noqa: N802
        path = self.path.partition("?")[0]
        try:
            body = self._body()
        except ValueError as exc:
            return self._err(400, "bad_json", str(exc))

        if path == "/api/v1/alerts/ingest":
            # Alertmanager webhook 接收端（2026-09-19）：把 AM 告警汇入 ga.alert_event。
            # 令牌校验见 GA_INGEST_TOKEN；为空则不校验（POC 期默认部署即带令牌）。
            if not self._write_authorized():
                return self._err(401, "unauthorized", "缺少或错误的 Bearer 令牌")
            try:
                return self._json(200, ingest_alertmanager(body))
            except Exception as exc:  # noqa: BLE001
                return self._err(500, "ingest_failed", f"{exc.__class__.__name__}: {exc}")

        if path == "/api/v1/lineage/report":
            # D1：作业图级血缘上报（自研侧上报，ADR-A7；机制变更已人工批准）
            if not self._write_authorized():
                return self._err(401, "unauthorized", "缺少或错误的 Bearer 令牌")
            return self._report_lineage(body)

        if path == "/api/v1/lineage/reconcile":
            # D1 对账（2026-09-19）：拉 OM 实际边 ↔ 比 GA 期望集，发现漂移并可自愈
            if not self._write_authorized():
                return self._err(401, "unauthorized", "缺少或错误的 Bearer 令牌")
            return self._reconcile_lineage(body)

        if path == "/chat":
            # 场景 D 问答：S4-2 起走 RAG（OM 元数据 → 向量检索 → 本地 LLM → **引用程序化校验**）。
            # 任一环节不可用时**降级为诚实提示**，不伪造答案（ADR-A3 边界）。
            if rag is None:
                return self._text(200, "（降级）RAG 模块不可用；当前可用："
                                       "/api/v1/alerts（告警）、/api/v1/approvals（审批台）。")
            try:
                res = rag.answer(body.get("question", ""))
            except Exception as exc:  # noqa: BLE001
                return self._text(200, f"（降级）智能问答暂不可用（{exc.__class__.__name__}）；"
                                       "当前可用：/api/v1/alerts（告警）、/api/v1/approvals（审批台）。")
            if res.get("ok"):
                trace = " → ".join(res.get("nodes") or [])
                tail = (f"\n\n—\nLangGraph 节点：{trace}" if trace else "")
                tail += f"\n检索到的资产：{', '.join(res['citations'])}" if res.get("citations") else ""
                # 上下文预算口径（S4-2，2026-09-19 新增）：把"喂进去多少"显式暴露，
                # 否则 10s 门禁（S4-1）没有可核验的口径——估算值/实测值/丢弃篇数一并给出。
                ctx = res.get("ctx") or {}
                if ctx:
                    tail += (f"\n上下文预算：估算 {ctx.get('prompt_tokens_est')}/{ctx.get('budget_tokens')} tokens"
                             f"（实测 {ctx.get('prompt_tokens_actual')}）"
                             f"，采用 {ctx.get('docs_used')}/{ctx.get('docs_total')} 篇"
                             + ("，末篇已截断" if ctx.get("truncated") else "")
                             + f"；num_ctx={ctx.get('num_ctx')}（Ollama 默认仅 2050，故显式抬高）")
                return self._text(200, res["answer"] + tail)
            return self._text(200, f"（降级）智能问答暂不可用：{res.get('reason', '未知原因')}；"
                                   "当前可用：/api/v1/alerts（告警）、/api/v1/approvals（审批台）。")
        if path == "/api/v1/approvals":
            return self._create_approval(body)
        m = re.fullmatch(r"/api/v1/approvals/([0-9a-fA-F-]{36})/decide", path)
        if m:
            return self._decide_approval(m.group(1), body)
        return self._err(404, "not_found", f"未知路径：{path}")

    # --- 业务实现 ---
    def _list_alerts(self, params):
        limit = min(int(params.get("limit", 50)), 500)
        where, args = [], []
        if params.get("severity"):
            where.append("severity = %s")
            args.append(params["severity"])
        if params.get("since"):
            where.append("received_at >= %s")
            args.append(params["since"])
        sql = ("SELECT id, alert_key, alert_name, severity, source, enhanced_summary, received_at "
               "FROM ga.alert_event " + ("WHERE " + " AND ".join(where) + " " if where else "") +
               "ORDER BY received_at DESC LIMIT %s")
        args.append(limit)
        with db_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
        return self._json(200, {"count": len(rows), "data": rows})

    def _get_alert(self, alert_id):
        with db_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM ga.alert_event WHERE id = %s", (alert_id,))
            row = cur.fetchone()
        if not row:
            return self._err(404, "not_found", f"告警 {alert_id} 不存在")
        return self._json(200, row)

    def _list_approvals(self, params):
        limit = min(int(params.get("limit", 50)), 500)
        where, args = [], []
        if params.get("status"):
            where.append("status = %s")
            args.append(params["status"])
        sql = ("SELECT * FROM ga.approval_request " +
               ("WHERE " + " AND ".join(where) + " " if where else "") +
               "ORDER BY created_at DESC LIMIT %s")
        args.append(limit)
        with db_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
        return self._json(200, {"count": len(rows), "data": rows})

    def _list_audit(self, params):
        limit = min(int(params.get("limit", 100)), 1000)
        where, args = [], []
        if params.get("entity"):
            where.append("entity_ref = %s")
            args.append(params["entity"])
        sql = ("SELECT * FROM ga.audit_log " +
               ("WHERE " + " AND ".join(where) + " " if where else "") +
               "ORDER BY at DESC LIMIT %s")
        args.append(limit)
        with db_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
        return self._json(200, {"count": len(rows), "data": rows})

    # --- 作业图级血缘（D1）---
    def _list_lineage(self, params):
        limit = min(int(params.get("limit", 100)), 500)
        where, args = [], []
        if params.get("job_key"):
            where.append("job_key = %s")
            args.append(params["job_key"])
        if params.get("status"):
            where.append("status = %s")
            args.append(params["status"])
        sql = ("SELECT id, job_key, from_type, from_fqn, to_type, to_fqn, source, status, "
               "om_response, reported_at, updated_at FROM ga.lineage_edge " +
               ("WHERE " + " AND ".join(where) + " " if where else "") +
               "ORDER BY updated_at DESC LIMIT %s")
        args.append(limit)
        with db_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
        return self._json(200, {"count": len(rows), "data": rows})

    def _report_lineage(self, body):
        if lineage is None:
            return self._err(500, "lineage_module_missing", "lineage 模块未加载")
        job_key = str(body.get("job_key") or "").strip()
        sql_text = body.get("sql") or ""
        mapping = body.get("mapping") or {}
        apply_ = bool(body.get("apply"))
        if not job_key:
            return self._err(400, "missing_field", "缺少必填字段：job_key")
        if not str(sql_text).strip():
            return self._err(400, "missing_field", "缺少必填字段：sql")
        if not isinstance(mapping, dict):
            return self._err(400, "bad_mapping", "mapping 必须是对象")

        plan = lineage.build_plan(str(sql_text), mapping)
        if plan["errors"]:
            # 宁缺勿错：解析/映射不确定 ⇒ 拒绝上报（ADR-A7 要求血缘准确率）
            return self._json(400, {"job_key": job_key, "errors": plan["errors"],
                                    "note": "解析或映射不确定，已拒绝上报（宁缺勿错）；请修正 SQL 或补全 mapping"})

        desired = plan["edges"]
        with db_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT from_type, from_fqn, to_type, to_fqn, status FROM ga.lineage_edge "
                        "WHERE job_key = %s AND status <> 'removed'", (job_key,))
            current = [dict(r) for r in cur.fetchall()]
        diff = lineage.plan_diff(desired, current)
        # **只有 status='applied' 才算"OM 里已经有"**。
        # 否则会出现这条实测缺陷（2026-09-19）：dry-run(apply=false) 先把行写成 planned，
        # 随后的 apply=true 把它当 kept 跳过 ⇒ **边永远不会被写进 OM**（而"先干跑再提交"正是主路径）。
        applied_keys = {lineage.edge_key(r) for r in current if r.get("status") == "applied"}
        to_push = diff["add"] + [e for e in diff["keep"] if lineage.edge_key(e) not in applied_keys]
        kept_n = len(diff["keep"]) - (len(to_push) - len(diff["add"]))

        om = None
        if apply_:
            try:
                om = lineage.OmClient()
            except Exception as exc:  # noqa: BLE001
                return self._err(502, "om_login_failed", f"{exc.__class__.__name__}: {exc}")

        added, removed = [], []
        with db_conn() as conn, conn.cursor() as cur:
            for e in to_push:
                status, resp = "planned", ""
                if om:
                    r = om.add_edge(e["from"]["type"], e["from"]["fqn"], e["to"]["type"],
                                    e["to"]["fqn"], str(sql_text))
                    status = "applied" if r["ok"] else ("entity_missing" if r["status"] == 404 else "failed")
                    resp = r["response"]
                cur.execute(
                    "INSERT INTO ga.lineage_edge (job_key, from_type, from_fqn, to_type, to_fqn, "
                    "source, sql_query, status, om_response) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (job_key, from_type, from_fqn, to_type, to_fqn) DO UPDATE SET "
                    "status=EXCLUDED.status, om_response=EXCLUDED.om_response, "
                    "sql_query=EXCLUDED.sql_query, updated_at=now()",
                    (job_key, e["from"]["type"], e["from"]["fqn"], e["to"]["type"], e["to"]["fqn"],
                     lineage.LINEAGE_SOURCE, str(sql_text), status, resp[:400]),
                )
                added.append({"from": e["from"]["fqn"], "to": e["to"]["fqn"], "status": status})
            for e in diff["remove"]:
                status, resp = "removed", ""
                if om:
                    r = om.delete_edge(e["from_type"], e["from_fqn"], e["to_type"], e["to_fqn"])
                    resp = r["response"]
                    if not r["ok"] and r["status"] != 404:
                        status = "failed"
                cur.execute(
                    "UPDATE ga.lineage_edge SET status=%s, om_response=%s, updated_at=now() "
                    "WHERE job_key=%s AND from_type=%s AND from_fqn=%s AND to_type=%s AND to_fqn=%s",
                    (status, resp[:400], job_key, e["from_type"], e["from_fqn"], e["to_type"], e["to_fqn"]),
                )
                removed.append({"from": e["from_fqn"], "to": e["to_fqn"], "status": status})
            audit(cur, "governance-agent", "lineage.report", "job", job_key,
                  {"apply": apply_, "parsed": len(desired), "added": len(added),
                   "kept": kept_n, "removed": len(removed)})
        return self._json(200, {"job_key": job_key, "apply": apply_, "parsed_edges": len(desired),
                                "added": added, "kept": kept_n, "removed": removed,
                                "note": "apply=false 时只留痕不写 OM；被拒（errors）时不落任何行"})

    def _reconcile_lineage(self, body):
        """D1 对账：期望边（GA 库） ↔ 实际边（OM）。repair=true 时对缺失边**重新上报**。

        纪律：
          * 只处理 `status <> 'removed'` 的期望边；
          * **只新增/修复，绝不删除** OM 上的边——OM 还承载 sqllineage 等离线血缘，
            删错就构成"血缘第二套真理源"问题（ADR-A7 明确禁止）；
          * 结果回写状态（`applied` / `drift` / `entity_missing` / `failed`）并记审计。
        """
        if lineage is None:
            return self._err(500, "lineage_module_missing", "lineage 模块未加载")
        job_key = str(body.get("job_key") or "").strip()
        repair = bool(body.get("repair"))
        where, args = ["status <> 'removed'"], []
        if job_key:
            where.append("job_key = %s")
            args.append(job_key)
        with db_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT job_key, from_type, from_fqn, to_type, to_fqn, status, sql_query "
                        "FROM ga.lineage_edge WHERE " + " AND ".join(where) + " ORDER BY id", args)
            rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return self._json(200, {"job_key": job_key or "(all)", "checked": 0,
                                    "note": "没有可对账的期望边（典型原因：尚未接源建表）"})
        try:
            om = lineage.OmClient()
        except Exception as exc:  # noqa: BLE001
            return self._err(502, "om_login_failed", f"{exc.__class__.__name__}: {exc}")

        present = drift = repaired = entity_missing = failed = 0
        details = []
        with db_conn() as conn, conn.cursor() as cur:
            for r in rows:
                if om.edge_exists(r["from_type"], r["from_fqn"], r["to_type"], r["to_fqn"]):
                    present += 1
                    new_status, resp = "applied", "reconcile: edge present"
                elif repair:
                    res = om.add_edge(r["from_type"], r["from_fqn"], r["to_type"], r["to_fqn"],
                                      r["sql_query"] or "")
                    if res["ok"]:
                        repaired += 1
                        new_status, resp = "applied", "reconcile: re-pushed"
                    elif res["status"] == 404:
                        entity_missing += 1
                        new_status, resp = "entity_missing", res["response"]
                    else:
                        failed += 1
                        new_status, resp = "failed", res["response"]
                else:
                    drift += 1
                    new_status, resp = "drift", "reconcile: 期望边在 OM 中不存在"
                details.append({"from": r["from_fqn"], "to": r["to_fqn"], "was": r["status"], "now": new_status})
                cur.execute("UPDATE ga.lineage_edge SET status=%s, om_response=%s, updated_at=now() "
                            "WHERE job_key=%s AND from_type=%s AND from_fqn=%s AND to_type=%s AND to_fqn=%s",
                            (new_status, resp[:400], r["job_key"], r["from_type"], r["from_fqn"],
                             r["to_type"], r["to_fqn"]))
            audit(cur, "governance-agent", "lineage.reconcile", "job", job_key or "(all)",
                  {"checked": len(rows), "present": present, "drift": drift, "repaired": repaired,
                   "entity_missing": entity_missing, "failed": failed, "repair": repair})
        return self._json(200, {"job_key": job_key or "(all)", "repair": repair, "checked": len(rows),
                                "present": present, "drift": drift, "repaired": repaired,
                                "entity_missing": entity_missing, "failed": failed, "details": details[:20],
                                "note": "只新增/修复，不删除 OM 上任何边（他源血缘不由本服务管）"})

    def _create_approval(self, body):
        for field in ("requester", "subject_type", "subject_ref"):
            if not body.get(field):
                return self._err(400, "missing_field", f"缺少必填字段：{field}")
        rid = str(uuid.uuid4())
        with db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ga.approval_request (id, requester, subject_type, subject_ref, reason) "
                "VALUES (%s, %s, %s, %s, %s)",
                (rid, body["requester"], body["subject_type"], body["subject_ref"],
                 body.get("reason", "")),
            )
            audit(cur, body["requester"], "approval.create", body["subject_type"],
                  body["subject_ref"], {"approval_id": rid, "reason": body.get("reason", "")})
        return self._json(201, {"id": rid, "status": "pending",
                                "note": "POC 期仅记录与审计，不作为在线放行依据（ADR-A3）"})

    def _decide_approval(self, rid, body):
        decision = str(body.get("decision", "")).lower()
        actor = body.get("decided_by") or body.get("actor") or ""
        if decision not in ("approve", "reject"):
            return self._err(400, "bad_decision", "decision 必须是 approve 或 reject")
        if not actor:
            return self._err(400, "missing_field", "缺少必填字段：decided_by")
        status = "approved" if decision == "approve" else "rejected"
        with db_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE ga.approval_request SET status=%s, decided_by=%s, decided_at=now(), "
                "comment=%s WHERE id=%s AND status='pending' RETURNING *",
                (status, actor, body.get("comment", ""), rid),
            )
            row = cur.fetchone()
            if not row:
                cur.execute("SELECT status FROM ga.approval_request WHERE id=%s", (rid,))
                exists = cur.fetchone()
                if not exists:
                    return self._err(404, "not_found", f"审批 {rid} 不存在")
                return self._err(409, "already_decided", f"审批已处于 {exists['status']} 状态")
            audit(cur, actor, f"approval.{status}", row["subject_type"], row["subject_ref"],
                  {"approval_id": rid, "comment": body.get("comment", "")})
        return self._json(200, row)


def main() -> int:
    ok, err = db_ok()
    print(f"[ga] DB({DB_CONF['host']}:{DB_CONF['port']}/{DB_CONF['dbname']}) ok={ok} {err}", flush=True)
    threading.Thread(target=consume_loop, daemon=True).start()
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"[ga] listening on {BIND}:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
