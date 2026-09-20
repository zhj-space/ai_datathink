# AI 数据治理平台设计方案（开源单机优先版）v3.3

> 文档定位：默认以单机 Docker Compose 作为起步与验证路径（见6.3），覆盖离线批处理与实时流式两类场景；当规模超出单机容量上限（见4.1.2）或需要真正高可用时，文档同时提供 3 节点 k3s 高可用扩展路径（见6.2）。
> **版本沿革**：v3.0——默认路径改回"单机优先，按需升级 3 节点"；实时链路改用 Flink CDC 直连捕获，移除 Debezium+Kafka Connect 独立组件。**v3.2 相对 v3.0 的调整**（合入 v3.1.1 架构调整版全部内容）：①新增 8 条架构决策记录 ADR-A1~A8（第 9 章）；②11 处正文表述修正（Kafka 部署形态、Checkpoint 按形态拆分、质量口径拆分、场景 B/C/E 重写、容量口径重算等）；③新增安全基线与治理运营模型（第 10 章）、验收标准汇总（第 11 章）。组件清单相对 v3.0 **零新增零替换**（唯一动作：SeaweedFS 由"触发式可选"提升为"3 节点形态必需"）。初版全文备份见《AI数据治理平台设计方案_v3.0_初版.md》。

## 0. 需求响应对照表

| # | 原始需求 | 设计响应 | 对应章节 |
|---|---|---|---|
| 1 | 组件用开源组件、主流方案 | 全部选型来自 Apache/CNCF/知名活跃社区项目；逐一核查 OSI 许可证，剔除 BSL/SSPL/多租户限制等"伪开源"陷阱 | 第3章 |
| 2 | 可单机部署（v3.0 恢复为默认路径） | 默认采用单机 Docker Compose 部署（见6.3），验证通过后若超出 4.1.2 单机容量上限或需要真正高可用，可按需升级到 6.2 的 3 节点 k3s 方案 | 第6章 |
| 3 | 可落地场景 | 给出5个可直接复制的落地场景，覆盖资产/血缘/质量/安全/AI五大治理能力域 | 第6章 |
| 4 | 考虑性能、可靠 | 容量分级规划、性能优化清单、单机/3节点两套可靠性设计、监控告警与灾备 | 第5章 |
| 5 | 支持离线和实时 | 双通道架构（批处理+流式），统一元数据底座作为唯一真理源，避免"两套血缘/两套质量结果"打架 | 第4章 |

---

## 1. 总体架构

```mermaid
flowchart TB
    subgraph SRC["数据源层（外部）"]
        DB[("业务库\n只读副本")]
        LOG["日志 / 埋点"]
        FILES["文件 / 离线数仓"]
    end

    subgraph STREAM["实时链路（Flink CDC 直连，默认）"]
        FL["Flink 作业：CDC Source（快照+增量）\n窗口规则 / 血缘图事件"]
        FLK2K["（可选开关）Flink CDC → Kafka cdc.* Topic\n仅当存在其他下游消费方时启用"]
    end

    subgraph BATCH["离线链路"]
        ING["OpenMetadata Ingestion + Airflow\n画像 / 质量测试 / sqllineage"]
    end

    subgraph CORE["统一治理存储（单一真理源）"]
        OM["OpenMetadata server"]
        PG[("PostgreSQL + pgvector\n含 gov_metrics 独立 schema")]
        OS[("OpenSearch\n检索 / 审计")]
    end

    subgraph GOV["能力层"]
        PRES["Presidio：PII 识别（仅离线批量）"]
        OPA["OPA：策略决策 PDP"]
        LLM["Ollama：本地 LLM"]
        GA["Governance Agent（自研）\n告警增强 / 开放 API / 指标 ETL / 审计汇聚"]
    end

    subgraph EFACE["场景 E 执行面（复用查询引擎，非新组件）"]
        QE["查询引擎：PG 视图+RLS /\nDoris·StarRocks 列权限 / Trino mask"]
    end

    subgraph SERVE["展现层"]
        PORTAL["治理门户（Streamlit）"]
        SUP["Superset（只读 gov_metrics）"]
    end

    KAFKA[("Kafka：日志事件 + governance.alerts\n单节点（默认）/ 企业集群（复用）")]
    GW["Traefik：TLS / SSO / 限流"]
    OPS["Prometheus + Alertmanager"]

    DB -->|元数据抽取| ING
    FILES --> ING
    DB -->|CDC binlog/WAL 直连| FL
    LOG --> KAFKA
    FL -.可选.-> FLK2K --> KAFKA
    FL -->|快照基线=权威质量指标| OM
    FL -->|增量窗口命中=异常告警| KAFKA
    KAFKA --> GA
    ING --> OM
    OM --> PG
    OM --> OS
    PRES <--> OM
    OPA --> GA
    LLM --> GA
    GA --> PG
    GA --> OS
    GA -->|指标 ETL| PG
    QE -.依据 PII 标签+OPA 策略配置.-> OPA
    GA -.汇聚访问/决策日志.-> OS
    PORTAL --> GW
    SUP --> PG
    OM --> GW
    OPS -.采集.-> CORE
```

**关键设计决策**：
- **各能力的用户界面优先复用组件自带 UI**，仅在确实缺失时轻量自建：数据目录/血缘/质量用 OpenMetadata 自带 UI，BI 报表用 Superset，批处理/流处理监控用 Airflow/Flink 自带 Dashboard；Governance Agent 的智能问答（场景D）与访问审批（场景E）之前只有 API 没有界面，新增轻量治理门户（Streamlit）补齐，完整盘点见 2.4。
- **血缘/质量/资产的唯一真理源是 OpenMetadata + PostgreSQL**。无论数据来自离线批处理还是实时流，最终都写入同一套元数据存储，不再出现"两个数据源不同步"的问题（历史上 DataHub/OpenMetadata + 独立图数据库并存导致过这类问题，本方案直接规避）。
- **Flink 作为实时流处理核心引擎**：承担有状态窗口计算、复杂规则/CEP，以及**作业图级血缘上报**（作业启动/变更时上报，非事件级血缘，见 ADR-A7）。**上报机制（v3.3 修正）**：由 **Governance Agent 自研上报**（解析 Flink SQL 作业定义 → 写 OpenMetadata 血缘 API），**不再依赖 OpenLineage**——实测 Flink 1.x 的 OpenLineage 集成不支持 Flink SQL、Flink 2.x 所需 SPI 在 1.20.1 不存在、且 `flink-cdc` 未实现 FLIP-314 血缘接口，故该路线在本项目技术栈下不可行（依据见 ADR-A7 与《…S3-5血缘上报D1记录.md》）。相比在 Governance Agent 里手写消费者重新实现窗口/状态管理，直接用 Flink 更贴合"主流方案"要求，也更可靠（原生 Checkpoint 容错）。单机模式下单节点 standalone 部署即可；3 节点模式下由 Flink Kubernetes Operator 管理，JobManager 获得 K8s 原生选举 HA。
- **实时链路改用 Flink CDC 直连捕获，移除 Debezium+Kafka Connect 独立组件，内置"两段式"切换开关（ADR-A1）**：Flink CDC（现为 Apache Flink 官方子项目 `apache/flink-cdc`，Apache-2.0）内置 MySQL/PostgreSQL/Oracle 等 CDC Source Connector，直接在 Flink 作业内读取源库变更，无需再单独部署 Kafka Connect 运行 Debezium；Kafka 的职责收窄为承接日志类事件接入与 `governance.alerts` 告警分发，不再承担 CDC 传输职责（详见3.3 与 ADR-A1/A2）。当出现其他 CDC 下游消费方、源库续接窗口无法满足恢复预算、或作业频繁背压时，启用两段式（Flink CDC → Kafka cdc.* Topic → 处理作业），**切换只加 Topic，不引入新组件**。
- **Governance Agent 收窄为 AI 编排与服务层**：不再自己实现流式规则引擎，改为订阅 Flink 产出的告警/结果做 AI 层面增强（异常摘要、根因线索、处置建议），并承担开放 API、审批网关等职责，分工更清晰。
- **Apache Superset 作为治理 BI 层**：直接对接 PostgreSQL，将资产覆盖率、质量趋势、告警趋势等治理指标可视化，服务于管理层/治理团队的常态化监控。
- **Governance Agent 是唯一自研组件**，内部按模块划分（实时告警增强模块 / AI 编排模块 / 开放 API 模块 / 审批网关模块），早期阶段合并部署为一个服务，未来可按模块拆分独立扩展。
- **物理部署默认单机，规模化后可按需升级 3 节点高可用**：本图是逻辑架构，与部署在 1 台还是 3 台服务器无关；单机 Docker Compose（6.3，默认起步）与 3 节点 k3s（6.2，规模化后可选）共用同一套逻辑架构，角色分工、副本策略、编排工具差异见第6章。

---

## 2. 核心组件选型与开源协议核查

### 2.1 核心必需组件

| 组件 | 承担能力 | 开源协议 | 许可证说明 |
|---|---|---|---|
| PostgreSQL + pgvector | 元数据库、向量库（一库多用） | PostgreSQL License / PostgreSQL License(类MIT) | 无商用限制 |
| OpenSearch | 全文检索、OpenMetadata 存储引擎 | Apache-2.0 | AWS 主导的 Elasticsearch 开源分支 |
| OpenMetadata（server + ingestion，内置 Airflow） | 元数据目录、血缘、内置数据质量测试/画像 | Apache-2.0 | 官方 docker-compose quickstart 最低 4 容器、6GiB 内存 + 4vCPU 即可单机运行 |
| sqllineage（作为 ingestion 内嵌库，非独立服务） | 复杂 SQL 脚本血缘解析，补充 OpenMetadata 原生解析 | MIT | 已核实 |
| Presidio（analyzer + anonymizer） | PII 识别与脱敏 | MIT | 微软出品，已核实 |
| Open Policy Agent（OPA） | 策略即代码、访问控制/审批规则 | Apache-2.0 | 已核实 |
| Ollama | 本地 LLM 推理引擎 | MIT | Ollama 本身 MIT，所加载模型许可证需单独核查 |
| Qwen2.5 / Qwen3（默认模型） | 问答、脱敏建议、影响分析生成 | Apache-2.0 | 中文场景主流、商用友好，优于 Llama 系列的社区许可证 |
| BGE-M3 / nomic-embed-text | 向量嵌入模型（RAG 用） | MIT / Apache-2.0 | 经 Ollama 或 sentence-transformers 加载 |
| LangChain / LangGraph | AI 编排框架 | MIT | 代码优先、可控性强，作为主推荐（而非 Dify） |
| Governance Agent（自研） | AI 编排、开放 API、审批网关、消费 Flink 输出做智能增强 | 自有 | 唯一自研组件，模块化设计 |
| 治理门户前端（自研，基于 Streamlit） | 场景D智能问答聊天界面、场景E访问审批台，调用 Governance Agent API | Apache-2.0（Streamlit） | 曾评估 Open WebUI，其许可证含"禁止修改/移除品牌"附加条款，非纯 OSI 开源（见2.3），改用无此类限制的 Streamlit 自建轻量前端 |
| Apache Kafka（单节点默认 / 复用企业集群；3 节点为规模化后形态） | 日志类事件接入 + 告警事件分发基础设施 | Apache-2.0 | KRaft 自 3.3 起 GA，无需 ZooKeeper；POC 单机档为 Compose 内单节点（或复用企业已有集群）；规模化后 3 节点部署通过 Strimzi Operator 管理，副本因子 3；不再承担 CDC 传输（见 Flink 行与 ADR-A1） |
| Apache Flink（Kubernetes 部署，内置 Flink CDC）| 实时流处理引擎：内置 CDC Source Connector 直连捕获数据库变更、有状态窗口计算、复杂规则、实时血缘事件生成 | Apache-2.0 | Flink CDC 现为 Apache Flink 官方子项目（apache/flink-cdc），内置 MySQL/PostgreSQL/Oracle 等连接器，免去独立部署 Kafka Connect+Debezium；OpenLineage 官方支持 Flink 集成；通过 Flink Kubernetes Operator 管理，JobManager HA 依赖 K8s 原生选举，无需 ZooKeeper |
| PgBouncer | 数据库连接池 | ISC（宽松许可） | 性能优化必需 |
| Traefik | 反向代理、统一网关、TLS 终止 | MIT | k3s 默认内置的 Ingress Controller，配合 MetalLB 提供跨节点漂移的虚拟 IP |
| Prometheus + node_exporter + cAdvisor + postgres_exporter + kafka_exporter | 指标采集与监控 | Apache-2.0 | 通过 kube-prometheus-stack Helm Chart 统一安装 |
| Alertmanager | 告警路由与去重 | Apache-2.0 | |
| Apache Superset | BI 报表与治理可视化大盘 | Apache-2.0 | 替代 Metabase/Grafana OSS（均 AGPL-3.0），数据源直接对接 PostgreSQL |
| k3s | 轻量 Kubernetes 发行版，3 节点集群编排底座 | Apache-2.0 | 单二进制、内置 etcd，专为少节点 HA 场景设计 |
| Helm | k3s 组件包管理与安装 | Apache-2.0 | |
| CloudNativePG | PostgreSQL 高可用 Operator | Apache-2.0 | 1主2从自动故障转移，复用 K8s API 协调，无需额外部署 etcd |
| Strimzi | Kafka Kubernetes Operator | Apache-2.0 | 管理 3 broker+controller 合一节点，副本因子 3 |
| OpenSearch Kubernetes Operator | OpenSearch 集群编排 | Apache-2.0 | 官方维护，管理 3 节点集群的扩缩容与故障恢复 |
| Flink Kubernetes Operator | Flink 作业与 HA 管理 | Apache-2.0 | JobManager HA 用 K8s 原生选举，无需 ZooKeeper |
| MetalLB | 裸金属 LoadBalancer | Apache-2.0 | 为 Traefik Ingress 提供跨节点漂移的虚拟 IP，消除入口单点 |

### 2.2 触发式可选扩展（默认不启用，达到特定条件再引入）

| 组件 | 引入条件 | 开源协议 | 备注 |
|---|---|---|---|
| Great Expectations 或 Soda Core | OpenMetadata 内置质量测试无法表达复杂规则 | Apache-2.0 | 二选一，避免同时引入造成技术栈分裂 |
| Grafana | 需要比 Prometheus 自带 UI 更丰富的运维大盘 | AGPL-3.0 | **仅限内部运维可视化**；若平台未来对外提供多租户 SaaS 服务，AGPL 的网络访问条款需法务复核（与 Dify 多租户条款问题同类处理原则） |
| Valkey | 出现明显的元数据查询热点、需要缓存层 | BSD-3-Clause | 替代 Redis（≥7.4 起默认 RSALv2/SSPLv1，非 OSI 开源） |
| NebulaGraph | 需要超出 OpenMetadata 内置血缘表达能力的多跳图算法分析 | Apache-2.0 | 不作为核心必需，避免血缘"两个真理源"问题 |
| Milvus | pgvector 向量规模超过千万级、检索延迟不达标 | Apache-2.0 | 单机场景通常无需集群模式 |
| SeaweedFS | **3 节点形态必需**（Flink Checkpoint/备份的共享对象存储，见 ADR-A4）；湖仓表文件场景按需 | Apache-2.0 | 替代 MinIO（主分支已改 AGPL-3.0）；生产规模湖仓存储通常独立扩展或复用企业已有对象存储 |
| Apache Iceberg | 企业已有/规划建设开放表格式湖仓架构 | Apache-2.0 | 生态最广，默认推荐；OpenMetadata 无原生连接器，需经 Trino/Doris/StarRocks 等查询引擎间接采集元数据与血缘 |
| Delta Lake | 同上，且希望 OpenMetadata 直接原生连接 | Apache-2.0 | OpenMetadata 官方专用连接器，无需查询引擎中转 |
| Apache Hudi | 同上，且有高频 upsert/近实时写入需求 | Apache-2.0 | 同 Iceberg，需经查询引擎间接接入，OpenMetadata 无原生连接器 |
| Trino（单节点） | 湖仓场景下需要采集 Iceberg/Hudi 元数据，或治理侧需要 SQL 抽样查询 | Apache-2.0 | 若企业已有 Doris/StarRocks 承担分析职责可直接复用，无需重复部署 |
| DuckDB | 治理侧对湖仓表做轻量抽样查询（PII 扫描、RAG 检索取数） | MIT | 内嵌式，无需服务化部署 |
| Apache Polaris 或 Hive Metastore | 湖仓需要多引擎并发读写、JDBC Catalog 并发能力不够 | Apache-2.0 | Catalog 服务化升级，默认走 Iceberg JDBC Catalog 复用 PostgreSQL 即可 |
| Dify | 业务方明确要低代码编排、且法务确认非多租户 SaaS 场景 | Apache-2.0 + 附加条款 | 附加条款限制未授权的多租户运营 |
| MLflow | 需要正式的模型注册/上线审批流程 | Apache-2.0 | 开源版审批能力弱，审批网关需自研补齐 |
| Keycloak | 企业无现成 IdP/SSO，需自建统一身份认证 | Apache-2.0 | 优先对接企业已有 AD/LDAP/SSO，无现成时才自建，见2.5 |
| oauth2-proxy | 需要统一 SSO 覆盖 Flink/Prometheus/Traefik 等无原生认证的 UI | MIT | 作为 Traefik ForwardAuth 中间件，CNCF Sandbox 项目，见2.5 |

### 2.3 明确不采用/需谨慎的组件

| 组件 | 问题 |
|---|---|
| Redpanda | 核心采用 Source Available（BSL 类）许可，非 OSI 认证开源，与需求1"开源组件"冲突，不作默认推荐，仅在用户明确接受非纯开源许可时才可考虑 |
| Apache Ranger | 官方定位是 Hadoop 生态安全框架，对 Doris/ClickHouse 等无官方插件，本方案改用 OPA 统一做策略引擎 |
| MinIO（主分支） | 已改 AGPL-3.0 |
| Redis ≥7.4 | 默认 RSALv2/SSPLv1，非 OSI 开源 |
| Open WebUI | 许可证表面类似 BSD-3-Clause，但附加条款明确禁止修改/移除“Open WebUI”品牌，与 Dify 附加条款同类问题，非纯 OSI 开源，改用 Streamlit 自建前端（见2.1） |

---

### 2.4 用户界面（UI）覆盖盘点

本节回答“最终平台是否有 UI”：结论是**有**，且绝大多数能力直接复用组件自带 UI，仅 Governance Agent 自研能力（场景D/E）需要新增一个轻量前端。

| 能力域 | UI 来源 | 现状 |
|---|---|---|
| 数据目录/血缘/质量浏览 | OpenMetadata 自带 UI | ✅ 齐全，无需自建 |
| PII 标签人工复核（场景A 中置信队列） | OpenMetadata 自带 Tasks / Request-for-Tags 工作流 | ✅ 直接复用，无需新建 |
| BI 报表/治理指标看板 | Apache Superset 自带 UI | ✅ 齐全 |
| 批处理调度监控 | Airflow 自带 UI（随 OpenMetadata ingestion 内置） | ✅ 齐全 |
| 实时流作业监控 | Flink 自带 Web Dashboard | ✅ 齐全 |
| 基础设施/集群监控 | Prometheus/Alertmanager 基础 UI；Grafana 可选更丰富 | ✅ 基本齐全（可选增强） |
| 网关/入口路由 | Traefik 自带 Dashboard（可选开启） | ✅ 可选 |
| AI 智能问答（场景D） | 治理门户（自研 Streamlit 聊天界面） | ⚠️ 之前缺失，本轮补齐 |
| 敏感数据访问审批（场景E） | 企业 IM 审批（企业微信/钉钉/飞书）卡片，或治理门户审批台兼底 | ⚠️ 之前缺失，本轮补齐 |
| OPA 策略编写 | 无 UI，Rego 代码 + Git 版本管理 | ⚪ 设计上故意如此（策略即代码） |

**结论**：平台本身不重复建设 UI，除了把已有组件的 UI 串连起来之外，仅为 Governance Agent 新增一个轻量治理门户（Streamlit）承载场景D的聊天问答与场景E的审批台，避免为此引入非纯开源的第三方 Chat UI（如 Open WebUI）。

### 2.5 统一入口与单点登录（回答"有没有统一的治理 UI"）

延续 2.4 的盘点：**目前没有单一 UI 合并所有能力，而是多个专业 UI 各自入口**。是否需要"统一"取决于对"统一"的定义——若指"一次登录、一个起始入口"，可以补齐；若指"重新做一套 UI 取代所有工具自带界面"，不建议（重复造轮子，且各工具原生 UI 已经很成熟）。

**统一登录（SSO/OIDC）**：

| 组件 | 是否原生支持 OIDC | 接入方式 |
|---|---|---|
| OpenMetadata | 是 | 直接配置 OIDC Provider |
| Apache Superset | 是（基于 Flask-AppBuilder） | 直接配置 OAuth/OIDC |
| Airflow | 是（基于 Flask-AppBuilder） | 直接配置 OAuth/OIDC |
| 治理门户（Streamlit，自研） | 需自行集成 | 开发时直接对接 OIDC |
| Flink Dashboard / Prometheus / Alertmanager / Traefik Dashboard | 否，无原生认证 | 经 oauth2-proxy 作为 Traefik ForwardAuth 中间件统一网关认证 |

身份提供方（IdP）优先对接企业已有 AD/LDAP/内部 SSO；若企业没有现成 IdP，才自建 Keycloak（Apache-2.0，触发式可选，见2.2）。

**统一入口（导航 launcher）**：治理门户（Streamlit）除原有问答/审批功能外，扩展为登录后的**首页/导航中心**：展示各能力入口卡片（资产目录→OpenMetadata、BI报表→Superset、实时作业→Flink、批处理→Airflow），点击后在同一 SSO 会话下深链接跳转；不做 iframe 完整嵌入（各工具都有较强的 CSP/frame-ancestors 限制，嵌入不可靠，直接跳转更稳定）。

**结论**：可以做到"一次登录 + 一个起始地址"的统一体验，但不做"单一 UI 取代所有 UI"，这是权衡工程投入与用户体验后的合理选择。

---

## 3. 离线与实时双链路设计

### 3.1 两类通道特征对比

| 维度 | 离线批处理通道 | 实时流式通道 |
|---|---|---|
| 延迟 | 分钟~小时级（按 Airflow 调度周期） | 秒级~亚秒级 |
| 一致性 | 强一致，全量/增量核对，适合审计 | 最终一致，允许短暂延迟，适合监控告警 |
| 典型场景 | 全量资产盘点、周期性合规审计、历史数据画像、复杂 SQL 血缘解析 | 交易异常检测、实时血缘登记、实时告警、访问策略实时评估 |
| 故障影响 | 影响下一周期结果，存量数据不受影响 | 短暂中断不影响历史数据；日志类事件在 Kafka 保留窗口内可重放（CDC 断点续接看源库续接窗口，见 ADR-A2）；批处理兜底校准 |
| 数据量级特征 | 大批量、高吞吐、周期性峰值 | 小批量高频、持续稳定流量 |

### 3.2 离线批处理链路

1. **抽取**：OpenMetadata Ingestion 连接器定期（默认每日，可配置）连接业务数据库/数据仓库，抽取 schema、样本数据、既有 SQL 脚本。
2. **画像与质量**：OpenMetadata 内置 Profiler 生成列级统计（空值率、唯一值、分布），内置质量测试（Data Quality Tests）执行规则校验；复杂规则不够用时才引入 Great Expectations/Soda Core。
3. **血缘解析**：OpenMetadata 原生连接器血缘 + sqllineage 解析复杂 ETL/视图 SQL 脚本，统一写入 OpenMetadata 血缘图。
4. **敏感数据发现**：Presidio Analyzer 对抽样数据批量扫描，按置信度分层处理（见第6章场景A）。
5. **产出**：资产目录、血缘图、质量报告、PII 标签，全部落地在 PostgreSQL/OpenSearch。

### 3.3 实时流式链路

1. **捕获（Flink CDC 直连，无需独立 Kafka Connect）**：在 Flink 作业内直接使用 Flink CDC 的 MySQL-CDC/Postgres-CDC/Oracle-CDC Source Connector 订阅业务数据库的 binlog（MySQL）/逻辑复制（PostgreSQL WAL）/redo log（Oracle 需商业插件，注意授权），直接产生行级变更事件流，不再需要单独部署 Kafka Connect 运行 Debezium。
2. **日志类事件接入**：应用层的日志/埋点事件（非数据库 CDC）仍通过 Kafka（KRaft 部署，无需 ZooKeeper）接入，Flink 作为 Kafka 消费者读取。
3. **流式处理（Flink）**：同一个 Flink 作业合并处理 CDC 直连流与 Kafka 日志流，执行：
   - 有状态窗口计算（滑动/滚动窗口内空值率突增、字段越界、行数环比骤降等）；
   - 复杂事件关联（多表联合异常检测等 CEP 场景，为后续增强预留能力）；
  - 由 **Governance Agent 自研上报**生成**作业图级血缘**（作业启动/变更时上报，ADR-A7）：解析 Flink SQL 作业定义得到 source→sink 边，再写入 OpenMetadata 血缘 API——**与离线血缘共享同一存储，不产生第二套真理源**。**v3.3 修正**：原表述依赖"官方 OpenLineage Flink 集成"，实测该路线在本项目技术栈下不可行（见 ADR-A7）；
   - 质量检测结果写入 OpenMetadata；异常事件发送到独立 Kafka Topic（如 `governance.alerts`）供下游消费。
4. **AI 增强与处置（Governance Agent）**：订阅 `governance.alerts` Topic，调用 Ollama 对异常做补充解释（摘要、可能根因、历史相似问题匹配），并按严重程度触发 Alertmanager/IM 告警或联动 OPA 执行自动降级策略。
5. **背压与容错**：Flink CDC 的 binlog/WAL 位点与 Flink 自身 Checkpoint 绑定一起持久化（状态与位点定期落盘），保证节点重启后可从最近 Checkpoint 恢复，避免重复处理或漏处理（相比 Debezium+Kafka Connect 方案需要另外管理 Kafka Connect 自己的 offset，单一 Checkpoint 机制更简洁）；Kafka 配置合理的 topic 保留时长（建议 ≥7 天）用于**日志类事件**重新回放；**CDC 断点续接取决于源库续接窗口**（MySQL binlog 保留期 / PG 复制槽 WAL 保有量，可靠性配套见 ADR-A2 四件套）；Governance Agent 对 `governance.alerts` 的消费同样采用 at-least-once + 幂等写入（业务主键+时间戳去重）。

### 3.4 统一治理平面与降级策略

- 离线和实时两条链路的最终写入目标是**同一个 OpenMetadata + PostgreSQL/OpenSearch**，用户在同一个界面/API 查询资产、血缘、质量结果，无需区分数据来自哪条通道。
- 当实时链路故障（Kafka/Flink CDC 捕获/Governance Agent 消费者不可用）时，离线批处理仍按周期运行，作为质量/血缘结果的兜底来源，保证核心治理能力不因实时组件故障而完全失效。

### 3.5 湖仓一体场景扩展（触发式：企业已有或规划建设湖仓架构时启用）

湖仓一体（数据湖+数据仓库融合）不是本方案的默认必需能力，而是作为**数据源类型的扩展**接入统一治理平面——当企业的数据除了传统业务数据库外，还有基于开放表格式（Iceberg/Hudi/Delta Lake）的湖仓时触发。

**表格式选择**（三选一或按需组合，均为 Apache-2.0）：

| 表格式 | 适用场景 | OpenMetadata 治理接入方式 |
|---|---|---|
| Apache Iceberg | 生态最广、与 Doris/StarRocks/Trino 等主流 OLAP 引擎原生集成最好，默认推荐 | **需经查询引擎间接接入**：OpenMetadata 无独立 Iceberg 原生连接器，需连接 Trino/Doris/StarRocks/Presto/Athena（均有官方连接器）来采集元数据与血缘 |
| Delta Lake | 希望 OpenMetadata **直接原生连接**、免去额外查询引擎中转 | OpenMetadata 官方专用 Delta Lake 连接器，可直连 |
| Apache Hudi | 高频 upsert/近实时写入场景更成熟 | 同 Iceberg，需经查询引擎间接接入，OpenMetadata 无原生连接器 |

以上结论已通过查阅 OpenMetadata 官方连接器文档核实（Delta Lake 在官方 Database 连接器列表中，Iceberg/Hudi 不在），避免凭印象误判。

**目录（Catalog）**：默认使用 Iceberg JDBC Catalog，复用核心组件 PostgreSQL（新建独立 schema，零新增服务，延续"一库多用"原则）；当需要多引擎（Spark+Trino+Flink+Doris）并发读写、更强的多表事务/并发保证时，可升级为 Apache Polaris（Apache-2.0，Iceberg REST Catalog 参考实现）或 Hive Metastore（Apache-2.0）。

**对象存储**：单机 POC 可部署 SeaweedFS（Apache-2.0，S3 兼容）承载表文件；生产规模的湖仓存储通常是独立扩展的存储层或企业已有的 MinIO/Ceph/云 OSS，治理平台此时只需只读凭证接入，不必自己托管。

**查询引擎（治理与分析共用）**：默认新增 Trino 单节点（standalone，服务于"OpenMetadata 元数据采集 + Governance Agent/Presidio 抽样查询"）；若企业已有 Doris/StarRocks 承担分析职责且已挂载 Iceberg Catalog，直接复用其作为 OpenMetadata 连接目标，无需重复部署 Trino。

**离线入湖**：批量 ETL（沿用 3.2 的 Airflow 调度）定期把源库/日志数据写入 Iceberg/Delta 表；OpenMetadata 照常抽取 schema、分区、统计信息。

**实时入湖（复用现有 Flink 作业，不新增管道）**：3.3 中已经存在的 Flink 流式作业，在原有"质量检测 + 实时血缘事件"输出之外，新增一个 Iceberg/Hudi/Delta Sink，将 CDC 流以近实时 upsert 方式写入湖仓表——同一个作业身兼质量监控与建仓两件事，避免为"实时入湖"另起一条数据管道。

**治理侧轻量查询**：Governance Agent/Presidio 对湖仓表做 PII 抽样或 RAG 检索（场景D）的数据获取时，直接用 DuckDB（MIT，内嵌式，原生支持读 Iceberg/Delta/Parquet 文件）做轻量查询，无需为这类内部辅助查询单独跑一个服务化查询引擎。

**PII 与访问控制在湖仓侧的落地**：Presidio 批量扫描结果、OPA 策略决策的口径与其他数据源一致（见2.1、5章场景A/E），执行点落在查询引擎侧——Trino 支持基于视图的列级脱敏，Doris/StarRocks 有自身的列级权限体系，需按引擎分别配置，但决策依据统一来自 OpenMetadata 的 PII 标签 + OPA 策略。

**容量说明**：湖仓底层数据卷通常远大于传统业务库，但**不计入本文第4章的容量规划**（该规划以"表数量"衡量元数据治理负载，与实际数据体量无关）；若单机同时部署 SeaweedFS 承载湖仓存储，需要为存储容量单独规划磁盘（取决于业务数据量而非表数量），不与治理平台自身的资源预算混算。

```mermaid
flowchart LR
    SRC2[("源库/日志")] --> BATCH2["批量ETL(Airflow)"]
    SRC2 --> CDC2["Flink CDC"]
    CDC2 --> FLINK2["Flink(质量检测+实时血缘+湖仓Sink)"]
    BATCH2 --> TABLE["Iceberg/Delta/Hudi 表"]
    FLINK2 --> TABLE
    TABLE -.存储于.-> OBJSTORE[("SeaweedFS/企业已有对象存储")]
    TABLE -.目录.-> CATALOG[("Iceberg JDBC Catalog(复用PostgreSQL)")]
    TABLE --> ENGINE["Trino/Doris/StarRocks 查询引擎"]
    ENGINE --> OMCONN["OpenMetadata 连接器采集"]
    TABLE -.轻量探针.-> DUCKDB["DuckDB(治理侧抽样查询)"]
    OMCONN --> OM2["OpenMetadata 统一治理平面"]
    DUCKDB --> OM2
```

---

## 4. 性能与可靠性设计（NFR）

### 4.1 容量规划分级（工程经验估算，实施前需实测压测校准）

#### 4.1.1 三节点高可用模式容量规划（规模化后的扩展路径，部署方式见6.2）

| 规模档位 | 表数量 | 日增数据量 | 实时事件吞吐 | 并发用户 | 每节点建议规格（×3台，对称部署） |
|---|---|---|---|---|---|
| 小型（部门级生产） | < 1,000 | < 10GB | < 100 events/s | < 20 | 16 vCPU / 64GB RAM / 1TB NVMe SSD ×3 |
| 中型（多业务线共享） | 1,000 ~ 10,000 | 10 ~ 100GB | 100 ~ 2,000 events/s | 20 ~ 100 | 24 vCPU / 96GB RAM / 2TB NVMe SSD ×3 |
| 大型（企业级） | 10,000 ~ 20,000 | 100GB~500GB | 2,000 ~ 5,000 events/s | 100 ~ 200 | 32 vCPU / 128GB RAM / 4TB（短保留）~8TB（长保留）NVMe ×3（口径见下方说明与 ADR-A8；Ollama 建议用 nodeAffinity 固定在其中1台并加装 GPU） |

> **容量口径说明（ADR-A8 修正）**：PostgreSQL 每实例为**全量拷贝**；OpenSearch/Kafka 的副本为**分区级**（RF=3、3 节点时每节点 ≈ 唯一数据量）。每节点磁盘 = PG全量×1.3 + 日增×Kafka保留天数 + OpenSearch索引(通常<100GB) + Checkpoint保留量 + 系统盘(480GB) + 20% 余量。大型档两种采购选择：**短保留**（Kafka 1~3 天，4TB/节点，预算紧时推荐，配合离线兜底校准）或**长保留**（7 天，8TB/节点，需更长回放窗口时）。每节点规格已预留约 30% 冗余，用于吸收其他节点故障时临时承接的负载。
> **集群上限提示**：超过"大型"档位（表数量>2万、吞吐>5,000 events/s、并发>200）时，建议扩容为 5 节点+（增加 Kafka/OpenSearch 副本与专用节点池），而非继续增大单节点规格。

#### 4.1.2 单机模式容量规划（默认起步路径，部署方式见6.3）

| 规模档位 | 表数量 | 日增数据量 | 实时事件吞吐 | 并发用户 | 建议单机规格 |
|---|---|---|---|---|---|
| 小型（POC/试点） | < 1,000 | < 10GB | < 100 events/s | < 20 | 8 vCPU / 32GB RAM / 500GB SSD（**离线验证档**：仅离线闭环，暂不含 Kafka/Flink/Superset；实时链路自 24C/96G 中型档起，避免与需求 5"支持实时"冲突） |
| 中型（部门级生产） | 1,000 ~ 10,000 | 10 ~ 100GB | 100 ~ 2,000 events/s | 20 ~ 100 | 24 vCPU / 96GB RAM / 2TB NVMe SSD（离线+实时完整链路，含 Flink+Superset） |
| 大型（多业务线共享） | 10,000 ~ 20,000 | 100GB~500GB | 2,000 ~ 5,000 events/s | 100 ~ 200 | 48 vCPU / 192GB RAM / 4~8TB NVMe（Ollama 如需更大模型建议加装 GPU；Flink 作业数增多需相应加大内存） |

> **单机上限提示**：表数量超过 2 万、实时事件吞吐超过 5,000 events/s 或并发用户超过 200 时，建议升级到 4.1.1 的三节点高可用模式。单机模式设计上限即在此，不建议继续堆资源。

#### 4.1.3 最小测试环境规格（功能验证专用，测试服务器资源有限时使用）

若测试环境服务器规格低于 4.1.2 的"小型"档位，可按以下**功能可跑通的绝对下限**评估，仅用于功能联调/验收测试，不代表可承载真实业务负载，**不能用于压测、不能带入生产**：

| 验证范围 | CPU | 内存 | 磁盘 | 说明 |
|---|---|---|---|---|
| 仅离线治理 + AI（暂不启用实时链路） | 8 vCPU | 16GB RAM | 200GB SSD | 对应先验证执行计划 Phase 1+3，Phase 2 实时链路延后 |
| 全链路（含 Kafka/Flink/Superset） | 12 vCPU | 24GB RAM | 300GB SSD | 全部核心组件跑通，仅做功能验证，无性能/可靠性余量 |

**进一步压缩资源的具体手段**（仅限测试环境，生产环境不要沿用）：
- Ollama 改用更小模型（如 Qwen2.5:1.5b/3b 量化版）替代 7B，可省 4~12GB 内存，代价是问答质量下降，仅适合功能联调，不适合验证准确率；
- OpenSearch/Kafka 手动调小 JVM heap（如 OpenSearch `-Xms1g -Xmx1g`、Kafka `KAFKA_HEAP_OPTS=-Xms512m -Xmx512m`）；
- Flink `taskmanager.numberOfTaskSlots` 调到 1~2，只验证规则逻辑是否跑通，不代表真实吞吐能力；
- 暂不部署 Prometheus/Superset，先用 OpenMetadata/Flink 自带 Web UI 做功能验证，功能跑通后再按需加回监控和 BI；
- OpenSearch/Kafka 均用单节点、不开副本，这是测试专用配置。

**与 3 节点高可用模式的取舍**：3 节点模式无论如何都需要 3 台服务器各自跑一份完整副本，总资源需求天然是单机模式的数倍；测试环境资源有限时**不建议**用 3 节点模式练习，应先用本节的单机最小配置验证功能，等测试环境资源充足或进入预生产阶段再验证 3 节点的高可用能力。

### 4.2 单容器资源基线（单机模式参考值；3节点模式下为每个副本 Pod 的资源配置）

| 服务 | CPU | 内存 | 说明 |
|---|---|---|---|
| PostgreSQL(+pgvector) | 2 vCPU | 4GB | 随数据量增长需扩容磁盘 |
| OpenSearch（单节点） | 2 vCPU | 8GB | 官方建议 JVM heap ≥4GB |
| OpenMetadata server | 2 vCPU | 4GB | |
| OpenMetadata ingestion（内置 Airflow） | 2 vCPU | 4GB | |
| Presidio analyzer | 1 vCPU | 2GB | |
| Presidio anonymizer | 0.5 vCPU | 1GB | |
| OPA | 0.5 vCPU | 512MB | Go 二进制，极轻量 |
| Ollama（CPU 推理 7B 量化模型） | 4~8 vCPU | 8~16GB | 有 GPU 则显著提速，AI 场景吞吐更稳定 |
| Governance Agent | 2 vCPU | 4GB | |
| Kafka（KRaft 单节点） | 2 vCPU | 4GB | JVM heap ~2GB + 页缓存 |
| Flink（JobManager+TaskManager，单节点 standalone，内置 Flink CDC） | 2~4 vCPU | 4~8GB | 规则/窗口越多需要越多内存；Checkpoint 存储**按形态拆分**（ADR-A4）：单机=本地数据盘+每日出机备份，3 节点=共享对象存储（SeaweedFS）+ rocksdb 增量 Checkpoint；已包含 CDC 连接器，无需额外 Kafka Connect 进程 |
| PgBouncer | 0.25 vCPU | 256MB | |
| Traefik | 0.5 vCPU | 256MB | |
| Prometheus + Alertmanager + Exporters | 1.5 vCPU | 3GB | |
| Apache Superset | 1 vCPU | 2GB | |
| 治理门户（Streamlit） | 0.5 vCPU | 512MB | |

> 在 3 节点高可用模式下，上表数值即为 CloudNativePG/Strimzi/OpenSearch Operator/Flink Operator 为每个副本 Pod 分配的资源请求（每节点各跑一份）；无状态服务（OpenMetadata server、Presidio、OPA、Governance Agent、Superset 等）按同表数值在 3 节点间调度分布，无需每节点都部署一份全量。

### 4.3 性能优化清单

- **连接池**：所有服务经 PgBouncer 访问 PostgreSQL，避免连接数暴涨拖垮数据库。
- **指标层解耦（ADR-A6）**：治理指标（资产覆盖率、质量趋势、告警趋势）由 Governance Agent 每小时 ETL 至独立 `gov_metrics` schema（自持表结构），Superset 只读该 schema，不直连 OpenMetadata 内部表（OM 不承诺内部 schema 稳定）。
- **索引与分区**：高频查询字段建索引；分区仅适用于 gov_metrics/审批记录等**自研表**（OpenMetadata 自带表不可手工分区，ADR-A6）；OpenSearch 合理规划 shard 数量（单 shard 建议控制在 10~50GB）。
- **Kafka 分区规划**：Topic 分区数与 Flink 消费并行度（Task Slot 数）对齐，避免分区过多导致 Kafka 元数据开销增大，或分区过少导致消费瓶颈。
- **缓存热点元数据**：高频访问的资产目录/血缘查询结果可加一层 Valkey 缓存（触发式引入，见2.2）。
- **AI 推理批处理**：Ollama 请求做并发限流与批处理，避免大模型推理拖慢实时告警链路；对延迟敏感的实时规则检测不依赖 LLM，仅用确定性规则。
- **分页与增量 API**：Governance Agent 对外 API 全部分页返回，避免全量拉取拖垮元数据库。

### 4.4 可靠性设计（3节点高可用场景）

3 节点通过 k3s + Operator 实现真正的多副本高可用，可**容忍任意 1 个节点故障**而不中断服务；但仍需清楚记录残余风险，而非假装故障不存在：

- **故障容忍边界**：etcd（k3s 控制面）、Kafka（KRaft quorum）、CloudNativePG 均基于"多数派"机制，3 副本最多容忍 1 个节点同时故障；若 2 个节点同时故障，集群会丧失写入能力（这是 3 节点拓扑的固有上限，需要更高可用性时应扩容到 5 节点）。
- **自动故障转移**：
  - PostgreSQL：CloudNativePG 检测到主库所在节点故障后，自动提升一个从库为新主库；
  - OpenSearch：OpenSearch Operator 自动重新选主、重新分配分片副本；
  - Kafka：Strimzi 管理的 broker 故障后，分区 leader 自动切换到其他副本 broker；
  - Flink：JobManager 故障后由 Flink Kubernetes Operator 触发重新调度，作业从最近 Checkpoint 恢复；
  - 无状态服务（OpenMetadata server、Presidio、OPA、Governance Agent、Superset）由 K8s 自动将 Pod 重新调度到健康节点。
- **数据备份**（多副本不能替代备份，仍需独立留存离线副本）：
  - PostgreSQL：CloudNativePG 内置 WAL 归档 + Barman Cloud 定期全量备份，支持时间点恢复（PITR）；
  - OpenSearch：定期 snapshot 到独立对象存储；
  - Kafka：核心 Topic 配置合理保留时长，Schema/Topic 定义纳入 IaC 版本管理；
  - Flink：Checkpoint/Savepoint 目录纳入定期备份。
- **配置即代码**：k3s 安装脚本、Helm values、Kafka Topic 定义、OPA 策略、OpenMetadata 连接器配置全部纳入 Git 版本管理，实现"重建而非修复"的快速恢复能力。
- **资源隔离**：Pod 级 CPU/内存 requests/limits，配合 PodDisruptionBudget 防止单一组件（如 Ollama 推理峰值）挤占其他核心服务资源，也避免节点维护时一次性驱逐过多副本导致服务中断。
- **优雅降级**：实时链路故障时离线批处理兜底（见3.4）；OPA 策略引擎不可用时默认拒绝高风险操作而非放行（fail-closed，而非 fail-open）。
- **入口单点的消除**：MetalLB 为 Traefik Ingress 提供跨节点漂移的虚拟 IP，避免"入口所在节点故障=整个平台不可访问"。
- **升级路径**：超出 3 节点容量上限后，扩容为 5 节点+（提高 Kafka/OpenSearch 副本数、拆分专用节点池），作为既定预案而非事后现想。

### 4.5 监控告警与 SLO 示例

| 监控项 | 采集方式 | 建议 SLO |
|---|---|---|
| 元数据同步延迟（离线） | Airflow/OpenMetadata ingestion 任务耗时 | 单次全量同步 < 调度周期的 50% |
| 实时管道端到端延迟 | Kafka consumer lag + 自定义埋点 | P95 < 30 秒 |
| 数据质量检测覆盖率 | OpenMetadata 质量测试执行数/资产总数 | 核心资产覆盖率 > 90% |
| 系统资源水位 | node_exporter/cAdvisor | CPU < 80%、内存 < 85% 持续告警 |
| 备份成功率 | 备份脚本退出码 + Prometheus pushgateway | 100%（失败即时告警） |
| Kafka 消费滞后 | kafka_exporter | lag 持续增长 5 分钟以上告警 |

### 4.6 灾备

- 建议 RPO（可容忍数据丢失） ≤ 1 小时（依赖 WAL 归档频率），RTO（恢复时间目标） ≤ 4 小时（依赖备份介质读取速度与重建自动化程度）。
- 每季度至少执行一次真实的备份恢复演练，而非只验证备份任务"跑成功"。

---

## 5. 落地场景

| 场景 | 通道 | 触发条件 | 核心流程 | 产出/价值 | 涉及组件 |
|---|---|---|---|---|---|
| A. 全量数据资产盘点与敏感数据发现 | 离线 | 新数据源接入 / 周期性全量扫描 | Ingestion 抽取 schema+样本 → Presidio 批量扫描 → **按置信度分层**（高置信自动打标签+建议脱敏策略，中置信通过 OpenMetadata 原生 Tasks/Request-for-Tags 工作流生成人工复核任务，低置信仅记录不打扰人工）→ 写入资产目录+PII 标签 → OPA 生成访问策略建议 | 资产目录覆盖率、敏感字段清单、脱敏策略建议；复核复用 OpenMetadata 自带界面，无需新建 UI | OpenMetadata、Airflow、Presidio、OPA |
| B. 实时交易/业务数据质量监控与异常告警 | 实时 | 业务库产生变更 | Flink CDC 直连捕获变更 → Flink 流式规则引擎评分（空值率突增/范围越界/行数环比骤降）→ 命中阈值**仅推送 `governance.alerts`，不进质量看板**（增量窗口统计的是变更行特征，不等价于全表指标；全表权威指标以快照/离线为准，ADR-A5）→ Governance Agent 消费告警做 AI 补充解释 → 严重异常触发 OPA 自动降级策略 | 分钟级异常发现、异常告警可追溯回放；质量看板只展示权威快照指标，不混入增量窗口数据 | Kafka、Flink（内置 CDC）、Governance Agent、Alertmanager |
| C. 变更影响分析与血缘可视化 | 离线+实时融合 | 表结构变更评审 / 实时管道拓扑变化 | OpenMetadata 聚合离线 sqllineage 血缘 + **作业图级**血缘边（作业启动/变更时上报），形成统一血缘图 → 变更前查询下游依赖 → 输出影响面报告 → CI/CD **先提示**（PR 评论列影响面），连续 4 周达标后升级为**阻断**（ADR-A7）；sqllineage 解析失败的 SQL 进"待人工确认"队列，禁止静默丢弃。**作业图级血缘由 Governance Agent 自研上报（v3.3 修正**：原表述为"Flink OpenLineage 集成"，实测不可行，见 ADR-A7） | 变更影响面报告、上线前风险预警，减少"改字段炸下游"事故；血缘准确率有验收口径（Top 50 核心表列级抽检 ≥90%） | OpenMetadata（唯一血缘权威源）、sqllineage、Flink、Governance Agent |
| D. 元数据智能问答与数据资产检索 | 离线索引+实时问答 | 业务/分析人员自然语言提问 | 用户在**治理门户（Streamlit 聊天界面）**提问 → 元数据描述/血缘摘要经嵌入模型入 pgvector（离线定期刷新）→ RAG 检索 → 结合检索结果调用 Ollama 生成回答 → **程序化校验回答中引用的表/字段名必须在 OpenMetadata 中真实存在**，否则标注"未核实"或拒绝返回 → 门户展示回答与引用来源 | 秒级问答响应、降低数据发现门槛、消解引用幻觉风险 | pgvector、Ollama、LangGraph、Governance Agent、治理门户、OpenMetadata |
| E. 敏感数据访问审批与合规脱敏（决策面/执行面/审计面三面分离，ADR-A3） | 决策面（OPA）+ 执行面（查询引擎配置）+ 审计面（异步） | 用户/应用发起数据访问请求 / 周期性合规审计 | **决策面 PDP**：OPA 依据 OpenMetadata PII 标签 + Rego 策略（Git 管理）评估"谁在什么条件下可访问哪些列"；**审批**：申请 → OPA 预评估 → 审批人决策（企业 IM 卡片/治理门户审批台）→ 授权（带时效，到期自动回收）→ 审计落库（PostgreSQL 独立表）；**执行面 PEP**：数据所在查询引擎的原生能力完成脱敏与拒绝——PG 视图+RLS、Doris/StarRocks 列级权限、Trino 列级 mask（**配置工作，非新组件**）；**审计面**：Governance Agent 异步汇聚访问/决策日志至 OpenSearch，不阻塞查询；Presidio 仅做离线批量 PII 识别，**移出在线路径** | **POC 期能力边界（需合规方书面确认）**：只做事后审计 + 主动告警，不做事前拦截——事前拦截的前提是业务侧统一经由受管查询引擎访问数据，属组织变革而非组件问题 | OPA、OpenMetadata（PII 标签）、查询引擎（PG/Doris/StarRocks/Trino 原生能力）、Governance Agent、治理门户、OpenSearch |
| F. 湖仓一体全链路治理（触发式） | 离线+实时融合 | 企业存在/规划建设 Iceberg/Delta/Hudi 湖仓架构 | 批量 ETL 与 Flink 实时 Sink 双通道写入湖仓表 → Trino/Doris/StarRocks 查询引擎挂载表 → OpenMetadata 经查询引擎连接器（Iceberg/Hudi）或原生连接器（Delta Lake）采集 schema/分区/血缘 → Presidio+DuckDB 对湖仓表抽样做 PII 扫描 → OPA 按标签生成访问策略，执行点落在查询引擎列级权限/脱敏视图 | 湖仓表纳入统一资产目录与血缘图，避免"湖仓成为治理盲区"；离线建仓与实时入湖共享同一质量/血缘产出 | Airflow、Flink、Iceberg/Delta/Hudi、Trino（或 Doris/StarRocks）、OpenMetadata、Presidio、DuckDB、OPA |

> 以上场景产出的资产覆盖率、质量趋势、告警趋势等指标统一沉淀在 PostgreSQL 中，由 Apache Superset 提供跨场景 BI 看板，作为管理层/治理团队的常态化监控入口。

---

## 6. 三节点高可用部署与资源规划

### 6.1 部署方式选择

| 维度 | 单机 Docker Compose（默认推荐，起步路径） | 3 节点 k3s（规模化后可选） |
|---|---|---|
| 适用场景 | 默认起步：POC、验证业务价值、规模尚未超出 4.1.2 上限 | 验证通过且需要真正高可用的生产/多业务线场景 |
| 故障容忍 | 无容错，单点故障 | 容忍任意 1 节点故障，自动故障转移 |
| 运维复杂度 | 低，`docker compose up` 即可 | 较高，需要 K8s/Helm 基础 |
| 硬件投入 | 1 台服务器 | 3 台服务器 |

选择依据：**默认从单机 Docker Compose 起步**，先验证组件本身的使用与业务价值，避免同时应对"新平台"和"新编排范式"两类学习曲线；当规模超出 4.1.2 单机容量上限、或业务确实需要容忍单节点故障的高可用能力时，再升级到 6.2 的 3 节点 k3s 方案。3 节点恰好是 etcd/Kafka KRaft/OpenSearch 等法定人数（quorum）机制的最小值，升级时能天然获得"防单点故障"的收益。

### 6.2 三节点 k3s 高可用部署（规模化后的扩展路径）

#### 6.2.1 节点角色与拓扑

3 台服务器均为 k3s server（control-plane）+ agent（worker）一体的"堆叠"（stacked）拓扑，内置 etcd 3 副本，可容忍 1 节点故障不丢失集群控制面。

| 组件 | Kubernetes 管理方式 | 副本策略 |
|---|---|---|
| PostgreSQL + pgvector | CloudNativePG Operator | 1 主 + 2 从，自动故障转移，复用 K8s API 协调，无需额外部署 etcd |
| OpenSearch | OpenSearch Kubernetes Operator | 3 节点集群，每节点兼 master-eligible + data 角色 |
| Kafka（KRaft） | Strimzi Operator | 3 broker+controller 合一节点，副本因子 3 |
| Flink | Flink Kubernetes Operator | JobManager HA 用 K8s 原生 ConfigMap 租约选举（无需 ZooKeeper），TaskManager 按需多副本 |
| OpenMetadata server / Presidio / OPA / Governance Agent / Superset | 官方 Helm Chart 或普通 Deployment | ≥2 副本，K8s Service 负载均衡 |
| 治理门户（Streamlit） | 自研 Deployment | 1~2 副本，无状态，与 Governance Agent 同生命周期 |
| OpenMetadata ingestion（Airflow scheduler） | 官方 Helm Chart | 单副本（调度器天然单例），节点故障由 K8s 自动重新调度到健康节点 |
| Ollama | Deployment + nodeAffinity | 固定在资源最充足（或有 GPU）的节点 |

#### 6.2.2 存储与网络

- **持久化存储**：CloudNativePG/Strimzi/OpenSearch Operator 均在应用层自行做多副本复制，不需要再引入 Ceph/Longhorn 等分布式存储层；直接使用 k3s 内置的 local-path-provisioner（基于各节点本地磁盘的动态卷）即可，减少额外组件。
- **入口与负载均衡**：k3s 默认内置 Traefik 作为 Ingress Controller（与原方案网关选型一致，无需更换）；额外部署 MetalLB 为 Traefik 提供跨节点漂移的虚拟 IP，避免入口本身成为单点。
- **监控**：改用 kube-prometheus-stack Helm Chart 统一安装 Prometheus + Alertmanager + 各 Exporter，比手工拼装容器更贴合 K8s 环境。

#### 6.2.3 关键安装步骤（示意，正式实施前需按官方文档核对版本）

```bash
# 1. 三节点安装 k3s（第一台 init，其余 join 同一集群）
curl -sfL https://get.k3s.io | sh -s - server --cluster-init
# 其余两台
curl -sfL https://get.k3s.io | K3S_URL=https://<node-1>:6443 K3S_TOKEN=<token> sh -s - server

# 2. 安装 Operator / Helm Chart（均为各项目官方仓库，具体版本号需按官方文档确认）
helm install cnpg cloudnative-pg/cloudnative-pg
helm install strimzi-kafka-operator strimzi/strimzi-kafka-operator
helm install opensearch-operator opensearch-project/opensearch-operator
helm install flink-kubernetes-operator flink-operator-repo/flink-kubernetes-operator
helm install metallb metallb/metallb
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack
helm install openmetadata open-metadata/openmetadata
helm install superset superset/superset
```

### 6.3 单机 Docker Compose 部署（默认推荐，起步与验证路径）

- 统一使用 Docker Compose 编排；OpenMetadata 部分直接采用官方维护的 quickstart docker-compose 作为治理内核，在其基础上补充实时链路与自研组件，避免重复造轮子、避免因手写配置与官方版本脱节。
- Traefik 作为统一入口，做反向代理、TLS 终止、基础限流。
- 无自动故障转移，容器所在主机故障即导致对应能力不可用；验证通过且需要高可用时，参考 6.2 升级到三节点方案。

#### 6.3.1 补充服务参考配置（示意，正式上线前需按官方文档校验参数）

```yaml
services:
  kafka:
    image: apache/kafka:latest
    environment:
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_NODE_ID: 1
      KAFKA_CONTROLLER_QUORUM_VOTERS: "1@kafka:9093"
      KAFKA_LISTENERS: "PLAINTEXT://:9092,CONTROLLER://:9093"
      KAFKA_ADVERTISED_LISTENERS: "PLAINTEXT://kafka:9092"
    deploy:
      resources:
        limits: { cpus: "2", memory: 4G }

  # 注：不再需要独立的 kafka-connect/debezium 服务，CDC 能力已改由 Flink CDC 连接器内置在 flink-jobmanager/flink-taskmanager 镜像中（需在自建镜像中将 flink-sql-connector-mysql-cdc 等 JAR 放入 /opt/flink/lib）

  flink-jobmanager:
    image: flink:1.19-scala_2.12
    command: jobmanager
    environment:
      FLINK_PROPERTIES: |
        jobmanager.rpc.address: flink-jobmanager
    deploy:
      resources:
        limits: { cpus: "1", memory: 2G }

  flink-taskmanager:
    image: flink:1.19-scala_2.12
    command: taskmanager
    depends_on: [flink-jobmanager]
    environment:
      FLINK_PROPERTIES: |
        jobmanager.rpc.address: flink-jobmanager
        taskmanager.numberOfTaskSlots: 4
    deploy:
      resources:
        limits: { cpus: "3", memory: 6G }

  pgbouncer:
    image: bitnami/pgbouncer:latest
    depends_on: [postgres]
    deploy:
      resources:
        limits: { cpus: "0.25", memory: 256M }

  governance-agent:
    build: ./governance-agent
    depends_on: [kafka, postgres, opa]
    deploy:
      resources:
        limits: { cpus: "2", memory: 4G }

  governance-portal:
    build: ./governance-portal
    depends_on: [governance-agent]
    environment:
      GOVERNANCE_API_URL: http://governance-agent:8080
    deploy:
      resources:
        limits: { cpus: "0.5", memory: 512M }

  traefik:
    image: traefik:v3.0
    ports: ["443:443"]
    deploy:
      resources:
        limits: { cpus: "0.5", memory: 256M }

  prometheus:
    image: prom/prometheus:latest
    deploy:
      resources:
        limits: { cpus: "1", memory: 2G }

  alertmanager:
    image: prom/alertmanager:latest
    deploy:
      resources:
        limits: { cpus: "0.25", memory: 256M }

  superset:
    image: apache/superset:latest
    depends_on: [postgres]
    environment:
      SUPERSET_SECRET_KEY: "change-me-in-real-deployment"
    command: >
      /bin/sh -c "superset db upgrade &&
      superset init &&
      gunicorn -w 2 -b 0.0.0.0:8088 'superset.app:create_app()'"
    deploy:
      resources:
        limits: { cpus: "1", memory: 2G }
```

---

## 7. 实施路线图（结果/门禁驱动，非日历驱动）

| 阶段 | 目标 | 准出门槛 |
|---|---|---|
| Phase 0：基础设施与安全底座 | 默认单机 Docker Compose 部署（PostgreSQL+pgvector、OpenSearch、Traefik）、基础监控上线；若直接从 3 节点 k3s 起步（见6.1 选择依据），则改为安装 k3s 集群 + CloudNativePG/Strimzi/OpenSearch Operator + MetalLB | 所有容器/集群节点均健康；备份脚本验证可恢复 |
| Phase 1：离线治理闭环 | OpenMetadata+Airflow 接入 1~2 个核心业务源、Presidio 批量扫描、OPA 基础策略、血缘解析、Superset 基础治理看板上线 | 试点数据源资产目录覆盖率 100%；敏感字段识别人工抽检准确率达标（如 >90%） |
| Phase 2：实时链路引入 | Flink CDC+Kafka 接入 1 条核心实时业务流（流式规则引擎+作业图级血缘事件，无需独立部署 Kafka Connect；源库续接窗口签约定与复制槽安全保护为前置门禁，见 ADR-A2）、Governance Agent 告警增强、告警打通 | 端到端延迟 P95 < 30 秒；CDC 断流恢复（≤续接窗口 1/3）与重快照演练通过；PG 复制槽保护验证通过；连续运行 1 周无未恢复故障 |
| Phase 3：AI 能力增强 | Ollama+嵌入模型、LangGraph RAG 问答、引用校验机制 | 问答准确率与引用可核验率抽样达标；人工复核吞吐量在置信度分层后可控 |
| Phase 4：性能加固与生产化 | 压测校准第4章容量估算值、备份恢复演练、按需引入其余可选组件（Grafana/Valkey/GX 等）；**若规模超出单机上限或需要高可用，按 6.2 升级为 3 节点 k3s** | 完成一次真实备份恢复演练；压测结果达到容量规划目标；如升级 3 节点，需额外验证自动故障转移生效 |
| Phase 5（按需触发）：湖仓一体扩展 | 企业存在/规划湖仓架构时启动：接入 Iceberg/Delta/Hudi 表格式、部署 Trino 或复用已有 Doris/StarRocks、OpenMetadata 湖仓连接器接入、Flink 作业新增湖仓 Sink | 湖仓核心表纳入资产目录与血缘图；PII 扫描覆盖湖仓表 |

---

## 8. 风险与待验证事项

- **3节点仍有上限**：最多容忍 1 个节点同时故障，2 个节点同时故障会丧失 quorum 导致写入不可用；需明确 RTO/RPO 并预先演练扩容到 5 节点的路径，不能假设 3 节点可以无限容错。
- **K8s 运维技能门槛**：相比单机 Docker Compose，3 节点 k3s + 多个 Operator 的运维复杂度显著提高，团队需具备基础的 Kubernetes/Helm 排障能力，建议先在 6.3 单机模式练习组件本身的使用，再迁移到 6.2 的三节点模式，避免同时应对"新平台"和"新编排范式"两类学习曲线。
- **CDC 对源库的负载**：Flink CDC 读取 binlog/WAL 会对生产库产生一定额外负载，接入前建议与 DBA 评估影响。
- **Flink JobManager 故障切换有短暂中断**：K8s 原生选举机制下故障切换非零延迟（通常数十秒级），期间实时处理会短暂中断，需依赖 Checkpoint 恢复和离线批处理兜底（见3.4）；State 随规则/窗口增多而增长，需监控内存与 Checkpoint 耗时。
- **Superset 与 OpenMetadata 共用 PostgreSQL 的资源争用**：BI 查询较重时可能影响元数据库性能，建议至少用独立 schema 隔离，规模变大后可评估拆分独立数据库实例。
- **Presidio 中文场景覆盖不足**：官方内置识别器以英文/西方地区格式为主，中国身份证号、大陆手机号、银行卡号、统一社会信用代码等需基于 Presidio 的 `PatternRecognizer` 机制自行补充规则，不能假设开箱即用。
- **Grafana 的 AGPL 合规判断**：若平台未来对外提供多租户 SaaS 服务，需法务复核，参考 Dify 多租户条款的同类处理原则。
- **容量规划数值为经验估算**：并非实测基准，正式承诺 SLA 前必须结合真实数据量与流量做压测校准。
- **Ollama 本地模型能力上限**：复杂问答/推理场景的准确率弱于云端大模型，需实测验证是否满足业务准确率要求，必要时保留云端大模型作为降级/对比选项（不作为默认依赖）。
- **湖仓元数据采集依赖查询引擎中转**：OpenMetadata 对 Iceberg/Hudi 无原生连接器，需经 Trino/Doris/StarRocks 间接采集，若该查询引擎不可用会连带影响湖仓元数据/血缘的更新时效。
- **对象存储访问凭证是新的攻击面**：SeaweedFS/云 OSS 的访问密钥需纳入统一的密钥管理与最小权限原则，避免凭证泄露导致湖仓数据大范围暴露（OWASP 访问控制/敏感数据暴露相关风险）。
- **小文件与 Compaction 运维负担**：Iceberg/Hudi/Delta 频繁小批量写入会产生大量小文件，需要定期 Compaction/Vacuum 作业，否则查询性能和存储成本会逐步劣化，需纳入运维计划而非默认假设"写入即完事"。

---

## 9. 架构决策记录（ADR，v3.2 合入自 v3.1.1）

> 以下 8 条 ADR 在组件清单零新增零替换的前提下，调整链路职责、执行点、容量口径与验收标准。组件纪律的唯一例外：**SeaweedFS 从"触发式可选"提升为"3 节点形态必需"**（用于 Checkpoint/备份的共享对象存储，2.2 清单内）。

### 9.0 总览

| ADR | 决策 | 解决的问题 | 代价 |
|---|---|---|---|
| A1 | 实时链路保留 Flink CDC 直连，内置"两段式"切换开关 | CDC 直连的缓冲/重放/多订阅降级 | 两段式启用时多一跳延迟（<1s 级）与一个 Topic 运维 |
| A2 | CDC 可靠性四件套：续接窗口签约定（按源库分列）+ 复制槽安全保护（PG）+ 作业自动拉起 + 重快照预案 | Flink 停机超续接窗口即断流；PG 复制槽在停机期阻止 WAL 回收，会撑爆源库磁盘 | 占用源库 binlog/WAL 磁盘；需要 DBA 配合 |
| A3 | 场景 E 重构为决策面/执行面/审计面分离 | 无执行点、"毫秒级动态脱敏"虚标 | POC 期不做事前拦截，需合规方确认边界 |
| A4 | Checkpoint 存储按形态拆分：单机本地盘+出机；3 节点共享对象存储 | Checkpoint 本地盘 ⇄ HA 恢复矛盾 | 3 节点形态引入 SeaweedFS（清单内） |
| A5 | 质量口径拆分：快照基线=权威指标，增量窗口=异常告警 | "变更行空值率"被当全表指标 | 实时看板不再展示"质量分" |
| A6 | 指标层独立 `gov_metrics` schema，Superset 不直连 OM 内库 | OM schema 不稳定，升级打挂大盘 | Governance Agent 增加 ETL（约 3~5 人天） |
| A7 | 血缘定准确率验收 + CI/CD 先提示后阻断 | 错误血缘误导变更影响分析 | 阻断能力延后约 4 周 |
| A8 | 容量按正确口径重算（分区级副本 + 保留期联动） | 大型档磁盘算不平 | 无（只是把数算对） |

### 9.1 ADR-A1 保留直连，内置两段式开关

**Context**：实时链路改用 Flink CDC 直连，收益真实（少一个组件、位点与 Checkpoint 统一、快照+增量一体、湖仓路径更短），但丢失了 Kafka 的缓冲、重放与多下游订阅能力。

**Decision**：默认直连（路线 A）；出现以下任一信号时切换两段式（路线 B），切换只加 Topic，不引入新组件：

| 切换信号 | 判定方式 |
|---|---|
| 存在其他 CDC 消费方（数仓/实时大屏想复用） | 平台组确认 |
| binlog/WAL 续接窗口无法满足恢复预算且 DBA 不让加 | 签约定失败 |
| 源库峰值变更率使作业频繁背压 | Flink 背压指标持续告警 |

**两段式做法**：`Flink CDC 捕获 → 写 Kafka Topic（cdc.<source>）→ 处理作业从 Kafka 消费`。Kafka 已在组件清单内，仅新增 Topic。

**Consequences**：直连时恢复预算与源库续接窗口（MySQL binlog / PG 复制槽 WAL）强绑定（见 A2）；两段式多一跳延迟与 Topic 运维，但换回重放与多订阅。

### 9.2 ADR-A2 CDC 可靠性四件套（直连形态的必备配套）

| # | 措施 | 具体要求 |
|---|---|---|
| 1 | **续接窗口签约定**（按源库类型分列） | **MySQL**：binlog 保留期 ≥ 3 × 最大允许停机时长，签约默认值 ≥48h（例：允许停机 16h → 3×16h = 48h，覆盖"周五晚故障 + 周末无人值守"场景）；由 `binlog_expire_logs_seconds` 控制，与 DBA 书面约定并纳入源库运维手册；监控"binlog 最老位点与当前时间差"，达保留期 70% 告警。**PostgreSQL**：续接能力由复制槽保有的 WAL 量决定——`wal_keep_size` ≥ 3 × 停机预算内的 WAL 增量，同样书面签约定 |
| 2 | **复制槽安全保护**（PG 源库必备，MySQL 无此项） | Flink CDC（Debezium 引擎）在 PG 上使用逻辑复制槽，**作业停机期间 slot 持续阻止 WAL 回收，会撑爆源库磁盘**（源库生产事故，比断流严重一个量级）。三道防线：① 源库设 `max_slot_wal_keep_size` 兜底上限（超限 slot 自动失效，宁可断流重快照、不撑爆磁盘）；② slot retained WAL（`pg_replication_slots` 的 `restart_lsn` 与当前 WAL 差值）纳入**最高优先级告警**，阈值为兜底值的 70%；③ 运维纪律：作业下线或长期停用，必须先删除对应 slot（`pg_drop_replication_slot`），写入作业下线 checklist 与 runbook |
| 3 | **作业自动拉起** | 单机：`restart: unless-stopped` + healthcheck；3 节点：Flink Operator 的 restart 策略；作业运行状态、Checkpoint 成功率、binlog/slot 水位为最高优先级告警 |
| 4 | **重快照预案** | 低峰执行、限制 snapshot chunk 大小与连接数、跳过非关键表；PG 场景重快照前先删除失效/旧 slot；预案演练纳入月度恢复演练 |

### 9.3 ADR-A3 场景 E 重构（决策面 / 执行面 / 审计面分离）

**Context**：若把 Governance Agent 放在数据访问路径上做动态脱敏，但业务系统走 JDBC 直连数据库，不会经过它；Presidio 是批量文本库，进在线路径撑不起毫秒级逐行脱敏。

**Decision**（组件全部不变，变的是职责位置）：

| 面 | 组件（原有） | 职责 |
|---|---|---|
| 决策面 PDP | OPA + OpenMetadata PII 标签 | 输出"谁在什么条件下可访问哪些列"；Rego 策略 Git 管理 |
| 执行面 PEP | **数据所在引擎的原生能力**：PG 视图+RLS、Doris/StarRocks 列级权限、Trino 列级 mask | 在 SQL 引擎内完成脱敏与拒绝。这是**配置工作，不是新组件** |
| 审计面 | Governance Agent + OpenSearch | 异步汇聚访问/决策日志，不阻塞查询 |
| 识别面 | Presidio | 仅离线批量 PII 识别与脱敏输出，**移出在线路径** |

**POC 期能力边界（需合规方书面确认）**：只做事后审计 + 主动告警，**不做事前拦截**。事前拦截的前提是业务侧统一经由受管查询引擎访问数据——那是组织变革，不是本平台的组件问题。

**审批最小状态机**（保留人工审批时）：`申请 → OPA 预评估 → 审批人决策（IM 卡片/门户）→ 授权（带时效）→ 到期自动回收 → 审计落库`。状态落 PostgreSQL 独立表；"到期自动回收"由 Governance Agent 定时任务执行 revoke（执行点在查询引擎侧，回收动作同样在引擎侧执行），纳入 runbook。

### 9.4 ADR-A4 Checkpoint 存储按形态拆分

| 形态 | 设计 | 边界（写进运维手册） |
|---|---|---|
| 单机（默认） | 本地数据盘 + 每日出机备份 | 不存在跨节点恢复问题 |
| 3 节点（扩展） | **必须**共享对象存储：SeaweedFS（2.2 清单内，提升为该形态必需），`state.backend: rocksdb` + 增量 Checkpoint + Savepoint 升级前必做 | 若坚持本地盘：JobManager/TaskManager 一旦漂移即读不到 checkpoint，恢复能力为零，此时不得宣称 Flink HA |

**3 节点形态随附的另外三件事**：k3s API Server 配 VIP（kube-vip/keepalived）；引入 MetalLB 前禁用内置 Klipper；文档明示"local-path PV 带节点亲和，状态 Pod 不可漂移，可用性由副本提升保证"。

### 9.5 ADR-A5 质量口径拆分（快照基线 vs 增量窗口）

| 产出 | 来源 | 归属 | 展示位置 |
|---|---|---|---|
| **权威质量指标**（全表空值率、唯一值、分布） | Flink CDC **快照阶段** + 离线 Profiler | OpenMetadata 质量测试结果 | 质量看板、审计报告 |
| **异常告警**（窗口内空值率突增、越界、行数骤降） | Flink CDC **增量阶段** | `governance.alerts` → 告警 | 告警通道，**不进质量看板** |

两类结果在 OM 中以不同的 test definition 类型区分，避免口径混用。**口径声明**：增量窗口统计的是**变更行**特征，不等价于全表指标，全表指标以快照/离线为准。

### 9.6 ADR-A6 指标层解耦与 ADR-A7 血缘验收

**A6 指标层**：Governance Agent 每小时把资产覆盖率、质量趋势、告警趋势抽取到 `gov_metrics` schema（自持表结构）；Superset 只读该 schema。OM 升级不再影响大盘；看板需标注"数据截至"时间戳（每小时 ETL，最长延迟 1h）。分区仅适用于 gov_metrics/审计等自研表，OM 自带表不可手工分区。

**A7 血缘验收**：

| 项 | 标准 |
|---|---|
| 表述 | 血缘事件为"作业图级血缘（作业启动/变更时上报）"，非事件级 |
| **上报机制（v3.3 修正）** | **由 Governance Agent 自研上报**：解析 Flink SQL 作业定义（`CREATE TABLE … WITH` + `INSERT INTO … SELECT … FROM`）得到 source→sink 边，经 OpenMetadata 血缘 API 写入（`LineageDetails.source=PipelineLineage` + 作业 SQL 原文）。**不再使用 OpenLineage**——理由（实测）：① Flink 1.x 的 OpenLineage 集成基于 `JobListener`，**不支持 Flink SQL** 且须改作业代码 + Application Mode；② Flink 2.x 的 OpenLineage SPI（FLIP-314）**在 Flink 1.20.1 中不存在**（`flink-dist` 实测 0 命中）；③ 即使升级 Flink 2.x，**`flink-cdc` 未实现 `LineageVertexProvider`**（源码树含 lineage 路径 = 0），CDC 源仍拿不到血缘。**准确性约束（不放松）**：解析或表名→FQN 映射不确定时**拒绝上报**（宁缺勿错），映射必须显式给出 |
| 准确率 | 试点 Top 50 核心表列级血缘人工抽检 ≥90%；sqllineage 解析失败的 SQL 进"待人工确认"队列，禁止静默丢弃 |
| CI/CD 接入 | 先做"提示"（PR 评论列影响面），连续 4 周达标后升级"阻断" |

### 9.7 ADR-A8 容量重算（修正口径 + 公式）

**正确口径**：PostgreSQL 每实例为全量拷贝；Kafka/OpenSearch 副本为**分区级**，RF=3、3 节点时每节点 ≈ 唯一数据量。

```
每节点磁盘 = PG全量 × 1.3 + 日增 × Kafka保留天数 + OpenSearch索引(通常<100GB)
           + Checkpoint保留量 + 系统盘(480GB) + 20% 余量
```

**大型档（500GB/天）两种采购选择**：

| 方案 | Kafka 保留期 | 每节点磁盘 | 说明 |
|---|---|---|---|
| 短保留 | 1~3 天 | 4TB | 配合离线兜底校准，**预算紧时推荐** |
| 长保留 | 7 天 | 8TB | 需要更长回放窗口时 |

> 单机档不受此影响（Kafka 只承接日志与告警，量级小）。

---

## 10. 安全基线与治理运营模型

**安全基线（9 条）**：① 密钥入统一管理（KMS/Vault 或加密 Secret）；② k3s 启用 secrets 加密；③ 网络隔离（单机 Compose 三网 / 3 节点 NetworkPolicy）；④ 仅暴露 443；⑤ 镜像固定 digest + 上线前扫描（含自建 Flink CDC 镜像）；⑥ K8s RBAC 最小权限；⑦ PII 样本只留脱敏特征、原文 ≤30 天加密留存；⑧ 平台自身操作审计落库出机；⑨ 月度补丁窗口。

**治理运营模型**：数据 Owner 认领资产与复核（SLA：中置信任务 3 个工作日）；月度 PII 口径会；误报标记闭环驱动阈值调优；月度恢复演练（含 CDC 重快照与复制槽删除演练）。

**上线 90 天三问**：周活多少人、识别准确率是否稳在验收线上、告警是否被真正处置。

---

## 11. 验收标准汇总

| 验收项 | 标准 | 阶段 |
|---|---|---|
| CDC 断流恢复 | 停机 ≤ 续接窗口 1/3 内恢复，无重新快照 | Phase 2 |
| PG 复制槽保护 | `max_slot_wal_keep_size` 生效验证 + slot 水位告警触发验证 + 作业下线删 slot 演练 | Phase 2 |
| 重快照演练 | 大表限速重快照完成且源库负载在约定阈值内 | Phase 2 |
| 场景 E 边界确认 | 合规方书面确认"POC 期事后审计"定位 | Phase 1 前 |
| 质量口径 | 质量看板无"增量窗口"数据混入 | Phase 2 |
| 血缘准确率 | Top 50 核心表列级抽检 ≥90% | Phase 1 |
| 指标层 | Superset 全部查询落在 gov_metrics，无 OM 内表直查 | Phase 1 |
| 3 节点升级门禁 | ADR-A4 三件事 + 磁盘公式落地 + 恢复演练通过 | Phase 4 |

---

## 附：修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v3.0 | 2026-09-14 | 默认路径改回单机优先；实时链路改用 Flink CDC 直连（初版全文备份：《AI数据治理平台设计方案_v3.0_初版.md》） |
| v3.1.0 | 2026-09-15 | 架构调整版（独立过程稿）：8 条 ADR + 6 处表述修正清单 + 安全基线/运营模型 + 验收标准汇总 |
| v3.1.1 | 2026-09-15 | ADR-A2 修复评审 P1-1/P1-2：三件套→四件套（公式示例自洽、PG 复制槽三道防线、全文口径统一为"续接窗口"） |
| v3.2 | 2026-09-15 | **合入 v3.1.1 全部内容**：8 条 ADR 入第 9 章；正文 11 处修正（第 1 章架构图替换、2.1 Kafka 行、2.2 SeaweedFS 行、3.1 表实时行、3.3.3/3.3.5、4.1.1 容量口径、4.1.2 小型档、4.2 Flink 行、4.3 索引分区+指标层、场景 B/C/E 重写、第 7 章 Phase 2 行）；新增第 10 章安全基线与运营模型、第 11 章验收标准汇总；组件零新增零替换。过程稿全文见《AI数据治理平台_评审与调整过程记录.md》 |
| v3.3 | 2026-09-19 | **ADR-A7 上报机制修正（不改决策，只改实现路径）**：作业图级血缘的**上报机制由"官方 OpenLineage Flink 集成"改为"Governance Agent 自研上报"**。**动因（均为实测）**：① Flink 1.x 的 OpenLineage 集成基于 `JobListener`、**不支持 Flink SQL**、须改作业代码 + Application Mode；② Flink 2.x 的 OpenLineage SPI（FLIP-314）**在 Flink 1.20.1 中不存在**（`flink-dist` 实测 0 命中）；③ 即使升级 Flink 2.x，**`flink-cdc` 未实现 `LineageVertexProvider`**（源码树含 lineage 路径 = 0），CDC 源仍无血缘。**修正范围**：§9.6 ADR-A7（新增"上报机制"行）、**§1 总体架构**中 Flink 一行、**§3.3 实时流式链路**、**场景 C** 表述；**ADR-A7 的决策要点不变**（仍为"作业图级、作业启动/变更时上报、准确率 Top 50 ≥90% 验收、CI/CD 先提示后阻断、唯一真理源 OpenMetadata），**组件清单仍零新增零替换**。实现与证据见《执行步骤/AI数据治理平台_单机版_S3-5血缘上报D1记录.md》与《…S3前置部署记录.md》§12.7~§12.9。**《实施执行计划 v2.0》无需同步**（其 A7 相关表述未指定上报机制）。**遗留（明确声明）**：`执行步骤/` 下各文档抬头仍写"由《设计方案 v3.2》拆解"——本次只改 ADR-A7 上报机制、不改任何可部署内容，故**不做机械式扫改**；待各文档下次实质修订时顺带更新引用版本号 |
