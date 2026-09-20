# AI 数据治理平台 · 单机版 测试入口与用例（人工自测用）

> **文档版本**：v1.0（2026-09-19）
> **用途**：给人工一个"能点进去自己测"的入口清单 + 用例与期望结果。
> **方式**：本环境**无固定企业 VPN、也未发布 443**（S3 前置已收敛）⇒ 沿用既定口径走 **SSH 隧道**，
> 把服务器上只绑回环的服务映射到**本机 127.0.0.1**，**不新增任何对外暴露**。
> **边界**：本环境是 **4.1.3 最小测试环境**，只能做**功能联调**；**问答准确率不得在本环境判读**（判读属人工专属）。

---

## 1. 怎么开（Windows 一键脚本）

脚本位置：`C:\Users\Yuanhui\.ai-datathink\open-tunnel.ps1`（与 SSH helper 同目录；**不含任何口令**，host/user 从同目录 `server-info.local.md` 读，密钥用 `~/.ssh/id_ed25519`）。

```powershell
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.ai-datathink\open-tunnel.ps1"           # 开隧道 + 自检
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.ai-datathink\open-tunnel.ps1" -Check    # 只自检
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.ai-datathink\open-tunnel.ps1" -Stop     # 关隧道
```

**实测（2026-09-19）**：9/9 入口返回 HTTP 响应（见下表）；隧道在**会话结束后仍存活**（新开终端再查门户 `/_stcore/health` = 200 已验证）；
关掉用 `-Stop`（脚本按 PID 文件精确杀进程，不会误杀其它 ssh）。

> ⚠️ **两个 PowerShell 坑（实测踩到，重写脚本时务必遵守）**：
> ① 脚本名含中文内容时，**必须存成 UTF-8 with BOM**——PS 5.1 默认按 ANSI(GBK) 读 `.ps1`，无 BOM 会中文乱码并报"字符串未终结"（本次第一次运行就是这个错）。
> ② 用 `curl.exe` 在 PS 里传中文 JSON **会被引号吃掉**（表现为 `bad_json`）——改用 `Invoke-WebRequest -Body ([Text.Encoding]::UTF8.GetBytes($json))`，或把 JSON 落文件后 `--data-binary @file`。

---

## 2. 浏览器入口（都在本机）

| 入口 | 地址 | 用途 | 账号 |
|---|---|---|---|
| **治理门户（主入口）** | http://127.0.0.1:8501 | 场景 D 问答 + 场景 E 审批台 + 统一入口 | **无需登录** |
| Superset | http://127.0.0.1:8088 | 指标看板（资产覆盖率等，只读 `gov_metrics`） | `SUPERSET_ADMIN_USER/PASSWORD`（服务器 `.env`） |
| OpenMetadata | http://127.0.0.1:8585 | 元数据浏览（当前仅 1 个资产） | `OPENMETADATA_ADMIN_PASSWORD`（服务器 `.env`，账号 `admin@open-metadata.org`） |
| Prometheus | http://127.0.0.1:9090 | 40 条告警规则、7 个抓取目标、指标查询 | 无需登录 |
| Alertmanager | http://127.0.0.1:9093 | 当前告警（**当前为空**） | 无需登录 |
| Airflow（OM ingestion） | http://127.0.0.1:8080 | 元数据采集调度（当前无 Dag 运行） | `AIRFLOW_ADMIN_USER/PASSWORD`（服务器 `.env`） |
| Flink JobManager | http://127.0.0.1:8081 | 作业/Checkpoint（**接源后才有作业**） | 无需登录 |
| OPA | http://127.0.0.1:8181 | 策略决策（开发者用） | 无需登录 |
| **GA API** | http://127.0.0.1:8085 | `/health` `/ready` `/chat` `/api/v1/*` | 写接口需 `GA_INGEST_TOKEN`（服务器 `.env`） |

> 口令一律**不写入仓库**；需要时自行在服务器查看（示例命令）：
> `python "$env:USERPROFILE\.ai-datathink\ssh_exec.py" -- "grep -E 'SUPERSET_ADMIN|OPENMETADATA_ADMIN|AIRFLOW_ADMIN' /data/ai-governance/.env"`

---

## 3. 测试用例（照着点，含期望结果）

### 3.1 场景 D：元数据问答（门户聊天框）

> ✅ **门户已视觉重构（2026-09-20，v1.5，按设计稿 `design/mockup.html`）**：**左侧导航 5 页**，顺序即 IA：
> **① 首页 / 能力入口**（统一入口网格：2 个站内模块 + 5 个外部深链）→ **② 智能问答（场景 D）** → **③ 治理指标** → **④ 访问审计与审批（场景 E）** → **⑤ 系统状态（运维视角）**。
> 也就是说：**服务好不好 → 看「系统状态」页；要问问题 → 看「智能问答」页；数据面 → 看「治理指标」页**——三件事三个页面，不再混在一个区块里。
> 深链可直接访问：`?page=home|chat|metrics|audit|status`（非法 slug 回落首页）。

| # | 输入 | 期望 | 观察点 |
|---|---|---|---|
| D1 | 平台里有哪些数据资产？属于哪个服务？ | 答出 `airflow_metadata_poc` / 服务 `airflow_poc` | 回答尾部应有 **节点轨迹**（`retrieve → generate → verify`）、**检索到的资产**、**上下文预算行** |
| D2 | 表 `sales.orders_vip` 的负责人是谁？ | **"元数据中未找到"**（**不编造**） | 这条是"防幻觉"的关键用例 |
| D3 | 随手指一个不存在的资产名（如 `dw.orders_fake`） | 回答里若出现该标识，**必须带"引用未核实"标注** | 引用程序化校验生效 |
| D4 | 同一个问题连问两次 | 第二次明显更快（prompt cache） | 快 ≠ 模型变强，**门禁判读只看首次**（S4-1 §3.1 口径） |

**已实测（经隧道、2026-09-19）**：D1 返回正确的资产/服务（正例，约 3~4 s）；D2 返回"元数据中未找到。"；两者都带 `上下文预算：估算 xxx/1800 tokens（实测 xxx）…num_ctx=4096`。

### 3.2 场景 E：审批（**门户暂无页面，走 GA API**）

> ⚠️ **更正（2026-09-19 实测核对代码后）**：门户的「访问审计与审批（场景 E）」页目前**只有说明文字**
> （"在线审批放行：**POC 不提供**"），**没有审批表单**；审批能力实现并在 **GA API** 上。
> 本文 v1.0 曾按设计印象写成"在门户里提交/审批"，**属错误，已更正**。

用 PowerShell 走一遍（**已实测通过**，2026-09-19）：

```powershell
# E1 创建（返回 id 与 status=pending）
$b1 = @{ requester='sec_reviewer'; subject_type='table'; subject_ref='mock_src.public.orders'; reason='POC 测试' } | ConvertTo-Json -Compress
$r = Invoke-RestMethod http://127.0.0.1:8085/api/v1/approvals -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($b1))
$r | ConvertTo-Json -Depth 5            # → { "id": "<uuid>", "status": "pending", "note": "POC 期仅记录与审计…" }

# E2 决定 ⚠️ decision 只接受 approve / reject（写 approved 会报 bad_decision —— 实测踩到）
$b2 = @{ decided_by='dba_reviewer'; decision='approve'; comment='POC 测试通过' } | ConvertTo-Json -Compress
Invoke-RestMethod "http://127.0.0.1:8085/api/v1/approvals/$($r.id)/decide" -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($b2)) | ConvertTo-Json -Depth 5

# E3 列表复核
Invoke-RestMethod "http://127.0.0.1:8085/api/v1/approvals?status=approved&limit=3" | ConvertTo-Json -Depth 5
```

| # | 动作 | 期望（实测结果） |
|---|---|---|
| E1 | 创建审批 | 返回 `id` + `status=pending` + 说明"POC 期仅记录与审计，不作为在线放行依据（ADR-A3）" |
| E2 | 决定（`decision=approve`） | 同一条记录变为 `status=approved`，`decided_by/decided_at/comment` 有值 |
| E3 | 审计轨迹 | `ga.audit_log` 出现**两条**：`approval.create` 与 `approval.approved`（自查命令见 §4 第 3 条） |

> 边界提醒：POC 期审批**只记录不拦截**（ADR-A3），不要在测试中期待"拦住查询"；门户若要真的能点，
> 属**增量功能**（调用上面已有 API 即可），可另行安排。

### 3.3 Superset 指标看板

| # | 动作 | 期望 |
|---|---|---|
| S1 | 打开数据集 `gov_metrics.asset_coverage_snapshot` | 可读到数据（最近 ETL 每小时 :25 跑） |
| S2 | 反向验证（可选） | Superset **查不到 OM 内表**（只读 `gov_metrics`，ADR-A6） |

### 3.4 OpenMetadata / 运维面

| # | 动作 | 期望 |
|---|---|---|
| O1 | OM 搜索资产 | 当前仅 **1 个**资产（`airflow_poc.airflow_metadata_poc`）——**接源后才会变多** |
| O2 | Prometheus → Status → Targets | **7/7 up**（cadvisor/flink×2/kafka/node/postgres/prometheus） |
| O3 | Prometheus → Alerts | 规则 **40 条**，当前**无活动告警** |
| O4 | Alertmanager | 空（若你手动触发一次告警，会同时出现在 GA 落库与当日日报） |

### 3.5 GA API（命令行，可选）

```powershell
Invoke-RestMethod http://127.0.0.1:8085/health
Invoke-RestMethod http://127.0.0.1:8085/ready
Invoke-RestMethod "http://127.0.0.1:8085/api/v1/alerts?limit=5"
$b = @{ question = '平台里有哪些数据资产？' } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8085/chat -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($b))
```

---

## 4. 测完请注意

1. **关隧道**：`open-tunnel.ps1 -Stop`（否则本机端口仍被占着）。
2. 你的测试**不会**改动平台数据；但如果连问多次，Ollama 会把模型留驻 10 分钟（`OllamaModelNotUnloading` 规则在 >1h 才报，属正常）。
3. 发现异常时，最有用的三条命令（服务器侧）：
   `docker ps --filter health=unhealthy`、`docker logs --tail 50 <容器>`、
   `python "$env:USERPROFILE\.ai-datathink\ssh_exec.py" -- "docker exec ai-governance-poc-postgres-1 psql -U postgres -d governance_agent -c 'select alert_name, received_at from ga.alert_event order by received_at desc limit 5'"`
4. **不要**在测试中把任何真实业务数据粘进门户聊天框（本环境未做在线 PII 脱敏，ADR-A3）。

---

## 附：OM 建采集任务（UI / API）实测流程 —— 含一个**必须知道**的坑

> 2026-09-19 实跑一次"建任务 → 跑一次 → 删除"全流程（用 API 走 UI 的同一条后端链路：OM → Airflow）。

### 三个动作（UI 上就是"保存 / Deploy / Run"）

| # | 动作 | API（UI 对应操作） | 实测结果 |
|---|---|---|---|
| 1 | **创建** | `POST /api/v1/services/ingestionPipelines` | 201，返回任务 id；此时 `deployed=false` |
| 2 | **部署** ⚠️ **别漏** | `POST /api/v1/services/ingestionPipelines/deploy/{id}` | 200 `"Workflow [<name>] has been created"`；Airflow 的 DAG 文件 `<name>.py` 出现，OM 侧 `deployed=true` |
| 3 | **运行** | `POST /api/v1/services/ingestionPipelines/trigger/{id}`（body 带 `runId`） | 200，DagRun 进入 `queued`；任务日志显示 `Processed records: 23 / Errors: 0 / Success 100% / 1.58s` |

**踩坑（本次实测第一遍就踩到）**：只做第 1 步不会生成 DAG。此时触发会失败，报
`Dag id <name> not found in DagModel`（插件把真实原因记在 `/opt/airflow/logs/openmetadata_airflow_api.log`）。
OM 侧只会记一条事件 `WorkflowEventConsumer - Triggering with signal: ingestionPipeline-entityCreated`，
**不会自动补 deploy**；Airflow 的 api-server 访问日志里也**根本没有 `/deploy` 请求**——这是判定"缺了哪一步"的最快证据。

> 接源当天在 **OM UI 上建采集任务**时同理：保存后确认任务状态是 **Deployed**（或 DAG 出现在 Airflow 的 DAG 列表里）再触发；
> 若 UI 上没有自动部署，用上面第 2 步的接口（或 UI 的 Deploy 动作）补一下即可。

### 两个已知缺口（本次一并发现，未修）

| # | 现象 | 影响 / 说明 |
|---|---|---|
| 1 | **运行状态没有回写在 OM 里可见**：跑完 `deployed=true` 但 `pipelineStatuses=0`，`/pipelineStatus` 三种口径均 404 | OM UI 上看不到"这次跑成功/失败"。**任务本身执行是成功的**（Airflow 侧日志为证）。属 OM 2.0.1 + Airflow 3.3.1 组合下的待查项 |
| 2 | 容器内 `airflow dags list-runs` 报 `Database migration required. Please run airflow db migrate` | 只影响**容器内 CLI 查运行历史**；Airflow 的 API server / scheduler **工作正常**（API 触发与执行都成功）。要用 CLI 排查时改用 Airflow UI 或 REST API |

### 清理（本次已执行，零残留）

`DELETE /api/v1/services/ingestionPipelines/{id}?hardDelete=true&recursive=true` ⇒ 两个演示任务均 200 删除，
**Airflow 里对应的 DAG 文件也随删**（`/opt/airflow/dags` 只剩原有那个），OM 任务列表回到 1 个，容器 19 个不变。

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-19 | 首次产出：给出"无固定 VPN + 未发 443"下的**人工自测入口**方案——本机 `open-tunnel.ps1` 一键 SSH 隧道（9 个入口，实测 **9/9 通**、隧道可跨会话存活、`-Stop` 精确关闭）；入口表（门户 8501 / Superset 8088 / OM 8585 / Prometheus 9090 / AM 9093 / Airflow 8080 / Flink 8081 / OPA 8181 / GA API 8085）与**口令只从服务器 `.env` 取、不写入仓库**的口径；按场景给出 **D 问答（含防幻觉负例与引用校验）/ E 审批 / Superset / OM 与运维面 / GA API** 五组用例及期望结果；记录两个实测踩坑（**PS 5.1 读 `.ps1` 必须 UTF-8 with BOM**、**`curl.exe` 在 PS 里传中文 JSON 会被引号吃掉**，改用 `Invoke-WebRequest` + UTF-8 字节）。**未新增对外暴露、未改架构与门禁判据** |
| v1.1 | 2026-09-19 | **两处按实机核对更正**（人工在门户实测反馈后）：① **首页那行 `Governance Agent 已就绪：{"status":"ok",…}` 不是问答结果、也不是报错**——它是门户把 GA `/health` **原始返回**贴出的就绪探针提示；**问答入口在左侧导航「智能问答（场景 D）」页**。已在 §3.1 顶部加"先分清两处"的提示。② **更正 §3.2 的错误用例**：门户的「访问审计与审批（场景 E）」页**目前只有说明文字、没有审批 UI**（"在线审批放行：POC 不提供"），审批能力在 **GA API**——已改为可复制的三步 PowerShell 用例并标注**实测结果**（创建 → `status=pending`；决定 → `approved`；`ga.audit_log` 两条轨迹 `approval.create` / `approval.approved`），并记录实测坑：**`decision` 只接受 `approve`/`reject`**（写 `approved` 会报 `bad_decision`）。**未改架构与门禁判据** |
| v1.2 | 2026-09-19 | **首页布局已修，文档随之更新**：门户 v1.3 把首页「智能问答（场景 D）」区块改为 **状态行人话（原始 JSON 收进折叠详情）+ 就地可提问**（此前"标题下面只有一行就绪提示、没有输入框"被人工判为布局问题）。§3.1 顶部提示由"先分清两处"改为"已修 + 现在的三处差异"。**验收**：门户重建 `308e50f8ff4c…`、重扫 0/0、`/_stcore/health` 200、深链 `?page=chat` 200；**Streamlit `AppTest` 无头跑 home/chat/metrics/audit 四页异常数全 0**，断言首页 `success=[Governance Agent 已就绪（version 0.1.0 · scope S3-6 首版）]`、`text_input=[直接提问（回答含 LangGraph 节点轨迹、检索到的资产、上下文预算口径）]`。**未改架构与门禁判据** |
| v1.3 | 2026-09-19 | **首页区块拆分（人工第二轮反馈："不应该把智能问答和它放在一起，分开说明"）**：首页改为 **能力入口 → 系统状态（运维视角） → 智能问答（场景 D）** 三段，**服务健康与业务功能彻底分开**；问答区块只放输入与回答，未就绪时只留一行"见上方系统状态"；**问答页就绪时不再显示状态行**。§3.1 顶部提示改写为"三区块 + 各自回答什么问题"。**验收**：门户重建 `e1fe07781a23…`、重扫 0/0、`/_stcore/health` 200、首页 200；**`AppTest` 四页异常数全 0**，断言首页 `subheader=['能力入口','系统状态（运维视角）','智能问答（场景 D）']`、**问答页 `success=[]`**。**未改架构与门禁判据** |
| v1.4 | 2026-09-19 | **新增附录「OM 建采集任务（UI/API）实测流程」**（人工要求"按 A 执行一次"）：实跑"创建 → **部署** → 运行 → 删除"全流程，确认 UI 的后端链路是 **OM → Airflow 插件 `/pluginsv2/api/v2/openmetadata/...`**，并抓出**一个必须知道的坑**——**只创建不部署不会生成 DAG**（缺 `POST /services/ingestionPipelines/deploy/{id}`），此时触发报 `Dag id … not found in DagModel`，而 OM 只记 `ingestionPipeline-entityCreated` 事件、Airflow api-server 里**没有 `/deploy` 请求**（最快的判定证据）。**成功验收**：`deploy` 返回 `Workflow […] has been created` → DAG 文件出现、`deployed=true` → 触发 200（`queued`）→ 任务日志 `Processed records: 23 / Errors: 0 / Success 100% / 1.58s`。**一并记录两个已知缺口**：① **运行状态未回写 OM**（`pipelineStatuses=0`、`/pipelineStatus` 404，OM UI 看不到本次运行结果，但任务执行确实成功）；② 容器内 `airflow dags list-runs` 报 `Database migration required`（仅影响 CLI 查历史，API/scheduler 正常）。**清理零残留**：两个演示任务 hardDelete 200、**DAG 文件随删**、OM 任务数回到 1、容器 19 不变。**未改架构与门禁判据** |
| v1.5 | 2026-09-20 | **门户视觉重构后的入口说明更新**（门户 v1.5，按设计稿 `design/mockup.html`）：§3.1 顶部提示由"首页三区块"改为**五页导航 + 首页入口网格**（首页/能力入口 → 智能问答 → 治理指标 → 访问审计与审批 → 系统状态），并注明深链 `?page=home|chat|metrics|audit|status`。**用例本体不变**（问答案例仍在「智能问答」页、指标在「治理指标」页、能力边界在「访问审计与审批」页）；上线实测：5 个深链全 200、容器 healthy、`AppTest` 降级 62/62 与就绪 57/57。详见《…S2-7治理门户.md》v1.5 |

---

*本文为单机版 POC 的人工自测入口说明，由《部署步骤_单机版》S1-7/安全面章节展开；不含新增架构决策。*
*问答准确率判读、准出签字属人工专属，Agent 不代判、不代签。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；入口地址与端口以服务器 `docker compose ps` 实测为准。
