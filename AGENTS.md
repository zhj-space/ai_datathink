# AGENTS.md · AI 数据治理平台

> **适用范围**：本目录（`D:\workspace\ai_datathink`）及其全部子目录。
> **读者**：在本仓库工作的 AI 助手（Codex / Claude Code / Cursor 等）。规则对文档写作、方案修改、部署脚本产出同等生效。
> **冲突处理**：若子目录存在更细的 `AGENTS.md`，以更靠近当前工作目录的那份为准；但第 5 节"硬约束"与第 9 节"红线"不可被下层文件放宽。
> **最后更新**：2026-09-21

---

## 0. 一分钟上下文

- **项目**：AI 数据治理平台。开源组件优先、单机起步、可演进到三节点高可用。
- **当前阶段**：POC——单机 Docker Compose，1 台 24 vCPU / 96GB / 2TB NVMe，全量约 18 个容器，周期 11~14 周。
- **范围**：离线治理闭环 + 实时链路 + AI 能力 + 试运行。生产三节点 k3s（M-Prod）触发式启动，当前不在范围内；湖仓一体（Phase 5）按需触发。
- **唯一自研组件**：Governance Agent（告警增强 / AI 编排 / 开放 API / 审批网关）。
- **仓库性质**：文档仓库（含代码目录，无构建产物）。**已启用 Git**：`origin = git@github.com:zhj-space/ai_datathink.git`（默认分支 **main**）。新增代码目录时须同步扩充第 7 节。
- **Git 纪律（2026-09-21 起）**：① **口令/密钥/公网 IP/主机名一律不入库**（占位符口径见 §9；`.gitignore` 已排除 `.env`、密钥、`__pycache__`、`*.bak-*`）；② **脚本保持 LF**（本仓库 `core.autocrlf=false`，否则部署到 Linux 会带 CRLF）；③ 提交信息按 §10.6；④ 推送前跑一次"敏感信息自检"（至少覆盖：已知口令字面量、私钥头、`AKIA`/`ghp_` 等 token 前缀、`postgres://user:pw@` 连接串）。

---

## 1. 仓库结构与文档地图

```
ai_datathink/
├─ 方案计划/     # 源文档（authoritative）：设计与计划
│  ├─ AI数据治理平台设计方案.md                 v3.3      ← 架构唯一权威
│  ├─ AI数据治理平台实施执行计划_v2.0.md         v2.0-r2   ← 阶段/门禁/排期权威
│  ├─ AI数据治理平台设计方案_v3.0_初版.md        历史备份，只读
│  ├─ AI数据治理平台实施执行计划.md              v1.1，已被 v2.0 取代（仅 Phase 5 仍引用）
│  └─ AI数据治理平台_评审与调整过程记录.md        过程稿归档，已冻结，不再维护
├─ 执行步骤/     # 拆解产物（derived）：部署与采购
   ├─ AI数据治理平台_部署步骤.md                 S1~S5（含三节点 M-Prod）
   ├─ AI数据治理平台_部署步骤_单机版.md           S1~S4（仅单机）
   ├─ 单机版部署计划.md                          Planning Agent 结构化格式
   ├─ AI数据治理平台_单机版_S1部署记录.md         S1 执行证据归档（含 §3.7 安全面复查、§7 S1-8 演练）
   ├─ AI数据治理平台_单机版_S1-8备份恢复脚本.md    S1-8 备份/恢复脚本原文 + 运行手册
   ├─ AI数据治理平台_单机版_S1准出材料包.md        S1 准出待签署材料（核对表 + 演练记录 + 降级确认 + 出机单）
   ├─ AI数据治理平台_单机版_S2接入方案.md          S2 准备稿（官方材料固定、必改点、镜像可达性、风险）
   ├─ AI数据治理平台_单机版_S2-4门禁③证据包.md      S2-4 T-M0-1 证据包（待 DBA+DE 判定）
   ├─ AI数据治理平台_单机版_S2-5OPA策略与部署.md    S2-5 OPA 部署与策略（含审核栏）
   ├─ AI数据治理平台_单机版_S2-5Presidio部署与规则.md S2-5 Presidio 部署与识别规则（含审核栏）
   ├─ AI数据治理平台_单机版_S2-5gov_metrics与ETL.md S2-5 gov_metrics 指标层与 ETL 首版（含口径声明）
   ├─ AI数据治理平台_单机版_S2-6Superset与指标看板.md S2-6 Superset 部署与看板（含正反验收）
   ├─ AI数据治理平台_单机版_S2-7治理门户.md        S2-7 自研 Streamlit 门户（含降级验证）
   ├─ AI数据治理平台_单机版_S2准出量测口径与抽检模板.md S2 准出五项判据的分子/分母口径、抽样方法、记录与判定模板（**判读签字为人工专属**）
   ├─ AI数据治理平台_单机版_接源前检查清单.md       **接第一个业务源当天的执行清单**（T-3~T-1 提前拿 / T-1 准备 / 当天 S1~S11 / T+1 对账 + G0/G1/G2 三道 Go/No-Go + 缺口表；**G0＝门禁④ 原件归档，硬约束 4**）
   ├─ AI数据治理平台_单机版_S3-1门禁④材料包.md     S3-1 门禁④就绪材料（合规边界确认书草案 + 续接窗口签约定草案 + 复制槽保护缺口清单；**待人工定稿签字**）
   ├─ AI数据治理平台_单机版_S3前置部署记录.md      S3-2/S3-3 落地（Kafka / Flink 自建镜像 + 告警链路补齐 + 镜像 digest 固定；含未做清单）
   ├─ AI数据治理平台_单机版_S3-6治理Agent接口规格.md Governance Agent 接口契约草案（门户契约 + 首版 9 接口 + ga schema + 验收判据；**代码未实现**）
   ├─ AI数据治理平台_单机版_S3-6治理Agent首版实现记录.md GA 首版实现与验收（**门户降级结束**、告警幂等落库、审批流闭环、库级 CONNECT 收敛；含 3 个实测踩坑）
   ├─ AI数据治理平台_单机版_供应链漏洞处置清单.md  镜像扫描结果的**决策件**（逐镜像"上游是否有更新"+ 建议动作 + 升级步骤/回滚 + 风险接受勾选表；**升级需人工批准**）
   ├─ AI数据治理平台_单机版_漏洞风险接受单.md      **一页可勾选**的漏洞决策单（A 类 8 条建议接受 / B 类 2 条可升版本 / C 类 superset 三路择一 + 签署栏；**判读签字为人工专属**）
   ├─ AI数据治理平台_单机版_组件部署状态.md        **一页归档**：19 容器逐项对照设计组件 + 宿主侧 4 件 + 未部署三类 + 运行面证据（供准出材料引用/交接核对）
   ├─ AI数据治理平台_单机版_测试入口与用例.md      **人工自测入口**：本机 SSH 隧道脚本（9 入口）+ 场景 D/E、Superset、OM 与运维面用例与期望；**口令只从服务器 `.env` 取，不入仓库**
   ├─ AI数据治理平台_单机版_S4-1Ollama部署与首token实测.md S4-1 落地 + **M0 第 6 项门禁实测**（3b 长上下文不通过 / 1.5b 通过；容器数 18→19）
   ├─ AI数据治理平台_单机版_S4-2RAG最小闭环记录.md S4-2 最小 RAG（pgvector + 引用程序化校验；**LangGraph 实现偏差待确认**）
   ├─ AI数据治理平台_单机版_经验沉淀与踩坑总表.md   **跨阶段经验总表**（六大主线 + 10 个主题的实测踩坑 +「静默失效 12 条」+ 判据陷阱 + 交付前检查清单；**派生文档**，逐条给出出处，不含新增架构决策）
   ├─ AI数据治理平台_POC服务器采购需求.md
   └─ AI数据治理平台_云厂商选型与成本测算.md
├─ opa-policies/  # 策略即代码（Policy as Code，Rego/JSON；部署到 OPA 容器只读挂载点）
   ├─ authz.rego            # 授权决策骨架（fail-closed；POC 不接在线拦截）
   ├─ access_audit.rego     # 事后审计 + 主动告警决策（场景 E / ADR-A3）
   ├─ data.json             # 策略参数（PII 标签、高风险操作、阈值、非工作时段、告警通道）
   └─ README.md             # 目录说明与纪律
└─ presidio-recognizers/   # PII 识别规则（识别器注册表；只读挂载进 presidio-analyzer）
   ├─ chinese_recognizers.yaml  # 3 个预定义 + 3 个中国证件自定义识别器
   └─ README.md                 # 目录说明与纪律
└─ gov-metrics-etl/        # 治理指标层（ADR-A6；自持表结构 + 每小时 ETL）
   ├─ schema.sql                # 建 schema/表/索引/角色与库级 CONNECT 收敛
   ├─ gov_metrics_etl.py        # ETL：OM API 取数 → 经 PgBouncer 写入 gov_metrics
   └─ README.md                 # 目录说明与口径
└─ superset-image/         # Superset 派生镜像（官方 lean 无 PG 驱动）
   ├─ Dockerfile                # FROM apache/superset:6.1.0 + uv 装 psycopg2-binary 进 /app/.venv
   └─ README.md                 # 构建与 digest 记录要求
└─ governance-portal/      # 治理门户（自研 Streamlit 前端：场景 D 问答 + 场景 E 审批台 + 统一入口）
   ├─ app.py / db.py / agent.py / config.py
   ├─ requirements.txt          # 固定版本（streamlit/pandas/psycopg2-binary）
   ├─ Dockerfile                # python:3.12-slim；pip 源与 trusted-host 参数化
   └─ README.md                 # 目录说明与降级口径
└─ 交付物/                 # **交付件编目与手册**（面向交付/使用/运维，正文口径引自源文档）
   ├─ 00_交付物清单.md          # 交付件 ↔ 文件 ↔ 版本 ↔ 状态；含"未交付/待办 13 项"与交付纪律
   ├─ 01_用户操作手册.md         # 业务/治理用户：隧道入口、门户五页、OM/Superset 用法、FAQ、账号口径
   ├─ 02_实施手册.md             # 实施/交付工程师：前置条件、S1~S4 判据、四道门禁、变更与回滚、故障处置
   ├─ 03_运维手册.md             # 运维/值班：日/周巡检命令、40 条告警处置、备份恢复、容量水位、已知缺口
   ├─ 04_架构设计（现状版）.md    # as-built 架构视图：现状图（PNG + Mermaid）+ 组件清单 + 五条数据流 + ADR-A1~A8 摘要 + 设计 vs 实测差异（权威源仍是设计方案 v3.3）
   └─ figures/                  # 交付用图（`架构图-现状-v1.0.png`，2600×1560，由 scratch/render_arch_png.ps1 生成）
   └─ scripts/                  # **部署脚本交付包**（35 文件，7 组：cron / backup-restore / monitor / metrics-etl / lineage / watermark-capacity / images）+ 同名 zip；脚本以仓库为唯一真相源
└─ flink-image/            # Flink 派生镜像（内置 Flink CDC：MySQL/PG + Kafka 连接器）
   ├─ Dockerfile                # FROM flink@sha256:…（固定 digest）+ 按 jars.sha256 校验下载连接器 JAR
   ├─ jars.sha256               # 5 个 JAR 的 sha256（构建期强制校验；换版本必须同步）
   └─ README.md                 # 版本矩阵、未纳入项（Oracle CDC / OpenLineage）、构建与纪律
└─ alerting/               # 告警规则与采集查询（S1-7 SLO + S3-4 复制槽 + S4-1 Ollama 运行面）
   ├─ poc-alerts.yml            # Prometheus 规则（现行 40 条 = SLO 4 + 复制槽 5 + Kafka lag 3 + 作业新鲜度 10 + **Flink 作业/Checkpoint 5** + **Ollama 运行面 7** + **通用容器运行面 6**；阈值由 SQL 计算，不硬编码兜底值）
   ├─ job_freshness.sh          # 备份/ETL/RAG 成功率采集（cron 每 5 分钟 → node_exporter textfile；标签用 task，不得用 job）
   ├─ ollama_probe.sh           # Ollama 运行面采集（cgroup v2 + /api/ps，cron 每分钟 → **独立** textfile 文件；Ollama 无 /metrics，且本环境 cAdvisor 取不到逐容器指标）
   ├─ container_probe.sh        # 通用容器运行面采集（遍历 docker ps + cgroup v2：CPU/内存/OOM，带 container/name 标签；cron 每分钟 → 独立 textfile 文件；**替代失效的 cAdvisor**）
   ├─ alert_digest.sh           # 告警日报（cron 每日 08:30 → /data/ai-governance/reports/alert-digest-<date>.md；IM 未配置前的可见性兜底）
   ├─ pg_exporter_queries.yml   # postgres_exporter 自定义查询（复制槽水位）
   ├─ alertmanager.yml.example  # Alertmanager 配置模板（含"告警汇入 GA"接收器；令牌占位符，真值不入库）
   └─ README.md                 # 纪律：promtool 校验 → /-/reload → 记录；deprecated 用法留痕；AM 接收器口径
└─ governance-agent/       # Governance Agent（**唯一自研组件**：告警落库增强 + 开放 API + 审批网关）
   ├─ agent.py                  # 标准库 http.server + psycopg2；Kafka 消费线程（confluent-kafka 可选）
   ├─ lineage.py                # D1 作业图级血缘：Flink SQL 解析 → OM 建/删边（纯 stdlib，无新依赖）
   ├─ lineage_reconcile.sh      # D1 对账执行器（cron 每日 04:10 → GA reconcile；成功戳供 job_freshness 采集）
   ├─ lineage_autoreport.sh     # D1 自动上报：遍历 /data/ai-governance/jobs/*.sql → 上报 → 全部成功才对账
   ├─ submit_flink_job.sh       # D1 提交包装：干跑门禁 → 登记 → 提交（sql-client）→ 上报
   ├─ test_lineage.py           # D1 解析准确性测试（ADR-A7 验收用；**不入镜像**，按需运行）
   ├─ schema.sql                # 独立库 governance_agent + ga schema **四表** + 角色 + **库级 CONNECT 收敛**
   ├─ requirements.txt          # 固定版本（psycopg2-binary 必需；confluent-kafka 可选）
   ├─ Dockerfile                # python:3.12-slim；内网 PyPI + trusted-host；非 root 运行
   └─ README.md                 # 边界（ADR-A3 不做事前拦截）、接口清单、未纳入项
└─ watermark-report/        # 单机水位周报（S4-4；对照 M-Prod 触发条件 2）
   ├─ watermark_report.sh       # Prometheus 取数 → Markdown 周报（控制台 + /data/ai-governance/reports/）
   ├─ install_cron.sh           # 幂等安装/卸载 cron（周一 08:00 → /etc/cron.d/ai-governance-backup）
   └─ README.md                 # 口径来源、部署步骤、7 条实测纪律
└─ capacity-baseline/       # 单机容量基线（S4-4 压测骨架；自压，非真实业务负载）
   ├─ run_baseline.sh           # A 宿主面 / B PG（经 PgBouncer）/ C Kafka / D OS / E Ollama / F 服务 API → 报告
   ├─ os_bulk_bench.py          # OpenSearch 写入子项（docker cp 进容器用 python3 跑，避开 shell 引号）
   └─ README.md                 # 口径与边界、运行方式、7 条实测纪律
└─ restore-annual-drill/    # 年度化恢复演练（S4-4；本地段执行器，复用 S1-8 既有演练脚本）
   ├─ run_annual_drill.sh       # §0 新鲜度 / §1 PG 逐库 / §2 OS 快照 / §3 配置归档 / §4 Flink CP / §5 汇总
   └─ README.md                 # 复用关系、5 条纪律（含"不等于全灾备可用"）
```

---

## 2. 文档权威性与冲突裁决

**源文档只有两份**：`方案计划/AI数据治理平台设计方案.md`（**v3.3**，2026-09-19 由 v3.2 修正 ADR-A7 上报机制）与 `方案计划/AI数据治理平台实施执行计划_v2.0.md`（v2.0-r2）。

`执行步骤/` 下的部署步骤与计划均声明"由源文档机械拆解而成，不含新增架构决策"。因此：

1. 部署文档与源文档冲突时，**以源文档为准**，并回报应修订哪一份。
2. 动手前先读对应主题的源文档章节，不凭文件名或记忆推测版本。设计方案第 9 章（ADR）与第 11 章（验收标准）是改动前的必查两节。
3. 被取代的文件（`_v3.0_初版`、`实施执行计划.md` v1.1、评审与调整过程记录）**默认只读**；确需引用时说明其历史定位，不得当作现状依据。
4. 同一主题出现两份重复文件时，先确认哪份是源，再决定改哪份，不要"顺手都改"。

---

## 3. 语言、文风与口径纪律

本节源自两份源文档开头的"口径纪律"，是本项目最核心的写作规则：

- **语言**：中文。组件名、命令、配置项、字段名保留原文，不翻译（如 `governance.alerts`、`max_slot_wal_keep_size`、`state.backend`）。
- **文风**：结论先行、短句、可核查。不使用"业界领先""极致性能""毫秒级脱敏"这类无法验证的表述——"毫秒级动态脱敏"正是被 ADR-A3 明确纠正过的虚标。
- **数字可回溯**：所有数字来自源文档；新增数字必须给出推导过程或来源，否则标 `待确认`。
- **版本号不臆造**：镜像、Chart、库版本一律标 `需按官方文档核对`（设计方案 6.2.3 已就此声明），禁止编造看似合理的版本号。
- **估算须声明**：容量、成本、性能数值须标"工程经验估算，实施前需实测压测校准"，不得写成 SLA 承诺。
- **不确定即声明**：源文档未覆盖的信息写 `待确认`，不要补全成结论。
- **表格优先**：对比、清单、口径类内容用表格承载，与现有文档保持一致；避免长段落罗列。
- **图示**：架构与流程用 Mermaid 或 ASCII 框图，沿用现有表达。

---

## 4. 术语与固定口径（不要另起说法）

| 概念 | 固定说法 | 不得写成 |
|---|---|---|
| 实时捕获 | Flink CDC 直连（默认）；必要时启用"两段式"开关 | Debezium + Kafka Connect（v3.0 已移除该组件） |
| Kafka 职责 | 日志类事件接入 + `governance.alerts` 分发 | CDC 传输通道 |
| 元数据真理源 | OpenMetadata + PostgreSQL（唯一） | 同时存在两套血缘/质量来源 |
| 质量口径 | 快照基线 = 权威质量指标（进看板）；增量窗口 = 异常告警（仅进告警通道，**不进质量看板**，ADR-A5） | 把"变更行空值率"当全表指标 |
| 治理指标层 | `gov_metrics` schema，Superset 只读该 schema（ADR-A6） | Superset 直连 OpenMetadata 内表 |
| 血缘粒度 | 作业图级血缘（作业启动/变更时上报，ADR-A7） | 事件级血缘 |
| 场景 E 定位 | 决策面（OPA）/ 执行面（查询引擎原生能力）/ 审计面（异步）；POC 期只做事后审计 + 主动告警（ADR-A3） | "事前拦截""在线动态脱敏" |
| PII 识别 | Presidio 仅离线批量识别，已移出在线路径 | 在线实时逐行脱敏 |
| 容器数 | 单机全量 **18**（Exporters 作为宿主二进制）；19 为另一口径；执行计划的"约 17"是约数 | 直接引用"17 容器" |
| 阶段与任务编号 | `P0-A`/`P0-B`/`M0`/`Phase 1~5`/`M-Prod`；部署段 `S1~S5`，任务如 `S1-1`、`S2-4` | 自创阶段名或编号 |
| 决策记录 | `ADR-A1`~`ADR-A8`，新增决策顺延编号并写入设计方案第 9 章 | 在部署文档里悄悄改架构 |
| 可靠性四件套 | 续接窗口签约定 + PG 复制槽安全保护 + 作业自动拉起 + 重快照预案（ADR-A2） | 简化成"三件套" |

---

## 5. 硬约束（违反即缺陷）

取自《部署步骤》0.3 节，对任何改动生效：

1. **Compose 是 POC 期唯一部署真相源**。不得引入任何 K8s/k3s 配置"为将来迁移做准备"（执行计划 §8 单机档纪律）；迁移能力由 M-Prod 迁移演练保证，不靠提前设计。
2. **所有服务经 PgBouncer（6432）访问 PostgreSQL**，不允许任何服务直连 5432。
3. **全部容器 `restart: unless-stopped` + healthcheck**。
4. **接入生产源库前**，合规边界书面文件与续接窗口签约定必须已归档；缺一不动生产库。
5. **遇 `unhealthy` 不自动降级、不自行改架构**：记录日志、停止、转人工。

---

## 6. 组件与许可证纪律

- **只选 OSI 认证开源**，逐一核查许可证，剔除 BSL/SSPL/附加条款类"伪开源"（设计方案 2.1/2.2/2.3）。
- **明确不采用**：Redpanda（BSL 类）、MinIO 主分支（AGPL-3.0）、Redis ≥7.4（RSALv2/SSPLv1）、Open WebUI（品牌附加条款）、Apache Ranger（生态不匹配，改用 OPA）。
- **有条件可用**：Grafana（AGPL-3.0，仅限内部运维可视化，对外多租户 SaaS 前须法务复核）、Dify（附加条款限制多租户运营）。
- **引入新组件的前提**：① 符合"触发式可选"表里的引入条件；② 许可证已核查；③ 同步更新设计方案对应的组件表与 ADR（如需）。
- **组件清单纪律**：v3.2 相对 v3.0 是"零新增零替换"，唯一例外是 SeaweedFS 由触发式可选提升为 3 节点形态必需。不要顺手动清单。
- **触发式组件默认不启用**：Valkey、Milvus、NebulaGraph、Trino、Iceberg 等仅在条件命中时引入，且不得改变核心链路。

---

## 7. 命令与环境

**目标环境**：服务器侧 Ubuntu 22.04 LTS 或 Rocky Linux 9 + Docker Compose；开发机可能是 Windows PowerShell。

**编码与路径（已实测的坑）**：PowerShell 5.1 默认按 ANSI 解码中文文件，`Get-Content` 不加 `-Encoding UTF8` 会读到乱码；路径含中文时用 `-LiteralPath`。示例：

```powershell
Get-Content -LiteralPath 'D:\workspace\ai_datathink\方案计划\AI数据治理平台设计方案.md' -Encoding UTF8
```

**可编程校验命令**（摘自《单机版部署计划》Verification）：

```bash
# S1 容器健康
docker compose ps --format '{{.Name}}\t{{.Health}}'
# S1 PgBouncer 池状态
docker exec pgbouncer psql -h localhost -p 6432 -U postgres pgbouncer -c "SHOW POOLS;"
# S1 OpenSearch 集群
curl -sk https://localhost:9200/_cluster/health
# S1 Prometheus 采集
curl -s localhost:9090/api/v1/targets | grep -c '"health":"up"'
# S3 PG 复制槽水位
docker exec postgres psql -U postgres -c "SELECT slot_name, pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS retained_bytes FROM pg_replication_slots;"
# 服务器验收
nproc; free -g; lsblk -d -o NAME,ROTA,SIZE; fio --name=seqwrite --rw=write --bs=1M --size=8G --direct=1 --runtime=60 --time_based
```

**命令纪律**：

- 命令中的版本号一律标 `需按官方文档核对`；自建镜像（如含 Flink CDC 连接器的 Flink 镜像）固定 digest，不用 `latest`。
- 命令中的主机名、token、域名用占位符（`<node-1>`、`<token>`、`<client-id>`），不写真实值。
- 网络受限、依赖下载失败或环境不具备时，**说明原因并给出可复现的命令**，不要用"应该可以"糊过去。
- **运维备注（2026-09-19 实测）**：访问 POC 服务器的 helper 偶发**密钥登录超时**（回退口令会被拒，因为服务器已 `PasswordAuthentication no`）——**这属网络抖动，不是凭据问题，重试即通**；遇到时不要据此判断密钥失效或去重置凭据。
- **运维备注（helper 输出编码，2026-09-19 实测）**：远程输出含 `⇒` 等非 GBK 字符时，helper 在 Windows 侧打印会抛 `UnicodeEncodeError: 'gbk' codec can't encode character …`（**命令其实已在服务器执行完**，只是回显失败，容易误判为"没跑"）。调用前设 **`$env:PYTHONIOENCODING='utf-8'`**。
- **运维备注（`docker exec` 与 stdin，2026-09-19）**：脚本经 `--script`（stdin 送入 bash）执行时，**`docker exec -i …` 会把脚本剩余内容当作自己的 stdin 吃掉**（表现为"脚本跑到一半就结束、退出码仍是 0"）。要么用 `docker exec -i … <<'EOF'` 显式喂 heredoc，要么**去掉 `-i`**。
- **运维备注（长等待会被 SSH 掐断，2026-09-19 两次踩到）**：`--script` 里**不要放大段 `sleep`**（如等告警 `for:` 到点）。helper 的通道超时约 600s，超时后远端 bash 被中断、输出全部丢失（**但服务器上的动作可能已经发生**，别据此判定"没做"）。正确做法：**短调用触发 → 隔几分钟再用短调用查状态**。
- **运维备注（镜像源现状，2026-09-19 实测）**：本环境 `registry-mirrors = docker.m.daocloud.io / docker.1panel.live`。**DaoCloud 会拦非白名单仓库**（`🚫 …DaoCloud/p…`）、`gcr.io`/`quay.io` **不可达**、`dockerpull.org`/`hub.rat.dev`/`docker.1ms.run` 解析失败、`docker.xuanyuan.me` 需付费；**`docker.1panel.live` 可用**（`alpine`、`zcube/cadvisor` 实测可拉，但**仓库内容偏旧**）。**拉新镜像前先验证可达性**，别把"拉不到"误判成"配置错"。（后果之一：cAdvisor 无法升级到能配 containerd 镜像存储的版本，见 §7 `container_probe.sh` 行。）
- **运维备注（trivy 扫描慢，2026-09-20 实测）**：`trivy image` 默认先**更新漏洞库**，本环境该步骤常达**数分钟甚至超出 SSH 会话窗口** ⇒ 直接在前台跑容易被掐断、误判为"扫描失败"。**本地库已是当日**时加 **`--skip-db-update`**（实测**2 秒**出结果）。另：`nohup … &` 起的后台任务会随 SSH 会话关闭被带走 ⇒ **别用后台跑 trivy**，要么前台（带 `--skip-db-update`），要么用 `docker exec -d` 之类真正的分离方式。
- **运维备注（Windows 侧脚本与中文，2026-09-19 实测）**：给人工用的 `.ps1` 脚本若含中文，**必须存成 UTF-8 with BOM**——PS 5.1 默认按 ANSI(GBK) 解析 `.ps1`，无 BOM 会中文乱码并直接报"字符串未终结/缺 `}`"（本次首次运行即踩到，用 `New-Object Text.UTF8Encoding($true)` 重写后正常）。另：**`curl.exe` 在 PowerShell 里传中文 JSON 会被引号规则吃掉**（服务端回 `bad_json`）⇒ 改用 `Invoke-WebRequest`/`Invoke-RestMethod` 并显式传 UTF-8 字节。人工自测入口见《…单机版_测试入口与用例.md》。

**代码目录（当前唯一一处）**：

| 目录 | 内容 | 部署落点 | 纪律 |
|---|---|---|---|
| `opa-policies/` | OPA 策略即代码（`*.rego` + `data.json`） | 服务器 `/data/ai-governance/config/opa/policies/`（只读挂载进 `opa` 容器 `/policies`） | **Rego 入 Git**；策略或阈值变更须同步《…S2-5OPA策略与部署.md》版本记录并**重跑决策用例**（编译通过 ≠ 逻辑正确）；**未启用 bundle 自动拉取**，改策略后须重启 OPA 容器 |
| `presidio-recognizers/` | Presidio 识别器注册表（`chinese_recognizers.yaml`） | 服务器 `/data/ai-governance/config/presidio/`（只读挂载进 `presidio-analyzer` 容器 `/config`） | **规则入 Git**；改规则后须同步《…S2-5Presidio部署与规则.md》版本记录 + sha256，并**重启 analyzer 容器**后重跑识别用例；中文文本以 `language=en` 调用（镜像仅装 en 模型，自定义识别器为纯正则） |
| `gov-metrics-etl/` | 指标层自持表结构 + ETL + **覆盖率分母盘点工具**（`schema.sql`、`gov_metrics_etl.py`、`coverage_denominator.py`） | 服务器 `/data/ai-governance/config/gov_metrics/schema.sql`、`/data/ai-governance/scripts/gov_metrics_etl.py`（cron `:25` 每小时）、`/data/ai-governance/scripts/coverage_denominator.py`（按需，**默认 dry-run**） | ADR-A6：Superset **只读 gov_metrics**、**禁止直连 OM 内表**；ETL **只走 OM API 取数**、**写库经 PgBouncer 6432**；**权限变更后必须回收 PgBouncer 池连接**（idle 服务端连接会让变更不即时生效）；**覆盖率分母只写在参考表 `gov_metrics.asset_expected`（只由盘点更新），不得写进每小时重写的快照表**；盘点范围须用 `--scope-note` 留痕；**改服务器副本必须同步本目录**（曾出现仓库版本滞后导致上传旧版后 ETL 连不上 OM）；S3 起迁入 Governance Agent |
| `superset-image/` | Superset 派生镜像（`Dockerfile`） | 服务器 `/data/ai-governance/superset-image/`，构建为 `ai-governance/superset:6.1.0-pg` | AGENTS §7：**基础按 digest 固定**（`apache/superset:6.1.0@sha256:16b50bbef664…`，非浮动 tag）、构建后记录 **digest**；**依赖必须钉版本**（`psycopg2-binary==2.9.13`——原先不钉，重建会静默换依赖）；驱动必须装进镜像自带 venv（`/app/.venv`，venv 无 pip → 用镜像自带 `uv`）；**本镜像是手工 `docker build`，compose 无 `build` 段**（`docker compose build superset` 是 no-op，实测踩到）；改基础 digest 属组件版本变更，须同步《…S2-6Superset与指标看板.md》并重跑验收；**重建不降漏洞**（实测 100 → 100；要降需升版本或显式升级个别 Python 依赖，均需批准） |
| `governance-portal/` | 治理门户（自研 Streamlit：场景 D 问答 + 场景 E 审批台 + 统一入口；**v1.5 起按设计稿重做视觉**） | 服务器 `/data/ai-governance/governance-portal/`，经 compose `build` 构建为 `ai-governance/governance-portal:0.1.0` | **只读 gov_metrics**（`portal_ro`），**不直连 OM 内表**；**Agent 未就绪必须降级显示**（异常即视为未就绪，fail-safe）；依赖版本固定；**基础镜像按 digest 固定**（`python:3.12-slim@sha256:23b5dc88c7dd…`，debian 13.7；改基础属组件版本变更，须同步《…S2-7治理门户.md》并重跑验收+重扫）；**构建镜像时用阿里云内网 PyPI 镜像 + `--trusted-host`**（公网源在构建容器内实测仅 ~100 kB/s）；**视觉与令牌**：`design/tokens.css` 与 `theme.py::TOKENS` 是令牌的**唯二出处**（改一处必须同步另一处），`app.py` **不得写死色值**，**不得再放第三份 CSS 副本**；**改 UI 必须跑 `tools/` 四个校验**（令牌/WCAG → mockup 结构 → HTML 解析 → AppTest；容器内即可跑，**改颜色必跑 `check_tokens.py`**）；`tools/app_test_regression.py` **默认按"Agent 未就绪"跑**（确定性），验就绪路径需 `PORTAL_TEST_AGENT_URL=http://governance-agent:8080`（此时降级组自动跳过） |
| `flink-image/` | Flink 派生镜像（`Dockerfile` + `jars.sha256`） | 服务器 `/data/ai-governance/flink-image/`，构建为 `ai-governance/flink:1.20.1-cdc3.6.0` | AGENTS §7：**基础镜像固定 digest（不用 latest）**、连接器 JAR 走 Maven Central 并**按 `jars.sha256` 强制校验**；改基础 digest / 连接器版本属组件版本变更，须同步《…S3前置部署记录.md》与《部署步骤_单机版》S3-3 并重跑验收；镜像漏洞扫描**未执行**（待办） |
| `alerting/` | 告警规则与采集（`poc-alerts.yml` + `pg_exporter_queries.yml` + `job_freshness.sh` + `ollama_probe.sh` + `container_probe.sh` + `alert_digest.sh`） | 规则 `/data/ai-governance/config/prometheus/rules/`（只读挂载进 `prometheus`）；`/etc/pg_exporter_queries.yml`（宿主 `postgres_exporter` 用 `--extend.query-path`）；`/data/ai-governance/scripts/*.sh`（`job_freshness.sh` cron 每 5 分钟 → `ai_governance_jobs.prom`；`ollama_probe.sh` cron **每分钟**（`/etc/cron.d/ai-governance-ollama`）→ `ai_governance_ollama_runtime.prom`；`container_probe.sh` cron **每分钟**（`/etc/cron.d/ai-governance-containers`）→ `ai_governance_containers.prom`，**三个采集器各写各的文件，避免一个坏行把其它打哑**） | **规则与采集入 Git**；改规则须 `promtool check rules` → `POST /-/reload` → 核对 `/api/v1/rules` `health=ok` → 追加版本记录；**复制槽水位阈值必须由 SQL 按源库兜底值现场计算**；**文本类规则必须限定 `mountpoint`**（否则误报 `/boot/efi`）；**自定义指标标签不得用 `job`**（与 Prometheus 冲突会被改名 `exported_job` ⇒ 规则静默失效，统一用 `task`/`container`+`name`）并保留 `count<预期`/`absent()` 护栏；采集脚本必须**原子写入 + 逐行格式自检**（任一非法行会被整文件拒收）、**独立文件**、**同一 series 只能有一个生产者**（否则抓取直接失败）；**OOM 类规则用累计计数、不用窗口式 `increase()`**（容器若在首次采集前就 OOM，序列首值即 1 ⇒ `increase` 恒为 0、永不触发，实测踩到）；**cAdvisor 在本环境只导出根 cgroup（containerd 镜像存储 vs v0.45.0、且镜像源拉不到更高版本）⇒「抓取目标 up ≠ 有数据」，容器级指标以 `container_probe.sh` 为准**；`--extend.query-path` 为 deprecated（实测留痕） |
| `governance-agent/` | Governance Agent（自研，唯一自研组件；含 **S4-2 RAG**） | 服务器 `/data/ai-governance/governance-agent/`，经 compose `build` 构建为 `ai-governance/governance-agent:0.1.0`；**仅回环 8085** + SSH 隧道 | **不得越界**：只做事后审计 + 主动告警，**审批只记录不拦截**（ADR-A3）、不做在线 PII 脱敏、不建第二套元数据真理源；RAG 只走 **OM REST API**（**不读 OM 内表**）、向量存 **GA 自持库** `rag` schema（不碰 gov_metrics），**引用必须程序化校验**（不存在的标识要标注未核实）；编排按设计用 **LangGraph**；库 `governance_agent` 用 `ga_writer`/`ga_ro`，**经 PgBouncer 6432**；**新建库必须做库级 CONNECT 收敛**（`REVOKE … FROM PUBLIC` + 按需 GRANT）；**权限变更后必须 `RECONNECT` 回收 PgBouncer 池连接**；依赖版本固定（含 `langgraph`/`langchain-*`，均 MIT），**改依赖或改库须重建镜像 → 重跑验收 → 重扫漏洞**；**新增模块必须同步 Dockerfile 的 COPY**；**RAG 上下文必须有预算**：`GA_RAG_CTX_BUDGET_TOKENS`（**默认 1800**，2026-09-19 人工定档"1.5b + 压预算"，口径=**整条 prompt**）+ **显式 `GA_RAG_NUM_CTX`（默认 4096）**——**Ollama 默认 `num_ctx` 实测仅 2050，超长 prompt 被静默截断**，不显式设置等于没有预算；**改预算/档位必须按 M0 第 6 项口径实测（冷启动首 token <8 s 留 20% 余量），且"每样本换上下文"规避 prompt cache**；`/chat` 必须回显预算口径（估算/**实测 token**/采用与丢弃篇数）；**新增告警规则必须同步补 `ADVICE` 模板**（`enhanced_summary` 入库时写入、不回填历史行） |
| `watermark-report/` | 单机水位周报（S4-4；CPU/内存/磁盘 + 平台面，对照 M-Prod 触发条件 2） | `/data/ai-governance/scripts/watermark_report.sh`、`…/install_watermark_cron.sh`；cron `0 8 * * 1` 落在 **`/etc/cron.d/ai-governance-backup`**；报告写 `/data/ai-governance/reports/` | **阈值与口径只从源文档引用**（>70% 连续两周 / 80% 转人工 / 可用 <20 GiB），**不得在脚本里另立**；**判定必须用两个相邻 7 天窗口**（`[7d]` 与 `[7d] offset 7d`），且**先实测数据实际跨度**，跨度 <14d 一律写「数据不足」**不得给倾向性结论**；**长窗均值不得用 `rate(metric[7d])`**（外推失真，用 `avg_over_time(rate(metric[5m])[d:5m])`）；**磁盘不做线性外推**（镜像拉取是台阶式）；**文本类指标必须限定 `mountpoint`**；**报告仅供参考，触发判定与 M-Prod 决策属人工专属**；**改脚本必须同步本目录并追加《…S4-4水位周报机制记录.md》版本记录** |
| `capacity-baseline/` | 单机容量基线（S4-4 压测骨架；**自压，非真实业务负载**） | `/data/ai-governance/scripts/capacity_baseline.sh`、`…/os_bulk_bench.py`；报告写 `/data/ai-governance/reports/`（已随配置备份出机） | **不得用于准出判读**（门禁① 已人工豁免，设计方案 4.1.3 明示本环境不满足压测档）；**PG 读写必须经 PgBouncer 6432**（硬约束 2）；**临时对象（库/Topic/索引）用完即删，且报告必须自查残留**；**DROP DATABASE 前必须 `RECONNECT` 回收池连接**（池内连接会让 DROP 报 database is being accessed by other users，实测留下残留库）；**bulk 的索引必须出现在 URL 里**（`/{index}/_bulk`），且**必须校验 `errors` 与成功条目数**——被拒的 bulk 依然"很快"，只看耗时会把 docs/s 算虚高（实测误得 18 万）；**bulk refresh=false 时 `_count` 为 0**，需显式 `_refresh` 后再计数；**OpenSearch 报文不得拼在多层 shell 引号里**（改为 `docker cp` python 进容器）；**改脚本必须同步本目录并追加《…S4-4容量基线记录.md》版本记录** |
| `restore-annual-drill/` | 年度化恢复演练（S4-4；**本地段**，复用 S1-8 既有演练脚本） | `/data/ai-governance/scripts/restore_annual_drill.sh`；报告写 `/data/ai-governance/reports/`（已随配置备份出机） | **不得声称"全灾备可用"**：报告 §7 固定声明 5 条未覆盖（PG PITR / Flink CP 出机恢复 / 出机副本恢复 / 主机级重建 RTO / 真实业务量恢复）；**PG PITR 在本环境不存在**（未启用 WAL 归档）⇒ **不得把 dump 恢复说成 PITR**；**对象级恢复耗时 ≠ 主机级 RTO**；配置归档**含真实密钥** ⇒ 解包目录必须在 `/tmp`、用毕即删、**删除前断言路径前缀**；**临时对象零残留是自检项**（须打印 `(none)`）；**数值比较不得用字符串比较**（`12.5 < 1.5` 会误判，用 awk）；**判读与签字属人工专属**；**改脚本必须同步本目录并追加《…S1-8备份恢复脚本.md》版本记录** |
| `backup-restore/` | 备份与恢复演练脚本（**2026-09-21 新增归档**：此前只存在于服务器 ⇒ 仓库漂移，现补齐） | 服务器 `/data/ai-governance/scripts/`（cron `/etc/cron.d/ai-governance-backup`）：`backup_pg.sh` :05、`backup_opensearch.sh` :15、`backup_config.sh` 02:55、`backup_offsite.sh` 03:00；`restore_drill*.sh`、`restore_annual_drill.sh` 手动执行 | **改脚本必须同步本目录**并追加《…S1-8备份恢复脚本.md》版本记录；**配置归档含真实密钥**（恢复必须一并恢复，否则 JWT/加密数据不可用）；**`DROP DATABASE` 前必须 `RECONNECT` 回收 PgBouncer 池连接**；**PG PITR 在本环境不存在**⇒不得把 dump 恢复说成 PITR；演练**判读属人工** |
| `tools/` | **仓库级提交前自检**（`pre_push_scan.sh` + `.githooks/pre-commit` 钩子；2026-09-21 新增） | 仓库根 `.githooks/`（一次性启用：`git config core.hooksPath .githooks`）；已知口令字面量清单放**仓库外** `~/.ai-datathink/secret-patterns.local` | 提交前自动扫「结构性敏感信息」（私钥头 / `AKIA`・`ghp_`・`xox*` token / 带口令连接串 / 口令赋值 / **公网 IP** / 云主机名）+ 字面量口令清单；**命中即拦提交**，只报 `文件:行号 + 规则名`、**不打印命中内容**；误报用 `git commit --no-verify` 并在提交信息说明；**改规则后必须自测**（干净文件应通过、含公网 IP 与已知口令应被拦、变量引用 `PGPASSWORD="$PG_PASSWORD"` 不得误报） |

> 新增其他代码目录时，须同步扩充本节与本文件第 1 节的文档地图。

> **告警汇入（Alertmanager → GA）的硬纪律（2026-09-19 新增）**：
> ① GA 的 `POST /api/v1/alerts/ingest` **必须做 `GA_INGEST_TOKEN`（Bearer）校验**，不得为了"先跑通"把令牌留空上线；
> ② 幂等键固定为 `alertmanager:<fingerprint>:<status>`、来源标记 `source='alertmanager'`（Kafka 路径仍为默认 `governance.alerts`），**两条来源必须可区分**；
> ③ 该端点只做**留痕/可审计**，**不得当作"通知到人"**——IM/Webhook 推送属另一件事，地址到位前不得声称"告警已能通知"；
> ④ AM 侧配置改动流程固定：`amtool check-config` 通过 → `SIGHUP` 热加载（**本环境未启用 lifecycle API，`/-/reload` 不可用**）；配置模板入仓库时必须用占位符，**真值不入 Git**；
> ⑤ 改动 GA 镜像的 compose `image:` 行须走"**去 digest → `build` → 取新 image ID → 重新钉 digest**"三步，否则构建失败。

> **作业图级血缘上报（D1）的硬纪律（2026-09-19 新增）**：
> ① **宁缺勿错**：解析或映射不确定时**必须拒绝上报**（400 + errors，且**不落任何行**）——绝不"猜一条边"（ADR-A7 要求血缘准确率）；
> ② 映射**必须显式**（`mapping.table_service`/`topic_service`/`default_schema`），**不得从表名/连接串猜服务名**；JDBC url 反解库名是**唯一**允许的隐式推断；
> ③ 血缘粒度是**表/主题级边**，`LineageDetails.source` 固定 `PipelineLineage` 并把**作业 SQL 原文**写入 `sqlQuery`；**不得声称已实现"作业节点"血缘**（那是 D2）；
> ④ **不得声称"血缘已可用"**：OM 无实体时边必然 404（先接源建表）；且 D1 **没有周期对账**——OM 删实体会连带删边而 GA 状态仍为 `applied`，这是**已知漂移**；
> ⑤ 改解析器**必须同步跑 `test_lineage.py`**（含负例），并追加《…S3-5血缘上报D1记录.md》版本记录；ADR-A7 的实现方式回写属**人工专属**。

> **D1 对账（`POST /api/v1/lineage/reconcile`）的硬纪律（2026-09-19 新增）**：
> ① **只新增/修复，绝不删除 OM 上的任何边**——OM 还承载 sqllineage 等离线血缘，删错即"第二套真理源"（ADR-A7）；
> ② 判"边是否存在"必须用 `GET /lineage/getLineageEdge/…` 并读 **`edge` 键**（该端点**存在时**返回 `{"edge":{…}}`；按顶层 `toEntity` 判会**永远 False** ⇒ 对账误报漂移并反复"假修复"，实测踩到）；
> ③ 对账**自己必须可告警**：成功戳 → `job_freshness.sh` 的 `task="lineage_reconcile"` → `LineageReconcileStale`（>30h）。**对账静默失败等于没做**；
> ④ 指标类采集器**任何一行值非数字都会让 node_exporter 拒收整个 textfile 文件**（实测：一行空值 ⇒ 所有新鲜度指标一起消失）⇒ 取值必须 `num_or0()` 兜底，且落盘前自检、非法则**保留旧文件**；新增指标必须带 `# HELP`/`# TYPE` 并通过 `promtool check metrics`。

> **D1 自主触发（提交即上报）的硬纪律（2026-09-19 新增）**：
> ① 作业血缘的**唯一真相是登记目录** `/data/ai-governance/jobs/<job_key>.sql`（mapping 同目录）——新作业**必须登记**，否则自动上报看不见它；
> ② 提交作业**一律走 `submit_flink_job.sh`**：它做"干跑门禁 → 登记 → 提交 → 上报"，**门禁不过不提交**（宁缺勿错）；
> ③ **`planned` 不等于"已在 OM"**：只有 `status='applied'` 才算已写入；否则（`planned`/`drift`/`entity_missing`/`failed`）**必须重推**——早期版本按"行存在即跳过"，导致"先干跑再提交"的主路径**永远写不进 OM**（实测踩到）；
> ④ 上报链失败**必须不更新成功戳**（由 `LineageReconcileStale` 兜底），不得"跳过对账但写成功"；
> ⑤ **基础镜像的浮动 tag 是个坑**：`python:3.12-slim` 会在重建时静默换基础（本环境实测 debian 13.6→13.7，HIGH/CRIT 由 10/3 变 0/0）⇒ 自建镜像的 `FROM` **必须固定 digest**（已固定为 `python:3.12-slim@sha256:23b5dc88c7dd…`，debian 13.7）；换基础属版本变更须记录；
> ⑥ **钉完 digest 必须重扫验证**：本地 tag 与"新 digest"要分别核实——本轮曾误把旧的 `78387bc3881b…` 当新基础钉上，两个镜像又回到 13 且**没有任何报错**；`docker pull` 后才拿到真正的新 digest `23b5dc88c7dd…`。

> **跨源 PromQL 规则的硬纪律（2026-09-19 实测踩到，会静默失效）**：
> 不同 exporter 的指标**标签集不同**（例：node_exporter 的 `job="node"` vs Flink 的 `job="flink"`），
> 直接写 `A and B` **永不匹配** ⇒ 规则永远 `inactive`，**条件满足也不报**。跨源组合**必须**用 `and on()`（本仓库的 offsite 规则也是这么写的）。
> **纪律**：新写的规则**必须做一次真实触发验证**（造出条件 → 看 `pending → firing` → 还原），不能只看 `promtool check rules` 通过。

---

## 8. 门禁与验收（交付前必过）

**单机版四道门禁（阻断式，不通过不得进入下一段）**：

| # | 门禁 | 位置 | 不通过怎么办 |
|---|---|---|---|
| ① | 服务器验收（fio / 容量：顺序写 ≥1GB/s、4K 随机读 ≥100k IOPS） | S1-1 前 | 拒收，重提采购 |
| ② | 备份可恢复（**RPO ≤ 1h** / RTO ≤ 4h） | S1-8 | 修脚本重验，DBA 签字 |
| ③ | **T-M0-1** OM × PgBouncer transaction 兼容性（日志零命中 `prepared statement .* does not exist` / `transaction aborted`） | S2-4 | **不自动降级**，DBA+DE 决策 session/直连 |
| ④ | 合规边界确认书 + 续接窗口签约定（两份书面文件归档） | S3-1 | **不动生产库** |

另有收口项：**PG 复制槽保护三项验证**（兜底生效 + 告警触发 + 删 slot 演练），位置 S3-4 / S3-7。

**分段准出**：

- **S1**：`docker compose ps` 全部 healthy｜备份恢复演练记录｜`SHOW POOLS` 无 waiting
- **S2**：资产覆盖率 100%｜PII 抽检 >90%｜血缘 Top 50 列级 ≥90%｜Superset 零查 OM 内表｜合规文件归档
- **S3**：端到端 P95 <30s｜断流恢复 ≤ 续接窗口 1/3｜PG slot 三项验证｜质量看板无增量数据｜连续 1 周无未恢复故障
- **S4**：问答准确率抽样达标｜CI/CD 提示在 ≥1 仓库生效｜完成一次真实备份恢复演练｜M-Prod 评审有书面结论

**交付要求**：任何"已完成"结论都要附验证证据（执行的命令 + 实际输出 + 判读），不能用"已配置""理论上"代替。跑不了的验证要说清原因。

---

## 9. 安全与合规红线

- **不写入真实凭证与敏感信息**：连接串、口令、token、域名、账号一律占位符；密钥入 KMS/Vault 或加密 Secret（安全基线①）。
  **公网 IP 与主机名同样按敏感信息处理**（2026-09-21 整改：同步 GitHub 时发现 6 份文档含服务器公网 IP/主机名共 12 处，已替换为 `<POC服务器公网IP>` / `<POC服务器主机名>`；**注意：首次提交的历史里仍留有原值**，如需彻底清除须重写历史或改私有仓库）。
  **⚠️ 挂账（2026-09-21，未闭环）**：GitHub 仓库 `zhj-space/ai_datathink` 经 API 实测 **`visibility=public`**，且**重写历史的旧提交对象仍能从远端按 SHA 拉取**（`git fetch origin f9c5d2c…` 实测成功）⇒ **定稿/对外之前必须二选一**：① 仓库设为 **private**（或删除重建后再推）；② 请 GitHub Support 触发仓库 GC。改完后应复核 `git fetch origin <旧SHA>` **必须失败**。同批建议：把本环境 POC 口令轮换一次（口令从未入库，但曾在会话/终端出现）。
- **PII 处理**：样本只留脱敏特征，原文 ≤30 天加密留存（安全基线⑦）；不得在仓库留下真实业务数据样本。
- **生产库**：未完成合规边界确认与续接窗口签约定不得接入；CDC 接入配置与复制槽相关变更须 DBA 审批后执行。
- **不执行破坏性操作**（`rm -rf`、`DROP`、删复制槽、`git reset --hard`）除非用户明确要求；删除复制槽必须走"作业下线 checklist"（ADR-A2 第 2 件第③道防线）。
- **fail-closed**：OPA 策略引擎不可用时默认拒绝高风险操作，不得改成放行。
- **不自行回滚**：M-Prod 切流期间回滚触发权归 PM，Agent 只上报建议。
- **最小暴露**：仅暴露 443；网络隔离按单机 Compose 三网 / 三节点 NetworkPolicy。

---

## 10. 变更流程与文档同步

**影响链**：`设计方案（口径 / ADR） → 实施执行计划（阶段 / 门禁 / 任务卡） → 部署步骤（S1~S5） → 部署计划（结构化）`。改动上层时，必须显式说明下层是否需要同步，不能只改一份就算完。

1. **改动前**：先读源文档对应章节，说明方案与影响面，再动手。
2. **版本与记录**：每份文档末尾的"修订记录 / 版本记录"必须追加一行（版本、日期 `YYYY-MM-DD`、变更要点）。
3. **命名**：正式文档用 `<主题>.md` + 文内版本号；历史版本备份用 `_vX.Y_初版` 之类后缀并归档，避免出现两份同名同版本的文档（评审记录第 5 节已就此类命名冲突提出过修订）。
4. **落位**：方案与计划进 `方案计划/`，部署、采购、成本测算进 `执行步骤/`；不散落在仓库根目录。
5. **一次一个主题**：不把无关的格式化、措辞润色混进同一次改动。
6. **提交信息（Git 已启用）**：`docs(设计方案): ...`、`docs(部署步骤): ...`、`fix(口径): ...`；提交后 `git push origin main`（远端 `git@github.com:zhj-space/ai_datathink.git`）。**改文档/脚本后要提交**，否则服务器与仓库会再次漂移（2026-09-21 已因漂移补过 9 个脚本）。
7. **AI 产出声明**：AI 生成的文档保留文末"AI 生成提示"声明（现有文档均有此约定）。

---

## 11. Agent 三级授权边界

沿用《实施执行计划》§8 与任务卡的口径，三级不可越界：

| 级别 | 可做的事 |
|---|---|
| **可自主执行** | 文档拆解与写作、compose/IaC 文件产出、部署与验证脚本编写、健康检查与指标采集、报告生成 |
| **起草 + 人工审核** | 业务源连接器配置（凭证与访问范围）、OPA 策略与 Presidio 规则阈值、CDC 接入生产源库的配置、合规边界文件、基础设施盘点表、切流步骤 |
| **人工专属** | 采购流程、跨组织签约定与提交、准出验收判读与签字、准确率判定（**不能由 Agent 自证**）、恢复演练结果验证、切流每步放行与回滚决定、M-Prod 触发评审结论 |

**Agent 明确不得做的事**：

- 自行更改架构或组件选型（含"顺手优化"）；引入未在组件清单内的组件。
- 自行降级（如 PgBouncer transaction → session/直连）、自行修改门禁标准。
- 替人工签署、判读或背书任何验收结论。
- 把经验估算写成 SLA 承诺，或把"待确认"当作已确认使用。
- 在 POC 期引入 K8s 相关配置"为将来做准备"。

---

## 12. 待确认项与占位符约定

| 占位符 | 含义 |
|---|---|
| `待确认` | 源文档无此信息，未获人工确认前不得当作已知 |
| `需按官方文档核对` | 版本号、参数取值等须以官方文档为准 |
| `<...>` | 命令或配置中的变量（节点名、token、域名、客户端 ID 等） |

**当前挂账的 6 项**（详见《部署步骤》附 C /《单机版部署计划》Open Questions）：全部镜像精确版本号、PgBouncer 是否 ≥1.21、`max_slot_wal_keep_size` / `wal_keep_size` 取值、POC 服务器数据盘设备名、企业 IdP 回调域名 / 客户端 ID、Ollama 首 token 是否 <10s。

**纪律**：这些项没有实测或人工确认前，不得擅自填值；收敛后回填到源文档，并在修订记录中体现。

---

## 13. 交付格式

回复与产出文档至少包含三件事：

1. **改了什么**：涉及的文件、章节、具体变更点。
2. **怎么验证的**：执行的命令与实际输出；无法验证时说明原因。
3. **还有什么没做**：遗留项、待确认项、需要人工决策的点。

其他要求：结论先行；引用具体章节号（如"设计方案 6.3""ADR-A4""执行计划 Phase 2 门禁"）；给出文件路径与行号便于核对；不确定的部分直说，不掩盖。

---

## 14. 维护本文件

- 本文件是活文档：同类问题出现两次，就把规则补进来。
- 保持简短——它会被完整读入上下文，建议控制在 32 KB 以内。
- 修改本文件时同步更新文首"最后更新"日期；涉及口径或边界的修改，需与源文档一致。
