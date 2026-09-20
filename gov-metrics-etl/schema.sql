-- gov_metrics 指标层（ADR-A6）
--
-- 定位：治理指标（资产覆盖率、质量趋势；告警趋势 Phase 2 补）的**自持表结构**，
--       由 ETL 每小时写入，Superset **只读**本库本 schema，**不得直连 OpenMetadata 内库/内表**。
-- 依据：设计方案 4.5 / 9.6（ADR-A6）、"索引与分区仅适用于 gov_metrics 等自研表"。
-- 部署：以超级用户（postgres）在服务器上执行；ETL 以 gov_metrics_writer 经 PgBouncer(6432) 写入。
--
-- 口径声明（务必与看板一致，避免混用）：
--   * asset_expected_count   = 数据源侧应被采集的对象数（由 S2-2 接入日志/盘点写入；未接入前为 NULL）
--   * asset_coverage_pct     = asset_total_count / asset_expected_count × 100（**仅在该源已盘点时才有值**）
--   * governance_completeness_pct = (asset_total_count - missing_owner) / asset_total_count × 100
--         ——这是"已登记负责人"的占比，属**治理完备度**，**不等于**资产覆盖率，不得混称。

-- 1) 指标库与 schema（同实例、独立库，确保 Superset 在库级就够不到 OM 内表）
--    注意：库名与 schema 名同名，便于"独立 schema gov_metrics"这一口径在两种读法下都成立。
--    由部署脚本创建（CREATE DATABASE 不能在事务中执行）。

-- 2) 自持表结构
CREATE SCHEMA IF NOT EXISTS gov_metrics;

CREATE TABLE IF NOT EXISTS gov_metrics.asset_coverage_snapshot (
    run_ts                      timestamptz      NOT NULL,      -- 数据截至时间（UTC）；看板须展示其最大值
    entity_type                 text             NOT NULL,      -- table / database / dashboard / pipeline / topic ...
    asset_total_count           bigint           NOT NULL,      -- OM 中已登记的该类资产数
    asset_expected_count        bigint,                          -- 源侧应采集数（未接入源前 NULL）
    asset_coverage_pct          numeric(5,2),                    -- 见文件头口径声明
    missing_owner               bigint           NOT NULL DEFAULT 0,
    missing_description         bigint           NOT NULL DEFAULT 0,
    missing_tags                bigint           NOT NULL DEFAULT 0,
    governance_completeness_pct numeric(5,2),                    -- 已有负责人的占比
    PRIMARY KEY (run_ts, entity_type)
);

CREATE TABLE IF NOT EXISTS gov_metrics.quality_trend_snapshot (
    run_ts          timestamptz  NOT NULL,
    suite_name      text         NOT NULL DEFAULT '__all__',     -- '__all__' 为汇总行
    passed          bigint       NOT NULL DEFAULT 0,
    failed          bigint       NOT NULL DEFAULT 0,
    aborted         bigint       NOT NULL DEFAULT 0,
    total           bigint       NOT NULL DEFAULT 0,
    pass_rate_pct   numeric(5,2),
    PRIMARY KEY (run_ts, suite_name)
);

CREATE TABLE IF NOT EXISTS gov_metrics.etl_run (
    run_ts       timestamptz  PRIMARY KEY,
    status       text         NOT NULL,        -- success / failed
    duration_ms  integer,
    rows_written integer,
    etl_version  text,
    error        text
);

-- 2b) 资产覆盖率**分母**参考表（2026-09-18 新增）
--     为什么单独建表：`asset_coverage_snapshot` 每小时由 ETL 重写（原来的 asset_expected_count 恒为 NULL），
--     分母必须放在"只由盘点更新"的参考表里，由 ETL 每轮读取后计算覆盖率，否则会被 ETL 覆盖回 NULL。
--     写入方：`coverage_denominator.py`（源侧盘点工具，默认 dry-run）；读取方：ETL。
CREATE TABLE IF NOT EXISTS gov_metrics.asset_expected (
    source_ref     text        NOT NULL,       -- 源标识（占位，如 <source-db-1>）
    entity_type    text        NOT NULL,       -- table / database / databaseSchema / ...
    scope_note     text        NOT NULL DEFAULT '',  -- **纳入范围说明**（库/schema 清单、排除项）——口径留痕
    expected_count bigint      NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_ref, entity_type)
);

-- 3) 索引（高频查询：按时间倒序取最新快照）
CREATE INDEX IF NOT EXISTS idx_asset_cov_run_ts
    ON gov_metrics.asset_coverage_snapshot (run_ts DESC);
CREATE INDEX IF NOT EXISTS idx_quality_run_ts
    ON gov_metrics.quality_trend_snapshot (run_ts DESC);

-- 4) 权限：ETL 写入角色 + Superset 只读角色
--    gov_metrics_writer：仅本库，具备建表/写入能力
--    superset_ro       ：仅本 schema 的 SELECT，无任何其他权限
GRANT USAGE, CREATE ON SCHEMA gov_metrics TO gov_metrics_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gov_metrics TO gov_metrics_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA gov_metrics
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gov_metrics_writer;

GRANT USAGE ON SCHEMA gov_metrics TO superset_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA gov_metrics TO superset_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA gov_metrics GRANT SELECT ON TABLES TO superset_ro;

-- 明确收回：不允许 Superset 角色写任何对象（fail-safe）
REVOKE CREATE ON SCHEMA gov_metrics FROM superset_ro;

-- 5) 分区说明（设计允许对自研表分区）：POC 数据量极小，**暂不分区**；
--    当 asset_coverage_snapshot 行数达到数十万级时，按 run_ts 做月度分区（届时属独立变更）。
