"""门户配置：全部来自环境变量，便于在不同段（S2/S3）切换，不硬编码环境地址。"""

import os


def _sqlalchemy_to_dsn() -> str:
    """从 POSTGRES_* 环境变量拼 DSN（经 PgBouncer 6432，硬约束 2：不直连 5432）。"""
    host = os.getenv("GOV_METRICS_DB_HOST", "pgbouncer")
    port = os.getenv("GOV_METRICS_DB_PORT", "6432")
    db = os.getenv("GOV_METRICS_DB_NAME", "gov_metrics")
    user = os.getenv("GOV_METRICS_DB_USER", "portal_ro")
    pw = os.getenv("GOV_METRICS_DB_PASSWORD", "")
    return f"host={host} port={port} dbname={db} user={user} password={pw}"


DSN = _sqlalchemy_to_dsn()

# Governance Agent（S3 提供）：未就绪时门户按设计"降级显示"
GOVERNANCE_API_URL = os.getenv("GOVERNANCE_API_URL", "http://governance-agent:8080")
GOVERNANCE_API_TIMEOUT = float(os.getenv("GOVERNANCE_API_TIMEOUT", "3"))

# 各能力入口（浏览器侧可点击；隧道类界面需用户本机已开 SSH 隧道）
ENTRY_LINKS = {
    "资产目录 / 血缘 / 质量（OpenMetadata）": os.getenv("PORTAL_LINK_OPENMETADATA", "http://localhost:8585/"),
    "治理 BI 看板（Superset）": os.getenv("PORTAL_LINK_SUPERSET", "http://localhost:8088"),
    "批处理作业（Airflow）": os.getenv("PORTAL_LINK_AIRFLOW", "http://localhost:8080"),
    "采集与告警监控（Prometheus）": os.getenv("PORTAL_LINK_PROMETHEUS", "http://localhost:9090"),
    "告警路由（Alertmanager）": os.getenv("PORTAL_LINK_ALERTMANAGER", "http://localhost:9093"),
}

# POC 期的能力边界声明（写进界面，避免误用）
POC_NOTES = {
    "scenario_e": (
        "POC 期口径（ADR-A3）：**只做事后审计 + 主动告警，不做事前拦截**，"
        "因此本页不提供在线审批放行功能；审批能力属后续阶段。"
    ),
    "pii": "PII 识别由 Presidio **离线批量**完成（已移出在线路径），不在本门户在线调用。",
}
