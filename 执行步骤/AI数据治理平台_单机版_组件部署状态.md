# AI 数据治理平台 · 单机版 组件部署状态（一页归档）

> **文档版本**：v1.0（2026-09-19）
> **用途**：一页说明"**该部署的组件是否都已部署**"，供准出材料引用与交接核对。
> **结论先行**：**设计方案组件清单里的组件全部在跑，无缺口** —— 本环境 **19 个容器全部 healthy**（＝本环境口径的满额），宿主侧 4 个组件全部 active。
> **唯一"该有还没有"的组件是 `oauth2-proxy`（S2-3 SSO 的执行组件）**，卡在**企业 IdP 回调域名 / 客户端 ID** 上（见 §3）。
> **口径**：本页数字取自 2026-09-19 实机（命令与输出见 §4）；**组件版本号以 compose 固定 digest 为准，不臆造**。

---

## 1. 设计组件 → 实际部署（按段）

| 段 | 设计组件 | 实际落点 | 镜像 / 版本 | 状态 |
|---|---|---|---|---|
| **S1 底座** | PostgreSQL + pgvector | `ai-governance-poc-postgres-1` | `pgvector/pgvector:pg16`（实拉 **PG 16.15**） | ✅ healthy |
| S1 | **PgBouncer**（硬约束② 唯一入口） | `…-pgbouncer-1` | `bitnami/pgbouncer:latest`（实拉 **1.24.1**、transaction、`max_prepared_statements=200`） | ✅ healthy |
| S1 | OpenSearch（OM 存储/检索） | `…-opensearch-1` | `opensearchproject/opensearch:latest`（实拉 2.19.x） | ✅ healthy |
| S1 | Traefik（反代/TLS） | `…-traefik-1` | `traefik:v3.0` | ✅ healthy（**443 已停止对外发布**） |
| S1 | Prometheus | `…-prometheus-1` | `prom/prometheus:latest`（digest 固定；`retention 15d`、`--web.enable-lifecycle`） | ✅ healthy |
| S1 | Alertmanager | `…-alertmanager-1` | `prom/alertmanager:latest`（digest 固定；receiver → **GA 落库**） | ✅ healthy |
| S1 | cAdvisor | `…-cadvisor-1` | `zcube/cadvisor:latest`（**社区镜像**，gcr.io 不可达） | ✅ healthy |
| **S2 离线治理** | OpenMetadata **server**（元数据唯一真理源） | `openmetadata_server` | `openmetadata/server:2.0.1` | ✅ healthy |
| S2 | OpenMetadata **ingestion**（内置 Airflow） | `openmetadata_ingestion` | `openmetadata/ingestion:2.0.1`（**Airflow 3.3.1**） | ✅ healthy |
| S2 | OPA（策略即代码） | `…-opa-1` | `openpolicyagent/opa:latest`（digest 固定） | ✅ healthy |
| S2 | Presidio analyzer | `…-presidio-analyzer-1` | `mcr.microsoft.com/presidio-analyzer:latest` | ✅ healthy |
| S2 | Presidio anonymizer | `…-presidio-anonymizer-1` | `mcr.microsoft.com/presidio-anonymizer:latest` | ✅ healthy |
| S2 | Apache Superset（看板） | `…-superset-1` | **自建** `ai-governance/superset:6.1.0-pg`（`…@sha256:0b42de36db7d…`） | ✅ healthy |
| S2 | 治理门户（自研 Streamlit） | `…-governance-portal-1` | **自建** `ai-governance/governance-portal:0.1.0`（`…@sha256:a8ac7d049c7d…`，2026-09-20 因**「需隧道」改徽标（v1.7）**重建） | ✅ healthy |
| **S3 实时链路** | Apache Kafka（KRaft 单节点） | `…-kafka-1` | `apache/kafka:4.0.0` | ✅ healthy |
| S3 | Apache Flink **JobManager** | `…-flink-jobmanager-1` | **自建** `ai-governance/flink:1.20.1-cdc3.6.0`（`…@sha256:5681153a3742…`；内置 **CDC 3.6.0** MySQL/PG + Kafka 连接器） | ✅ healthy |
| S3 | Apache Flink **TaskManager** | `…-flink-taskmanager-1` | 同上（`slots=2`） | ✅ healthy |
| **S4 AI 能力** | Ollama（本地推理） | `…-ollama-1` | `ollama/ollama@sha256:da6e0dc5…`（**0.34.2**；`qwen2.5:1.5b/3b` + `nomic-embed-text`） | ✅ healthy |
| **自研核心** | **Governance Agent**（唯一自研组件） | `…-governance-agent-1` | **自建** `ai-governance/governance-agent:0.1.0`（`…@sha256:44ca36b1e519…`，2026-09-19 最新一次因**RAG 上下文预算定档 1800** 重建） | ✅ healthy |

**容器合计 19 / 19 healthy**（bootstrap 一次性任务 `openmetadata_migrate_all` 已退出且不常驻，属正常）。

---

## 2. 宿主侧组件（不容器化，按 S1 设计）

| 组件 | 作用 | 状态 |
|---|---|---|
| `node_exporter` | 宿主指标（`172.17.0.1:9100`，仅 docker0 网关可达） | ✅ active |
| `postgres_exporter` | PG 复制槽水位（`172.17.0.1:9187`，经 PgBouncer 采集） | ✅ active |
| `kafka_exporter` | Kafka 消费滞后（`172.17.0.1:9308`；broker 走回环 `127.0.0.1:9094` EXTERNAL） | ✅ active |
| `fail2ban` | SSH 防爆破 | ✅ active |

---

## 3. 未部署的组件（三类，均有明确理由）

| # | 未部署 | 为什么 | 性质 |
|---|---|---|---|
| 1 | **触发式组件**：Valkey、NebulaGraph、Milvus、SeaweedFS（3 节点形态必需）、Apache Iceberg、Trino（单节点） | 设计方案规定**默认不启用**，仅在触发条件命中时引入，且**不得改变核心链路** | ✅ 符合纪律，**不应部署** |
| 2 | **三节点 M-Prod 组件**：k3s、MetalLB、kube-vip/keepalived、CloudNativePG、OpenSearch Operator、Flink Operator | 生产形态**触发式启动**，当前阶段不在范围内 | ✅ 未到阶段 |
| 3 | **`oauth2-proxy`**（S2-3 SSO 的执行组件） | 需要**企业 IdP 回调域名 / 客户端 ID**（P0-A 申请）；且 443 已收敛为隧道，SSO 入口方案需一并重划 | ⏳ **唯一"该有还没有"的组件** |

> 另：**Oracle CDC 连接器**明确不纳入（商业插件 + 授权评估）；**OpenLineage jar** 已确认在 Flink 1.20.1 + Flink SQL 下不可行（`flink-cdc` 未实现 FLIP-314 血缘接口），**改由 GA 自研上报**（ADR-A7 已回写，设计方案 v3.3）。

---

## 4. 运行面证据（2026-09-19 实机）

```
容器总数 = 19     healthy = 19
宿主侧   = node_exporter active / postgres_exporter active / kafka_exporter active / fail2ban active
告警规则 = 40     （flink-jobs 5 + job-freshness 10 + **ollama-runtime 7** + **container-runtime 6** + poc-slo 4 + postgres-replication-slot 5 + kafka-lag 3）
抓取目标 = 7/7 up （cadvisor / flink×2 / kafka / node / postgres / prometheus）
cron     = ai-governance-backup / -gov-metrics / -lineage / -alert-digest / **-ollama** / **-containers**（另有系统自带 e2scrub_all）
新鲜度指标 = 6    （backup_pg / backup_opensearch / backup_config / etl_gov_metrics / etl_rag / lineage_reconcile）
Ollama 运行面 = 探针每分钟（/api/ps → 独立 textfile 文件）
容器运行面   = 通用探针每分钟（cgroup v2，全容器 CPU/内存/OOM + 容器名标签 → 独立 textfile 文件）
RAG 预算口径 = 1,800 tokens（人工定档：1.5b + 压预算；num_ctx=4096；实测冷启动首 token 6.93s）
周期报告 = watermark-2026-W38 / capacity-baseline / restore-annual-drill / alert-digest
对外监听 = 仅 22（0.0.0.0）；三个 exporter 在 docker0 网关 172.17.0.1；其余全部回环
镜像与磁盘 = 2026-09-19 回收后：镜像 34.26 GB → **29.01 GB**、构建缓存 2.93 GB → **1.554 GB**（合计释放≈6.63 GB）；
             `/` 已用 44 GB → **38 GB**、可用 51 GB → **57 GB**（仍远高于 20 GiB 加盘阈值）
```

> ⚠️ **cAdvisor：已确认在本环境无法修复（外部阻塞），容器级能力已由自采替代（2026-09-19）**
> **现象**：容器 `healthy`、抓取目标 `up`，但 `/api/v1/label/id/values` 仅 `["/","1","ubuntu"]` —— **逐容器指标一条都没有**（"目标 up ≠ 有数据"）。
> **根因**：Docker **29.8.1** 使用 **containerd 镜像存储**（`io.containerd.snapshotter.v1`），`layerdb/mounts/*/mount-id` 不存在（计数 0）、`GraphDriver` 为 **null**，而现役 cAdvisor 为 **v0.45.0（2022-09-23）**，日志持续报 `failed to identify the read-write layer ID`。
> **修复尝试（三条路全部实测不通）**：① 升镜像 —— 各镜像源被策略拦/不可达，可用的 `docker.1panel.live` 上 cadvisor 最高只到 **v0.45.0**；② `--docker_only=false` —— 序列从 1 增到 55 但 **name 标签全空**（只有 cgroup 路径）⇒ 无法按容器名告警；③ v0.45 **不支持** containerd 集成（挂 sock + `--containerd-namespace=moby` 无效）。
> **处置**：cAdvisor **保留部署但不可用于容器级监控**（属外部阻塞，非本项目配置错误）；容器级 CPU/内存/OOM 由新增 **`container_probe.sh`**（cgroup v2，每分钟，独立 textfile 文件）自采，并配 `container-runtime` 组 **6 条规则**。
> **复核触发点**：镜像源放开或能拿到 **≥ v0.5x** 的 cAdvisor 时，重新评估是否切回（见 §5 第 7 条）。

> ⚠️ **OM 采集任务（OM → Airflow）实测发现（2026-09-19）**：链路可用但**必须显式"部署"**——
> 只创建不部署不会生成 DAG（触发会报 `Dag id … not found in DagModel`）；**运行状态未回写 OM**（`pipelineStatuses=0`、`/pipelineStatus` 404 ⇒ OM UI 看不到本次运行结果，但任务执行成功）。
> 详见《…单机版_测试入口与用例.md》附录与 v1.4。

**容器数口径说明（与设计方案 4.2 的差异）**：设计方案 4.2 表共 **16 行**，其中「Prometheus + Alertmanager + Exporters」一行含 3 个、「Flink」一行含 2 个 ⇒ 实际 **18** 容器（Exporters 作宿主二进制）；**本环境把 cAdvisor 也容器化 ⇒ 19**（已在《部署步骤_单机版》0.3 声明，**不得**用于对外表述平台容器数）。

---

## 5. 本页的复核触发条件（命中任一即需更新）

1. 任一组件**升级 / 换镜像 / 重建**（含基础镜像 digest 变更）；
2. **新增组件**（触发式组件启用，须先改设计方案的组件表与 ADR）；
3. **S2-3 SSO 落地**（`oauth2-proxy` 从"未部署"转为"已部署"）；
4. 容器数口径变化（如 Exporters 改为容器化）；
5. 进入 **M-Prod（三节点）** 阶段。
6. **告警规则数 / 采集器 / cron 变化**（本页 §4 的运行面证据须同步）；
7. **cAdvisor 修复或替换**（§4 的告警说明须同步删除/改写）——**触发条件：镜像源放开或出现 ≥ v0.5x 的可用 cAdvisor 镜像**；
8. **镜像/构建缓存回收或磁盘水位变化**（§4 的"镜像与磁盘"行须同步）。

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-19 | 首次产出：把"该部署的组件是否都已部署"整理为一页归档 —— 19 个容器（全部 healthy）逐项对照设计组件、宿主侧 4 个组件、**未部署三类（触发式 / M-Prod / oauth2-proxy）**、运行面证据（27 规则 / 7 目标 / 6 新鲜度指标 / 4 份周期报告 / 暴露面仅 22）、容器数口径说明（16 行 → 18 vs 本环境 19）与复核触发条件。**结论：组件层面无缺口；唯一"该有还没有"是 oauth2-proxy（缺 IdP 信息）** |
| v1.1 | 2026-09-19 | **运行面证据随 C/B 两项同步**：告警规则 **27 → 34**（新增 `ollama-runtime` 7 条）、新增 **Ollama 运行面探针**（每分钟，cgroup v2 + `/api/ps` → 独立 textfile 文件）与独立 cron `ai-governance-ollama`、GA 镜像 digest **`1e64eb59…` → `2c8b39e9…`**（因 RAG 上下文预算与 Ollama 增强模板重建；重扫 HIGH+CRITICAL = 0）；**新增 cAdvisor 缺陷说明**（only-root-cgroup，根因 containerd 镜像存储 vs v0.45.0，"目标 up ≠ 有数据"），复核触发条件由 5 条增至 **7 条**。组件清单本身与容器数口径未变 |
| v1.2 | 2026-09-19 | **① cAdvisor 结论定稿 + 能力替代**：三条修复路径实测均不通（镜像源被拦/不可达、可用镜像站上 cadvisor 最高 v0.45.0；`--docker_only=false` 有序列但 **name 全空**；v0.45 的 containerd 集成无效）⇒ 记为**外部阻塞**，容器级指标改由新增 **`container_probe.sh`**（cgroup v2、全容器、带 `container`/`name` 标签、独立 textfile 文件 + 独立 cron `ai-governance-containers`）自采。**② 规则 34 → 40**（新增 `container-runtime` 6 条，含 OOM critical 与"序列数不足"护栏），**OOM 真实触发验证通过**（64MB 一次性容器 → firing 落库 → 清理 → resolved 零残留）。**③ GA 镜像 digest `2c8b39e9…` → `394700abe6ed…`**（补容器告警 ADVICE 模板；重扫 HIGH+CRITICAL = 0）。**④ 新增镜像/磁盘回收记录**：镜像 34.26→29.01 GB、构建缓存 2.93→1.554 GB（合计**释放≈6.63 GB**），`/` 已用 44→**38 GB**、可用 **57 GB**。复核触发条件 7 → **8 条** |
| v1.3 | 2026-09-19 | **S4-1/S4-2 定档落地**（人工决定"1.5b + 压预算"）：GA 镜像 digest **`394700abe6ed…` → `44ca36b1e519…`**（`rag.py` 预算默认值 3000 → **1800**，与 compose 同口径；重扫 HIGH+CRITICAL = 0）；§4 运行面证据新增 **RAG 预算口径 = 1,800 tokens** 一行；compose 校验和 **`62aeeef899d2bede…`**。依据：1.5b 在 1,819 tokens 下冷启动首 token **6.93 s**（对 10 s 门禁留 31% 余量），3,000 tokens 下 11.31 s 越线、3b 17~21 s 不通过。**未改架构、组件清单与门禁判据** |
| v1.4 | 2026-09-19 | **新增 OM → Airflow 采集链路的实测告警说明**（在 cAdvisor 说明之后）：① **必须显式"部署"**才生成 DAG（只创建不部署时触发报 `Dag id … not found in DagModel`）；② **运行状态未回写 OM**（`pipelineStatuses=0`、`/pipelineStatus` 404，OM UI 看不到运行结果，但任务实际执行成功）；③ 容器内 `airflow dags list-runs` 报 `Database migration required`（仅影响 CLI 查历史）。详见《…单机版_测试入口与用例.md》附录（v1.4）。**未改架构、组件清单与门禁判据** |
| v1.5 | 2026-09-20 | **治理门户视觉重构上线（v1.5）**：门户镜像 **`e1fe07781a23…` → `650a57b42570…`**（747 MB，内含 `theme.py` / `design/` / `tools/` / `.streamlit/config.toml`）；compose 校验和 **`d030657ae80c5bd1…`**；**重扫 HIGH/CRITICAL = 0**；容器 healthy、`/_stcore/health` 200、**5 个深链全 200**（`home/chat/metrics/audit/status`）。设计系统自带的 4 个校验脚本在生产容器内实跑：**令牌/WCAG 通过、mockup 结构 42/42、解析 26 项、AppTest 降级 62/62 与就绪 57/57**。IA 由"首页三区块"改为**5 页导航 + 首页入口网格**。§2 宿主侧、§3 未部署三类与容器数口径（19）**均未变** |
| v1.6 | 2026-09-20 | **门户再重建（入口卡隐藏 URL）**：镜像 **`650a57b42570…` → `ba381aa10dad…`**；compose 校验和 **`ca2d8c791bce6391…`**；**重扫 HIGH/CRITICAL = 0**；容器 healthy、5 深链全 200；回归 **降级 63/63 / 就绪 58/58**（新增"入口卡可见文案不含 URL"断言）。§2/§3/容器数口径未变 |
| v1.7 | 2026-09-20 | **门户再重建（「需隧道」改徽标）**：镜像 **`ba381aa10dad…` → `a8ac7d049c7d…`**；compose 校验和 **`bc24dd9171aff05e…`**；**重扫 HIGH/CRITICAL = 0**（`--skip-db-update`，秒级）；容器 healthy、5 深链全 200；回归 **降级 65/65 / 就绪 60/60**（新增"徽标呈现 + 不得出现在 hint 文案"2 项断言）。§2/§3/容器数口径未变 |

---

*本成果由 AI 生成，仅供决策参考；组件版本与 digest 以 `/data/ai-governance/docker-compose.yml` 为准，执行前请按各组件官方文档核对。*
