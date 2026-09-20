# AI 数据治理平台 · 单机版 S2-5｜gov_metrics 指标层与 ETL 首版

> **定位**：`执行步骤/` 下的部署与代码产物，由《部署步骤_单机版》S2-5 第 3 件展开，**不含新增架构决策**。
> **依据**：设计方案 **4.5**（指标层解耦、索引与分区只适用于自研表）、**ADR-A6**（gov_metrics 独立 schema；Superset 只读该 schema，**不直连 OM 内库/内表**）；S2-5 第 3 件（首版只做**资产覆盖率 + 质量趋势**，告警趋势 Phase 2 补）。
> **状态**：**已部署并验证通过**（含权限正反验证）；覆盖率分母依赖业务源（S2-2），当前为 NULL。
> **配套目录**：仓库 `gov-metrics-etl/`（`schema.sql` + `gov_metrics_etl.py` + README）。

---

## 1. 结论摘要

| 项 | 结果 |
|---|---|
| 库与 schema | **独立库 `gov_metrics`**，内含 **schema `gov_metrics`**（两种读法都成立：既是"独立 schema"，又在库级与 OM 内库隔离） |
| 自持表 | `asset_coverage_snapshot`（资产覆盖/完备度）、`quality_trend_snapshot`（质量趋势）、`etl_run`（运行日志与"数据截至"来源） |
| 角色 | `gov_metrics_writer`（ETL 写入，仅本库）、`superset_ro`（**仅 gov_metrics 的 SELECT**） |
| ETL | `/data/ai-governance/scripts/gov_metrics_etl.py`，每小时 **:25** 由 `/etc/cron.d/ai-governance-gov-metrics` 触发（与备份 :05/:15 错峰） |
| 取数方式 | **只走 OpenMetadata REST API**（`search` 聚合 + `dataQuality` 端点），**不读 OM 内部表** |
| 写入方式 | 经 **PgBouncer 6432**（从 postgres 容器内发起，硬约束 2：不直连 5432） |
| 首次运行 | ✅ 成功，写入 **9 行**（8 行资产 + 1 行质量），耗时 **399ms**；`数据截至 = 2026-09-17 10:01:02+00` |
| **权限反向验证** | ✅ `superset_ro` 连 `openmetadata_db` / `airflow_db` 均被**库级拒绝**；读 `gov_metrics` 正常（8 行） |
| 对既有服务影响 | ✅ 重启 PgBouncer 后 OM 查询正常、ETL 正常；ingestion 因断连自动重启一次后恢复 healthy |

---

## 2. 口径声明（**不得混用**）

| 指标 | 定义 | 现状 |
|---|---|---|
| `asset_total_count` | OM 中已登记的该类资产数 | 有值 |
| `asset_expected_count` | **源侧应被采集的对象数** | **NULL**（依赖 S2-2 接入业务源后盘点写入） |
| `asset_coverage_pct` | `asset_total_count / asset_expected_count × 100`（**资产覆盖率**） | NULL（分母未定，**不得**用其他数替代） |
| `governance_completeness_pct` | `(total − missing_owner) / total × 100`（**治理完备度**，即已有负责人占比） | 有值（total=0 时为 NULL） |
| `quality_trend_snapshot` | 质量用例通过/失败趋势（首版） | 汇总行 `__all__`（当前全 0，**无业务源时为空属预期**） |

> 这一项刻意分开命名，避免"把治理完备度当资产覆盖率"的口径混用（对应本项目一贯的口径纪律）。

---

## 3. 部署记录

### 3.1 结构与权限（`schema.sql` 摘要）

```sql
-- 独立库 gov_metrics（CREATE DATABASE 由部署脚本执行，不在事务内）
CREATE SCHEMA IF NOT EXISTS gov_metrics;

CREATE TABLE gov_metrics.asset_coverage_snapshot (
    run_ts timestamptz NOT NULL, entity_type text NOT NULL,
    asset_total_count bigint NOT NULL, asset_expected_count bigint,
    asset_coverage_pct numeric(5,2),
    missing_owner bigint NOT NULL DEFAULT 0,
    missing_description bigint NOT NULL DEFAULT 0,
    missing_tags bigint NOT NULL DEFAULT 0,
    governance_completeness_pct numeric(5,2),
    PRIMARY KEY (run_ts, entity_type));

CREATE TABLE gov_metrics.quality_trend_snapshot (
    run_ts timestamptz NOT NULL, suite_name text NOT NULL DEFAULT '__all__',
    passed bigint NOT NULL DEFAULT 0, failed bigint NOT NULL DEFAULT 0,
    aborted bigint NOT NULL DEFAULT 0, total bigint NOT NULL DEFAULT 0,
    pass_rate_pct numeric(5,2), PRIMARY KEY (run_ts, suite_name));

CREATE TABLE gov_metrics.etl_run (
    run_ts timestamptz PRIMARY KEY, status text NOT NULL,
    duration_ms integer, rows_written integer, etl_version text, error text);

-- 权限：writer 可写；superset_ro 仅 SELECT
GRANT USAGE, CREATE ON SCHEMA gov_metrics TO gov_metrics_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA gov_metrics TO superset_ro;
REVOKE CREATE ON SCHEMA gov_metrics FROM superset_ro;

-- 库级 CONNECT 收敛（默认 PUBLIC 可连所有库 → 收回，只放行各自角色）
REVOKE CONNECT ON DATABASE openmetadata_db FROM PUBLIC;
GRANT  CONNECT ON DATABASE openmetadata_db TO openmetadata_user;
REVOKE CONNECT ON DATABASE airflow_db FROM PUBLIC;
GRANT  CONNECT ON DATABASE airflow_db TO airflow_user;
REVOKE CONNECT ON DATABASE gov_metrics FROM PUBLIC;
GRANT  CONNECT ON DATABASE gov_metrics TO gov_metrics_writer, superset_ro;
```

> 表属主已归 `gov_metrics_writer`（便于后续自行加索引/分区）。**分区**：POC 数据量极小，按设计"分区仅适用于自研表"但**暂不分区**；行数到数十万级时按 `run_ts` 做月度分区（独立变更）。

### 3.2 ETL 调度

```
# /etc/cron.d/ai-governance-gov-metrics
25 * * * * root /usr/bin/python3 /data/ai-governance/scripts/gov_metrics_etl.py >> /data/backup/log/gov_metrics_etl.log 2>&1
```

**幂等**：每次运行一个 `run_ts`，重复执行同秒内 `ON CONFLICT DO UPDATE`，不产生重复行；失败也会写 `etl_run(status='failed', error=...)`。
**过渡定位**：S3 引入 Governance Agent 后，本逻辑迁入 GA（当前为 S2 交付的过渡实现）。

---

## 4. 验证结果（实测）

### 4.1 ETL 运行与落库

```
[2026-09-17 10:01:02+00] ETL 成功：写入 9 行，用时 399ms
   资产 pipeline         total=1 missing_owner=1
   （其余 7 类资产 total=0 —— 尚未接入业务源，属预期）

run_ts                  | entity_type    | total | missing_owner | completeness_pct
2026-09-17 10:01:02+00  | pipeline       |     1 |             1 | 0.00
2026-09-17 10:01:02+00  | table/database/… |   0 |             0 | NULL
质量汇总： __all__ passed=0 failed=0 total=0
数据截至 = 2026-09-17 10:01:02+00        ← 看板"数据截至"时间戳来源
```

### 4.2 权限正反验证

| 验证 | 结果 |
|---|---|
| **正向**：`superset_ro` 读 `gov_metrics.asset_coverage_snapshot` | ✅ 可读（8 行） |
| **反向**：`superset_ro` 连 `openmetadata_db` | ✅ **`FATAL: permission denied for database "openmetadata_db"`** |
| **反向**：`superset_ro` 连 `airflow_db` | ✅ **`FATAL: permission denied for database "airflow_db"`** |
| **反向**：`superset_ro` 在 `gov_metrics` 建表 | ✅ `permission denied for schema gov_metrics` |
| 既有服务未受影响 | ✅ OM 查询正常、ETL 查询正常；ingestion 断连后自动重启并恢复 healthy |

---

## 5. 踩坑与重要发现（如实记录）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 权限收紧后，`superset_ro` **经 PgBouncer 仍能连** `openmetadata_db`（但直连 PG 已被正确拒绝） | **PgBouncer 会保留空闲的服务端连接**：该池中的 idle 连接是在收紧**之前**建立的，权限变更对既有连接不生效 | 重启 PgBouncer 回收池后复验 → 正确拒绝。**运维结论：任何库级/角色级权限变更，都必须回收 PgBouncer 池连接后才算生效**（写入本节与 AGENTS 纪律） |
| 2 | ETL 首次运行报 `SASL authentication failed` / `Password for user ...` | ETL 通过 `docker exec psql` 写库时**未传 `PGPASSWORD`** | 在 `docker exec` 上加 `-e PGPASSWORD=<writer 口令>`（口令取自 `.env`，不入仓库） |
| 3 | 重启 PgBouncer 后 `openmetadata-ingestion` 短暂重启（health: starting） | Airflow 组件在 DB 连接被切断时退出 | `restart: unless-stopped` 兜住，约 1 分钟后恢复 healthy；**属预期行为**，无需人工干预 |

---

## 6. 与设计边界的对齐（防越界）

| 设计口径 | 本环境落实 |
|---|---|
| ADR-A6：Superset **只读** gov_metrics，禁止直连 OM 内表 | 库级隔离 + 角色只授 SELECT；**反向验证**已通过（连 OM 库被拒） |
| ADR-A6：OM 升级不影响大盘（指标层解耦） | ETL 经 **API** 取数（`search` 聚合 + `dataQuality`），**未引用任何 OM 内表**；OM 表结构变化不影响 ETL |
| 看板标注"数据截至"，最长延迟 1h | 每行带 `run_ts`；`etl_run` 记录每次运行；小时级调度满足 ≤1h |
| 硬约束 2：所有服务经 PgBouncer 6432 | ETL 写库经 `pgbouncer:6432`；未使用 5432 |
| 分区仅适用于自研表 | 本表为自研表，允许分区；POC 暂不分区（已声明） |

---

## 7. 覆盖率分母机制（2026-09-18 新增）

**问题**：`asset_coverage_snapshot.asset_expected_count` 原为**硬编码 NULL**——分母无处存放；即便手工 UPDATE，也会被下一轮 ETL（每小时 :25）**重写回 NULL**。

**处置**：分母改为**独立参考表 + 盘点工具 + ETL 消费**三段式：

| 段 | 落地物 | 说明 |
|---|---|---|
| 存放 | **新表 `gov_metrics.asset_expected`**（`source_ref`/`entity_type`/`scope_note`/`expected_count`/`updated_at`） | 只由盘点更新，ETL 只读 ⇒ 不会被重写 |
| 取数 | `gov-metrics-etl/coverage_denominator.py`（部署于 `/data/ai-governance/scripts/`） | **默认 dry-run**；计数 SQL 交客户端容器执行（PG `postgres:16-alpine` / MySQL `mysql:8`），**无需 Python 数据库驱动**；`--with-om` 与 OM 已登记对象比对并输出**缺失清单**；`--scope-note` 留痕纳入范围 |
| 消费 | `gov_metrics_etl.py` 新增 `collect_expected()` | 每轮读参考表 → 写 `asset_expected_count` 与 `asset_coverage_pct`；**参考表无记录 ⇒ 仍写 NULL**（"未盘点"与"覆盖率 0"可区分） |

**实测（演练，无副作用）**：

```text
回归（参考表为空）      ETL 仍写 asset_expected_count=NULL / asset_coverage_pct=NULL  ✔ 改动前行为不变
盘点演练（POC 自身 PG）  --db openmetadata_db --include public --entity-types table
                        分母(expected)=195   OM已登记=0   缺失 schema=1   覆盖率(若落库)=0.00%
apply + ETL             asset_expected 写入 195 → ETL 快照 asset_expected_count=195、asset_coverage_pct=0.00  ✔
清演练行 + ETL          回到 NULL（未盘点）✔；参考表行数 0（无残留）
权限                    superset_ro 可 SELECT 参考表；INSERT → permission denied  ✔
```

> **纪律**：① 分母**只写在参考表**，不要写快照表；② 盘点范围（库/schema、排除项）必须用 `--scope-note` 留痕，改范围 = 改分母须同步《…S2准出量测口径与抽检模板.md》；③ **改服务器副本必须同步仓库本目录**（本轮实测漂移：只改服务器基址而漏改仓库，重新上传旧版后 ETL 连不上 OM）。

## 8. 待人工确认 / 后续

| # | 项 | 说明 |
|---|---|---|
| 1 | **资产覆盖率分母**（`asset_expected_count`） | 需 S2-2 接入业务源后由盘点/接入日志写入；在此之前 `asset_coverage_pct` 保持 NULL，**不得用完备度替代** |
| 2 | 治理完备度口径是否只算"负责人" | 现口径 = 有 owner 的占比；描述/标签以 `missing_*` 单独列出，是否纳入"完备度"需业务方定 |
| 3 | 质量趋势数据 | 无业务源与质量用例时为空属预期；接入源并跑质量测试后自动产生 |
| 4 | 告警趋势 | Phase 2（依赖 Kafka `governance.alerts`，S3） |

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-17 | 首次产出：`gov_metrics` 独立库+schema、三张自持表、`gov_metrics_writer`/`superset_ro` 角色、库级 CONNECT 收敛、ETL 首版（API 取数 + PgBouncer 写入 + 每小时 :25 cron）、首次运行 9 行落库、权限正反验证（含 PgBouncer 池复用导致权限不即时生效的发现）、口径声明与待确认项 |
| v1.1 | 2026-09-18 | **新增 §7 覆盖率分母机制**（原 §7 顺延为 §8）：新表 `gov_metrics.asset_expected`（分母只由盘点更新，避免被每小时 ETL 重写回 NULL）+ 盘点工具 `coverage_denominator.py`（默认 dry-run、无 Python 驱动依赖、`--with-om` 出缺失清单、`--scope-note` 留痕）+ ETL 增加 `collect_expected()` 消费分母并计算 `asset_coverage_pct`。实测：回归（空表 ⇒ NULL 不变）、盘点演练（195 / OM 0 / 0.00%）、apply 后 ETL 正确计算、清行后回 NULL、`superset_ro` 可读不可写。附三条纪律（分母只写参考表 / 范围留痕 / 服务器与仓库同步——本轮实测漂移教训） |

---

*本文件为 S2-5 的 gov_metrics 与 ETL 产物，由《部署步骤_单机版》S2-5 展开，不含新增架构决策；口径与阈值的最终确认权归人工。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；命令与参数执行前请按官方文档核对。
