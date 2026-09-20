# governance-agent/ · Governance Agent（自研，唯一自研组件）

> **定位**（《设计方案 v3.2》2.3）：AI 编排与服务层 —— ① 消费 Flink 产出的告警做增强 ② 开放 API ③ 审批网关 ④ AI 编排（场景 D）。
> **契约**：《AI数据治理平台_单机版_S3-6治理Agent接口规格.md》（门户已按 `GET /health` 200 判"就绪"、`POST /chat` 取答案实现，**不得擅自更改**）。
> **部署落点**：服务器 `/data/ai-governance/governance-agent/`，compose `build` 为 `ai-governance/governance-agent:0.1.0`。

## 边界（ADR-A3，违反即缺陷）

- **只做事后审计 + 主动告警**；**审批只记录，不阻断任何在线请求**——不存在"事前拦截"。
- 不做在线 PII 脱敏（Presidio 仅离线批量）。
- 不建第二套元数据/血缘真理源（OpenMetadata 为唯一权威）。
- `/chat` 在 S4（Ollama + RAG）上线前**明确降级**，不伪造答案。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活（**不依赖下游**；门户以此判就绪） |
| GET | `/ready` | 就绪（报告 DB / Kafka 连通性与已消费条数） |
| GET | `/api/v1/alerts?limit=&since=&severity=` | 告警列表 |
| GET | `/api/v1/alerts/{id}` | 告警详情 |
| POST | `/api/v1/alerts/ingest` | **Alertmanager webhook 接收端（2026-09-19 新增）**：把 AM 告警汇入 `ga.alert_event`；需 `Authorization: Bearer <GA_INGEST_TOKEN>` |
| POST | `/api/v1/lineage/report` | **作业图级血缘上报（D1，2026-09-19 新增）**：解析 Flink SQL → 在 OM 建/删血缘边；需同一 Bearer 令牌 |
| GET | `/api/v1/lineage/report?job_key=&status=` | 血缘边与上报状态查询（`planned`/`applied`/`entity_missing`/`failed`/`removed`） |
| POST | `/api/v1/approvals` | 提交审批（`requester`/`subject_type`/`subject_ref`/`reason`） |
| GET | `/api/v1/approvals?status=` | 审批列表 |
| POST | `/api/v1/approvals/{id}/decide` | 审批决定（`decision=approve\|reject`、`decided_by`、`comment`） |
| GET | `/api/v1/audit?entity=&limit=` | 审计轨迹 |
| POST | `/chat` | 场景 D 问答：**S4-2 起走 RAG**（OM 元数据 → pgvector 检索 → 本地 LLM → **引用程序化校验**）；任一环节不可用则降级 |

## 告警汇入（Alertmanager → GA，2026-09-19 新增）

**为什么需要**：Alertmanager 的告警原先只在 UI 里留存（receiver 为空），既不落库也不可审计——21 条 Prometheus 规则等于"有判定、无留痕"。
本端点把 AM 的告警汇入 `ga.alert_event`（`source='alertmanager'`），**不引入新组件、不改变 AM 的判定逻辑**。

| 项 | 口径 |
|---|---|
| 端点 | `POST /api/v1/alerts/ingest`（接收 Alertmanager `webhook_configs` 的标准 payload） |
| 鉴权 | `Authorization: Bearer <GA_INGEST_TOKEN>`；**令牌非空即强制校验**（为空才开放，仅容器网内）——**部署即带令牌，不裸奔** |
| 幂等键 | `alertmanager:<fingerprint>:<status>` ⇒ 重复投递（`repeat_interval` 到点重发）**不产生重复行**；`firing` 与 `resolved` **各占一行**，便于还原"何时起、何时恢复" |
| 来源标记 | `source='alertmanager'`；Kafka 路径仍是表默认值 `governance.alerts`，**两条来源可区分** |
| 上游配置 | 见仓库 `alerting/alertmanager.yml.example`（`route.receiver=ga-ingest`、`send_resolved: true`；**令牌用占位符，真值不入库**） |
| 网络 | 走 compose 网络内按容器名互访（`http://governance-agent:8080`）；**宿主仍只发布回环 8085**，未扩大暴露面 |

> **边界**：本端点做的是"**事后留痕 + 可审计**"，**不是通知**——与"IM/Webhook 推送"有意区分，后者仍待地址。

> **规则化增强（`ADVICE` 表）**：入库时按告警名匹配一条处置建议写入 `enhanced_summary`（**不是 LLM 生成**，LLM 增强属后续）。
> 覆盖范围：复制槽 5 条、主机 SLO 3 条、**Flink 作业/Checkpoint 5 条（2026-09-19 补齐）**。
> **注意**：该字段**入库时写入、不回填历史行**；新增规则须同步往 `ADVICE` 加模板，否则日报/告警视图的"增强摘要"列为空。

## 作业图级血缘上报（D1，2026-09-19 新增）

**为什么自研上报**：ADR-A7 要求"作业图级血缘（作业启动/变更时上报）"，但实测 **Flink 侧拿不到**——
1.x 的 OpenLineage 路径不支持 Flink SQL；2.x 所需 SPI 在 1.20.1 不存在；`flink-cdc` 未实现 FLIP-314（源码树 0 命中）。
ADR-A7 原文**未限定必须由 OpenLineage 上报** ⇒ 机制变更为"GA 自研上报"（**人工选定 D1**，2026-09-19）。

| 项 | 口径 |
|---|---|
| 输入 | Flink SQL 作业原文（`CREATE TABLE … WITH (…)` + `INSERT INTO … SELECT … FROM …`） |
| 输出 | OM 血缘边（`PUT /api/v1/lineage/{fromType}/name/{fromFQN}/{toType}/name/{toFQN}`，`source=PipelineLineage`，并把作业 SQL 写入 `sqlQuery`） |
| 粒度 | **表/主题级边**；**不建作业实体**（D2 才做） |
| **宁缺勿错** | 解析或映射不确定 ⇒ **HTTP 400 + errors，且不落任何行**（ADR-A7 要求血缘准确率） |
| 映射 | 显式 `mapping`（`table_service`/`topic_service`/`default_schema`），缺项即报错；**不从表名猜服务** |
| 幂等 | 库侧唯一键 `(job_key, from_type, from_fqn, to_type, to_fqn)`；OM 侧同一对 from/to 是 upsert |
| 变更/下线 | 不在本次期望集里的旧边会被 `DELETE` 并标 `removed` |
| 模块 | `lineage.py`（纯 stdlib，不引依赖）；**解析准确性测试** `test_lineage.py`（不入镜像，按需运行） |

**已知边界（不得当成"血缘已可用"）**：
* **OM 里没有表实体时连不上边**（实测 404）；真实业务边**要等 S2-2 接源 + OM 采集建表**；
* **没有周期对账**：OM 删实体会连带删边，而 GA 库里的状态会留在 `applied` ⇒ 存在漂移，需后续对账任务；
* 触发靠**调用方显式 POST**（部署脚本/编排接入未做）；门户**无血缘页**。

详见《执行步骤/AI数据治理平台_单机版_S3-5血缘上报D1记录.md》。

## 数据与权限

- 独立库 `governance_agent` + schema `ga`（三表：`alert_event` / `approval_request` / `audit_log`），DDL 见 `schema.sql`。
- **经 PgBouncer 6432**（硬约束②，不直连 5432）；角色 `ga_writer`（读写，无 SUPERUSER/CREATEDB/CREATEROLE）与 `ga_ro`（只读）。
- 告警**幂等落库**：`alert_key` = payload 的 `id`/`alert_key`，否则原始消息 `sha256` ⇒ 重复投递不产生重复行（at-least-once + 幂等，设计方案 3.3）。

## 依赖与版本纪律

> **基础镜像按 digest 固定（2026-09-19）**：`FROM python:3.12-slim@sha256:23b5dc88c7dd…`（debian 13.7）。
> 动因：浮动 tag 会在重建时**静默换基础**——本环境实测过一次（debian 13.6 → 13.7，HIGH/CRIT 由 10/3 变 0/0）。
> **钉完必须重扫验证**（本地 tag 与"新 digest"要分别核实；钉到旧 digest 不会有任何报错）。换基础属组件版本变更，须重跑验收 + 重扫。

- `psycopg2-binary`（必需）、`confluent-kafka`（**可选**：缺失时服务仍启动、告警消费不可用，`/ready` 如实报告 `kafka=false`）。
- 版本固定于 `requirements.txt`；改版本须重跑验收并更新《…S3-6首版实现记录.md》。
- 构建用阿里云内网 PyPI + `--trusted-host`（公网源在构建容器内实测仅 ~100 kB/s）。

## 未纳入（待后续）

| 项 | 说明 |
|---|---|
| LLM 增强（`enhanced_summary` 由 LLM 生成） | S4：Ollama + RAG 上线后接入；首版只做**规则化增强**（规则名 → 处置建议模板） |
| IM/Webhook 通知 | S3-6 后段：Alertmanager 通道打通后由本服务或 AM 直接投递 |
| 认证 | 首版仅内网访问（不发布公网端口）；对外暴露与 SSO 一并在 S2-3 规划 |

## S4-2 RAG（2026-09-18 起）

| 项 | 内容 |
|---|---|
| 模块 | `rag.py`：`--ingest`（离线刷新向量库）/ `--ask`（排障）/ `answer()`（主链） |
| 存储 | **GA 自持库** `governance_agent` 的 `rag` schema（`rag.doc`，含 `vector(768)` 与 HNSW 余弦索引）；**不碰 gov_metrics、不读 OM 内表** |
| 主链（三步显式） | `retrieve()`（pgvector 余弦 top-k）→ `generate()`（Ollama，默认 `qwen2.5:1.5b`，见 S4-1 首 token 实测）→ `verify_citations()`（**引用方括号 FQN 必须在 OM 真实存在，否则标注"未核实"**） |
| 相关环境变量 | `GA_OM_BASE` / `GA_OLLAMA_URL` / `GA_RAG_MODEL` / `GA_EMBED_MODEL`（默认 `nomic-embed-text`）/ `GA_RAG_TOP_K` |
| **编排（按设计）** | **LangGraph 图**：`retrieve → generate → verify → END`（状态 `RagState`；`/chat` 回答里会附带**实际执行的节点轨迹**便于取证）。LLM 客户端用 `langchain-ollama` 的 `ChatOllama`；嵌入沿用 Ollama HTTP `/api/embed` |
| 依赖（已入镜像，均 MIT） | `langgraph==1.2.11`、`langchain-core==1.6.3`、`langchain-ollama==1.1.0`（引入后镜像 205MB → 294MB；**重扫结论：HIGH/CRITICAL 数量与引入前一致，未新增**） |
| **上下文预算（2026-09-19 起，硬纪律）** | `GA_RAG_CTX_BUDGET_TOKENS`（**默认 1800**，口径=**整条 prompt**：模板+上下文+问题）+ **显式 `GA_RAG_NUM_CTX`（默认 4096）**。动因实测：**Ollama 默认 `num_ctx` 仅 2050，超长 prompt 被静默截断**（8,158/24,558 字符都只处理 2050）⇒ 不显式设 `num_ctx` 就等于没有预算。**取值 1800 为人工决定**（档位保持 1.5b）：实测 1.5b 在 1,819 tokens 下冷启动首 token 6.93 s、热态 4.85 s；3,000 tokens 下冷启动 11.31 s（越线）；3b 在 3,000 下 17~21 s（不通过）。裁剪在 `fit_contexts()`：按相关度装入，装不下的截断或丢弃、其后不再尝试（预算 1800 时同一批 12 篇只装得下 7 篇）；换算系数 `GA_RAG_CHARS_PER_TOKEN=1.5`（实测 1.70，保守取值）。`/chat` 与 `answer()` 均回显**估算/实测 token、采用与丢弃篇数、是否截断、num_ctx**——**改档位或换机器必须重新实测** |
| 告警处置建议 | `agent.py` 的 `ADVICE`（规则名 → 处置建议，**现 26 条**：复制槽/宿主机/Flink/Ollama/通用容器五类）。**新增告警规则必须同步加模板**：`enhanced_summary` 是**入库时写入**的，不回填历史行，漏加则日报的"摘要"列一直为空 |

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；依赖版本与兼容性执行前请按官方文档核对。
