# AI 数据治理平台 · 单机版 S3-6 Governance Agent 接口规格（草案）

> **文档性质**：唯一自研组件的**接口契约草案**——先定契约再写代码，避免门户/Agent 两边口径漂移。
> **依据**：《设计方案 v3.2》2.3（Governance Agent 收窄为 AI 编排与服务层）、3.3、ADR-A3/A7；《实施执行计划 v2.0》P2 第 3 周；《部署步骤_单机版》S3-6。
> **现状（2026-09-18 更新）**：**首版已实现并上线**（见《AI数据治理平台_单机版_S3-6治理Agent首版实现记录.md》），**门户"Agent 未就绪"降级已结束**。本文件继续作为接口契约的权威来源：后续改动必须与本契约保持一致（门户按 `GET /health` 200 判就绪、`POST /chat` 取答案）。
> **版本**：v1.0（草案，2026-09-18）

---

## 1. 定位与非目标

| 项 | 内容 |
|---|---|
| **定位** | 平台**唯一的自研组件**：① 消费 Flink 产出的告警做 AI 层增强；② 开放 API；③ 审批网关；④ AI 编排（场景 D 问答的编排层） |
| **明确不做** | ❌ **不做事前拦截**（ADR-A3：POC 期只做事后审计 + 主动告警）❌ 不在线做 PII 脱敏（Presidio 仅离线批量）❌ 不自建流式规则引擎（规则在 Flink 作业里）❌ 不成为第二套元数据/血缘真理源（血缘以 OpenMetadata 为唯一权威） |
| **部署形态** | Compose 单容器 `governance-agent`（2 vCPU / 4G，与设计方案 4.2 一致）；**不发布公网端口**，经 `frontend-net` 由 Traefik 终止（或先仅回环 + 隧道，与 Superset/门户同口径） |
| **依赖** | Kafka `governance.alerts`（已就绪）｜PostgreSQL（经 **PgBouncer 6432**，硬约束②）｜OPA（决策，已就绪）｜Ollama + RAG（S4，未就绪） |

---

## 2. 与治理门户的既有契约（不可擅自更改——门户已按此实现）

门户 `governance-portal/agent.py` 已实现的调用（**必须兼容**）：

| # | 方法与路径 | 请求 | 期望 | 门户行为 |
|---|---|---|---|---|
| 1 | `GET {GOVERNANCE_API_URL}/health` | 无 | **HTTP 200** ⇒ 视为"就绪" | 非 200 或异常 ⇒ 显示"Agent 未就绪"降级提示（**fail-safe**） |
| 2 | `POST {GOVERNANCE_API_URL}/chat` | `{"question": "<文本>"}` | 200 + 文本 | 异常 ⇒ 显示降级提示 |

其中 `GOVERNANCE_API_URL` 默认 `http://governance-agent:8080`（compose 已注入）。

> **要点**：`/health` 必须**轻量且不依赖下游**（DB/Kafka 不通时，服务自身仍应能回答 health），否则会误判"Agent 就绪但功能不可用"。可用 `/ready` 单独表达"下游可用"。

---

## 3. 首版接口清单（最小可用集）

| # | 方法与路径 | 用途 | 首版状态 |
|---|---|---|---|
| 1 | `GET /health` | 存活（供门户就绪判定与容器 healthcheck） | ✅ 实现 |
| 2 | `GET /ready` | 就绪（DB / Kafka 连通性） | ✅ 实现 |
| 3 | `GET /api/v1/alerts?limit=&since=&severity=` | 查询已消费的告警（场景 B 的落库视图） | ✅ 实现 |
| 4 | `GET /api/v1/alerts/{id}` | 告警详情（含增强摘要字段，LLM 未上线时为空） | ✅ 实现 |
| 5 | `POST /api/v1/approvals` | 提交审批请求（场景 E，**仅记录，不拦截**） | ✅ 实现 |
| 6 | `GET /api/v1/approvals?status=` | 审批列表（门户"审批台"数据源） | ✅ 实现 |
| 7 | `POST /api/v1/approvals/{id}/decide` | 审批决定（approve/reject + 意见 + 审批人） | ✅ 实现 |
| 8 | `GET /api/v1/audit?entity=&limit=` | 审计轨迹（谁在何时对什么做了什么） | ✅ 实现 |
| 9 | `POST /chat` | 场景 D 问答（S4-2 起走 RAG） | ✅ **已实现**：RAG（LangGraph `retrieve → generate → verify`）+ **引用程序化校验**；回答尾部附**节点轨迹**、**检索到的资产**与**上下文预算口径行**（估算/**实测 token**、采用与丢弃篇数、是否截断、`num_ctx`）⇒ S4-1 的 10s 门禁口径**可核验**；RAG 不可用时仍降级（不伪造答案） |
| 10 | `POST /api/v1/alerts/ingest` | **Alertmanager webhook 接收端**（2026-09-19 新增）：把 AM 告警汇入 `ga.alert_event`，补上"规则有判定、告警无留痕"的缺口 | ✅ 实现（`source='alertmanager'`；幂等键 `alertmanager:<fingerprint>:<status>`；**Bearer 令牌强制校验**） |
| 11 | `POST /api/v1/lineage/report` · `GET /api/v1/lineage/report` | **作业图级血缘上报（D1，2026-09-19 新增）**：解析 Flink SQL → 在 OM 建/删血缘边（`source=PipelineLineage` + `sqlQuery`） | ✅ 实现（**宁缺勿错**：解析/映射不确定即 400 且不落行；幂等键见 `ga.lineage_edge`；**Bearer 令牌强制校验**） |
| 12 | `POST /api/v1/lineage/reconcile` | **血缘对账（D1，2026-09-19 同日新增）**：逐边核对 OM 实际边 ↔ GA 期望集；`repair=true` 时对缺失边**重新上报**（自愈） | ✅ 实现（缺失置 `drift`；**只新增/修复，绝不删 OM 上的边**；由 cron 每日 04:10 调用，`LineageReconcileStale` 兜底告警） |

**统一响应约定**：JSON；错误用 `{"error": {"code": "...", "message": "..."}}` + 合适的 HTTP 状态码；时间为 RFC3339 UTC。

**接口 10 的约定（2026-09-19 加入，与 §3 表格同源）**：

| 项 | 口径 |
|---|---|
| 上游 | Alertmanager `webhook_configs`（payload 为标准 AM webhook 结构，含 `alerts[]`） |
| 鉴权 | `Authorization: Bearer <GA_INGEST_TOKEN>`；**令牌非空即强制校验**（401 返回 `unauthorized`） |
| 幂等 | 键 `alertmanager:<fingerprint>:<status>` ⇒ 同一条告警重复投递**不新增行**；`firing`/`resolved` 各一行 |
| 响应 | `{"received": n, "stored": m, "duplicates": n-m}`（`received=0` 表示空通知） |
| 边界 | **留痕/可审计，不是通知**；IM/Webhook 推送仍待地址（Open Question #1） |

**接口 11 的约定（D1，2026-09-19 加入）**：

| 项 | 口径 |
|---|---|
| 请求体 | `{job_key, sql, mapping{table_service,topic_service,default_schema}, apply:bool}` |
| 语义 | `apply=false` 只解析+留痕；`apply=true` 另外**写 OM**（建/删边） |
| 拒绝条件 | 解析到不了的表/边、映射缺项、不认识的 connector ⇒ **HTTP 400 + errors，且不落任何行**（宁缺勿错） |
| 存储 | `ga.lineage_edge`（唯一键 `job_key+from+to`；状态 `planned`/`applied`/`entity_missing`/`failed`/`removed`） |
| 响应 | `{job_key, apply, parsed_edges, added[], kept, removed[]}` |
| 边界 | 只做**表/主题级边**；**不建作业实体**（D2）；**无周期对账**；OM 写权限仍用 admin token（待收口） |

**接口 12 的约定（D1 对账，2026-09-19 加入）**：

| 项 | 口径 |
|---|---|
| 请求体 | `{job_key?, repair:bool}`（`job_key` 省略 = 全部作业） |
| 判据 | `GET /api/v1/lineage/getLineageEdge/{fromType}/name/{fromFQN}/{toType}/name/{toFQN}`（**该端点存在时返回 `{"edge":{…}}`**——按顶层 `toEntity` 判会永远为 False，实测踩坑） |
| repair=false | 缺边置状态 `drift`（**只检出，不改 OM**） |
| repair=true | 缺边**重新 PUT**：成功→`applied`；实体不存在→`entity_missing`；其他→`failed` |
| **禁止** | **绝不 DELETE OM 上的边**——OM 还承载 sqllineage 等离线血缘，删错即"第二套真理源"（ADR-A7） |
| 响应 | `{job_key, repair, checked, present, drift, repaired, entity_missing, failed, details[]}` |
| 定时 | cron `/etc/cron.d/ai-governance-lineage` 每日 04:10；成功戳→`job_freshness`→`LineageReconcileStale`（>30h） |

---

## 4. 数据模型（首版，Schema `ga`）

> 与 ADR-A6 的 `gov_metrics` 同口径做法：**自持表结构**、经 **PgBouncer 6432** 读写、角色最小权限、**不直连 OM 内表**。

| 表 | 关键列 | 说明 |
|---|---|---|
| `ga.alert_event` | `id`、`alert_key`(唯一，幂等去重)、`source`、`severity`、`payload`(jsonb)、`received_at`、`enhanced_summary`、`enhanced_at` | 消费 `governance.alerts` 落库；**at-least-once + 幂等写入**（业务主键 + 时间戳去重，设计方案 3.3） |
| `ga.approval_request` | `id`、`requester`、`subject_type`、`subject_ref`、`reason`、`status`(pending/approved/rejected)、`decided_by`、`decided_at`、`comment`、`created_at` | 场景 E 审批流；**事后审计定位**（不阻断任何在线请求） |
| `ga.audit_log` | `id`、`actor`、`action`、`entity_type`、`entity_ref`、`detail`(jsonb)、`at` | 统一审计轨迹 |

**角色**：`ga_writer`（ETL/消费者写入）、`ga_ro`（查询只读）；DDL 与授权脚本随代码入 Git，部署落点 `/data/ai-governance/config/governance-agent/schema.sql`。

---

## 5. 消费与增强链路（S3-6 口径）

```text
Flink 作业（S3-5）
   └─ 异常命中 → Kafka topic `governance.alerts`（2 分区 / 保留 7d）
          └─ Governance Agent 消费（at-least-once）
                ├─ 幂等落库 ga.alert_event
                ├─ 规则化增强（首版：按 severity/规则名补上下文与处置建议模板）
                ├─ LLM 增强（**S4 起**：Ollama + RAG 生成根因线索/处置建议 → 写 enhanced_summary）
                └─ 通知：Alertmanager/IM（S3-6「打通」）
```

**边界**：Agent 只做"增强与分发"，**不改变 Flink 的判定结果**，也不回写执行面（不做事前拦截）。

---

## 6. 依赖与构建

| 项 | 选择 | 理由 |
|---|---|---|
| 运行时 | Python 3.12 slim | 与门户/ETL 同栈，构建加速器已验证可用（阿里云内网 PyPI + `--trusted-host`） |
| Web | 标准库 `http.server`（首版）| **零额外依赖**，避免引入未经核对的框架；接口少、无并发压力（POC 单机、内部访问） |
| Kafka 客户端 | `kafka-python` 或 `confluent-kafka`（**待定，需按官方文档核对与 broker 4.0.0 的兼容性**） | 消费 `governance.alerts` 必需 |
| PG 客户端 | `psycopg2-binary` | 与门户/ETL 一致 |
| 版本纪律 | `requirements.txt` 固定版本；镜像固定 tag + 记录 digest；**不用 `latest`** | AGENTS §7 |

---

## 7. 验收（首版）

| # | 判据 | 方法 |
|---|---|---|
| 1 | 门户**不再降级** | 门户首页/问答页显示"Agent 已就绪"，`GET /health` 200（页面视觉复核） |
| 2 | 告警可落库可查 | 向 `governance.alerts` 投一条测试消息 → `GET /api/v1/alerts` 返回该条；**重复投递不产生重复行**（幂等） |
| 3 | 审批流可走通 | `POST /approvals` → `POST /{id}/decide` → `GET /approvals?status=approved` 返回；`ga.audit_log` 有两条轨迹 |
| 4 | 只读边界 | `ga_ro` 无写权限；Agent 不经 5432（只用 PgBouncer 6432） |
| 5 | 暴露面 | 容器**不发布公网端口**（仅内网/回环 + 隧道） |
| 6 | 不越界 | `/chat` 在 S4 前**明确降级**，不返回伪造答案 |

---

## 8. 待人工确认（写代码前需要拍板）

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | 审批流的**业务字段**（申请对象粒度：表/字段/角色？是否需双人复核？） | 决定 `approval_request` 表结构 | 首版用最简字段（发起人/对象/理由/决定），后续按真实流程扩展 |
| 2 | 告警**通知通道**（IM/Webhook 地址） | S3-6「Alertmanager/IM 打通」与 S3-4 判据② | 给一个 Webhook 即可，规则与链路已就绪 |
| 3 | 增强是否**首版就接 LLM** | 依赖 S4 Ollama | 建议首版只做规则化增强，LLM 增强随 S4 上线（避免 100G 单盘 + CPU 推理拖慢告警链路） |

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-18 | 首次产出（草案）：门户契约（`/health` 200 判定 + `/chat`）、首版 9 个接口、`ga` schema 三表与最小权限角色、消费增强链路与 ADR-A3 边界、依赖选型与版本纪律、6 项验收判据、3 项待人工确认 |
| v1.1 | 2026-09-18 | **状态更新：首版已实现并上线**（实现与验收见《AI数据治理平台_单机版_S3-6治理Agent首版实现记录.md》）：9 个接口按契约落地（`/chat` 保持降级）；§8 三项待人工确认**首版取最简默认值推进**（审批字段用最简组合；LLM 增强留待 S4；通知地址仍待人工）。另补记两条实现期得出、须写入后续设计的纪律：**新库必须做库级 CONNECT 收敛**、**权限变更后必须 `RECONNECT` 回收 PgBouncer 池连接** |
| v1.2 | 2026-09-19 | **新增接口 10 `POST /api/v1/alerts/ingest`（Alertmanager webhook 接收端）**：解决"21 条规则有判定、告警却无留痕"的缺口（AM 原 `default` receiver 为空）。口径：Bearer 令牌强制校验（`GA_INGEST_TOKEN`）、幂等键 `alertmanager:<fingerprint>:<status>`（重复投递不新增行、`firing`/`resolved` 各一行）、`source='alertmanager'` 与 Kafka 来源可区分、响应 `{received, stored, duplicates}`。**边界：留痕/可审计 ≠ 通知**，IM/Webhook 推送仍待地址。上游配置模板见新增 `alerting/alertmanager.yml.example`（令牌占位符，真值不入库）。实现与验收见《…S3-6治理Agent首版实现记录.md》§7 |
| v1.3 | 2026-09-19 | **新增接口 11 `POST·GET /api/v1/lineage/report`（作业图级血缘上报 D1）**：因 Flink 侧拿不到血缘（1.x 不支持 SQL / 2.x SPI 在 1.20.1 缺失 / flink-cdc 未实现 FLIP-314），经人工批准改为**自研侧上报**——GA 解析 Flink SQL → 在 OM 建/删边（`source=PipelineLineage` + `sqlQuery`）。口径：**宁缺勿错**（解析/映射不确定即 400 且不落行）、幂等键 `(job_key, from, to)`、显式 `mapping`、变更时删旧边、Bearer 令牌。**边界：只做表/主题级边（不建作业实体）、无周期对账、OM 写权限待收口**。实现与端到端验收见新增《…S3-5血缘上报D1记录.md》 |
| v1.4 | 2026-09-19 | **新增接口 12 `POST /api/v1/lineage/reconcile`（D1 对账）**：逐边核对 OM 实际边 ↔ GA 期望集，`repair=true` 自愈（缺边重新 PUT）；新增状态 `drift`；cron 每日 04:10 + 新规则 `LineageReconcileStale`（**规则总数 21 → 22**）。**硬约束写进契约**：只新增/修复，**绝不删除 OM 上的任何边**（他源血缘不由本服务管，ADR-A7）。附实测踩坑：`getLineageEdge` 存在时返回 `{"edge":{…}}`，按顶层 `toEntity` 判会**永远 False** ⇒ 对账误报漂移。实现与漂移闭环验收见《…S3-5血缘上报D1记录.md》§5.5 |
| v1.5 | 2026-09-19 | **接口 9 `POST /chat` 的口径补充**（契约不变：仍是 **200 + 文本**）：回答尾部新增**上下文预算口径行**——`上下文预算：估算 X/3000 tokens（实测 Y），采用 m/n 篇[，末篇已截断]；num_ctx=4096`。动因：S4-1 的门禁是「流式首 token <10s」，而 **Ollama 默认 `num_ctx` 实测仅 2050、超长 prompt 被静默截断** ⇒ 不把"喂进去多少"显式暴露，门禁口径就不可核验（见《…S4-2…》§7）。同时把 §3 表格中接口 9 的状态由"降级返回"更新为**已实现（RAG + 引用校验，异常时降级）** |

---

*本文件为自研组件 Governance Agent 的接口规格草案，由《设计方案 v3.2》2.3/3.3 与《部署步骤_单机版》S3-6 展开；不引入组件清单外的新组件。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；依赖版本与兼容性执行前请按官方文档核对。
