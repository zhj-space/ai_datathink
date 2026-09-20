# AI 数据治理平台 · 单机版 S4-2 RAG 最小闭环记录

> **执行日期**：2026-09-18 ｜ **执行方**：Agent（开发 + 部署 + 验收）
> **依据**：《部署步骤_单机版》S4-2；《设计方案 v3.2》S4-2（pgvector 向量索引 / 元数据摘要向量化管道 / RAG 链路 / **引用程序化校验**）、ADR-A3、ADR-A6
> **代码入 Git**：`governance-agent/rag.py`（新增）+ `agent.py`（`/chat` 接 RAG）+ `Dockerfile` / `README.md`
> **版本**：v1.0

---

## 1. 结论摘要

| # | 事项 | 结果 |
|---|---|---|
| 1 | **最小 RAG 闭环上线** | ✅ 端到端跑通：**OM 元数据 → 向量（pgvector）→ 检索 → 本地 LLM → 引用校验** |
| 2 | **门户场景 D 不再降级** | ✅ `POST /chat` 返回**基于检索的真实回答**（含检索到的资产清单）；任一环节不可用时仍**降级为诚实提示** |
| 3 | **引用程序化校验生效** | ✅ 定向实测：真引用 `unverified=[]`；**假引用 3 个全部被标注**「⚠ 引用未核实（可能为模型编造）」——设计原文要求 |
| 4 | **不幻觉（负例）** | ✅ 问不存在的表 `sales.orders_vip` → 回答**「元数据中未找到」** |
| 5 | 边界 | ✅ 只走 **OM REST API**（**不读 OM 内表**）；向量存 **GA 自持库** `rag` schema（**不碰 gov_metrics**） |
| 6 | **LangGraph（按设计引入）** | ✅ **2026-09-18 已按设计引入**：`retrieve → generate → verify → END` 图（节点轨迹可在 `/chat` 回答中看到）；依赖 `langgraph 1.2.11` / `langchain-core 1.6.3` / `langchain-ollama 1.1.0`（均 MIT）；**重扫未新增 HIGH/CRITICAL**（见 §6.2） |

---

## 2. 落地内容

| 项 | 内容 |
|---|---|
| 向量存储 | **pgvector 0.8.6**（扩展已启用）；`governance_agent` 库 → schema **`rag`** → 表 **`rag.doc`**（`doc_id/doc_type/fqn/title/content/embedding vector(768)`） |
| 索引 | `hnsw (embedding vector_cosine_ops)` 余弦索引 + `fqn` B-tree |
| 嵌入模型 | `nomic-embed-text`（**768 维**，实测维度已核验） |
| 生成模型 | **`qwen2.5:1.5b`**（档位依据《…S4-1Ollama部署与首token实测.md》：3b 在长上下文下不达标） |
| 权限 | `rag` schema 授权给 `ga_writer`（GA 自持库，**不新增角色**） |
| 定期刷新 | cron **每小时 :40**（滞后于 ETL :25，避免抢 IO）⇒ 设计"离线定期刷新"口径；**最长滞后 1 小时** |

### 2.1 主链（三步显式，便于后续替换实现）

```text
retrieve()            问题 → 嵌入 → pgvector 余弦 top-k（默认 k=4）
   ↓
generate()            上下文 + 强约束 prompt（"只能依据上下文；没有就说未找到；引用用 [FQN]"）→ Ollama
   ↓
verify_citations()    提取回答中的 [FQN] → 逐个核对是否在 OM 真实存在（依据 rag.doc，其数据来自 OM API）
                      未通过 ⇒ **在回答前插入「⚠ 引用未核实」清单**（不静默放行、也不直接丢弃）
```

### 2.2 相关环境变量（写入 compose）

| 变量 | 值 |
|---|---|
| `GA_OM_BASE` | `http://openmetadata-server:8585/api/v1` |
| `GA_OLLAMA_URL` | `http://ollama:11434` |
| `GA_RAG_MODEL` | `qwen2.5:1.5b` |
| `GA_EMBED_MODEL` | `nomic-embed-text` |
| `GA_RAG_TOP_K` | `4` |
| `OPENMETADATA_ADMIN_PASSWORD` | 用于读 OM 元数据（**POC 简化**：与 ETL 同口径使用 admin 账号，待服务账号/SSO 替换） |

---

## 3. 验收证据（实测）

```text
摄入            rag.py --ingest → 写入 1 篇；rag.doc 现有 1 篇
                内容：pipeline:airflow_poc.airflow_metadata_poc（OM 当前唯一资产）
                向量维度：768 ✔

/chat 正例      Q: 平台里有哪些数据资产？属于哪个服务？
                A: 平台里有以下数据资产：**资产名称**：airflow_metadata_poc；**服务**：airflow_poc …
                — 检索到的资产：airflow_poc.airflow_metadata_poc

/chat 负例      Q: 表 sales.orders_vip 的负责人是谁？
                A: 元数据中未找到。          ← **不编造**

引用校验（定向）
  真引用 "资产是 [airflow_poc.airflow_metadata_poc]"           → unverified = []                 ✔
  假引用 "[sales.orders_vip] / [dw.orders_fake] / [etl_fake_001]" → ⚠ 引用未核实（三个全部标注）✔

检索抽样        平台有哪些资产？(score 0.479) / 血缘相关(0.459) / 质量测试(0.494) → 均命中唯一资产

定期刷新        cron `/etc/cron.d/ai-governance-gov-metrics` 增 `40 * * * * … rag.py --ingest`；手工按同命令执行成功

终态            19 容器全 healthy；compose 校验和 e202262088cb4eb12a43ef8d04d5b2d51730ea7975cce71b75967812ae232fc1
```

---

## 4. 本轮踩坑（5 个，均可复用）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 容器里 `python rag.py` 报 `No such file` | **Dockerfile 只 `COPY agent.py`**，漏了新增的 `rag.py` | Dockerfile 改为 `COPY agent.py rag.py ./`（**新增模块必须同步 Dockerfile**） |
| 2 | `docker compose` 报 `mapping key "GA_OM_BASE" already defined` | **我重跑了一次"补丁脚本"导致 env 变量被插入两遍** | 用"第二次运行前的备份"还原（该备份已含首次补丁）；**教训：插入式补丁脚本必须幂等**（先判断是否已存在） |
| 3 | `failed to solve: build tag cannot contain a digest` | compose 的 `image:` 已带 `@sha256:`，构建时被当作 tag | **构建前先去 digest（纯 tag）→ 构建 → 再重新固定新 digest** |
| 4 | 校验脚本 `import rag` 失败 | `docker exec` 的 cwd 是 `/tmp`，不在 `/app` | 用 `-e PYTHONPATH=/app`（或 `-w /app`） |
| 5 | `docker cp` 进容器的文件删不掉（`Operation not permitted`） | 该文件属主是 root，而容器以 `ga` 运行 | `docker exec -u root … rm` 清理 |

> 另有 2 个此前已记录、本轮再次出现的坑：`docker exec` 漏 `-i` 导致 heredoc 未送入（**已第 3 次踩到**，建议后续脚本模板统一带 `-i`）。

---

## 5. 未做 / 待确认

| # | 项 | 说明 |
|---|---|---|
| 1 | **资产量极少** | 当前仅 **1 篇**（OM 自举的 pipeline 资产）⇒ RAG 能答但**知识面窄**；**接业务源（S2-2）后价值才真正体现** |
| 2 | **问答准确率不可判读** | 4.1.3 明文：本环境仅"功能联调"，**准确率不得在本环境判读**（准确率判定亦为人工专属） |
| 3 | **GA 读 OM 用 admin 账号** | POC 简化（与 ETL 同口径）；**含真实凭证的业务源接入前须重评**，替换为服务账号/API Key |
| 4 | 向量库访问控制 | `rag.doc` 仅容器网可达（GA 自持库、无对外端口）；**未做行级/字段级鉴权** |
| 5 | 向量与元数据一致性 | 每小时刷新 ⇒ 元数据变更后**最长 1 小时**内检索结果滞后；看板/问答须标注"数据截至"（与 ADR-A6 口径一致） |
| 6 | 引用校验的强度 | 当前校验"**标识是否存在**"；**未校验**"该资产的描述是否支持该结论"（属更强的一致性校验，可按需加强） |
| 7 | **LangGraph 偏差** | 见 §6 |

---

## 6. LangGraph 编排（**已按设计引入**）

> **过程如实记录**：首版曾以"直连三步函数"落地（未引入 langgraph），已在记录中声明为设计偏差并提请人工确认；**2026-09-18 人工要求"按设计引入"，当日完成并验证**。接口未变（`ingest()` / `retrieve()` / `answer()` 语义一致）。

### 6.2 引入结果与验收

| 项 | 结果 |
|---|---|
| 图结构 | `StateGraph(RagState)`：节点 `retrieve → generate → verify`，边顺序连接至 `END`；状态含 `question / contexts / answer / unverified / nodes` |
| LLM 客户端 | `langchain-ollama` 的 `ChatOllama`（`model=qwen2.5:1.5b`、`base_url=http://ollama:11434`、`temperature=0.1`）；嵌入仍走 Ollama HTTP `/api/embed` |
| 依赖版本（入 `requirements.txt`） | `langgraph==1.2.11`、`langchain-core==1.6.3`、`langchain-ollama==1.1.0`（**均 MIT**） |
| 镜像 | 重建后 id `0857cafe…`；体积 **205MB → 294MB**（+~89MB）；compose digest 已更新 |
| **节点轨迹取证** | `/chat` 回答尾部出现 `LangGraph 节点：retrieve → generate → verify`；直接调用 `rag.answer()` 返回 `nodes=['retrieve','generate','verify']` ✔ |
| 功能回归 | 正例（正确引用资产）✔；**负例（不存在的表）→「元数据中未找到」**✔；引用校验 `unverified=[]`（真引用）、混合输入只标注假引用 `['dw.orders_fake']` ✔；`/health`、`/ready`（db/kafka=true）✔ |
| **重扫（新增依赖的 CVE 面）** | trivy（同一口径：`--ignore-unfixed --severity HIGH,CRITICAL`，双 DB 源 ghcr.io）→ **Total 13（HIGH 10 / CRITICAL 3）**，**与引入前完全一致 ⇒ 未新增高/严重漏洞** |

### 6.1 引入 LangGraph/LangChain 的**条件与实测结论**（一次性容器预验证 → 随后正式引入）

| # | 条件 | 实测结论 | 证据 |
|---|---|---|---|
| 1 | **安装阶段要有对外出口** | ⚠️ 关键坑：在 **internal 网络（data-net/backend-net）里装不了包**（DNS/出口不通），须用**默认 bridge 网络**安装；**运行阶段**才只需要 backend-net（连 Ollama/PgBouncer/OM） | `Temporary failure in name resolution`（internal 网络）→ 换默认网络后安装成功 |
| 2 | **内网 PyPI 可达** | ✅ 与 GA 镜像同一套配置（阿里云内网源 + trusted-host）**可直接安装** | `pip install langgraph langchain-ollama` 成功 |
| 3 | **Python 3.12 兼容** | ✅ 实装：`langgraph 1.2.11` / `langchain-core 1.6.3` / `langchain-ollama 1.1.0`（另拉入 `langgraph-checkpoint 4.2.0`、`langgraph-sdk 0.4.4`、`langgraph-prebuilt 1.1.0`、`pydantic 2.13.5`、`httpx 0.28.1`、`tenacity 9.1.4`、`orjson 3.12.0`） | `pip list` |
| 4 | **许可证（AGENTS §6 硬要求）** | ✅ `langchain-core` **MIT**、`langchain-ollama` **MIT**、**`langgraph` 元数据声明 `License-Expression: MIT`**（含 `License-File: LICENSE`）；⚠️ **langgraph-* 子包各自带 LICENSE，逐一核对**属引入时的补充动作 | dist-info `METADATA` + `licenses/` 目录 |
| 5 | **体积与供应链** | 依赖树**增量约 73 MB**（site-packages 9 → 82 MB）；引入后必须**重建镜像 + 重新扫描漏洞**（新增依赖带来新的 CVE 面）+ 记录新 digest | `du -sm site-packages` 前后对比 |
| 6 | **运行期资源与链路** | ✅ 无需额外 CPU/内存（推理仍在 Ollama）；容器内可直连 `ollama:11434`，**最小 LangGraph + ChatOllama 跑通**：输出「数据血缘是指数据的来源、流转路径和依赖关系的追踪记录。」 | 一次性容器实测（已删除，未进部署） |

**成本/收益判断（我的建议）**：

| 维度 | 说明 |
|---|---|
| 成本 | +73 MB 依赖、多一套版本与许可证维护、镜像重建与重扫、约**半天**工程（含验收） |
| 收益 | 获得**多步编排能力**（条件路由 / 工具调用 / 重试 / 人工中断），**在当前"1 个资产 + 三步直连"场景下用不到**；接源后 RAG 变复杂时价值才显现 |
| 结论 | 预验证结论为"建议接源后再引入"，但**人工要求严格对齐设计原文 ⇒ 已于当日按上表 6 项条件完成引入**（见 §6.2），6 项条件全部满足 |

---

## 7. RAG 上下文预算（2026-09-19 新增，B 项）

### 7.1 为什么必须有（S4-1 §4 第 3 项的挂账）

S4-1 的门禁判据是「流式**首 token < 10s**」，而 prefill 耗时近似随上下文长度线性增长 ⇒
**没有上下文预算，10s 门禁就没有稳定口径**（S4-1 原文："建议在 S4-2 定义该预算后再定判读口径"）。

### 7.2 实测发现：Ollama 默认 `num_ctx` 只有 2050，**超长 prompt 会被静默截断**

| 实测（`qwen2.5:1.5b`，2026-09-19） | chars | `prompt_eval_count` |
|---|---|---|
| 4 篇合成文档（低于上限） | 1,622 | **952**（换算 **1.70 字符/token**） |
| 20 篇合成文档 | 8,158 | **2050**（= 被截断） |
| 60 篇合成文档 | 24,558 | **2050**（= 被截断） |

**没有任何报错或提示**，模型只看到前 2050 tokens —— 这对 RAG 是**静默降质**（上下文丢了一半，答案却照常生成）。

> 连带更正：S4-1 §3 中"≈5.2k tokens（10,394 字符）"的长上下文样本，**实际只处理了 2050 tokens**
> （见《…S4-1…》§3.3）。结论（3b 不通过 / 1.5b 通过）不变，但口径收窄。

### 7.3 落地口径与实现

| 项 | 取值 | 依据 |
|---|---|---|
| **上下文预算** | ✅ **`GA_RAG_CTX_BUDGET_TOKENS=1800`（2026-09-19 人工决定：档位保持 1.5b + 压预算）**。取舍实测：1.5b 在 1,819 tokens 下**冷启动首 token 6.93 s / 热态 4.85 s**（对 10 s 门禁留 31% 余量）；2,062 tokens 下 7.70 s / 5.66 s（也达标但余量薄）；**3,000 tokens 下冷启动 11.31 s（越线）**；3b 在 3,000 下 17~21 s（不通过） | S4-1 §4 第 3 项建议（"例如 ≤3k tokens"）+ **S4-1 §3.4 的实测**；口径为**整条 prompt（模板 + 上下文 + 问题）**的上限 |
| **上下文窗口** | `GA_RAG_NUM_CTX=4096` | 必须**显式高于预算**，否则 Ollama 仍按默认 2050 截断；`4096 > 1800 + num_predict(256)` |
| 换算系数 | `GA_RAG_CHARS_PER_TOKEN=1.5` | 实测 1.70，取 **1.5 保守值**（估算偏大 ⇒ 宁可少喂，不冒超预算风险） |
| 裁剪策略 | 按**相关度顺序**装入；装不下的：末篇可截断（≥200 字符）或整篇丢弃，**其后一律不再尝试** | 相关度靠后的文档价值更低；行为可预测、可复现 |
| 最小上下文 | `GA_RAG_MIN_CTX_TOKENS=200` | 预算再小也给上下文留出余量 |
| 可核验 | `rag.answer()` / `POST /chat` 返回 `ctx`：预算、估算值、**实测 token（Ollama `prompt_eval_count`）**、采用/丢弃篇数、是否截断、`num_ctx` | 让"喂进去多少"**可审计**，而不是黑箱 |

**代码落点**：`governance-agent/rag.py`（`fit_contexts()`、`PROMPT_TEMPLATE`、`llm()` 传 `num_ctx`）、`agent.py`（`/chat` 尾部追加口径行）；
**compose** 显式声明两个 env（`GA_RAG_CTX_BUDGET_TOKENS` / `GA_RAG_NUM_CTX`）。

### 7.4 验收证据（实测）

| # | 验证 | 结果 |
|---|---|---|
| 1 | 裁剪行为 | 12 篇合成上下文 ⇒ 预算 1800 时**采用 7 篇**（`prompt_tokens_est=1716`）、预算 3000 时采用 11 篇（`2889`）——预算越小，丢弃的检索结果越多（**这就是压预算的代价**） |
| 2 | **`num_ctx` 真的抬高** | 裁剪后实测 `prompt_tokens=**2491**`（裁剪前 2,711）——**均 >2050**，说明默认上限已被显式抬高生效（否则会被截到 2050） |
| 3 | **门禁口径复测** | 预算内 prompt 流式**首 token**：**冷启动 7.01 s ✅（<10s）**、热态 0.04 s（含 prompt cache 失真，按 S4-1 §3.1 不作判读依据）；单次采样，**判读属人工** |
| 4 | 估算是否保守 | 估算 2,889 vs 实测 2,491 ⇒ 估算高出 **16%**，方向正确（宁可少喂） |
| 5 | `/chat` 可见 | 切到 1800 后真实调用返回尾部：`上下文预算：估算 261/1800 tokens（实测 194），采用 1/1 篇；num_ctx=4096（Ollama 默认仅 2050，故显式抬高）`；正例 3.10 s / 负例 2.70 s（当前真实库仅 1 篇，故与预算上限无关） |
| 6 | 回归 | `/health` `/ready` `/api/v1/alerts` `/api/v1/approvals` 均 200；负例仍回答「元数据中未找到」（不幻觉） |
| 7 | 镜像与扫描 | GA 重建 `2c8b39e9…`（296.9 MB，后随预算定档再重建为 `44ca36b1…`）；trivy（`--ignore-unfixed --severity HIGH,CRITICAL`）**HIGH+CRITICAL = 0** |
| 8 | **"能喂几篇"不固定** | 换一批更短的文档时，同为 1800 预算 ⇒ `docs_used=9` + `truncated=true`（`prompt_tokens_est=1809`）；长文档时为 7 篇。**篇数取决于单篇长度，不要把它当固定值** |

### 7.5 未做 / 边界

| # | 项 | 说明 |
|---|---|---|
| 1 | **现实体量下未压测** | OM 当前仅 **1 个可检索实体**（214 字符）⇒ 真实检索远小于预算；本项验证用的是**合成上下文**（12 篇，模拟接源后体量）。**接源后须用真实数据复测** |
| 2 | 预算与 `num_ctx` 是**工程取值**，非源文档硬性指标 | 1,800 tokens 来自 2026-09-19 的实测取舍（判据：冷启动首 token < 8.0 s，对 10 s 门禁留 20% 余量）；`num_ctx=4096` 由预算 + 输出长度推导。**若改档位或换机器须重新实测**；若接源后上下文不够用，可回到 2,000（实测仍达标）但余量变薄 |
| 3 | 问题（question）超长时 | 预算按"模板 + 问题 + 上下文"计算，问题极长时会挤压上下文至 `MIN_CTX_TOKENS`；**未对问题本身设长度上限**（正常交互不触发） |
| 4 | 首 token 复测次数 | 单次采样，**不是多次采样的统计值**（S4-1 已声明需多次采样校准） |

---

## 8. 影响面与需同步的文档

| 文档 | 是否同步 | 说明 |
|---|---|---|
| 设计方案 / 实施执行计划 | **否** | 未新增组件（pgvector/Ollama 均为清单内） |
| 《部署步骤_单机版》S4-2 | **是** | 增落地注（向量存储/模型/主链/引用校验/cron） |
| 《单机版部署计划》S4-2 | **是** | 增执行状态注 |
| `governance-agent/README.md` | **是** | 增 S4-2 小节（含 LangGraph 偏差声明） |
| AGENTS.md | **是** | 文档地图登记本记录 |

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-18 | 首次产出：S4-2 最小 RAG 闭环——pgvector 0.8.6 + `rag.doc`(vector 768 + HNSW 余弦索引)、`rag.py`（摄入/检索/生成/引用校验）、GA `/chat` 接 RAG（保留降级）；验收含正例/负例（不幻觉）/引用校验真假两态/cron :40 定期刷新；记录 5 个新踩坑（Dockerfile 漏 COPY、补丁脚本非幂等导致 compose 重复 key、build tag 不能带 digest、PYTHONPATH、docker cp 属主）；**声明 LangGraph 实现偏差待人工确认**；边界：只走 OM API、向量存 GA 自持库 |
| v1.1 | 2026-09-18 | **新增 §6.1「引入 LangGraph/LangChain 的条件与实测结论」**（一次性容器验证，**未触碰部署**）：① **安装阶段必须有对外出口**（internal 网络装不了包，`Temporary failure in name resolution`）② 内网 PyPI 可装 ③ Python 3.12 兼容实装版本（langgraph 1.2.11 / langchain-core 1.6.3 / langchain-ollama 1.1.0 …）④ **许可证：三者均 MIT**（langgraph 元数据 `License-Expression: MIT`）⑤ **依赖树增量约 73 MB** + 引入后须重建镜像/重新扫描 ⑥ 运行期无需额外资源且**最小 LangGraph + ChatOllama 已跑通**（输出真实回答）。附成本/收益判断：**建议接源后再引入**（当前场景用不到多步编排），若要求严格对齐设计可立即按 6 项条件执行 |
| v1.2 | 2026-09-18 | **按设计引入 LangGraph（人工要求）**：§6 由"设计偏差声明"改写为**"已按设计引入"**并新增 §6.2 结果与验收——图结构 `retrieve → generate → verify → END`（状态 `RagState`）、LLM 用 `ChatOllama`、依赖 `langgraph==1.2.11` + `langchain-core==1.6.3` + `langchain-ollama==1.1.0`（均 MIT）；镜像重建为 `0857cafe…`（205MB → 294MB）、compose digest 更新；**节点轨迹取证**（`/chat` 尾部与 `rag.answer().nodes` 均为 `['retrieve','generate','verify']`）；功能回归（正例/负例/引用校验/health/ready）通过；**重扫 Total 13（HIGH 10 / CRIT 3）与引入前一致，未新增高/严重漏洞**。§6.1 的"建议接源后引入"结论按人工决策被覆盖（如实记录） |
| v1.3 | 2026-09-19 | **新增 §7 RAG 上下文预算（B 项，落地 S4-1 §4 第 3 项挂账）**：① **实测更正**——Ollama **默认 `num_ctx` 仅 2050 tokens**，8,158/24,558 字符的 prompt 都被**静默截断到 2050**（换算实测 **1.70 字符/token**）；② **口径**：整条 prompt ≤ **3000 tokens**、显式 `num_ctx=4096`、保守系数 1.5、按相关度裁剪（末篇可截断/整篇丢弃，其后不再尝试）；③ **实现**：`rag.py` 的 `fit_contexts()` + `llm(num_ctx=…)`，`/chat` 返回预算口径行，compose 显式声明两个 env；④ **验收**：12 篇 → 采用 11 篇丢弃 1 篇、实测 prompt **2,491 tokens（>2050，证明 num_ctx 生效）**、**预算内流式首 token 冷启动 7.01 s**、估算比实测保守 16%、回归通过；⑤ **镜像** GA 重建 `2c8b39e9…`（296.9 MB）、**重扫 HIGH+CRITICAL = 0**；⑥ 边界：现实体量未压测（OM 目前仅 1 个实体，验证用合成上下文）、预算属工程取值、单次采样 |
| v1.4 | 2026-09-19 | **预算取值的实测代价与复核提示**（人工要求"尝试用 3b"后同口径复测）：在 **2,751 估算 / 2,847 实测 tokens** 的预算内 prompt 下——**1.5b 冷启动首 token 11.31 s（越线 >10 s）**、热态 8.29~8.39 s（余量仅 1.7 s）；**3b 冷启动 20.68 s / 热态 17.3~17.4 s（明确不通过）**。故 **v1.3 的"7.01 s ✅"为单次乐观样本**，已更正表述为"1.5b 通过的前提是上下文 ≤ 约 2,000~2,500 tokens"。§7.3 预算行加 ⚠️ 待人工复核标记：备选为「压预算到 ≤2,000」或「保持 3,000 但消除冷启动（预热/延长 keep_alive，须同步调整 `OllamaModelNotUnloading` 口径）」。**本次未改任何部署配置**，模型测后已卸载。详见《…S4-1…》§3.4 |
| v1.5 | 2026-09-19 | **预算取值定档：3,000 → 1,800（人工决定"1.5b + 压预算"）**。① **取值实测**（1.5b、`num_ctx=4096`、每样本换上下文规避 prompt cache，判据=冷启动首 token <8.0 s）：1,500 → 实际 1,576 tokens、**冷 6.88 s / 热 4.64 s**、采用 6/12 篇；**1,800 → 1,819 tokens、冷 6.93 s / 热 4.85 s、采用 7/12 篇**；2,000 → 2,062 tokens、冷 7.70 s / 热 5.66 s、采用 8/12 篇 ⇒ **取范围内余量最充足的 1,800**（2000 也达标，留作接源后的上调备选）。② **落地**：compose `GA_RAG_CTX_BUDGET_TOKENS: "1800"` + `rag.py` 代码默认值同步改为 1800（避免两处口径漂移）；GA 重建 **`44ca36b1e519…`**、**重扫 HIGH+CRITICAL = 0**、compose 校验和 **`62aeeef899d2bede…`**。③ **验收**：`/health` `/ready` 200；`/chat` 正例 3.10 s（引用 airflow_poc 正确）、负例 2.70 s（"元数据中未找到"，不编造）；`/chat` 尾部口径行显示 `261/1800 tokens`。④ **代价如实记录**：预算 1800 时同一批 12 篇检索结果只装得下 7 篇（3000 时 11 篇）⇒ **接源后若发现上下文不够，优先考虑"回到 2,000"或"补 GPU 上前提下的更大预算"，而不是随手调大** |

---

*本文件为 S4-2 最小闭环的实现与验收记录，由《部署步骤_单机版》S4-2 展开；不含新增架构决策。*
*问答准确率判定为人工专属，Agent 不代判、不代签。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；模型与依赖版本执行前请按官方文档核对。
