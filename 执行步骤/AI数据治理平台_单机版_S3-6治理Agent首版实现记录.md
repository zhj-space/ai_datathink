# AI 数据治理平台 · 单机版 S3-6 Governance Agent 首版实现记录

> **执行日期**：2026-09-18 ｜ **执行方**：Agent（设计/编码/部署/验收）
> **依据**：《…S3-6治理Agent接口规格.md》v1.0（接口契约）；《设计方案 v3.2》2.3 / 3.3 / ADR-A3；《部署步骤_单机版》S3-6
> **代码入 Git**：`governance-agent/`（`agent.py` / `schema.sql` / `requirements.txt` / `Dockerfile` / `README.md`）
> **版本**：v1.0

---

## 1. 结论摘要

| # | 事项 | 结果 |
|---|---|---|
| 1 | **组件上线** | ✅ `ai-governance/governance-agent:0.1.0`（自建镜像，Id `079c22e0…`）healthy；**容器数 17 → 18** |
| 2 | **门户降级结束** | ✅ 门户容器内实测 `agent.health() → (True, …)`：**治理门户不再显示"Agent 未就绪"** |
| 3 | **告警落库链路** | ✅ Kafka `governance.alerts` → 消费 → 幂等落库（重复投递**不产生重复行**）→ 规则化增强写入 → 查询 API 返回 |
| 4 | **审批网关（场景 E）** | ✅ 提交 → 决定 → 列表 → 审计轨迹闭环；重复决定 409、缺字段 400；**只记录不拦截（ADR-A3）** |
| 5 | **权限收敛** | ✅ 新库 `governance_agent` 同样做**库级 CONNECT 收敛**；反向验证：`ga_writer` 连 OM / gov_metrics / superset 库**全部被拒**；`ga_ro` 只读不可写 |
| 6 | **场景 D** | ⚠️ `/chat` **明确降级**（LLM 问答属 S4），不伪造答案 |
| 7 | **对外暴露** | ✅ **不发布公网端口**；仅回环 `127.0.0.1:8085`（宿主 8080 已被 Airflow 占用）+ SSH 隧道 |

---

## 2. 交付内容

### 2.1 接口（与规格一致）

| 方法 | 路径 | 验证结果 |
|---|---|---|
| GET | `/health` | 200（**不依赖下游**，门户据此判就绪） |
| GET | `/ready` | `{"db": true, "kafka": true, "consumed_alerts": N}` |
| GET | `/api/v1/alerts?limit=&since=&severity=` | 返回 1 条测试告警 |
| GET | `/api/v1/alerts/{id}` | 返回详情（含原始 payload） |
| POST | `/api/v1/approvals` | 201 + `pending` + 说明"仅记录与审计" |
| GET | `/api/v1/approvals?status=` | `count=1`（approved） |
| POST | `/api/v1/approvals/{id}/decide` | 200 → `approved`；重复决定 **409**；缺字段 **400** |
| GET | `/api/v1/audit?entity=` | `count=2`（create + approved） |
| POST | `/chat` | 200 + **降级说明**（S4 前的诚实行为） |

### 2.2 数据与权限

| 项 | 内容 |
|---|---|
| 库/模式 | `governance_agent` 库 + `ga` schema（三表：`alert_event` / `approval_request` / `audit_log`）——**与 `gov_metrics` 分离**，不污染 Superset 的只读面（ADR-A6） |
| 角色 | `ga_writer`（读写；**无** SUPERUSER/CREATEDB/CREATEROLE）、`ga_ro`（仅 SELECT） |
| 访问路径 | **经 PgBouncer 6432**（硬约束②）；容器内环境变量实测**不含 5432** |
| 幂等 | `alert_key` = payload `id`/`alert_key`，否则原始消息 `sha256`；`ON CONFLICT DO NOTHING` |
| 增强 | 首版**规则化**（规则名 → 处置建议模板，8 条映射）；LLM 增强属 S4，写同一 `enhanced_summary` 字段 |

### 2.3 部署形态

| 项 | 取值 |
|---|---|
| 镜像 | `ai-governance/governance-agent:0.1.0`（`python:3.12-slim`；依赖固定版本；构建用阿里云内网 PyPI + `--trusted-host`） |
| 依赖 | `psycopg2-binary==2.9.10`（必需）、`confluent-kafka==2.8.0`（**可选**：缺失时服务仍启动，`/ready` 如实报 `kafka=false`） |
| 资源 | 2 vCPU / 4G（设计方案 4.2） |
| 网络 | `backend-net`（门户/PgBouncer）+ `data-net`（Kafka）+ `frontend-net`（使回环发布生效） |
| 健康检查 | HTTP `/health`（30s 间隔） |

---

## 3. 验收证据（原始输出摘录）

```text
[1] /health   {"status": "ok", "service": "governance-agent", "version": "0.1.0", "scope": "S3-6 首版"}
    /ready    {"db": true, "db_error": "", "kafka": true, "kafka_error": "", "consumed_alerts": 0}

[2] 门户视角   portal sees agent ready = (True, '{"status": "ok", ...}')     ← 降级结束

[3] 告警 E2E   投递 {"id":"ga-verify-001","alertname":"PgReplicationSlotWatermarkHigh","severity":"critical",...}
               → GET /api/v1/alerts 返回 count=1，enhanced_summary = "复制槽水位达兜底值 70%：检查 CDC 作业是否停机/背压…"

[4] 幂等       同一条消息再投一次 → 该 alert_key 行数 = 1（总行数 = 1）✔

[6] 审批流     POST → 201 {"id":"a14c98aa-…","status":"pending","note":"POC 期仅记录与审计…"}
               decide → 200 {"status":"approved","decided_by":"sec@example.com",...}
               列表   → count=1 [('sales.orders','approved','sec@example.com')]
               重复决定 → 409   缺字段 → 400

[7] 审计       count=2  - sec@example.com approval.approved table sales.orders
                         - de@example.com  approval.create  table sales.orders

[8] /chat      200 "（降级）智能问答需 S4 的 Ollama + RAG 上线后才能提供；当前可用：…"

[9] 权限       ga_ro SELECT → 1 ✔      ga_ro INSERT → ERROR: permission denied for table alert_event ✔
               GA 容器环境变量含 5432 的次数 = 0 ✔（只走 PgBouncer）

[10] 终态      18 容器全部 healthy；GA /ready db=true kafka=true
```

---

## 4. 本轮的实测踩坑（值得复用）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | `ga_writer` 能连 `openmetadata_db`（应被拒） | PG **默认给 PUBLIC 授了各库的 CONNECT**；新建角色因此可达任意库。既有库 `airflow_db`/`gov_metrics`/`superset` 已收敛，本轮新库与 `openmetadata_db`/`postgres`/`template1` 尚未 | 统一做**库级 CONNECT 收敛**：`REVOKE … FROM PUBLIC` + 按需 `GRANT` 给应用角色（沿用 gov_metrics 既有做法） |
| 2 | 收敛后**仍能连上** openmetadata_db | **PgBouncer 池中已缓存的 idle 服务端连接**让权限变更"看起来没生效"（AGENTS §7 已写明该纪律，本轮实测复现） | 经 6432 管理台执行 **`RECONNECT`** 回收 server 连接后复验 → `permission denied for database "openmetadata_db"` ✔ |
| 3 | 宿主 8080 已被占用 | Airflow（ingestion）先占了回环 8080 | GA 回环端口改用 **8085**（容器内仍监听 8080，与门户的 `GOVERNANCE_API_URL` 契约一致） |

> **给后续的纪律**：① 新建库必须同步 CONNECT 收敛；② **权限变更后一律回收 PgBouncer 池连接**再验证（`RECONNECT`），否则会得出"权限没生效"的错误结论。

---

## 5. 未做 / 待确认

| # | 项 | 说明 |
|---|---|---|
| 1 | **LLM 增强** | `enhanced_summary` 目前由**规则模板**生成；LLM（Ollama + RAG）属 S4，届时替换同一字段 |
| 2 | **IM/Webhook 通知** | 人工决定"暂时不处理"；Alertmanager 侧的通道与 S3-4 判据② 仍待地址（本服务已具备落库与查询，IM 只是"提醒面"） |
| 3 | 认证/鉴权 | 首版**仅内网访问**（不发布公网端口），未做 API 认证；对外暴露需与 S2-3 SSO 一并规划（**不得**在没有认证的前提下直接发布公网） |
| 4 | 审批流的业务字段 | 首版取最简字段（发起人/对象/理由/决定/意见）；是否需双人复核等待人工拍板（规格 §8 第 1 项） |
| 5 | Flink 作业联动 | 告警的真实生产者是 S3-5 的 Flink 作业（依赖源库）；本轮用 Kafka 控制台投递验证了消费侧 |

---

## 6. 影响面与需同步的文档

| 文档 | 是否同步 | 说明 |
|---|---|---|
| 设计方案 / 实施执行计划 | **否** | 无架构或口径变更（GA 是清单内既有组件） |
| 《…S3-6治理Agent接口规格.md》 | **是** | 增"实施状态：已实现（见本记录）" |
| 《部署步骤_单机版》S3-6 段 | **是** | 增落地注（镜像、端口、库、权限收敛、验收） |
| 《单机版部署计划》 | **是** | S3-6 状态注 + **容器数 17 → 18** |
| 《…S3前置部署记录.md》 | **是** | §1 容器数行更新为 18；GA 从"未做"移出 |
| AGENTS.md | **是** | §1 文档地图（`governance-agent/` + 本记录）、§7 新增代码目录行 |
| 仓库外敏感文件 | **是** | GA 库凭据（`GA_WRITER_PASSWORD`/`GA_RO_PASSWORD` 在服务器 `.env`；访问方式 +8085） |

---

## 7. 告警汇入（Alertmanager → GA，2026-09-19）

### 7.1 为什么做

实测发现：**Alertmanager 的 `receivers` 只有一个空的 `default`（无任何 URL）** ⇒ 21 条 Prometheus 规则的告警**只存在于 AM UI 里**（UI 一关即无从追溯），既不落库也不可审计。
对 Kafka 来源的告警 GA 已经落库，但 Prometheus/Alertmanager 这一侧**没有任何留痕**。

**本次改动**：给 AM 增加一个接收器 `ga-ingest`，把告警投给 GA 的新端点 → 落进 `ga.alert_event`。**不引入新组件、不动规则与阈值、不改变 AM 的判定逻辑**。

### 7.2 交付内容

| 项 | 内容 |
|---|---|
| GA 代码 | `agent.py` 新增 `ingest_alertmanager()` 与 `POST /api/v1/alerts/ingest`；`store_alert()` 增加 `source` 参数 |
| 鉴权 | 新增 `GA_INGEST_TOKEN`（48 hex，服务器 `.env`）；**AM 侧以 `Authorization: Bearer` 携带**；GA 侧令牌非空即强制校验 |
| 幂等键 | `alertmanager:<fingerprint>:<status>` ⇒ 重复投递不新增行；`firing`/`resolved` 各一行（可还原起止） |
| 来源区分 | `source='alertmanager'`（Kafka 路径仍为默认 `governance.alerts`） |
| 上游配置 | `alerting/alertmanager.yml.example`（令牌占位符，**真值不入库**）+ 服务器 `/data/ai-governance/config/alertmanager/alertmanager.yml` |
| compose | GA 段加 `GA_INGEST_TOKEN`；**重建镜像并重新固定 digest**（新 `09421ae7c3be27…`）；compose 校验和由 `e2022620…` 变为 **`0d07688e1be4c5bb…`**（现行） |

### 7.3 验收证据（命令 + 实际输出）

```
--- AM 配置校验 ---
docker exec ai-governance-poc-alertmanager-1 amtool check-config /etc/alertmanager/alertmanager.yml
Checking '/etc/alertmanager/alertmanager.yml'  SUCCESS   （1 receivers；0 inhibit rules）

--- 1) 无令牌 POST（应 401）---
http=401  {"error": {"code": "unauthorized", "message": "缺少或错误的 Bearer 令牌"}}

--- 2) 合成 AM payload（应 stored=1）---
{"received": 1, "stored": 1, "duplicates": 0}   http=200

--- 3) 重复投递同一 payload（应 duplicates=1，不新增行）---
{"received": 1, "stored": 0, "duplicates": 1}   http=200

--- 4) resolved 变体（同 fingerprint，应新增 1 行）---
{"received": 1, "stored": 1, "duplicates": 0}   http=200

--- 5) 落库查询 ---
count: 3
  id=14 name=GaIngestSynthetic sev=warning src=alertmanager key=alertmanager:synthetic0001:resolved
  id=12 name=GaIngestSynthetic sev=warning src=alertmanager key=alertmanager:synthetic0001:firing
  id=1  name=PgReplicationSlotWatermarkHigh sev=critical src=governance.alerts key=ga-verify-001

--- 6) 真实端到端（向 AM 注入告警，等 group_wait 30s）---
AM inject http=200
GA 中 alertmanager 来源：
  GaIngestPathTest  info  key=alertmanager:a669f2e8707f626b:firing      ← AM webhook 真实投递成功
AM 日志中的 webhook/error 关键字：**空**（无投递错误）

--- 7) GA 镜像漏洞重扫（未新增依赖）---
Total: 13 (HIGH: 10, CRITICAL: 3)   ← 与改动前完全一致，未引入新漏洞
```

### 7.4 踩坑与纪律

| # | 事项 | 说明 |
|---|---|---|
| 1 | **容器网内互访不需要改端口发布** | GA 容器内监听 `0.0.0.0:8080`，宿主只发布回环 `127.0.0.1:8085`；AM 与 GA 同在 `backend-net`/`frontend-net`，直接用容器名 `http://governance-agent:8080` 即可——**暴露面没有扩大** |
| 2 | **`docker compose build` 时 `image:` 行不能带 digest** | 否则构建失败/行为异常；流程固定为"去 digest → build → 取新 image ID → 重新钉回"。本次新 digest `09421ae7c3be274220811fd22a005d5c18925b2b3063581ae4a064079eb77e63` |
| 3 | **AM 未启用 lifecycle API** | 本环境 AM 启动参数没有 `--web.enable-lifecycle`，**`/-/reload` 不可用**；配置生效用 `docker kill --signal=HUP <容器>`（热加载，无需重启） |
| 4 | `resolved` 的 AM 侧投递受 `group_interval`（5m）顺延 | GA 侧的 `resolved` 落库已用合成 payload 验证；**AM 侧真实 resolved 通知在本次观察窗口内未出现**（AM 仍把测试告警列为 active），列为待观察项，不伪造 |

### 7.5 边界与未做

| 项 | 说明 |
|---|---|
| **留痕 ≠ 通知** | 本次只解决"有判定、无留痕"；**告警主动通知到人仍缺 IM/Webhook 地址**（Open Question #1 保留） |
| 门户展示 | 门户当前**没有告警页**（4 个页面：首页/治理指标/问答/审批台）⇒ 本次交付是"**落库 + API 可查**"，**未承诺门户可见**；加页面属另一件事 |
| 历史告警 | 改动前只存在于 AM UI 的告警**无法追回**（AM UI 不持久化历史通知） |

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-18 | 首次产出：GA 首版上线（接口 9 个、`ga` schema 三表、`ga_writer`/`ga_ro` 角色、经 PgBouncer 6432、幂等消费 + 规则化增强）；验收 10 项（门户降级结束、告警幂等落库、审批流闭环、审计、只读边界、/chat 降级、无公网端口）；记录 3 个实测踩坑（**默认 PUBLIC CONNECT ⇒ 新库需库级收敛**、**池连接导致权限变更不即时生效 ⇒ `RECONNECT`**、宿主 8080 被 Airflow 占用 ⇒ GA 用 8085）；容器数 17 → 18 |
| v1.1 | 2026-09-19 | **新增 §7 告警汇入（Alertmanager → GA）**：修掉"21 条规则有判定、告警却无留痕"的缺口（AM 原 receiver 为空）。GA 加 `POST /api/v1/alerts/ingest` + `GA_INGEST_TOKEN`（Bearer 强制校验）；幂等键 `alertmanager:<fingerprint>:<status>`；`source='alertmanager'`。验收：401 未授权、合成 payload stored=1、重复投递 duplicates=1（不新增行）、resolved 变体新增行、**真实端到端**（向 AM 注入 → 30s 后 GA 收到 `firing`，AM 无投递错误）、**镜像重扫 Total 13（HIGH 10 / CRIT 3）与改动前一致**。踩坑三条：容器网内互访不需改端口发布（暴露面未扩大）、`build` 前必须去 digest（新 digest `09421ae7…`）、AM 未启用 lifecycle API 须用 `SIGHUP` 热加载。**边界：留痕 ≠ 通知（IM 仍待地址）；门户无告警页，本次不承诺门户可见**。同步接口规格 v1.2、新增 `alerting/alertmanager.yml.example`、AGENTS §1/§7 |
| v1.2 | 2026-09-19 | **补齐 `ADVICE` 规则化增强模板（Flink 5 条）**：为 `FlinkMetricsScrapeFailed` / `FlinkRegisteredJobNotRunning` / `FlinkJobRestarted` / `FlinkCheckpointFailed` / `FlinkCheckpointStalled` 增加处置建议（此前日报与告警视图的"增强摘要"列为空）。**验收**：合成 AM payload 投递 → 落库 `enhanced_summary` **已填充**（引用了 ADR-A2 第 3 件与 `state.checkpoints.dir` 排查方向）→ **告警日报摘要列显示该建议**；探针行已删（残留 0）。新镜像 `1e64eb59e317b041…`、compose 校验和 **`d87f85ba6237d09a…`**、**重扫仍 0/0**（基础未变）。**注意**：该字段入库时写入、**不回填历史行**；新增规则须同步加模板 |
| v1.3 | 2026-09-19 | **`ADVICE` 增至 12 条（新增 Ollama 7 条）+ `/chat` 暴露 RAG 上下文预算口径**。① **新增模板**：`OllamaApiDown` / `OllamaContainerCpuSaturated` / `OllamaContainerMemoryHigh` / `OllamaContainerOomKilled` / `OllamaModelNotUnloading` / `OllamaProbeStale` / `OllamaRuntimeMetricsMissing`（对应 `alerting/poc-alerts.yml` 新增的 `ollama-runtime` 组 7 条）；② **`/chat` 输出新增预算脚注**（估算/**实测 token**、采用/丢弃篇数、是否截断、`num_ctx`），使 S4-1 的 10s 门禁口径**可核验**（接口仍是"200 + 文本"，契约不变）；③ **验收**：合成 AM 投递（标签 `container="selftest-20260919"`）首次 `stored=1` 且 `enhanced_summary` 非空、同键重投 `duplicates=1`、无令牌 **401**；真实规则触发落库行同样带建议并进入当日日报；`/health` `/ready` `/api/v1/alerts` `/api/v1/approvals` 回归 200。**镜像** 重建为 **`2c8b39e97dcd00aed490754d43ebb4080aa12dca1e6e3a0cd4a44bf068fca926`**（296.9 MB）、**重扫 HIGH+CRITICAL = 0**；compose 校验和 **`82c9ebb00aa670a59fb807bfc95eb00daad3ce3b3ba136cbdc44c2e5b4993ad2`**（新增 `GA_RAG_CTX_BUDGET_TOKENS` / `GA_RAG_NUM_CTX` 两个 env）。同步《…S4-1…》v1.1、《…S4-2…》v1.3、《…组件部署状态.md》v1.1。**未改架构、组件清单与门禁判据** |
| v1.4 | 2026-09-19 | **`ADVICE` 增至 26 条（新增通用容器 6 条）**：为 `ContainerMetricsMissing` / `ContainerProbeStale` / `ContainerSeriesIncomplete` / `ContainerOomKilled` / `ContainerMemoryNearLimit` / `ContainerCpuSaturated` 补处置建议（对应 `alerting/poc-alerts.yml` 新增的 `container-runtime` 组，规则总数 **34 → 40**）。**验收**：真实 OOM 触发（一次性 64MB 容器 → `oom_kill=1`）⇒ 落库行 `enhanced_summary` **非空**（`13:35:32Z` firing / `13:40:32Z` resolved）、清理后零残留。**镜像** 重建为 **`394700abe6ed1ece66c6692230b4a77e0999b6ca7aaa63aa0450e253f8fff69b`**、**重扫 HIGH+CRITICAL = 0**；compose 校验和 **`6f7d40c6bc0fc1f6627932c827d23b25819ff9cf81d73c2f14657dfd5160e9d0`**。**同轮运维**：回收未使用镜像与构建缓存（释放≈6.63 GB，`/` 可用 51 → 57 GB）。同步《…组件部署状态.md》v1.2 与 `alerting/README.md` v1.2。**未改架构、组件清单与门禁判据** |
| v1.5 | 2026-09-19 | **RAG 上下文预算定档 1800（随 GA 重建）**：人工决定"档位保持 **1.5b** + 压低上下文预算"，`rag.py` 默认值与 compose 同步由 3000 改为 **1800**（避免两处口径漂移），GA 重建为 **`44ca36b1e519046418232c10865e85c976b936c3065788f89e281aac625faa0d`**、**重扫 HIGH+CRITICAL = 0**、compose 校验和 **`62aeeef899d2bedeface5bf2580236afcaa0e4c6b9d40651cdeba62bafa8bdfa`**。**验收**：`/health` `/ready` 200、`/chat` 正例 3.10 s（引用正确）/ 负例 2.70 s（不编造）、`/chat` 尾部口径行显示 `261/1800 tokens`。**依据**（M0 第 6 项口径实测）：1.5b 在 1,819 tokens 下冷启动首 token **6.93 s**、热态 4.85 s；3,000 tokens 下 11.31 s（越线）；3b 17~21 s（不通过）。详见《…S4-2…》v1.5 与《…S4-1…》v1.4。**未改架构、组件清单与门禁判据** |

---

*本文件为 Governance Agent 首版的实现与验收记录，由《…S3-6治理Agent接口规格.md》与《部署步骤_单机版》S3-6 展开；仅实现清单内组件，未引入新组件。*
*门禁判定与签字为人工专属，Agent 不代签、不背书。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；依赖版本与兼容性执行前请按官方文档核对。
