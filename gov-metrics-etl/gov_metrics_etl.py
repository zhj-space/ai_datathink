"""gov_metrics 指标 ETL（首版：资产覆盖率 + 质量趋势）。

依据：设计方案 ADR-A6（指标层解耦）、S2-5 第 3 件（首版只做资产覆盖率 + 质量趋势）。
纪律：
  * 取数**只走 OpenMetadata REST API**（search 聚合 + dataQuality 端点），**不读 OM 内部表**；
  * 写库**只经 PgBouncer 6432**（硬约束 2），不直连 5432；
  * 幂等：每次运行一个 run_ts，重复执行同秒内不产生重复行（ON CONFLICT DO UPDATE）。

运行环境：POC 宿主机（python3 标准库即可），由 /etc/cron.d/ai-governance-gov-metrics 每小时触发。
S3 起本逻辑迁入 Governance Agent（当前为过渡实现）。
"""

import base64
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ENVF = "/data/ai-governance/.env"
OM_BASE = os.environ.get("OM_BASE", "http://127.0.0.1:8585/api/v1")
# 注：2026-09-18 443 收敛后，OM 不再经 Traefik 443，改走**回环 8585**（SSH 隧道口径）。
# 此处用环境变量兜底，便于后续换成 OM 容器内地址（如 http://openmetadata-server:8585/api/v1）。
PG_CONTAINER = "ai-governance-poc-postgres-1"
PG_HOST = "pgbouncer"
PG_PORT = "6432"
ETL_DB = "gov_metrics"
ETL_USER = "gov_metrics_writer"
ETL_VERSION = "v1.0"

# 资产类型 → OM 检索索引（口径：只统计"数据资产"，不含 user/tag 等组织类实体）
ASSET_INDICES = {
    "table": "table_search_index",
    "database": "database_search_index",
    "databaseSchema": "database_schema_search_index",
    "dashboard": "dashboard_search_index",
    "pipeline": "pipeline_search_index",
    "topic": "topic_search_index",
    "mlmodel": "mlmodel_search_index",
    "container": "container_search_index",
}

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def env(key):
    with open(ENVF, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return ""


def om(path, token=None, method="GET"):
    req = urllib.request.Request(OM_BASE + path, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {"raw": exc.read().decode("utf-8", "replace")[:200]}
    except Exception as exc:  # noqa: BLE001
        return -1, {"error": str(exc)}


def login():
    pw = env("OPENMETADATA_ADMIN_PASSWORD")
    body = json.dumps({
        "email": "admin@open-metadata.org",
        "password": base64.b64encode(pw.encode()).decode(),
    }).encode()
    req = urllib.request.Request(OM_BASE + "/users/login", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
        return json.loads(r.read())["accessToken"]


def search_total(token, index, must_not_field=None):
    params = {"q": "*", "index": index, "size": 0, "from": 0}
    if must_not_field:
        dsl = {"query": {"bool": {"must_not": [{"exists": {"field": must_not_field}}]}}}
        params["query_filter"] = json.dumps(dsl)
    code, res = om("/search/query?" + urllib.parse.urlencode(params), token)
    if code != 200:
        raise RuntimeError("search %s 失败: HTTP %s %s" % (index, code, res))
    return res.get("hits", {}).get("total", {}).get("value", 0)


def sql_lit(value):
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def psql(sql):
    """经 PgBouncer(6432) 执行 SQL（从 postgres 容器内发起，符合硬约束 2）。"""
    writer_pw = env("GOV_METRICS_WRITER_PASSWORD")
    cmd = ["docker", "exec", "-i", "-e", "PGPASSWORD=" + writer_pw, PG_CONTAINER, "psql",
           "-h", PG_HOST, "-p", PG_PORT, "-U", ETL_USER, "-d", ETL_DB,
           "-v", "ON_ERROR_STOP=1", "-f", "-"]
    proc = subprocess.run(cmd, input=sql.encode(), capture_output=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError("psql 失败: " + proc.stderr.decode("utf-8", "replace")[:400])
    return proc.stdout.decode("utf-8", "replace")


def collect_expected():
    """读取资产覆盖率**分母**（`gov_metrics.asset_expected`，由 coverage_denominator.py 盘点写入）。

    口径：分母按 entity_type 求和（多源时即各源应采集数之和）；表为空或某类型无记录 ⇒ 该类型返回 None，
    `asset_expected_count`/`asset_coverage_pct` 仍写 NULL（**"未盘点"与"覆盖率为 0"必须可区分**）。
    """
    out = psql("SELECT entity_type || '|' || sum(expected_count) FROM gov_metrics.asset_expected GROUP BY entity_type;")
    expected = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        entity_type, count = line.split("|", 1)
        try:
            expected[entity_type] = int(count)
        except ValueError:
            continue
    return expected


def collect_asset_metrics(token):
    rows = []
    for entity_type, index in ASSET_INDICES.items():
        total = search_total(token, index)
        if total == 0:
            rows.append((entity_type, 0, 0, 0, 0))
            continue
        rows.append((
            entity_type,
            total,
            search_total(token, index, "owners"),
            search_total(token, index, "description"),
            search_total(token, index, "tags"),
        ))
    return rows


def collect_quality_metrics(token):
    """质量趋势：优先用 testSuites/executionSummary；无数据时写零值汇总行。"""
    code, summary = om("/dataQuality/testSuites/executionSummary", token)
    suite_total_code, suites = om("/dataQuality/testSuites?limit=1000&fields=tests", token)
    suite_count = suites.get("paging", {}).get("total", 0) if suite_total_code == 200 else 0

    passed = failed = aborted = total = 0
    if code == 200 and isinstance(summary, dict):
        payload = summary.get("data", summary)
        passed = payload.get("success", payload.get("passed", 0)) or 0
        failed = payload.get("failed", 0) or 0
        aborted = payload.get("aborted", 0) or 0
        total = payload.get("total", (passed + failed + aborted)) or 0
    return [( "__all__", passed, failed, aborted, total, suite_count, code) ]


def main():
    started = time.time()
    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00")
    rows_written = 0
    error = None
    try:
        token = login()
        asset_rows = collect_asset_metrics(token)
        quality_rows = collect_quality_metrics(token)
        expected_map = collect_expected()

        sql = ["BEGIN;"]
        for entity_type, total, miss_owner, miss_desc, miss_tags in asset_rows:
            complete = None
            if total > 0:
                complete = round((total - miss_owner) * 100.0 / total, 2)
            # 覆盖率：分母来自盘点参考表（未盘点 ⇒ 两个字段都为 NULL，不与"覆盖率 0"混淆）
            expected = expected_map.get(entity_type)
            coverage = round(total * 100.0 / expected, 2) if expected else None
            sql.append(
                "INSERT INTO gov_metrics.asset_coverage_snapshot "
                "(run_ts, entity_type, asset_total_count, asset_expected_count, asset_coverage_pct, "
                " missing_owner, missing_description, missing_tags, governance_completeness_pct) VALUES ("
                f"{sql_lit(run_ts)}, {sql_lit(entity_type)}, {total}, {sql_lit(expected)}, {sql_lit(coverage)}, "
                f"{miss_owner}, {miss_desc}, {miss_tags}, {sql_lit(complete)}) "
                "ON CONFLICT (run_ts, entity_type) DO UPDATE SET "
                "asset_total_count = EXCLUDED.asset_total_count, "
                "asset_expected_count = EXCLUDED.asset_expected_count, "
                "asset_coverage_pct = EXCLUDED.asset_coverage_pct, "
                "missing_owner = EXCLUDED.missing_owner, "
                "missing_description = EXCLUDED.missing_description, "
                "missing_tags = EXCLUDED.missing_tags, "
                "governance_completeness_pct = EXCLUDED.governance_completeness_pct;"
            )
            rows_written += 1

        for suite, passed, failed, aborted, total, suite_count, src_code in quality_rows:
            rate = round(passed * 100.0 / total, 2) if total else None
            sql.append(
                "INSERT INTO gov_metrics.quality_trend_snapshot "
                "(run_ts, suite_name, passed, failed, aborted, total, pass_rate_pct) VALUES ("
                f"{sql_lit(run_ts)}, {sql_lit(suite)}, {passed}, {failed}, {aborted}, {total}, {sql_lit(rate)}) "
                "ON CONFLICT (run_ts, suite_name) DO UPDATE SET "
                "passed = EXCLUDED.passed, failed = EXCLUDED.failed, aborted = EXCLUDED.aborted, "
                "total = EXCLUDED.total, pass_rate_pct = EXCLUDED.pass_rate_pct;"
            )
            rows_written += 1

        duration_ms = int((time.time() - started) * 1000)
        sql.append(
            "INSERT INTO gov_metrics.etl_run (run_ts, status, duration_ms, rows_written, etl_version, error) VALUES ("
            f"{sql_lit(run_ts)}, 'success', {duration_ms}, {rows_written}, {sql_lit(ETL_VERSION)}, NULL) "
            "ON CONFLICT (run_ts) DO UPDATE SET status = 'success', duration_ms = EXCLUDED.duration_ms, "
            "rows_written = EXCLUDED.rows_written, etl_version = EXCLUDED.etl_version, error = NULL;"
        )
        sql.append("COMMIT;")
        psql("\n".join(sql))
        print(f"[{run_ts}] ETL 成功：写入 {rows_written} 行，用时 {duration_ms}ms")
        for row in asset_rows:
            print("   资产 %-16s total=%s missing_owner=%s" % (row[0], row[1], row[2]))
        return 0
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:500]
        print(f"[{run_ts}] ETL 失败：{error}", file=sys.stderr)
        try:
            psql(
                "INSERT INTO gov_metrics.etl_run (run_ts, status, duration_ms, rows_written, etl_version, error) VALUES ("
                f"{sql_lit(run_ts)}, 'failed', {int((time.time() - started) * 1000)}, 0, "
                f"{sql_lit(ETL_VERSION)}, {sql_lit(error)}) "
                "ON CONFLICT (run_ts) DO UPDATE SET status = 'failed', error = EXCLUDED.error;"
            )
        except Exception as exc2:  # noqa: BLE001
            print("  失败状态也未能写入：%s" % exc2, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
