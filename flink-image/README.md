# flink-image/ · Flink 派生镜像（内置 Flink CDC）

> **依据**：AGENTS §7（自建镜像固定基础 tag/digest，不用 latest，构建后记录 digest）；《部署步骤_单机版》S3-3。
> **部署落点**：服务器 `/data/ai-governance/flink-image/`，构建为 `ai-governance/flink:1.20.1-cdc3.6.0`。

## 为什么需要派生镜像

官方 `flink` 镜像**不含** Flink CDC 连接器与 Kafka 连接器，而 ADR-A1 要求 **Flink CDC 直连源库**（不部署 Kafka Connect），
日志类事件又要经 Kafka 接入 ⇒ 两类连接器必须进 `/opt/flink/lib`。派生镜像把"基础镜像 digest + 连接器坐标 + sha256"三件事固定下来，避免每次手工塞包。

## 版本矩阵（已实测可用，2026-09-18）

| 组件 | 版本 | 说明 |
|---|---|---|
| Flink | **1.20.1**（scala_2.12 / java17） | 基础镜像按 **digest** 固定：`flink@sha256:d51a693da76581f2483d3f83d5997d3226b076a9f990a7f7d1eed144434b8e1e` |
| Flink CDC | **3.6.0-1.20** | MySQL / PostgreSQL SQL 连接器（shaded fat jar，自带全部依赖） |
| flink-connector-kafka | **3.4.0-1.20** | 瘦包，**必须**同时装 `flink-connector-base` |
| flink-connector-base | **1.20.1** | Kafka 连接器的运行依赖 |

> 版本取自 Maven Central 实际可用坐标（后缀 `-1.20` 表示对应 Flink 1.20 线），非臆造；替换版本须同步 `jars.sha256`。

## 未纳入（明确声明）

| 项 | 原因 | 何时处理 |
|---|---|---|
| **Oracle CDC 连接器** | 需商业插件与授权评估（设计方案 3.3） | 接入 Oracle 源前另行确认 |
| **OpenLineage 上报 JAR** | 属 S3-5 作业开发范围；**预研结论见下节** | 按官方兼容矩阵选定版本后加入（ADR-A7 作业图级血缘） |

## OpenLineage 接入预研（2026-09-18，A4）

**目的**：为 S3-5 的"作业图级血缘上报"（ADR-A7）消掉未知量——**先确认两端**（Flink 侧 JAR、OpenMetadata 侧接收端），再决定版本。

| 项 | 结论 | 依据 |
|---|---|---|
| 制品坐标 | **`io.openlineage:openlineage-flink`**（Maven Central 可用） | 实查 `maven-metadata.xml` |
| 可用版本 | 最新 **1.53.0**；`lastUpdated=2026-09-01`（活跃维护） | 同上 |
| **版本号含义** | 是 **OpenLineage 自己的版本线**，**不是 Flink 版本号** ⇒ **不能凭版本号推断与 Flink 1.20.1 的兼容性** | 同上 |
| JAR 形态 | 33.6 MB / 5122 classes，**不含 Flink 类**（Flink 由集群提供）；MANIFEST 声明 **`Premain-Class` / `Agent-Class`** ⇒ 设计上走 **javaagent** 接入 | 实下 jar 统计 + MANIFEST |
| **接入方式：javaagent 实测失败（重要更正）** | ❌ **1.53.0 用 `-javaagent` 挂载会直接让 JVM 启动失败**：`NoSuchMethodException: …OpenLineageFlinkJobListener.premain(...)` + `FATAL ERROR … processing of -javaagent failed`（实测 JM/TM 双双 unhealthy，**已回滚**）。静态核查 **1.20.5 / 1.21.1 / 1.24.2 的 listener 类同样不含 `premain`/`agentmain`** ⇒ 非版本偶发；且 `io.openlineage` 组下**无独立 java-agent 制品** | 2026-09-18 实测（证据与 sha256 见 `/data/ai-governance/experiments/openlineage/README.md`） |
| **官方兼容矩阵 / 正确接入方式** | ⚠️ **未取到**：文档站（openlineage.io）为 JS 渲染，静态抓取 HTTP 200 但无正文 ⇒ **须人工在浏览器查看官方 Flink 集成页**核定"正确接入方式 + 版本矩阵" | 抓取结果 |
| **OpenMetadata 接收端** | ✅ **已实测可用**：`POST /api/v1/openlineage/lineage`，**需认证**（无 token → 401，Bearer JWT → 通过），**需两个 query 参数 `producer` 与 `schemaURL`**（缺则 400 且报明"query param producer must not be null, query param schemaURL must not be null"）；按正确形态提交最小事件 → **HTTP 200 `{"status":"success","message":"Event processed, no lineage edges created",...}`** | 2026-09-18 实测（探针见《…S3前置部署记录.md》§12.7） |

**接入步骤（**须等官方接入方式核对后**执行；不要照"直接进 lib / 挂 javaagent"做——已实测失败）**：

1. **先确认正确接入方式**（javaagent 挂哪一类、还是以库方式在作业里注册 listener）与**版本矩阵**（人工查官方页）；
2. 按核定方式在 `Dockerfile` 追加 `io.openlineage:openlineage-flink:<核定版本>` 的下载 + **sha256 校验**（与现有 5 个 JAR 同一套纪律）；
3. 重建镜像 → 记录**新 image id** → 重跑"JM/TM healthy + 作业提交 + Checkpoint"三项验收；
4. transport 指向 `http://openmetadata-server:8585/api/v1/openlineage/lineage`（**已实测**：需认证；需 `producer`/`schemaURL` 参数；合规事件 → 200）；
5. **javaagent 类改动一律先在隔离环境验证**（本轮教训：直接挂到在跑的集群上导致 JM/TM 同时 unhealthy）。

**待确认**：① 与 Flink 1.20.1 的官方兼容结论；② OpenLineage→OM 的**长期凭据形态**（当前探测用 Bearer JWT；长期方案建议按官方文档确认 API Key/服务账号做法，避免 token 轮换导致上报中断）。

## 构建

```bash
# 服务器侧（构建时从 Maven Central 取 JAR，并按 jars.sha256 校验）
cd /data/ai-governance/flink-image
docker build -t ai-governance/flink:1.20.1-cdc3.6.0 .

# 构建后记录 digest / image id（**必做**，回写到《…S3前置部署记录.md》）
docker image inspect ai-governance/flink:1.20.1-cdc3.6.0 --format '{{.Id}} {{.Created}}'
```

## 纪律

- **改基础 tag 或连接器版本 = 组件版本变更**：须同步《AI数据治理平台_单机版_S3前置部署记录.md》与《部署步骤_单机版》S3-3，并重跑验收（JobManager 起来 + TaskManager 注册 + JAR 在 `/opt/flink/lib` + 版本与本文一致）。
- **JAR 走 Maven Central 并验 sha256**：`jars.sha256` 入 Git；换版本必须同步更新该文件（构建期 `sha256sum -c` 失败属预期保护）。
- 基础镜像固定 digest，**不用 `latest`**；上线前按供应链要求扫描镜像（固定 digest + 扫描记录）。
- 单机形态**不得宣称 Flink HA**。

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；版本与坐标执行前请按各组件官方文档核对。
