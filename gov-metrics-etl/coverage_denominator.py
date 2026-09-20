#!/usr/bin/env python3
"""资产覆盖率**分母**盘点工具（S2 准出判据 1 的取数器）。

作用：把"源侧应采集的对象数"盘点出来（覆盖率分母），并（可选）与 OpenMetadata 已登记对象比对，
      输出**缺失清单**；`--mode apply` 时写入 `gov_metrics.asset_expected`，由 ETL 每轮读取计算覆盖率。

为什么要有它：`asset_coverage_snapshot` 由 ETL 每小时重写，原来的 `asset_expected_count` 恒为 NULL；
分母必须放在"只由盘点更新"的参考表里，否则会被 ETL 覆盖回 NULL。

**默认 dry-run：不写任何数据**（先看结果，再决定是否落库）。

依赖：**无 Python 数据库驱动** —— 计数 SQL 交给客户端容器执行（PG 用 `postgres:16-alpine`，MySQL 用 `mysql:8`）；
     写库复用 ETL 的方式（`docker exec <postgres容器> psql` 经 PgBouncer 6432）。

用法：
  python3 coverage_denominator.py --engine pg --host <源库> --port 5432 --db <库> \
      --user <只读账号> --password-env SRC_RO_PASSWORD --include schema_a,schema_b \
      --source-ref <source-db-1> [--with-om] [--client-network data-net] [--mode apply]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ENVF = "/data/ai-governance/.env"
PG_CONTAINER = "ai-governance-poc-postgres-1"
PG_HOST, PG_PORT, PG_DB = "pgbouncer", "6432", "gov_metrics"


def env(key: str) -> str:
    """先查进程环境，再查服务器 .env（与 ETL 同口径，凭据不入仓库）。"""
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


def run_client(engine: str, host: str, port: int, db: str, user: str, password: str,
               sql: str, client_network: str | None) -> str:
    """在客户端容器里执行只读查询，返回原始输出（`-A -t` / `-N -B`，分隔符 |）。"""
    net = ["--network", client_network] if client_network else []
    if engine == "pg":
        cmd = ["docker", "run", "--rm", "-i", *net, "-e", f"PGPASSWORD={password}",
               "postgres:16-alpine", "psql", "-h", host, "-p", str(port), "-U", user, "-d", db,
               "-A", "-t", "-F", "|", "-v", "ON_ERROR_STOP=1", "-c", sql]
    else:
        cmd = ["docker", "run", "--rm", "-i", *net, "-e", f"MYSQL_PWD={password}",
               "mysql:8", "mysql", "-h", host, "-P", str(port), "-u", user, "-N", "-B", "-e", sql]
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"客户端容器查询失败({engine}): "
                           + proc.stderr.decode("utf-8", "replace")[:400])
    return proc.stdout.decode("utf-8", "replace")


def count_sql(engine: str, entity_type: str, include: list[str], exclude: list[str]) -> str:
    """按实体类型给出计数 SQL。

    ⚠ 映射假设（**需人工确认，见《…S2准出量测口径与抽检模板.md》§6-1**）：
      * PG：table → 基表数；databaseSchema → 纳入的 schema 数；database → 1（源库本身）
      * MySQL：table → 基表数；database → 纳入的库数；databaseSchema → 与 database 同口径（MySQL 库≈schema）
    """
    inc = ",".join("'" + s + "'" for s in include)
    exc = ",".join("'" + s + "'" for s in exclude)
    sys_schemas = ("'pg_catalog','information_schema'" if engine == "pg"
                   else "'mysql','performance_schema','sys','information_schema'")
    if entity_type == "table":
        where = [f"table_type = 'BASE TABLE'", f"table_schema NOT IN ({sys_schemas})"]
        if inc:
            where.append(f"table_schema IN ({inc})")
        if exc:
            where.append(f"table_schema NOT IN ({exc})")
        return ("SELECT table_schema || '|' || count(*) FROM information_schema.tables WHERE "
                + " AND ".join(where) + " GROUP BY table_schema ORDER BY 1;")
    if entity_type in ("database", "databaseSchema"):
        if engine == "pg" and entity_type == "databaseSchema":
            where = [f"schema_name NOT IN ({sys_schemas})"]
            if inc:
                where.append(f"schema_name IN ({inc})")
            if exc:
                where.append(f"schema_name NOT IN ({exc})")
            return ("SELECT 'schemas' || '|' || count(*) FROM information_schema.schemata WHERE "
                    + " AND ".join(where) + ";")
        where = [f"table_schema NOT IN ({sys_schemas})"]
        if inc:
            where.append(f"table_schema IN ({inc})")
        if exc:
            where.append(f"table_schema NOT IN ({exc})")
        return ("SELECT 'schemas' || '|' || count(DISTINCT table_schema) FROM information_schema.tables WHERE "
                + " AND ".join(where) + ";")
    raise ValueError(f"不支持的 entity_type: {entity_type}")


def om_login() -> str:
    pw = env("OPENMETADATA_ADMIN_PASSWORD")
    base = os.environ.get("OM_BASE", "http://openmetadata-server:8585/api/v1")
    body = json.dumps({"email": "admin@open-metadata.org",
                       "password": base64.b64encode(pw.encode()).decode()}).encode()
    req = urllib.request.Request(base + "/users/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"{}").get("accessToken", "")


OM_INDEX = {"table": "table_search_index", "database": "database_search_index",
            "databaseSchema": "database_schema_search_index"}


def om_registered_names(token: str, entity_type: str, limit_pages: int = 20) -> set[str]:
    """取 OM 已登记对象的 fullyQualifiedName 集合（分页直到取完或到页数上限）。"""
    base = os.environ.get("OM_BASE", "http://openmetadata-server:8585/api/v1")
    index = OM_INDEX.get(entity_type)
    if not index:
        return set()
    names: set[str] = set()
    for page in range(limit_pages):
        params = urllib.parse.urlencode({"q": "*", "index": index, "size": 1000, "from": page * 1000})
        req = urllib.request.Request(f"{base}/search/query?{params}")
        req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            print(f"  [warn] OM 查询失败 HTTP {exc.code}（index={index}）", file=sys.stderr)
            break
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break
        for hit in hits:
            src = hit.get("_source", {})
            names.add(src.get("fullyQualifiedName") or src.get("name") or "")
        if len(hits) < 1000:
            break
    names.discard("")
    return names


def psql_write(sql: str) -> str:
    """经 PgBouncer(6432) 写 gov_metrics（与 ETL 同路径：从 postgres 容器内发起）。"""
    writer_pw = env("GOV_METRICS_WRITER_PASSWORD")
    cmd = ["docker", "exec", "-i", "-e", "PGPASSWORD=" + writer_pw, PG_CONTAINER, "psql",
           "-h", PG_HOST, "-p", PG_PORT, "-U", "gov_metrics_writer", "-d", PG_DB,
           "-v", "ON_ERROR_STOP=1", "-f", "-"]
    proc = subprocess.run(cmd, input=sql.encode(), capture_output=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError("写库失败: " + proc.stderr.decode("utf-8", "replace")[:400])
    return proc.stdout.decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser(description="资产覆盖率分母盘点（默认 dry-run）")
    ap.add_argument("--engine", choices=["pg", "mysql"], required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--db", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password-env", default="SRC_RO_PASSWORD",
                    help="口令所在的环境变量名（先查进程环境，再查 /data/ai-governance/.env）")
    ap.add_argument("--include", default="", help="纳入的 schema/库清单（逗号分隔，空=全部非系统）")
    ap.add_argument("--exclude", default="", help="排除项（逗号分隔）")
    ap.add_argument("--entity-types", default="table", help="默认 table；可加 databaseSchema,database")
    ap.add_argument("--source-ref", default="<source-db-1>", help="源标识（写入参考表的主键之一）")
    ap.add_argument("--scope-note", default="", help="纳入范围说明（口径留痕，建议填）")
    ap.add_argument("--with-om", action="store_true", help="与 OM 已登记对象比对并输出缺失清单")
    ap.add_argument("--client-network", default="", help="客户端容器加入的网络（内网目标用 data-net，外部源留空）")
    ap.add_argument("--mode", choices=["dry-run", "apply"], default="dry-run")
    ap.add_argument("--report", default="/tmp/coverage_report.json")
    args = ap.parse_args()

    port = args.port or (5432 if args.engine == "pg" else 3306)
    password = env(args.password_env)
    if not password:
        print(f"[FATAL] 未取到口令：环境变量/`.env` 中缺少 {args.password_env}", file=sys.stderr)
        return 2

    includes = [s.strip() for s in args.include.split(",") if s.strip()]
    excludes = [s.strip() for s in args.exclude.split(",") if s.strip()]
    entity_types = [t.strip() for t in args.entity_types.split(",") if t.strip()]

    token = ""
    if args.with_om:
        try:
            token = om_login()
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] OM 登录失败，跳过比对：{exc}", file=sys.stderr)

    report: dict = {"source_ref": args.source_ref, "engine": args.engine, "db": args.db,
                    "include": includes, "exclude": excludes, "mode": args.mode, "results": {}}
    upserts = []

    for entity_type in entity_types:
        sql = count_sql(args.engine, entity_type, includes, excludes)
        try:
            raw = run_client(args.engine, args.host, port, args.db, args.user, password,
                             sql, args.client_network or None)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {entity_type} 盘点失败：{exc}", file=sys.stderr)
            return 3
        per_schema, total = {}, 0
        for line in raw.splitlines():
            line = line.strip().rstrip("|")
            if not line or "|" not in line:
                continue
            name, cnt = line.rsplit("|", 1)
            try:
                per_schema[name] = int(cnt)
                total += int(cnt)
            except ValueError:
                continue
        item = {"expected_count": total, "per_schema": per_schema}

        if token:
            registered = om_registered_names(token, entity_type)
            item["om_registered_count"] = len(registered)
            expected_fqns = set()
            for schema_name in per_schema:
                expected_fqns.add(schema_name)
            missing_schemas = sorted(s for s in per_schema if not any(r.startswith(s + ".") or r == s for r in registered))
            item["missing_schema_count"] = len(missing_schemas)
            item["missing_schemas"] = missing_schemas[:50]
            item["coverage_pct_if_applied"] = (
                round(len(registered) * 100.0 / total, 2) if total else None)
        report["results"][entity_type] = item
        upserts.append((entity_type, total))
        print(f"[{entity_type}] 分母(expected)={total}  " +
              (f"OM已登记={item.get('om_registered_count')}  缺失schema={item.get('missing_schema_count')}  "
               f"覆盖率(若落库)={item.get('coverage_pct_if_applied')}%" if token else "(未做 OM 比对)"))

    with open(args.report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n报告已写入 {args.report}")

    if args.mode == "apply":
        sql = ["BEGIN;"]
        for entity_type, total in upserts:
            note = args.scope_note.replace("'", "''")
            sql.append(
                "INSERT INTO gov_metrics.asset_expected (source_ref, entity_type, scope_note, expected_count) "
                f"VALUES ('{args.source_ref}', '{entity_type}', '{note}', {total}) "
                "ON CONFLICT (source_ref, entity_type) DO UPDATE SET "
                "scope_note = EXCLUDED.scope_note, expected_count = EXCLUDED.expected_count, updated_at = now();")
        sql.append("COMMIT;")
        out = psql_write("\n".join(sql))
        print("已写入 gov_metrics.asset_expected：\n" + out.strip())
        print("提示：下一轮 ETL（每小时 :25）会自动计算 asset_coverage_pct；也可手动跑一次 ETL。")
    else:
        print("dry-run：**未写任何数据**（确认无误后加 --mode apply 落库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
