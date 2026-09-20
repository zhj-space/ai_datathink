# AI 数据治理平台 · 单机版 接源前检查清单（S2-2 / S3-5 接第一个业务源）

> **文档版本**：v1.0（2026-09-19）
> **用途**：把"接第一个业务源"当天要一次性确认/执行的事**列全**，避免返工。
> **性质**：由《部署步骤_单机版》S2-2/S3-5/S3-4、`S2准出量测口径与抽检模板`、`S3-1门禁④材料包`、`S3-5血缘上报D1记录` 机械汇总，**不含新增架构决策**。
> **结论先行**：**当前有 3 项"到点必须已解决"的前置未完成**（门禁④ 两份**原件**归档、S2 准出 **4 项口径**拍板、停机预算 `T_stop` / 实测 `R_WAL`）——
> **这三项没到位，当天一定会卡住或返工**。其余项 Agent 可自主准备（见 §3）。

---

## 0. 责任人图例与 Go/No-Go

| 标记 | 含义（AGENTS §11） |
|---|---|
| `[H]` | **人工专属**：Agent 不代签、不判读、不预设 |
| `[A+H]` | Agent 起草 + 人工审核后执行（凭证、访问范围、CDC 配置、策略阈值） |
| `[A]` | Agent 自主执行 |

**Go/No-Go 门（不通过就停）**：

| 门 | 判据 | 谁定 |
|---|---|---|
| **G0** | **门禁④ 两份文件（合规边界确认书 + 续接窗口签约定）的原件已归档** ｜ 硬约束 4 原文："接入生产源库前，合规边界书面文件与续接窗口签约定必须已归档；缺一不动生产库" | `[H]` |
| **G1** | `T_stop`（停机预算）与 `R_WAL`（WAL/binlog 实测速率）已给定 ⇒ 兜底值可算 | `[H]`（业务方/PM + DBA） |
| **G2** | 源库侧参数已按文件 B 落值并复核 | `[H]`（DBA） |

> ⚠️ **当前状态提醒**：门禁④ 已由人工于 2026-09-18 **确认通行**，但**两份文件的原件归档仍待 SEC+合规方 / DBA 完成**（签字栏空白，Agent 未代签）。**G0 的书面归档是硬约束，请勿以"已通行"替代**。

---

## 1. T-3 ~ T-1 天：必须**提前拿到**的东西（拿不到就别定接源日）

| # | 事项 | 具体内容 | 责任人 | 拿不到会怎样 |
|---|---|---|---|---|
| 1 | **源库标识与连接信息** | 类型/版本（PG 16.x？MySQL 8.x？）、主机:端口、库名、schema 清单、表范围、**明确排除项** | `[H]` 提供 | S2-2 起不来 |
| 2 | **只读账号** | 仅 SELECT；用于 OM ingestion + 覆盖率分母计数 | `[H]` 提供（Agent 起草权限清单） | 分母/资产都取不到 |
| 3 | **CDC 账号** | **只读 + 复制权限**（PG：`REPLICATION` + `SELECT`；MySQL：`REPLICATION SLAVE`/`REPLICATION CLIENT` + `SELECT`） | `[H]` 提供 | S3-5 换 source 卡住 |
| 4 | **网络可达性** | POC 主机 5432/3306 到源库是否可达；是否需加白/跳板/隧道 | `[A+H]` 探测（Agent 给命令，人工放行） | 当天才发现不通 |
| 5 | **停机预算 `T_stop`（小时）** | 业务方给"最长可接受 CDC 停机时长"上界 | `[H]`（业务方 + PM） | 兜底值算不出来 ⇒ G1 不过 |
| 6 | **实测 WAL/binlog 生成速率 `R_WAL`** | **必须实测**（采样 ≥1h 或覆盖业务高峰），不得用经验值 | `[H]`（DBA） | 兜底值失真，可能撑爆源库磁盘 |
| 7 | **S2 准出 4 项口径拍板** | ① 纳入范围界定（哪些 schema/库、含不含视图与分区子表）② PII 取**查准**还是**查全**（建议两者都报、门槛取查准）③ 抽样样本量 ④ 血缘 Top 50 的遴选规则（建议"下游依赖数 + 业务重要性"，由 DE/GOV 指定并留清单） | `[H]`（DE/GOV/QA） | **各量各的**，准出结论无法判定 ⇒ 返工 |
| 8 | **凭证落地方式** | 不写仓库；落 `.env`（0600）或 KMS/Vault | `[H]` 决定 | 违反安全基线① |
| 9 | **版本与 license 复核（若引入新连接器）** | 只选 OSI 认证开源；版本按官方文档核对 | `[A+H]` | 组件清单纪律 |
| 10 | **源库侧参数与复制前置**（**2026-09-19 预演新增，最容易漏的一项**） | **PG**：`wal_level=logical`（**CDC 必需**；本机实测默认为 `replica` ⇒ 需改并**重启 PG**，属停机窗口的一部分）、`max_replication_slots`、`max_wal_senders`、`max_slot_wal_keep_size`、`wal_keep_size`；**MySQL**：`binlog_format=ROW`、`binlog_row_image=FULL`、`binlog_expire_logs_seconds` | `[H]`（DBA） | **接源当天才发现 ⇒ 必须另约停机窗口改参数**，接源日直接作废 |
| 11 | **复制账号权限清单** | PG：`LOGIN` + `REPLICATION` + 目标 schema 的 `SELECT`（快照用）；MySQL：`REPLICATION SLAVE` + `REPLICATION CLIENT` + `SELECT` | `[A+H]` 起草权限清单 + `[H]` 建号 | 权限缺一项，CDC 起不来或快照失败 |

---

## 2. T-1 天：Agent 可自主完成的准备（现在就做，不用等源库）

| # | 事项 | 命令 / 动作 | 证据落点 |
|---|---|---|---|
| 1 | **分母 SQL 试跑脚本就绪** | 用 `coverage_denominator.py`（**默认 dry-run**）；`--source-ref <source-db-1> --scope-note "<纳入范围>"`。**预演实测三条注意**：① 在**宿主**上跑必须显式给 `OM_BASE=http://127.0.0.1:8585/api/v1`（默认是容器名，宿主解析不了）；② `--exclude` **只按 schema 过滤**（生成的是 `table_schema NOT IN (...)`）⇒ **无法排除单张分区子表**；③ 内网目标需 `--client-network data-net` | 输出贴入接源记录 |
| 2 | **基线快照（接源前水位）** | `docker compose ps`、磁盘可用、容器数、规则数、告警活动数、`ga.alert_event` 计数 | 接源记录 §前置 |
| 3 | **备份链有效性** | 手动跑一次 `backup_pg.sh` / `backup_config.sh`，确认产物与成功戳新鲜 | `/data/backup/log/*` |
| 4 | **告警阈值待算项标注** | 复制槽告警阈值 = **兜底值 × 70%**（兜底值未落定前**不得硬编码**）；`alerting/pg_exporter_queries.yml` 的 SQL 按源库兜底值现场计算 | 规则文件 + 版本记录 |
| 5 | **D1 血缘登记准备** | 真实作业 SQL 落到 `/data/ai-governance/jobs/<job_key>.sql` + 同目录 `mapping.json`（`table_service` 填 **OM 里真实服务名**） | 目录 + 上报响应 |
| 6 | **磁盘余量核对** | 当前根分区可用 ~52 GiB；**加盘阈值 = 可用 < 20 GiB**；接源后镜像/索引/快照都会增长 | 周报 §2 |
| 7 | **演练窗口与回滚方案** | 三项演练（断流恢复 / 复制槽保护 / 重快照）的时间窗、参与人、回滚触发权（**M-Prod 期回滚权归 PM**） | S3-7 记录 |
| 8 | **已知缺口书面确认** | 出机介质（已暂缓 ⇒ 门禁② 该项挂账）、IM/Webhook（告警无主动通知，现为落库）——**接源前请人工确认可接受** | 见 §5 |

---

## 3. 接源当天：按顺序执行（每步带判据）

| 步 | 动作 | 执行方 | 判据（做到才算过） |
|---|---|---|---|
| **S1** | **G0 复核**：两份原件是否已归档 | `[H]` | 书面文件已归档；否则**停** |
| **S2** | 源库侧参数落值：**先确认 `wal_level=logical`（PG，改它需重启）**；再 `max_slot_wal_keep_size = ceil(3 × T_stop × R_WAL)`（且 ≤ 磁盘可承受上限，见文件 B §5.4）、`wal_keep_size` 按文件 B；MySQL 侧对应 `binlog_format=ROW` + `binlog_expire_logs_seconds` | `[A+H]` 起草 + `[H]` DBA 执行 | 参数已在源库生效并被复核（**Agent 不得自行执行源库变更**） |
| **S3** | OM 建服务/连接（凭证与访问范围人工确认） | `[A+H]` | 连接测试通过；**只读** |
| **S4** | 跑第一次 ingestion → **建出真实表实体**（⚠️ **建完必须确认已 Deploy**：2026-09-19 实测，只保存不部署时 Airflow 里不会生成 DAG，触发会报 `Dag id … not found in DagModel`；补 `POST /services/ingestionPipelines/deploy/{id}` 或点 UI 的 Deploy 即可） | `[A]` | OM 中 `tables > 0`（**这是 D1 血缘的前置**：无实体则连边必然 404） |
| **S5** | 分母盘点写库：`coverage_denominator.py --mode apply` | `[A]` | `gov_metrics.asset_expected` 有记录；`--scope-note` 已留痕 |
| **S6** | Flink CDC 作业：换 source 为 CDC → **走 `submit_flink_job.sh` 提交** | `[A]`（作业提交）+ `[H]`（规则逻辑上线前审核） | 干跑门禁通过（血缘可解析）；作业 RUNNING；Checkpoint 完成 |
| **S7** | 血缘边自动生成与对账 | `[A]` | OM 中能看到 **作业图级**边（`source=PipelineLineage`）；`lineage_autoreport.sh` 通过 |
| **S8** | Presidio 离线批量 PII 识别（中文以 `language=en` 调用） | `[A]` | 识别结果入库；**样本只留脱敏特征** |
| **S9** | Kafka topic 规划（`governance.alerts` + 日志事件类） | `[A]` | 命中规则只进告警、**不进质量看板**（ADR-A5） |
| **S10** | S2 准出三项指标取数（覆盖率 / PII 抽检 / 血缘 Top50 列级） | `[A]` 取数 + `[H]` 判读 | 覆盖率 100%、PII >90%、血缘 Top50 ≥90%（**判读与签字人工专属**） |
| **S11** | S3-7 三项演练：断流恢复（≤ 续接窗口 1/3）、复制槽保护（兜底生效 + 水位告警触发 + 删 slot 演练）、重快照预案 | `[A]` 脚本 + `[H]` 判读（DBA 签字） | 三项均通过并归档记录 |

---

## 4. 接源后 T+1：对账与观察（防"接完就不管"）

| # | 检查 | 期望 | 异常怎么办 |
|---|---|---|---|
| 1 | `lineage_autoreport.sh` 每日对账 | `present` 覆盖全部期望边、`drift=0` | 有 drift ⇒ 看 `lineage_reconcile.log`；`entity_missing` ⇒ OM 里表实体缺了 |
| 2 | Kafka 消费滞后告警 | `kafka-lag` 3 条规则 `health=ok`，无 firing | 查 Flink 消费者是否停滞 |
| 3 | PG 复制槽水位 | `postgres-replication-slot` 5 条规则正常；槽 `active` 且有消费者 | 见《S3-1门禁④材料包》§5.6 作业下线 checklist |
| 4 | 质量看板 | 只有**快照基线**数据；无增量窗口数据混入（ADR-A5） | 检查 test definition 类型 |
| 5 | 覆盖率数值 | ETL 下一轮后 `asset_coverage_pct` 不再是 NULL | NULL = 参考表未盘点 |
| 6 | 备份/ETL 新鲜度 | `job-freshness` 10 条规则正常（含 `lineage_reconcile`） | 看 `/data/backup/log/*` |
| 7 | 磁盘 | 可用空间未接近 20 GiB 阈值 | 触发加盘评估（人工） |

---

## 5. 当前的空白项与已知缺口（接源前请人工过一遍）

| # | 项 | 现状 | 影响 |
|---|---|---|---|
| 1 | **源库信息** | **待人工提供**（S2-2 未开始） | 整个链条的前置 |
| 2 | **门禁④ 两份原件归档** | 通行决定已下（2026-09-18），**原件待 SEC/合规方 + DBA 归档** | **硬约束 4**：未归档不动生产库 |
| 3 | **`T_stop` / `R_WAL`** | 未指定 / 未实测 | 兜底值算不出 ⇒ 复制槽保护无法落值 |
| 4 | **S2 准出 4 项口径** | 未拍板 | 三项指标无法判定 |
| 5 | **出机介质** | **已暂缓**（人工决定）；门禁② 该项**挂账** | 备份仍在同一故障域 |
| 6 | **IM/Webhook 地址** | 未提供；告警**只在 AM UI + GA 落库**，无主动通知 | 值班可能不知情 |
| 7 | **门禁②/③ 原件签字** | 判定已通行；**原件待归档** | 准出材料不完整 |
| 8 | **8 条漏洞风险接受** | 待勾选 | 供应链清单未收口 |
| 9 | **基础镜像/依赖钉版** | GA/门户/superset 已钉（2026-09-19） | 已闭环 |

---

## 6. 附：一页速查（按角色）

| 角色 | 接源当天要做的 |
|---|---|
| **DBA** | G0 复核 ｜ `max_slot_wal_keep_size` / `wal_keep_size` 落值并复核 ｜ 提供 `R_WAL` 实测 ｜ S3-7 复制槽三项验证签字 |
| **DE / GOV** | OM 服务与连接审批（凭证/范围）｜ 窗口规则逻辑审核 ｜ 血缘 Top50 遴选与准确率判读 ｜ 覆盖率/PII 判读 |
| **PM / 业务方** | 给 `T_stop` ｜ 拍板 4 项口径 ｜ 出机介质与 IM 地址决策 ｜ M-Prod 评审结论 |
| **SEC / 合规方** | 门禁④ 文件 A 原件归档 |
| **Agent（可自主）** | 分母盘点（dry-run → apply）｜ OM ingestion ｜ 作业提交（含血缘门禁）｜ 血缘自动上报与对账 ｜ PII 批量识别 ｜ 指标取数 ｜ 演练脚本 ｜ 证据归档 |

---

## 7. 预演发现（2026-09-19，在 POC 自建库上跑 S2~S10 的实测记录）

**怎么做的**：在 POC 自身的 PostgreSQL 上建一个 **mock 源库**（schema `sales`：4 张业务表 + 1 条视图 + 1 个分区父表与子表 + 合成数据），
按本清单把"建实体 → 血缘 → 覆盖率分母"这一段真跑一遍，**用完即删**（残留核验：mock 库 0、OM 表/库/服务均 0、GA 血缘行 none、登记目录只剩 1 个演练文件）。

| # | 发现 | 证据 | 对清单的处置 |
|---|---|---|---|
| 1 | **源库侧 `wal_level` 默认为 `replica`，而 Flink CDC 的 PG 源必须要 `logical`** | mock 源实测：`wal_level = replica`；`max_replication_slots/max_wal_senders = 10`（够用）；`max_slot_wal_keep_size = -1`（无兜底）、`wal_keep_size = 0`（待落值） | **新增 §1 第 10/11 项**（源库参数与复制前置、复制账号权限清单），并写进 §3 的 S2 |
| 2 | 改 `wal_level` **需要重启 PG** ⇒ 属**停机窗口**的一部分 | 与第 1 条同源 | 写进 S2 判据——**接源当天才发现就必须另约窗口，接源日作废** |
| 3 | **OM 有真实表实体后，D1 血缘能产出真实边** | mock 源 → OM 建 6 张表 → D1 `apply=true` → `status=applied`；OM 读回 `downstream=[…sales.orders_agg]`、`edge_exists=True` | 验证 S4→S7 连通：**S4（建出表实体）确实是 S7（血缘）的前置** |
| 4 | **覆盖率分母端到端算出 100%** | `分母=6 / OM已登记=6 / 缺失schema=1 / 覆盖率(若落库)=100.0%` | S5 判据可用；**但需显式 `OM_BASE`**（见第 5 条） |
| 5 | **`coverage_denominator.py` 的 `--exclude` 只按 schema 过滤，排除不了单张分区子表**；且**宿主侧必须显式给 `OM_BASE`** | 对照实测：`--exclude sales.events_p0` → 分母仍 6；`--exclude sales` → 分母 0；不给 `OM_BASE` 时 `--with-om` 报 `name resolution` | 写进 §2 第 1 项的三条注意；**若人工口径要求"排除分区子表"，需先改工具**（§6 待确认口径 #1 的落地阻塞） |

> **未覆盖（明确声明）**：本次**没有**走 OM 的 **Postgres 源 ingestion 管道（Airflow DAG）**——该段在 S2-2 已用"OM→Airflow 端到端闭环"验过，但**用真实源库跑 ingestion** 仍是接源当天的新增风险点；建议接源前单独安排一次准备。
> 另：Presidio 批量识别与 PII 抽检未在本轮重跑（已在 S2-5 验过）。

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-19 | 首次产出：按"T-3~T-1 提前拿 / T-1 Agent 准备 / 当天 S1~S11 / T+1 对账"四段组织接源检查清单；含 **G0/G1/G2 三道 Go/No-Go**（其中 G0＝门禁④ 原件归档，硬约束 4）、**当前 9 项空白与缺口**与**按角色速查表**。口径全部引自《实施执行计划 v2.0-r2》《部署步骤_单机版》S2-2/S3-4/S3-5/S3-7、《…S2准出量测口径与抽检模板.md》§6（4 项待确认）、《…S3-1门禁④材料包.md》§5（按源库填报表 / 参数推导 / 磁盘余量 / 作业下线 checklist）、《…S3-5血缘上报D1记录.md》§5.6（作业登记与提交包装）与 `gov-metrics-etl/README.md`（分母只由盘点写、`--scope-note` 留痕）。**不含新增架构决策** |
| v1.1 | 2026-09-19 | **按"接源预演"实测回写（新增 §7）**：在 POC 自建库上建 mock 源（4 表 + 1 视图 + 分区父子 + 合成数据）把"建实体 → 血缘 → 覆盖率分母"真跑一遍，用完即删（残留：mock 库 0、OM 实体 0、GA 血缘行 none）。**五条发现**：① **源库侧 `wal_level` 默认 `replica`，而 Flink CDC PG 源必须 `logical`**（并新增"源库参数与复制前置""复制账号权限清单"两行到 §1；`max_replication_slots/max_wal_senders=10` 够用，`max_slot_wal_keep_size/wal_keep_size` 待落值）；② 改 `wal_level` **需重启 PG ⇒ 属停机窗口**（接源当天才发现就作废）；③ **OM 有真实表实体后 D1 能产出真实血缘边**（`applied` + OM 读回 `edge_exists=True`，验证 S4 是 S7 的前置）；④ 覆盖率分母端到端算出 **100%（6/6）**；⑤ **`coverage_denominator.py` 的 `--exclude` 只按 schema 过滤、排除不了单张分区子表**，且**宿主侧必须显式 `OM_BASE`**（否则报 name resolution）——已写进 §2 注意事项。**未覆盖声明**：OM 的 Postgres 源 ingestion 管道（Airflow DAG）未在本轮预演（S2-2 只验过 OM→Airflow 闭环） |
| v1.2 | 2026-09-19 | **S4 步骤补一条实测警告**（来自"OM 建采集任务"全流程实跑，详见《…单机版_测试入口与用例.md》附录）：**在 OM 建完采集任务必须确认已 Deploy**——只保存不部署时 Airflow 不会生成 DAG，触发报 `Dag id … not found in DagModel`；补 `POST /services/ingestionPipelines/deploy/{id}`（或 UI 的 Deploy）即可。同轮还记录两个已知缺口：**OM 侧看不到运行状态**（`pipelineStatuses=0`、`/pipelineStatus` 404，但任务执行成功）、**容器内 `airflow dags list-runs` 报 `Database migration required`**（仅影响 CLI）。**未改门禁判据** |

---

*本成果由 AI 生成，仅供决策参考；命令与版本号执行前请按各组件官方文档核对。门禁/准出判读与签字、源库变更执行均为人工专属。*
