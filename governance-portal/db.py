"""gov_metrics 只读访问（ADR-A6：门户/看板只读指标层，绝不直连 OM 内表）。"""

from __future__ import annotations

import psycopg2
import psycopg2.extras

import config


def _connect():
    conn = psycopg2.connect(config.DSN, connect_timeout=5)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def fetch(sql: str, params: tuple | None = None) -> list[dict]:
    with _connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params or ())
        return [dict(r) for r in cur.fetchall()]


def as_of() -> str | None:
    """看板/门户的"数据截至"（= 最近一次成功 ETL 的时间）。"""
    rows = fetch("select max(run_ts) as as_of from gov_metrics.asset_coverage_snapshot")
    value = rows[0]["as_of"] if rows else None
    return value.isoformat() if value else None


def latest_asset_coverage() -> list[dict]:
    return fetch(
        """
        select entity_type, asset_total_count, asset_expected_count, asset_coverage_pct,
               missing_owner, missing_description, missing_tags, governance_completeness_pct
          from gov_metrics.asset_coverage_snapshot
         where run_ts = (select max(run_ts) from gov_metrics.asset_coverage_snapshot)
         order by asset_total_count desc, entity_type
        """
    )


def latest_quality_trend() -> list[dict]:
    return fetch(
        """
        select suite_name, passed, failed, aborted, total, pass_rate_pct
          from gov_metrics.quality_trend_snapshot
         where run_ts = (select max(run_ts) from gov_metrics.quality_trend_snapshot)
         order by suite_name
        """
    )


def recent_etl_runs(limit: int = 10) -> list[dict]:
    return fetch(
        """
        select run_ts, status, duration_ms, rows_written, etl_version, error
          from gov_metrics.etl_run
         order by run_ts desc
         limit %s
        """,
        (limit,),
    )
