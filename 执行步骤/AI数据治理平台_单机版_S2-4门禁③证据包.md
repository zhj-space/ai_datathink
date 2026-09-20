# AI 数据治理平台 · 单机版 S2-4 门禁③（T-M0-1）证据包

> **门禁定义**：**T-M0-1** OpenMetadata × PgBouncer **transaction 模式**兼容性实测——日志**零命中** `prepared statement .* does not exist` / `transaction aborted`（《部署步骤_单机版》S2-4、《实施执行计划 v2.0-r2》M0 实测清单第 1 项）。
> **执行日期**：2026-09-17 ｜ **执行方**：Agent（脚本与采集）｜ **判定与签字**：**人工专属（DBA + DE）**
> **失败处理纪律**：不自动降级——若命中非零，输出本证据包（日志 + 复现步骤）交 DBA+DE 决策 session / 直连（本环境**未触发**降级）
> **关联文档**：《S2接入方案》§9（ingestion 落地与端到端闭环）、S1 记录 §3.2（PgBouncer 基线）

---

## 1. 结论摘要

| 判据 | 结果 | 判定 |
|---|---|---|
| OM server 日志：`prepared statement .* does not exist` | **0 次** | ✅ 通过 |
| OM server 日志：`transaction aborted` | **0 次** | ✅ 通过 |
| PgBouncer 日志：同上两判据 | **0 / 0 次** | ✅ 通过 |
| 广义交叉核对（`prepared statement` / `transaction` / `deadlock` / `SASL` / `PSQLException`） | OM 侧 **全为 0** | ✅ 无隐藏异常 |
| PgBouncer 日志 ERROR / WARNING 级 | **0 / 0 行** | ✅ |
| `SHOW POOLS` 等待 | `cl_waiting = 0`（两份库），`maxwait = 0` | ✅ 无排队 |
| 负载真实性 | 池统计 `44~54 xacts/s`、`126~140 queries/s`，`wait 0 us` | ✅ 有实际负载 |

**Agent 侧结论**：**判据全部满足，建议判定为通过**。**最终判定与签字请 DBA + DE 在 §8 填写**（Agent 不代签、不背书）。

> **v1.1 补充（2026-09-17）**：v1.0 遗留的"仅 API 等价调用、未走页面"的方法偏差**已消除**——本包新增 §3.6「页面口径复跑（真实浏览器 UI 走查）」与 §4.5「页面口径复跑窗口判据」，由无头 Chrome 逐条执行门禁原文的页面动作并复采双侧日志，结论与 v1.0 一致（**全 0 命中**）。

---

## 2. 执行口径与方法说明（含偏差，须判读者知悉）

| 项 | 内容 |
|---|---|
| 环境 | 单机 POC：OM **2.0.1**（server，经 `pgbouncer:6432`）+ Airflow **3.3.1**（ingestion，元数据库同样经 6432）+ PostgreSQL 16.15 + OpenSearch 3.8.0 |
| 时间窗 | 2026-09-17 **13:18:34 ~ 13:21**（CST）；取证窗口 `--since 60m`（含前述端到端联调） |
| 连接方式 | OM `DB_HOST=pgbouncer`、`DB_PORT=6432`、`DB_SCHEME=postgresql`、`DB_DRIVER_CLASS=org.postgresql.Driver`（**无 DB_PARAMS 覆盖**，即使用 JDBC 默认的服务端预编译行为） |
| PgBouncer 关键配置 | `pool_mode=transaction`、`max_prepared_statements=200`、`auth_type=scram-sha-256`、`default_pool_size=25`、`max_client_conn=200`、`server_reset_query=DISCARD ALL`（bitnami 默认） |
| **方法偏差（v1.0，已消除）** | 门禁原文写的是「OM **登录** → **浏览元数据** → **搜索** → 跑完整 ingestion → **打开血缘与质量页**」。v1.0 由 Agent 以**等价 API 调用**完成（无浏览器操作）。**v1.1 已补齐**：Agent 用无头 Chrome（CDP，真实键盘/鼠标事件）按页面口径逐条复跑，结果见 §3.6 与 §4.5；本行保留以记录方法演进。 |

---

## 3. 动作序列与结果

### 3.1 读路径（浏览元数据）

| # | 动作 | 结果 |
|---|---|---|
| 1 | 登录（basic + base64 口令） | HTTP **200**（token 718 字符） |
| 2 | 服务清单 pipelineServices / databaseServices | **200 / 200** |
| 3 | 管道资产清单 / 实体详情（FQN） | **200 / 200** |
| 4 | **血缘图**（`/lineage/getLineage`，上下游各 3 层） | **200**（2895B） |
| 5 | 实体版本历史（用实体 id） | **200** |
| 6 | 数据质量套件 / 用例（`/dataQuality/testSuites`、`testCases`） | **200 / 200** |

### 3.2 搜索路径（触发 OpenSearch 链路，全部经 OM 的 DB 层记录检索实体）

| 索引 | 结果 |
|---|---|
| `pipeline_search_index` / `table_search_index` / `column_search_index` / `database_search_index` | **200 / 200 / 200 / 200** |
| `database_schema_search_index` / `test_case_search_index` | **200 / 200**（首轮误用驼峰索引名报 500，属调用方拼写错误，非服务端问题） |
| `tag_search_index` / `glossary_term_search_index` / `user_search_index` | **200 / 200 / 200** |

### 3.3 写路径（事务与索引写入）

| 动作 | 结果 |
|---|---|
| PATCH 实体描述（`Content-Type: application/json-patch+json`） | **200** |
| 写后立即读回 | **200**（值已生效） |

### 3.4 完整 ingestion（经 Airflow）

| 动作 | 结果 |
|---|---|
| 触发 ingestion pipeline（OM → Airflow → DAG） | HTTP **200**，DagRun 入队 |
| DagRun 执行 | **success**（任务 1 次尝试通过） |
| OM 侧 `pipelineState` | **success**；`records: 8, errors: 0, failures: []` |

### 3.5 并发压力

| 动作 | 结果 |
|---|---|
| 30 次并发请求（10× 搜索 + 10× 实体读 + 10× 服务清单，10 线程） | 状态码分布 **{200: 30}** |

> 全序列共 27 个动作，**除调用方自身的 6 处请求写法错误外，其余全部 2xx**；6 处错误已逐一定位（索引名驼峰、PATCH content-type、版本接口用 FQN、聚合接口缺参），**与应用/数据库层无关**，详见 §5。

### 3.6 页面口径复跑（真实浏览器 UI 走查，2026-09-17 17:45~17:57 UTC）

**方法**：无头 Chrome（`--headless=new`）+ CDP 协议，**真实键盘事件**（`Input.dispatchKeyEvent`）填写受控表单、**真实鼠标事件**（`Input.dispatchMouseEvent`）点击；每步落 DOM/文本/截图（产物见 §9）。

| # | 门禁动作 | 实际页面操作 | 页面呈现判据 | 结论 |
|---|---|---|---|---|
| 1 | OM **登录** | `https://<POC公网IP>/signin` → `#email` / `#password` 真键盘输入 → 点 `button[data-testid=login]` | 左侧导航渲染完成、`[data-testid=nav-user-name]` = `admin` | ✅ |
| 2 | **浏览元数据** | 侧栏点「探索」（`[data-testid=app-bar-item-explore]`）→ `/explore` | 「数据资产…正在浏览整个数据资产」；资产卡出现 `airflow_poc / airflow_metadata_poc`，描述即 S2-4 写入验证文本 | ✅ |
| 3 | **搜索**（触发 OpenSearch 链路） | 顶部搜索框 `#searchBox` 输入 `airflow_metadata_poc` + 回车 | URL → `/explore/?search=airflow_metadata_poc&sort=_score`；结果 **1 result**；详情面板含「血缘关系：未找到 lineage 连接」 | ✅ |
| 4 | **跑完整 ingestion** | OM 实体页**只**提供「在 Airflow 中查看」外链，其 `href=http://openmetadata-ingestion:8080/dags/airflow_metadata_poc`（**容器内主机名，浏览器不可达**——回环+隧道部署方式的直接体现）；改由 Airflow UI（隧道 `127.0.0.1:18080`）点 `[data-testid=trigger-dag-button]` → 弹窗 `[data-testid=trigger-dag-submit]` | DagRun `manual__2026-09-17T17:50:04.811601+00:00` = **success**（17:50:05 → 17:50:10 UTC）；页面显示「成功」 | ✅ |
| 5 | **打开血缘页** | ① 管道实体页点「血缘关系」tab（`[data-testid=lineage]`）→ `/pipeline/airflow_poc.airflow_metadata_poc/lineage`；② 侧栏「血缘关系」→ `/lineage` | ① 出「搜索血缘关系 / 影响分析 / airflow_metadata_poc / airflow_poc」；② 出 Service 血缘树（`airflow_poc` · Pipeline Services） | ✅ |
| 6 | **打开质量页** | 直达 `/data-quality`（侧栏「观测」为折叠菜单，实测点开未展开子项，故用应用内路由） | 页面出「数据质控 / 数据健康 / 数据资产覆盖范围 **0** / 测试用例结果 **0** / 数据维度…」；**数值为 0 属预期**（尚无质量测试定义，属 P2/S3） | ✅ 页面可用；数据为空为已知 |

**OM 侧闭环证据**：上述运行可在 OM 管道实体「执行」记录页（`/pipeline/airflow_poc.airflow_metadata_poc/executions`）看到——`01:49`（本地 +08:00）一次 **Success / 结束 01:50 / 4.23 s**（即本次触发），`01:55` 一次 `Pending`（另一入口的对照运行，回填机制见 §5 第 4 项）。

**页面口径 ≠ 只截图**：本节每一条判据都取自**渲染后的 DOM 文本/状态**（而非"页面能打开"），并与 API/库内数据交叉核对；截图仅作留档。

---

## 4. 判据原始输出

### 4.1 OM server 日志（窗口 60m，2233 行）

```
prepared statement .* does not exist    0 次
transaction aborted                     0 次
--- 广义交叉核对 ---
prepared statement                      0 次
transaction                             0 次
deadlock                                0 次
SASL                                    0 次
connection reset                        0 次
PSQLException                           0 次
EntityNotFoundException                 0 次
```

### 4.2 PgBouncer 日志（窗口 60m，392 行）

```
prepared statement .* does not exist    0 次
transaction aborted                     0 次
SASL                                    0 次
server conn crashed                     0 次
query_wait_timeout                      0 次
ERROR 级行数                             0
WARNING 级行数                           0
--- 池统计（节选） ---
54 xacts/s, 140 queries/s, 3 client parses/s, 1 server parses/s, 59 binds/s, xact 556 us, query 115 us, wait 0 us
```

### 4.3 池与会话状态（`SHOW POOLS` 节选）

```
database         | user              | cl_active | cl_waiting | maxwait | pool_mode
airflow_db       | airflow_user      |         7 |          0 |       0 | transaction
openmetadata_db  | openmetadata_user |        32 |          0 |       0 | transaction
pgbouncer        | pgbouncer         |         1 |          0 |       0 | statement   ← 管理库自身，属预期
```

### 4.4 服务端连接归属（`pg_stat_activity`）

```
OpenMetadata                 3 个服务端连接
PostgreSQL JDBC Driver       3 个服务端连接
```

### 4.5 页面口径复跑窗口（`--since 40m`，2026-09-17 17:11 ~ 17:51 UTC）

覆盖范围：§3.6 的全部页面动作 + Airflow UI 触发的一次完整 ingestion（17:50:04 → 17:50:10 UTC）。

```
--- OM server（窗口内 7468 行）---
prepared statement .* does not exist   0
transaction aborted                    0
prepared statement（广义）              0
deadlock                               0
SASL                                   0
connection reset                       0
PSQLException                          0
ERROR 级行数                            0

--- PgBouncer（窗口内 277 行）---
prepared statement .* does not exist   0
transaction aborted                    0
SASL                                   0
server conn crashed                    0
query_wait_timeout                     0
ERROR / WARNING 级行数                  0 / 0
unexpected eof                         0

--- 池统计（节选）---
49 xacts/s, 136 queries/s, 1 client parses/s, 0 server parses/s, xact 595 us, query 108 us, wait 0 us
57 xacts/s, 150 queries/s, 2 client parses/s, 1 server parses/s, xact 624 us, query 115 us, wait 0 us

--- SHOW POOLS（节选）---
openmetadata_db | openmetadata_user | cl_active 32 | cl_waiting 0 | maxwait 0 | transaction
airflow_db      | airflow_user      | cl_active  7 | cl_waiting 0 | maxwait 0 | transaction
superset        | superset_user     | cl_active  5 | cl_waiting 0 | maxwait 0 | transaction
pgbouncer       | pgbouncer         | cl_active  1 | cl_waiting 0 | maxwait 0 | statement（管理库自身，预期）

--- pg_stat_activity（服务端连接归属）---
PostgreSQL JDBC Driver  4
OpenMetadata            1
```

**判读**：页面口径复跑窗口内两侧判据**全 0 命中**，与 §4.1/§4.2 的 API 口径结论一致；`pool_mode=transaction` 未变（**未触发降级**）。

---

## 5. 异常项分类说明（不掩盖）

| # | 现象 | 分类 | 说明 |
|---|---|---|---|
| 1 | PgBouncer 日志 `closing because: client unexpected eof` **5 次** | **良性，非池化缺陷** | 全部落在 `airflow_db/airflow_user` 连接：其中 3 次时间戳（04:21:06）与 **ingestion 容器重建**（04:21:07 启动）吻合——旧连接被切断；另 2 次 `age=0s`，为 Airflow CLI/探针的短连接立即关闭。**无 ERROR/WARNING，且不涉及 `openmetadata_db`** |
| 2 | 首轮 6 个非 2xx（版本历史 404、两个搜索 500、聚合 500、PATCH 415） | **调用方写法错误** | 逐条已修正并复验 200：索引名应为 `database_schema_*`/`test_case_*`（驼峰索引不存在）；PATCH 须用 `application/json-patch+json`；版本接口用实体 **id**；聚合接口需显式聚合字段参数 |
| 3 | `server_reset_query=DISCARD ALL` | **观测项（供 DBA/DE 判读）** | 这是 bitnami 镜像默认值。PgBouncer 官方文档对 transaction 模式建议**留空** `server_reset_query`（会话不持久化，重置语句语义不同）。本轮实测**未观察到任何异常**，故不作为缺陷上报，仅作为配置复核项 |
| 4 | OM 管道实体「执行」记录中，**刚触发完的运行显示 `Pending`（无结束时间）** | **已定位：机制性回填时延，非缺陷**（v1.1 新增） | 现象：Airflow 侧 `success`、OM ingestion 记录 `pipelineState=success / records=10~11 / errors=0`，唯独管道实体执行记录停在 `Pending`。**定位**：库内 `entity_extension_time_series`（`extension='pipeline.pipelineStatus'`）显示每次 ingestion 会把**最近 N 次 DagRun**（连接配置 `numberOfStatus: 10`）整体重推，**当次运行上报的是自身启动态 Pending**，其终态由**下一次运行**回填——实测 `17:49:55Z` 那条在 `17:55` 的运行后被改写为 `Successful`（`endTime=17:50:10Z`），页面同步显示 `Success / 4.23 s`。**两个触发入口（Airflow 直触、OM 侧触发）表现一致**。⇒ 该 `Pending` 会在下一次运行（或下一次状态推送）后自动收敛；若要求"跑完立刻看到终态"，可再触发一次或等待下一次运行 |

---

## 6. 复现命令

```bash
# 1) 判据（OM 侧）
docker logs --since 60m openmetadata_server 2>&1 | grep -ciE 'prepared statement .* does not exist'
docker logs --since 60m openmetadata_server 2>&1 | grep -ci 'transaction aborted'

# 2) 判据（PgBouncer 侧）
docker logs --since 60m ai-governance-poc-pgbouncer-1 2>&1 | grep -ciE 'prepared statement .* does not exist'
docker logs --since 60m ai-governance-poc-pgbouncer-1 2>&1 | grep -ci 'transaction aborted'

# 3) 池状态
docker exec -e PGPASSWORD="<见 .env>" ai-governance-poc-postgres-1 \
  psql -h pgbouncer -p 6432 -U postgres -d pgbouncer -c "SHOW POOLS;"

# 4) 完整动作序列（Agent 使用的脚本，可按需重跑）
#    /tmp/s24_sequence.py（读/搜索/写/ingestion/并发）与 /tmp/s24_fix_calls.py（修正后复验）

# 5) 页面口径复跑（Agent 已用无头 Chrome 完成，见 §3.6；下列为人工复现路径）
#    ★ 访问方式已于 2026-09-18 变更：443 不再对外发布，OM 改走隧道（见《…S3前置部署记录.md》§10）
#       先开隧道：ssh -N -L 8585:127.0.0.1:8585 root@<POC服务器IP>
#    OM：http://localhost:8585/ → 登录 admin@open-metadata.org → 侧栏「探索」→ 顶部搜索 airflow_metadata_poc
#        → 打开「血缘关系」tab（或侧栏「血缘关系」）→ 直达 /data-quality 看质量页
#    Airflow（隧道）：http://127.0.0.1:18080/dags/airflow_metadata_poc → 点「触发」→ 弹窗内点「触发」
#    再回 OM：/pipeline/airflow_poc.airflow_metadata_poc/executions 看执行记录

# 6) 页面口径复跑所用脚本（均在本机仓库外 C:\Users\Yuanhui\.ai-datathink\）
#    om_walk.py + om_steps_*.json / af_steps_*.json   UI 走查（真实键盘/鼠标事件，逐步落 DOM+截图）
#    s24_ui_logs.sh                                   复跑窗口双侧判据采集（§4.5）
#    af_check.sh / om_status_shape.sh / om_ts_probe.sh / om_trigger_compare.sh
#                                                      DAG 状态、OM 实体状态、库内时间序列、两入口对照
```

---

## 7. 与降级决策的关系

本环境**未触发**降级路径：判据全 0，`pool_mode=transaction` 保持不变（硬约束 5：不自动降级）。
若后续接入真实业务源后出现命中，按门禁纪律：**输出证据包 → 停止 → 转人工（DBA+DE）决策 session 或直连**。

---

## 8. 判定栏（人工专属）

| 项 | 内容 |
|---|---|
| 判据是否满足（零命中） | ☐ 确认　☐ 有异议（说明：________________） |
| 判定结论 | ☐ 通过　☐ 有条件通过（条件：________________）　☐ 不通过 |
| 是否需要人工 UI 复跑补充证据 | **页面口径已由 Agent 于 2026-09-17 用无头 Chrome 完成（§3.6，含逐步截图）**：☐ 判读者核对留档即可　☐ 仍要求人工重新复跑　☐ 不需要 |
| `server_reset_query` 观测项是否需调整 | ☐ 保持现状　☐ 需评估（说明：________________） |
| 「执行」记录 `Pending → Success` 回填机制（§5 第 4 项） | ☐ 已知悉，不处置　☐ 要求进一步说明（________________） |
| DBA 签字 / 日期 | 签字：________________　____ 年 __ 月 __ 日 |
| DE 签字 / 日期 | 签字：________________　____ 年 __ 月 __ 日 |

> **判定记录（2026-09-18，人工决定）**：**项目决策方确认门禁③ 通行（判为通过）**——判据双侧 0 命中、页面口径已补齐、无降级需求。
> 判定栏的勾选与 DBA / DE 签字栏**保持空白**（Agent 不代签、不背书）；**两份签署原件仍须由 DBA + DE 按上表归档**。
> **判定生效后的影响已记录**：① 部署基线中 `pool_mode=transaction` 定版（硬约束② 不变）；② 本证据包的部署状态在 **2026-09-18 的 FERNET_KEY 重做**后发生变化，故同日**复跑一次判据采集**（结论：仍 0 命中，见下）。
> ② 的**复跑结果（2026-09-18 已回填）**：FERNET_KEY 重做（OM 元数据库以自持 key 重建）后，重建 ingestion 管道并触发 —— DagRun `manual__2026-09-18T05:41:37.231248+00:00` = **success**（end 05:41:44Z）、OM 侧 `pipelines total=1`；同窗口判据：
> **OM 侧** `prepared statement .* does not exist` **0**、`transaction aborted` **0**；**PgBouncer 侧** 两者同样 **0**；`SHOW POOLS` 无等待（`cl_waiting=0`、`maxwait=0`），`pool_mode=transaction` **未降级**。
> ⇒ **判据满足、结论不变**（判据是配置行为，不随元数据重置而变；本轮已用同样口径复证）。完整命令与日志窗口见《AI数据治理平台_单机版_S3前置部署记录.md》§7.3。

---

## 9. 留档产物清单（仓库外，含页面截图）

| 类别 | 位置 / 命名 |
|---|---|
| 页面截图（PNG）与 DOM/文本留档 | `C:\Users\Yuanhui\.ai-datathink\shots\`（OM 走查：`omA_*` ~ `omM_*`；Airflow 走查：`afA_*` ~ `afC_*`） |
| UI 走查脚本 | `C:\Users\Yuanhui\.ai-datathink\om_walk.py` + `om_steps_*.json` / `af_steps_*.json` |
| 判据与状态采集脚本 | `s24_ui_logs.sh`（§4.5）、`af_check.sh`、`om_status_shape.sh`、`om_ts_probe.sh`（§5 第 4 项定位）、`om_trigger_compare.sh`（两入口对照） |

**关键截图索引**（供判读者按需查看）：

| 判据 | 截图文件 |
|---|---|
| OM 登录成功（侧栏 + `admin`） | `omA_after_login.png` |
| 浏览元数据（`/explore` 资产卡） | `omC_diag_explore.png` |
| 搜索结果（1 result + 详情面板） | `omE_02_search.png` |
| 管道实体页（含「在 Airflow 中查看」外链） | `omG_09_pipeline_page.png` / `omH_11_pipeline_lineage_tab.png` |
| 实体血缘页 | `omI_14_pipeline_lineage_tab.png` |
| 全局血缘页 | `omI_15_global_lineage.png` |
| 数据质量页（`/data-quality`） | `omH_12_data_quality.png` |
| Airflow 触发弹窗 / DagRun 成功 | `afC_03_trigger_dialog.png` / `afC_04_dag_after.png` |
| OM「执行」记录（Success 与 Pending 对照） | `omL_17_executions.png` / `omM_17_executions.png` |

> 脚本与截图均保存在**仓库之外**（与 `server-info.local.md` 同目录），避免把真实口令、内网主机名等带入仓库（AGENTS 第 9 节红线）。

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-17 | 首次产出：S2-4 门禁③（T-M0-1）证据包。动作序列 27 项（读/搜索/写/ingestion/30 并发）、双侧日志判据 0 命中、池状态无等待、异常项分类（5 次 eof 为容器重建与 CLI 短连接所致）、观测项（`server_reset_query`）、复现命令与人工判定栏 |
| v1.1 | 2026-09-17 | **页面口径复跑补齐（§3.6）**：以无头 Chrome（真实键盘/鼠标事件）逐条执行门禁原文的页面动作——OM 登录 → 探索 → 搜索 → 血缘页（实体 + 全局）→ 质量页（`/data-quality`），并由 Airflow UI 触发一次完整 ingestion（DagRun `manual__2026-09-17T17:50:04.811601+00:00` = success）；新增 §4.5 复跑窗口（40m）双侧判据，**全 0 命中**，与 v1.0 一致；§2 方法偏差标记为**已消除**；新增 §5 第 4 项观测（**执行记录 `Pending → Success` 的回填机制**，用库内 `entity_extension_time_series` 与页面双向定位，已排除触发入口差异）；§6 补 UI 复跑命令与脚本清单；新增 §9 留档产物清单；判定栏新增 2 行。**源文档与部署基线未改动，无架构变更** |
| v1.2 | 2026-09-18 | **门禁③ 判定与 FERNET_KEY 重做后复跑的记录**：§8 增人工判定记录（**项目决策方确认通行**；勾选与 DBA/DE 签字栏保持空白，**Agent 不代签**，原件仍由 DBA+DE 归档）；同日 FERNET_KEY 重做（元数据库以自持 key 重建）后按本包口径**复跑判据**：DagRun success、OM 与 PgBouncer 两侧判据 **0 命中**、池无等待、未降级 ⇒ **结论不变**。证据见《AI数据治理平台_单机版_S3前置部署记录.md》§7 |

---

*本证据包由 Agent 采集与整理；**门禁判定与签字为人工专属，Agent 不代签、不背书**。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；命令与参数执行前请按各组件官方文档核对。
