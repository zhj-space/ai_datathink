-- Governance Agent 首版表结构（S3-6）
-- 依据：《…S3-6治理Agent接口规格.md》§4；ADR-A3（只做事后审计 + 主动告警，不做事前拦截）
-- 纪律：自持表结构；经 PgBouncer 6432 访问；角色最小权限；不碰 OpenMetadata 内表

-- 0) 独立库（由部署脚本创建，此处仅声明口径）
--    CREATE DATABASE governance_agent OWNER ga_writer;
--    GA 的状态与 gov_metrics（Superset 只读）分离，避免 ADR-A6 的只读面被污染

CREATE SCHEMA IF NOT EXISTS ga;

-- 1) 告警事件（消费 Kafka governance.alerts，幂等落库）
CREATE TABLE IF NOT EXISTS ga.alert_event (
    id               bigserial    PRIMARY KEY,
    alert_key        text         NOT NULL UNIQUE,   -- 幂等键：payload 里的 id/alert_key，否则原始消息 sha256
    alert_name       text         NOT NULL DEFAULT 'unknown',
    severity         text         NOT NULL DEFAULT 'unknown',
    source           text         NOT NULL DEFAULT 'governance.alerts',
    payload          jsonb        NOT NULL,
    enhanced_summary text,                            -- 规则化增强（LLM 增强属 S4）
    enhanced_at      timestamptz,
    received_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alert_received_at ON ga.alert_event (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_severity    ON ga.alert_event (severity);

-- 2) 审批请求（场景 E：审批网关；**只记录不拦截**）
CREATE TABLE IF NOT EXISTS ga.approval_request (
    id            uuid         PRIMARY KEY,
    requester     text         NOT NULL,
    subject_type  text         NOT NULL,              -- table / column / role / dataset ...
    subject_ref   text         NOT NULL,              -- 目标引用（如 schema.table）
    reason        text         NOT NULL DEFAULT '',
    status        text         NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_by    text,
    decided_at    timestamptz,
    comment       text,
    created_at    timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_approval_status ON ga.approval_request (status, created_at DESC);

-- 3) 审计轨迹（谁在何时对什么做了什么）
CREATE TABLE IF NOT EXISTS ga.audit_log (
    id          bigserial    PRIMARY KEY,
    actor       text         NOT NULL,
    action      text         NOT NULL,
    entity_type text         NOT NULL,
    entity_ref  text         NOT NULL,
    detail      jsonb        NOT NULL DEFAULT '{}'::jsonb,
    at          timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON ga.audit_log (entity_type, entity_ref, at DESC);

-- 4) 权限：Agent 读写角色 + 只读角色（最小权限）
--    ga_writer：仅本库本 schema 的读写（**不给 SUPERUSER/CREATEDB/CREATEROLE**）
--    ga_ro    ：仅 SELECT（供门户/后续查询用；不得写）
GRANT USAGE, CREATE ON SCHEMA ga TO ga_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ga TO ga_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ga TO ga_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA ga
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ga_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA ga
    GRANT USAGE, SELECT ON SEQUENCES TO ga_writer;

GRANT USAGE ON SCHEMA ga TO ga_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA ga TO ga_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA ga GRANT SELECT ON TABLES TO ga_ro;
REVOKE CREATE ON SCHEMA ga FROM ga_ro;     -- fail-safe：只读角色不得建对象

-- 4b) 作业图级血缘上报（D1，2026-09-19）
--     定位：ADR-A7 要求"作业图级血缘（作业启动/变更时上报）"，但实测 Flink 侧无法产出
--     （1.x 不支持 SQL；2.x 所需 SPI 在 1.20.1 不存在；flink-cdc 未实现 FLIP-314）
--     ⇒ 改为**自研侧（GA）上报**。本表既是"**期望边集合**"（对账基准），也是上报结果的留痕。
--     幂等键 = (job_key, from_type, from_fqn, to_type, to_fqn)：同一作业重复上报不会新增行。
CREATE TABLE IF NOT EXISTS ga.lineage_edge (
    id          bigserial   PRIMARY KEY,
    job_key     text        NOT NULL,                    -- 作业标识（上报与对账的主键）
    from_type   text        NOT NULL,                    -- table / topic
    from_fqn    text        NOT NULL,                    -- OM 实体 FQN
    to_type     text        NOT NULL,
    to_fqn      text        NOT NULL,
    source      text        NOT NULL DEFAULT 'PipelineLineage',
    sql_query   text,                                    -- 作业 SQL 原文（追溯用）
    status      text        NOT NULL DEFAULT 'planned'
                CHECK (status IN ('planned', 'applied', 'entity_missing', 'failed', 'removed', 'drift')),
    om_response text,                                    -- OM 返回摘要（失败时便于定位）
    reported_at timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_key, from_type, from_fqn, to_type, to_fqn)
);
CREATE INDEX IF NOT EXISTS idx_lineage_job ON ga.lineage_edge (job_key, updated_at DESC);

GRANT SELECT, INSERT, UPDATE ON ga.lineage_edge TO ga_writer;
GRANT USAGE, SELECT ON SEQUENCE ga.lineage_edge_id_seq TO ga_writer;
GRANT SELECT ON ga.lineage_edge TO ga_ro;

-- 4c) 已建表的 CHECK 约束升级（2026-09-19 加 'drift' 状态，用于对账发现漂移）
--     CREATE TABLE IF NOT EXISTS 不会改已存在表的约束，故对既有环境显式升级一次（幂等）。
DO $$
DECLARE c text;
BEGIN
    SELECT conname INTO c FROM pg_constraint
     WHERE conrelid = 'ga.lineage_edge'::regclass AND contype = 'c';
    IF c IS NOT NULL AND c <> 'lineage_edge_status_check_v2' THEN
        EXECUTE format('ALTER TABLE ga.lineage_edge DROP CONSTRAINT %I', c);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'lineage_edge_status_check_v2') THEN
        ALTER TABLE ga.lineage_edge ADD CONSTRAINT lineage_edge_status_check_v2
          CHECK (status IN ('planned', 'applied', 'entity_missing', 'failed', 'removed', 'drift'));
    END IF;
END $$;

-- 5) **库级 CONNECT 收敛**（沿用 gov_metrics 的既有做法，2026-09-18 补齐）
--    默认所有角色都有 PUBLIC 的 CONNECT ⇒ 新角色能连上**任何**库。本环境统一收敛为"显式授权"：
--      REVOKE CONNECT ON DATABASE governance_agent FROM PUBLIC;
--      GRANT  CONNECT ON DATABASE governance_agent TO ga_writer, ga_ro;
--    ⚠ **权限变更后必须回收 PgBouncer 池连接**（`RECONNECT;` 经 6432 管理台），
--      否则已缓存的 idle 服务端连接会让变更"看起来没生效"（本环境 2026-09-18 实测踩到）。
