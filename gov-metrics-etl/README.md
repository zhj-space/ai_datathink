# gov_metrics 指标层 ETL（ADR-A6）

> **定位**：治理指标的**自持表结构 + 每小时 ETL**。《部署步骤_单机版》S2-5 第 3 件（首版只做资产覆盖率 + 质量趋势，告警趋势 Phase 2 补）。
> **硬约束**：Superset **只读 `gov_metrics`**，**禁止直连 OM 内表**（ADR-A6）。本 ETL 取数**只走 OpenMetadata REST API**（`search` 聚合 + `dataQuality` 端点），**不读 OM 内部表**。
> **权威文档**：《AI数据治理平台_单机版_S2-5gov_metrics与ETL.md》（含部署、运行与验证记录）。

| 文件 | 用途 |
|---|---|
| `schema.sql` | 建 schema / **四张**自持表 / 索引 / 角色（`gov_metrics_writer` 写入、`superset_ro` 只读） |
| `gov_metrics_etl.py` | 每小时 ETL：从 OM API 取资产与质量指标 → 经 PgBouncer(6432) 写入 `gov_metrics`；**并读取覆盖率分母参考表** |
| `coverage_denominator.py` | **资产覆盖率分母盘点工具**（2026-09-18 新增）：源侧计数 →（可选）与 OM 比对出缺失清单 → `--mode apply` 写入参考表；**默认 dry-run** |

**口径（不得混用）**：

| 指标 | 定义 | 现状 |
|---|---|---|
| `asset_coverage_pct` | 已登记资产 / **源侧应采集数** | 分母来自参考表 `gov_metrics.asset_expected`（由 `coverage_denominator.py` 盘点写入）；**无记录 ⇒ NULL（未盘点），与"覆盖率 0%"可区分** |
| `governance_completeness_pct` | 已有负责人的资产占比（治理完备度） | 现在即可计算 |
| `quality_trend_snapshot` | 质量用例通过/失败趋势 | 无数据源时为空表（预期） |

**纪律**：

1. ETL **不直连 PostgreSQL 5432**，一律经 **PgBouncer 6432**（硬约束 2）。
2. 本目录属**自研表**，允许分区（设计 4.5），但 POC 数据量小**暂不分区**。
3. 变更本目录后须同步《…S2-5gov_metrics与ETL.md》版本记录，并重跑 ETL 与权限验证（含"Superset 角色读不到 OM 表"的**反向验证**）。
4. ETL 在 S3 将迁移进 Governance Agent（当前为过渡实现，属 S2 交付）。
5. **分母只由盘点工具写、ETL 只读**：`asset_expected` 是"只由盘点更新"的参考表；不要把分母写进 `asset_coverage_snapshot`（每小时会被重写回 NULL）。
6. **盘点范围必须留痕**：`--scope-note` 填清库/schema 清单与排除项；改范围 = 改分母，须同步《AI数据治理平台_单机版_S2准出量测口径与抽检模板.md》。
7. **改服务器副本必须同步本目录**（2026-09-18 实测漂移：只改了服务器上的 ETL 基址而漏改仓库版本，重新上传后 ETL 连不上 OM——排障时注意 `etl_run` 里的失败可能源于"脚本版本被覆盖"而非 OM 故障）。
8. **OM 地址用环境变量兜底**：`OM_BASE` 默认 `http://127.0.0.1:8585/api/v1`（2026-09-18 443 收敛后不再经 Traefik）；后续若迁进 GA，可改指向容器内地址。
