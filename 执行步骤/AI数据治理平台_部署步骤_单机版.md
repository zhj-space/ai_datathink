# AI 数据治理平台 · 单机版部署步骤

> **依据**：《实施执行计划 v2.0-r1》P0-B / Phase 1~4 +《设计方案 v3.2》4.1.2（单机中型档）、4.2（单容器资源基线）、4.3（优化清单）、6.3（单机 Compose）、第 9 章 ADR、第 11 章验收标准。
> **范围**：**仅单机版（POC 单机）**，即 S1 底座 → S2 离线 → S3 实时 → S4 AI/试运行。三节点 M-Prod 不在本文范围。
> **形态**：1 台 24 vCPU / 96GB / 2TB NVMe，Docker Compose 编排，**18 个容器**（执行计划称「约 17」，差异已更正，见 0.1 注）。
> **周期**：P0-B 2~3 周 + Phase 1 3~4 周 + Phase 2 3~4 周 + Phase 3+4 5~7 周 ≈ **11~14 周**。
> **口径纪律**：所有数字回溯自上述源文档；命令中版本号一律 `需按官方文档核对`（设计方案 6.2.3 已声明）；源文档不含的信息标 `待确认`，不编造。

---

## 0. 单机版总览

### 0.1 四段链路与容器展开

```
S1 单机底座 ──▶ S2 离线治理 ──▶ S3 实时链路 ──▶ S4 AI 与试运行
   P0-B 2~3周      Phase1 3~4周    Phase2 3~4周     Phase3+4 5~7周
   6 容器          13 容器         17 容器          18 容器
```

| 段 | 对应阶段 | 新增容器 | 累计 | 准出门禁 |
|---|---|---|---|---|
| S1 | P0-B | PG+pgvector、PgBouncer、OpenSearch、Traefik、Prometheus、Alertmanager | 6 | 全部 healthy；备份可恢复（**RPO≤1h**/RTO≤4h，DBA 签字）；`SHOW POOLS` 无 waiting |
| S2 | Phase 1 | OM server、OM ingestion、Presidio×2、OPA、Superset、门户 | 13 | 覆盖率 100%；PII >90%；血缘 Top50 ≥90%；Superset 零查 OM 内表；**合规文件归档** |
| S3 | Phase 2 | Kafka、Flink JM、Flink TM、Governance Agent | 17 | P95<30s；断流恢复≤续接窗口 1/3；PG slot 三项验证；质量看板无增量数据；连续 1 周无未恢复故障 |
| S4 | Phase 3+4 | Ollama | 18 | 问答准确率抽样达标；CI/CD 提示在 ≥1 仓库生效；备份恢复演练完成；M-Prod 评审有书面结论 |

> ### ⚠️ 容器数口径更正（重要，与执行计划存在差异）
>
> 执行计划 §1 写「**约 17 容器**」，但按设计方案 4.2「单容器资源基线」逐行核算，单机全量实际为 **18 个容器**。差异逐项核对如下：
>
> | 计数 | 明细 | 数 |
> |---|---|---|
> | 4.2 表格条目 | 共 16 行 | 16 条服务条目 |
> | 其中 Flink 一行含 2 个容器 | JobManager + TaskManager | +1 |
> | 其中「Prometheus + Alertmanager + Exporters」一行含 3 个 | Prometheus、Alertmanager、Exporters（node_exporter/cAdvisor） | +2 |
> | **实际容器总数** | | **19**（若 Exporters 按宿主进程而非容器计 → **18**） |
>
> - 执行计划的「约 17」是**约数**（原文用「约」），未展开逐容器核算。
> - 本文按「**每段在上一段基础上新增**」口径逐段累加，S4 达 **18 个**（Exporters 作为宿主二进制不计入容器）。
> - **口径声明**：容器数以本节逐段清单为准；若与执行计划「约 17」有出入，以 4.2 基线展开为准，并建议回报执行计划修订。
> - 内存校验不受影响：18 容器 limits 之和 **52.75~60.75GB ≤ 96GB×70%≈67.2GB** ✅

### 0.1.1 逐段容器清单（18 个，可直接核对）

| 段 | 容器 | 个数 |
|---|---|---|
| S1 | postgres、pgbouncer、opensearch、traefik、prometheus、alertmanager | 6 |
| S2 | openmetadata-server、openmetadata-ingestion、presidio-analyzer、presidio-anonymizer、opa、superset、governance-portal | 7 |
| S3 | kafka、flink-jobmanager、flink-taskmanager、governance-agent | 4 |
| S4 | ollama | 1 |
| | **合计** | **18** |

> 另需宿主侧或容器侧提供 Exporters（node_exporter / cAdvisor）——若容器化则总容器数为 19。

### 0.2 五条硬约束（违反即缺陷）

| # | 约束 | 来源 |
|---|---|---|
| 1 | Compose 是 POC 期**唯一部署真相源**；不得引入任何 K8s 配置「为将来做准备」 | 执行计划 §8 |
| 2 | 所有服务经 PgBouncer 访问 PG，**不允许直连 5432** | 设计方案 4.3 |
| 3 | 全部容器 `restart: unless-stopped` + healthcheck | 执行计划 P0-B-1 |
| 4 | S3 接生产源库前，合规边界文件 + 续接窗口签约定必须已归档 | 执行计划 Phase 1/2 门禁 |
| 5 | 遇 `unhealthy` **不自动降级、不自行改架构**，记录停止转人工 | 执行计划 T-M0-1 |

---

### 0.3 本环境实测配置与降级声明（2026-09-16 SSH 实测）

> **结论先行**：本环境**不满足设计方案 4.1.2「中型档」**（无 2TB 数据盘），也**低于 4.1.3「最小测试环境规格」的磁盘门槛**（要求 200GB / 300GB，本机 99GB）。
> 经人工决策，本环境按「**最小测试环境 + 磁盘降级变体**」使用：**仅用于功能验证，不用于压测，不带入生产**（对应 4.1.3 原文"不能用于压测、不能带入生产"）。

| 项 | 实测值 | 设计方案目标 | 判定 |
|---|---|---|---|
| 主机 | `<POC服务器主机名>`（阿里云 ECS） | — | — |
| 操作系统 | Ubuntu 26.04.1 LTS（kernel 7.0.0-30-generic） | Ubuntu 22.04 LTS 或 Rocky Linux 9 | ⚠️ 高于文档基线，组件兼容矩阵需按官方文档核对后写入部署基线 |
| CPU | 24 vCPU（AMD EPYC，1 socket × 12 core × 2 thread） | 24 vCPU（4.1.2 中型档） | ✅ |
| 内存 | 92 GiB（标称 96GB），无 swap | 96GB | ✅ |
| 磁盘 | `nvme0n1` **100G 单盘**，`/` 99G（可用 91G） | 系统盘 480G + 数据盘 2TB NVMe | ❌ |
| `/data` | 根文件系统上的**普通目录**（非独立数据盘） | 独立数据盘挂载点 | ❌ 降级 |
| Docker / Compose | 未安装（本文件 S1-2 安装） | S1-2 安装 | 符合进度 |
| GPU | 无 | 可选 | 记录 |
| 网络监听 | 仅 22/443 对外监听；**22 已由人工限源 IP**（2026-09-17） | S1-6 后仅暴露 443 | 待办：密钥登录（安全组限源已完成） |

**三条降级声明（须人工确认并归档）**：

1. **门禁①（服务器验收 fio / 容量）在本环境不适用**——无数据盘，`fio 顺序写 ≥1GB/s`、`4K 随机读 ≥100k IOPS` 无验收对象。
   免测放行属**人工专属决策**（AGENTS §11：Agent 不得自行修改门禁标准）；本环境据此仅按"功能验证"定位使用，**验收结论不得引用本环境数据**。
2. **磁盘低于 4.1.3 门槛**：本机 99G < 离线档 200GB / 全链路 300GB。必须执行 0.3.1 的保留期与堆内存收紧；
   磁盘水位纳入最高优先级告警，**达 80% 记录、停止、转人工**（硬约束 5：不自动降级）。
3. **容器数口径**：本环境为 **19 个容器**（cAdvisor 容器化；node_exporter / postgres_exporter 为宿主二进制）。
   与「单机全量 18 容器」口径的差异在此声明，**不得**用于对外表述平台容器数。

> 📋 **组件部署状态（一页归档）**：见《AI数据治理平台_单机版_组件部署状态.md》——19 个容器逐项对照设计组件、宿主侧 4 个组件、**未部署三类**（触发式 / M-Prod / `oauth2-proxy`）与运行面证据。**结论：组件层面无缺口；唯一"该有还没有"是 oauth2-proxy（缺 IdP 回调域名/客户端 ID）**。

#### 0.3.1 本环境参数调整（相对 4.2 单容器资源基线）

| 项 | 默认（4.2 基线） | 本环境取值 | 依据 / 影响 |
|---|---|---|---|
| OpenSearch heap | 4g | **1g** | 4.1.3「手动调小 JVM heap（如 `-Xms1g -Xmx1g`）」；索引规模受限 |
| Kafka heap | 2g | **512m** | 4.1.3「`KAFKA_HEAP_OPTS=-Xms512m -Xmx512m`」 |
| Flink task slots | 4 | **2** | 4.1.3「调到 1~2，只验证规则逻辑是否跑通，不代表真实吞吐能力」 |
| Ollama 模型 | 7B 量化 | **Qwen2.5 1.5b / 3b 量化**（**2026-09-18 实测更正**：Ollama 实际磁盘占用 **~12.2 GB**——官方镜像 **9.19 GB** + 模型 **3.0 GB**，原估 ~2 GB 只算了模型且偏小） | 4.1.3「仅适合功能联调，不适合验证准确率」→ **问答准确率不得在本环境判读** |
| Superset / Prometheus | 部署 | 保留（收紧保留期） | 分别对应 S2-6 看板门禁与 S1-7 采集门禁 |
| Prometheus retention | 未规定 | **15d** | 磁盘受限（`--storage.tsdb.retention.time=15d`） |
| Docker 日志 | 未规定 | **json-file，max-size 10m × 3** | 防止日志写满 99G 系统盘 |
| Flink Checkpoint 出机 | 每日 | 保留，但**不出机时仅留本地 2 份** | 磁盘受限；出机介质待确认 |

**磁盘预算（工程经验估算，实施前需实测校准，不作 SLA）**：镜像 ~15GB｜OpenSearch 索引 ~5GB｜PG ~5GB｜Kafka ~2GB｜
Flink Checkpoint ~2GB｜Prometheus ~3GB｜Ollama 模型 ~2GB｜系统与缓冲 ~10GB ≈ **44GB / 可用 91GB**。

#### 0.3.2 本环境的门禁与待确认项差异

| 项 | 默认口径 | 本环境状态 |
|---|---|---|
| 门禁① 服务器验收（fio / 容量） | 拒收重提采购 | **人工豁免**（无数据盘），豁免结论需书面归档 |
| 门禁② 备份可恢复 | **RPO ≤1h** / RTO ≤4h | **口径已定**：2026-09-17 人工决策取设计方案 4.6 的 ≤1h（执行计划已回填 v2.0-r2）；按**每小时**逻辑备份/快照实现，**不引入 WAL 归档**。实现与演练见《AI数据治理平台_单机版_S1-8备份恢复脚本.md》 |
| 门禁③ T-M0-1 | 不自动降级 | 不变 |
| 门禁④ 合规边界 + 续接窗口 | 未签不动生产库 | 不变 |
| 数据盘设备名（附 C 第 4 项） | 服务器到货后 `lsblk` 确认 | **不适用**（无数据盘） |
| 容器数 | 18 | 本环境 19（见降级声明 3） |
| 主机安全 | 仅暴露 443 | **2026-09-17 实测**：公网仅 22/443 可达（9100/9090/9200/6432 均不可达）→ 安全组已实质承担过滤，宿主 `ufw` inactive（**无第二道防线**）；**22 已由人工限源 IP**，近 24h 成功登录仅本机来源（84 次）、非本机成功登录 **0**、未发现入侵；24h `Failed password` **37** 次（截至 09-16 22:35，之后无）。**2026-09-18 已闭环（人工放行）**：公钥登录 + `PasswordAuthentication no` + `PermitRootLogin prohibit-password`（实测密钥可用、口令被拒）、root 口令轮换、`fail2ban` 启用；**node_exporter 已收紧为 `172.17.0.1:9100`**；**443 不再对外发布（宿主无 443 监听）**——OM 改走回环 8585 + SSH 隧道（S1-6 注）。**⇒ 当前对外仅 22（且已限源）；无任何对外业务端口**。详见《…S1部署记录.md》§3.7/§3.7.5/§5（第 5、6 项）与《…S3前置部署记录.md》§12 |
| 镜像可复现性 | 固定 tag/digest，不用 `latest`（AGENTS §7） | **2026-09-18 已处置**：compose 中 **15 个镜像全部固定到 digest**（含原 5 个 `:latest` 与 `pgvector/pgvector:pg16`、`traefik:v3.0`）；**未重建容器**（运行态零变更，下次重建生效）；compose 校验和重取 **`b70ae7a9…`**。仍缺：**镜像漏洞扫描未执行**（供应链动作）。详见《AI数据治理平台_单机版_S3前置部署记录.md》§5 |

---

## 附：完整单机 docker-compose.yml（可直接落地）

> 以下为 S1~S4 全部 18 容器的整合骨架。OpenMetadata 部分**建议直接取官方 quickstart 再叠加**（设计方案 6.3 明确要求避免手写配置与官方版本脱节），此处给出的是叠加后的结构与关键参数。
> **内存合计校验**：18 容器 limits 之和约 52.75~60.75GB ≤ 96GB × 70% ≈ 67.2GB ✅（对应 4.2 基线）
>
> ⚠️ **落地优先于本骨架**：S1 段已按本环境实测落地（补齐 healthcheck、显式环境变量、镜像固定 tag、Prometheus 网络调整等），
> **以服务器上的 `/data/ai-governance/docker-compose.yml` 为准**，差异与理由见《AI数据治理平台_单机版_S1部署记录.md》§2/§4；
> 下方骨架保留为 S2~S4 的结构参考，不得直接覆盖已落地的 S1 段。

```yaml
name: ai-governance-poc

networks:
  frontend-net: { name: frontend-net }
  backend-net:  { name: backend-net, internal: true }
  data-net:     { name: data-net,    internal: true }

services:
  # ========== S1 单机底座 ==========
  postgres:
    image: pgvector/pgvector:pg16            # 版本需按官方文档核对
    command: >
      postgres -c max_connections=80
               -c shared_buffers=2GB
               -c effective_cache_size=6GB
    volumes: ["/data/postgres:/var/lib/postgresql/data"]
    networks: [data-net]
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy: { resources: { limits: { cpus: "2", memory: 4G } } }

  pgbouncer:
    image: bitnami/pgbouncer:latest          # 版本需按官方文档核对，需 ≥1.21 才支持 max_prepared_statements（待确认）
    environment:
      PGBOUNCER_POOL_MODE: transaction       # 单机档起配（执行计划 P0-B 第2周）
      PGBOUNCER_DEFAULT_POOL_SIZE: "25"
      PGBOUNCER_MAX_CLIENT_CONN: "200"
      PGBOUNCER_AUTH_USER: postgres          # 使 auth_query 生效：多角色可经 6432 认证（2026-09-17 S2-1 实测补强，见 S1 记录 §3.2）
    depends_on: [postgres]
    networks: [data-net, backend-net]
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -h localhost -p 6432"]
      interval: 10s
      retries: 5
    deploy: { resources: { limits: { cpus: "0.25", memory: 256M } } }

  opensearch:
    image: opensearchproject/opensearch:latest   # 版本需按官方文档核对
    environment:
      discovery.type: single-node
      OPENSEARCH_JAVA_OPTS: "-Xms4g -Xmx4g"      # 官方建议 heap ≥4GB（4.2）
      bootstrap.memory_lock: "true"
    volumes: ["/data/opensearch:/usr/share/opensearch/data"]
    ulimits:
      memlock: { soft: -1, hard: -1 }
      nofile:  { soft: 65536, hard: 65536 }
    networks: [data-net, backend-net]
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "curl -sk https://localhost:9200/_cluster/health || exit 1"]
      interval: 30s
      retries: 5
    deploy: { resources: { limits: { cpus: "2", memory: 8G } } }

  traefik:
    image: traefik:v3.0                       # 版本需按官方文档核对
    ports: ["443:443"]                        # 仅暴露 443（安全基线第④条）
    networks: [frontend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "0.5", memory: 256M } } }

  prometheus:
    image: prom/prometheus:latest             # 版本需按官方文档核对
    ports: ["127.0.0.1:9090:9090"]            # 仅回环：供 SSH 隧道看 UI（2026-09-17「方案 C」；对外暴露面不增加）
    volumes: ["/data/prometheus:/prometheus"]
    networks: [backend-net, frontend-net]     # frontend-net：提供默认路由（抓宿主 node_exporter）且使回环端口发布生效
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "1", memory: 2G } } }

  alertmanager:
    image: prom/alertmanager:latest           # 版本需按官方文档核对
    ports: ["127.0.0.1:9093:9093"]            # 仅回环：同上
    networks: [backend-net, frontend-net]     # 仅挂 internal 网络时回环端口发布不生效（已实测）
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "0.25", memory: 256M } } }

  # ========== S2 离线治理 ==========
  # OpenMetadata server / ingestion 取自官方 quickstart，此处只标注必改项：
  openmetadata-server:
    image: openmetadata/server:latest         # 版本需按官方文档核对
    environment:
      DB_HOST: pgbouncer                      # ★关键：不是 postgres
      DB_PORT: "6432"
      ELASTICSEARCH_HOST: opensearch
    networks: [backend-net, frontend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "2", memory: 4G } } }

  openmetadata-ingestion:                     # 内置 Airflow，来自官方 quickstart
    networks: [backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "2", memory: 4G } } }

  presidio-analyzer:
    image: mcr.microsoft.com/presidio-analyzer:latest   # 版本需按官方文档核对
    networks: [backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "1", memory: 2G } } }

  presidio-anonymizer:
    image: mcr.microsoft.com/presidio-anonymizer:latest
    networks: [backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "0.5", memory: 1G } } }

  opa:
    image: openpolicyagent/opa:latest         # 版本需按官方文档核对
    networks: [backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "0.5", memory: 512M } } }

  superset:
    image: apache/superset:latest             # 版本需按官方文档核对
    depends_on: [pgbouncer]
    environment:
      SUPERSET_SECRET_KEY: "${SUPERSET_SECRET_KEY}"   # 从密钥管理注入，不得硬编码
    networks: [backend-net, frontend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "1", memory: 2G } } }

  governance-portal:
    build: ./governance-portal
    environment: { GOVERNANCE_API_URL: "http://governance-agent:8080" }
    networks: [frontend-net, backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "0.5", memory: 512M } } }

  # ========== S3 实时链路 ==========
  kafka:
    image: apache/kafka:latest                # 版本需按官方文档核对
    environment:
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_NODE_ID: 1
      KAFKA_CONTROLLER_QUORUM_VOTERS: "1@kafka:9093"
      KAFKA_LISTENERS: "PLAINTEXT://:9092,CONTROLLER://:9093"
      KAFKA_ADVERTISED_LISTENERS: "PLAINTEXT://kafka:9092"
      KAFKA_HEAP_OPTS: "-Xms2g -Xmx2g"        # 4.2「JVM heap ~2GB + 页缓存」
    volumes: ["/data/kafka:/var/lib/kafka/data"]
    networks: [data-net, backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "2", memory: 4G } } }

  flink-jobmanager:
    image: ${FLINK_CDC_IMAGE}:${FLINK_CDC_DIGEST}   # 自建镜像，固定 digest（安全基线第⑤条）
    command: jobmanager
    environment:
      FLINK_PROPERTIES: |
        jobmanager.rpc.address: flink-jobmanager
        state.backend: hashmap                # 单机形态（ADR-A4）
        state.checkpoints.dir: file:///opt/flink/checkpoints
    volumes: ["/data/flink-checkpoint:/opt/flink/checkpoints"]
    networks: [data-net, backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "1", memory: 2G } } }

  flink-taskmanager:
    image: ${FLINK_CDC_IMAGE}:${FLINK_CDC_DIGEST}
    command: taskmanager
    depends_on: [flink-jobmanager]
    environment:
      FLINK_PROPERTIES: |
        jobmanager.rpc.address: flink-jobmanager
        taskmanager.numberOfTaskSlots: 4
    volumes: ["/data/flink-checkpoint:/opt/flink/checkpoints"]
    networks: [data-net, backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "3", memory: 6G } } }

  governance-agent:
    build: ./governance-agent
    depends_on: [kafka, pgbouncer, opa]
    networks: [backend-net, data-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "2", memory: 4G } } }

  # ========== S4 AI 能力 ==========
  ollama:
    image: ollama/ollama:latest               # 版本需按官方文档核对
    volumes: ["/data/ollama:/root/.ollama"]
    networks: [backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "8", memory: "16G" } } }   # 4.2 基线 4~8 vCPU / 8~16GB
```

> 自建 Flink 镜像需把 CDC connector JAR 放入 `/opt/flink/lib`（设计方案 6.3.1 注释）。`/data/ollama` 目录需在 S1-1 一并创建。

---

## S1 单机底座部署（P0-B，2~3 周）

### S1-1 服务器初始化（第 1 周）

**前置**：服务器到位并通过验收——`nproc`=24 / `free -g`=96 / 系统盘 480G + 数据盘 2TB / **fio 顺序写 ≥1GB/s、4K 随机读 ≥100k IOPS**。**未通过不得开工。**

```bash
# OS：Ubuntu 22.04 LTS（或 Rocky Linux 9）
lsblk -d -o NAME,ROTA,SIZE                 # 确认数据盘 ROTA=0（NVMe）
mkfs.xfs /dev/nvme1n1                       # 设备名以实际为准 —— 待确认
mkdir -p /data && mount /dev/nvme1n1 /data
echo '/dev/nvme1n1 /data xfs defaults 0 0' >> /etc/fstab

# 目录规划（全部容器数据卷落 /data）
mkdir -p /data/{postgres,opensearch,kafka,flink-checkpoint,backup,superset,prometheus,ollama}

# 内核参数（OpenSearch 要求）
sysctl -w vm.max_map_count=262144
sysctl -w vm.swappiness=1
ulimit -n 65535
```

> **本环境适配（见 0.3）**：本机无独立数据盘，`/data` 为根文件系统上的普通目录——**跳过 `mkfs.xfs` 与 `/etc/fstab` 挂载步骤**，
> 仅 `mkdir -p /data/...`；`lsblk` 验收改为"确认仅 1 块 100G 盘、`/` 可用余量 ≥ 80G"。
> 内核参数与句柄上限**必须持久化**（`sysctl -w` / `ulimit` 重启即失效）：
> `/etc/sysctl.d/99-ai-governance.conf`（`vm.max_map_count=262144`、`vm.swappiness=1`）与
> `/etc/security/limits.d/99-ai-governance.conf`（`nofile 65535`、`memlock unlimited`）。
> **门禁①（fio / 容量）在本环境不适用**，豁免结论见 0.3 降级声明 1。

**验收**：`df -h /data` 显示 2TB；`sysctl vm.max_map_count` = 262144。
**失败**：容量/性能不达标 → 拒收，重提采购。

### S1-2 Docker 与 Compose 安装（第 1 周）

```bash
curl -fsSL https://get.docker.com | sh
docker --version && docker compose version   # 版本需按官方文档核对
systemctl enable --now docker
```

**验收**：`docker run --rm hello-world` 成功。

### S1-3 三网隔离（第 1 周）

```bash
docker network create --internal backend-net
docker network create --internal data-net
docker network create frontend-net
```

| 网络 | 成员 |
|---|---|
| `frontend-net` | Traefik、门户、OM server、Superset |
| `backend-net` | OM server/ingestion、Presidio、OPA、Governance Agent、Superset |
| `data-net` | PostgreSQL、PgBouncer、OpenSearch、Kafka、Flink、Governance Agent |

**验收**：跨网容器 `ping` 不通；同网可通。

### S1-4 部署 PostgreSQL + pgvector 与 PgBouncer（第 2 周）

关键参数（依据见 0.2 与 compose 文件）：

| 参数 | 值 | 依据 |
|---|---|---|
| `pool_mode` | `transaction` | 执行计划 P0-B 第 2 周 |
| `default_pool_size` | 25 | 同上 |
| PG `max_connections` | 80 | 「收至 60~80，省内存给 shared_buffers / effective_cache_size」 |
| PG 内存 limit | 4GB | 4.2 基线 |

**transaction 模式已知陷阱**（实测确认，非假设）：
- 会话级 `prepared statements` 需 PgBouncer **1.21+** 的 `max_prepared_statements` —— **版本待确认**
- `SET` 会话变量、`advisory lock`、`LISTEN/NOTIFY` 在 transaction 模式下行为异常
- **多角色认证**：bitnami 镜像默认仅 `auth_file` 中的 `postgres` 可认证；**新建角色必须同时设置 `PGBOUNCER_AUTH_USER`** 才走 `auth_query`（2026-09-17 实测，见 S1 记录 §3.2）

**验收**：`SHOW POOLS;` 可查询且 **waiting = 0**。

### S1-5 部署 OpenSearch（第 2 周）

**验收**：`curl -sk https://localhost:9200/_cluster/health` 返回 `green` 或 `yellow`（单节点 yellow 属预期）。
**注**：不关闭 security plugin（安全基线）。

### S1-6 Traefik 入口 + TLS（第 3 周）

**验收**：`openssl s_client -connect <host>:443` 握手成功。
**注**：SSO 对接在 S2-3，此处只验 TLS 终止。

> **本环境后续变更（2026-09-18）：SSO 前**不再对外发布 443**，OM 改走 SSH 隧道**。
> **动因**：本环境**没有固定的企业 VPN / 出口 IP** ⇒ 曾讨论的"按源 IP 收敛 443"无法稳定实施（出口 IP 一变就锁死自己）；而 443 上的**唯一路由就是 OpenMetadata**（Traefik `PathPrefix('/')`）。因此选择"撤回暴露"而非"限源"：
> | 变更 | 内容 |
> |---|---|
> | Traefik | 动态路由 `openmetadata.yml` **撤下**（改名 `.disabled-<ts>` 保留回滚）；traefik 容器继续运行，但 `ports: ["443:443"]` **已注释** ⇒ **宿主无 443 监听** |
> | OpenMetadata | 增加 `ports: ["127.0.0.1:8585:8585"]`（**仅回环**）⇒ 访问方式与 Superset/Airflow/Prometheus/门户统一为 **SSH 隧道** |
> | 附带修复 | `gov_metrics` ETL 原走 `https://localhost/api/v1`（经 443）⇒ 改为 `http://127.0.0.1:8585/api/v1`；门户的 OM 链接改为 `http://localhost:8585/` |
>
> **人工访问方式**：
> ```bash
> ssh -N -L 8585:127.0.0.1:8585 -L 8080:127.0.0.1:8080 -L 9090:127.0.0.1:9090 \
>        -L 9093:127.0.0.1:9093 -L 8088:127.0.0.1:8088 -L 8501:127.0.0.1:8501 root@<POC服务器IP>
> # 浏览器：http://localhost:8585   （OM）
> ```
> **预期附带收益（待首次浏览器使用确认）**：`http://localhost` 属浏览器"安全上下文"，此前因自签证书未受信导致 Service Worker 被禁、OM 新 UI 登录 401 循环的问题**应随之消失**（上一条本环境适配注保留，供将来恢复 443 时参考）。
> **回滚**：还原 `dynamic/openmetadata.yml` + 打开 traefik 的 443 发布 + 回滚 compose（备份 `docker-compose.yml.bak-20260918-142305`），ETL 基址需一并回改。
> **安全组**：宿主已无 443 监听；**可选**把安全组里 443 的入方向规则也删掉（纵深防御，非必需）。

> **本环境适配（2026-09-17 实测，重要）**：POC 用**自签证书**时，浏览器必须先把该证书导入系统"受信任的根证书颁发机构"，否则会出连锁问题——
> **Chrome/Edge 在证书报错的页面上会禁用 Service Worker / PWA 能力**，而 OpenMetadata 2.0.x 的新 UI 依赖 service worker 完成登录态切换，
> 表现为**登录成功（HTTP 200）后立刻 401 并被踢回登录页、循环往复**（Traefik access log 可复现该循环）。
> 处置：导出 `/data/tls/server.crt`（`CA:TRUE`，SAN 含服务器 IP）→ 本机导入到"受信任的根证书颁发机构"（当前用户即可，无需提权）→ **完全重启浏览器** → 清除该站点数据后重试。
> 证书有效期至 2028-12-19；生产须换企业 PKI/ACME（附 C 待确认项）。

### S1-7 Prometheus + Alertmanager（第 3 周）

采集所有容器 + 宿主机（node_exporter / cAdvisor）。SLO 阈值见 4.5：CPU<80%、内存<85%。

**验收**：`/targets` 全部 UP。

**运维接入（2026-09-17「方案 C」）**：Prometheus `9090` 与 Alertmanager `9093` **只发布到宿主 `127.0.0.1`**，人工通过 SSH 隧道查看 UI——
保持"仅暴露 443"（安全基线④）不变，对外暴露面零新增：

```bash
# 本机（Windows PowerShell / CMD / Git Bash 通用）
ssh -N -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 root@<POC服务器IP>
# 然后浏览器打开 http://localhost:9090（Prometheus）、http://localhost:9093（Alertmanager）
```

> **注意**：仅挂 internal 网络的容器，其回环端口发布**不生效**（本环境已实测复现）——Alertmanager 因此需同时挂 `frontend-net`，
> 与 S1 记录偏差 4/5 同款处理。**不得**把这两个端口改成 `0.0.0.0` 绑定。

> **告警规则载体补齐（2026-09-18）**：S1-7 落地时只验了 `/targets` 全 UP，**规则从未被加载**（`rule_files` 为空、`/api/v1/rules` → `groups: []`），即 4.5 的 SLO 阈值当时**没有载体**。现已补齐：规则入 Git（`alerting/poc-alerts.yml` → 服务器 `/data/ai-governance/config/prometheus/rules/`），Prometheus 加 `rule_files` 与 `--web.enable-lifecycle`（`POST /-/reload` 热加载），另新增 `postgres_exporter` 抓取（经 **PgBouncer 6432 回环**，不直连 5432）与复制槽告警 5 条（S3-4 判据②）。
> **投递链路**已用临时 sink 端到端证实（Prometheus firing → Alertmanager → sink 收到真实通知体）；**通知通道地址仍待定**（S3-6）。证据见《…S3前置部署记录.md》§4。

### S1-8 备份脚本 + 首次恢复演练（第 3 周）

> ⏸️ **人工决定（2026-09-19）：出机介质（`OFFSITE_TARGET`）暂缓，备份类事项本轮不推进。**
> 含义：**出机链路仍未闭环**，门禁②（备份可恢复）的该项判据**继续挂账**，不视为已完成；本文件与《…S1-8备份恢复脚本.md》§9 的"待人工提供信息"清单**保留待用**（链路本身已演练通过，介质就绪后按 §9.3 四步启用即可）。
> 其余不受影响：小时级 PG 备份 / OpenSearch 快照 / 配置归档 / 逐库与年度化恢复演练（本地段）**均照常运行**。

三路备份（ADR-A4 单机形态「本地数据盘 + 每日出机备份」）：

| 对象 | 方式 | 频率 | 目的地 |
|---|---|---|---|
| PostgreSQL | `pg_dump -Fc` 逐库 + `pg_dumpall --globals-only` | 每小时 `:05` | `/data/backup/pg/` + 出机 |
| OpenSearch | snapshot（`fs` 仓库，需 `path.repo`） | 每小时 `:15` | `/data/opensearch/snapshots/` + 出机 |
| Flink Checkpoint | 目录每日出机 | S3 起启用 | `/data/backup` + 出机 |
| 出机 | `rsync` 到出机介质 | 每日 `03:00` | `OFFSITE_TARGET`（**待确认**） |

**RPO ≤1h 的推导**：每小时备份一次 ⇒ 最坏丢数据 1 小时。**本口径不启用 WAL 归档 PITR**（分钟级手段）；
若需收窄到 ≤15min，属口径变更，须走 ADR 流程（执行计划 v2.0-r2 已就此声明）。

**脚本与运行手册**：见《AI数据治理平台_单机版_S1-8备份恢复脚本.md》（含 4 个脚本原文、cron 调度、运行手册、演练判读边界）。
部署落点：脚本 `/data/ai-governance/scripts/`，调度 `/etc/cron.d/ai-governance-backup`。

**门禁**：**RPO ≤ 1h / RTO ≤ 4h 的恢复演练记录**（口径经 2026-09-17 人工决策回填）。
脚本 Agent 自主执行；**演练结果的验证与签字为人工专属（DBA）**。

> **本环境落地状态（2026-09-17）**：脚本与调度已上线，首次恢复演练通过（PG `pg_restore` 0.07s、OpenSearch 快照恢复 158ms，产物均已清理）。
> **两条判读警告**：① S1 阶段 PG 与 OpenSearch 均**无业务数据**，演练只证明备份→恢复机制可跑通，**不构成业务数据可恢复的证明**（S2 起用真实数据复演）；
> ② **出机链路未闭环**（介质地址待确认，脚本未配置时退出码 2 且不伪造成功），门禁② 判读时须把这一项列为未完成。

> ✅ S1 准出门禁：全部容器 healthy；备份可恢复；`SHOW POOLS` 无 waiting。

---

## S2 离线治理闭环部署（Phase 1，3~4 周）

### S2-1 部署 OpenMetadata（第 1 周）

**做法**：取 OpenMetadata 官方 quickstart docker-compose 作治理内核（设计方案 6.3，不手写）。
**唯一必改**：连接串**指向 PgBouncer:6432**。

> **本环境落地（2026-09-17 实测，详见《AI数据治理平台_单机版_S2接入方案.md》§8）**：
> ① 实际采用官方 **2.0.1 发布资产的"自带基础设施"变体**（`docker-compose-openmetadata.yml` + `docker-compose-ingestion.yml`），**不是** postgres 版（那会引入第二套 PG + Elasticsearch 9.3，破坏组件与容器口径）；
> ② 镜像引用须用 **Docker Hub 名**（`openmetadata/server:2.0.1`）——`docker.getcollate.io/*` 在本环境不可达；
> ③ OpenSearch 接入须 **JKS 信任库（`ELASTICSEARCH_TRUST_STORE_PATH`）+ 与证书 SAN 对齐的网络别名**，**不关闭 security plugin**；
> ④ OM 口令经 API 传输需 **Base64**；⑤ **必须关闭自助注册**（官方默认开启，且 DB `openmetadata_settings.enableSelfSignup` 会覆盖 env）。

部署后**立即执行 S2-4 实测**，通过后才接业务源。

> **ingestion 落地要点（2026-09-17 实测，见《S2接入方案》§9）**：
> ① 服务定义必须带官方 `entrypoint: /bin/bash` + `command: ["/opt/airflow/ingestion_dependency.sh"]`，否则容器会跑成无参数的 `airflow` 并反复退出；
> ② **Airflow 3.x + SimpleAuthManager 的 API 认证走 JWT**（`POST /auth/token` 取 token 再 `Bearer`），basic auth 恒 401；OM 侧 `AIRFLOW_USERNAME/PASSWORD` 须与容器内 `simple_auth_manager_passwords.json` 一致；
> ③ **FERNET_KEY 必须在首次迁移前定版**且 OM 与 ingestion 两边一致——事后变更会导致既有 secrets 报 `Encryption key not found`；
> ④ Airflow 元数据库（`airflow_db`）与 OM 元数据库分库，**两者均可经 PgBouncer 6432 transaction 模式**（`airflow db migrate` 实测 71 表、0 命中，方案 A 通过）；
> ⑤ 容器端口 8080 **不发布**，OM 通过 data-net 内部访问。

### S2-2 接入业务源（第 1~2 周）

- 第 1 周：接入第 1 个核心业务源（schema / 血缘抽取）
- 第 2 周：接入第 2 个源 + sqllineage 补充复杂 SQL 解析

**执行方**：连接器配置 **Agent 起草 + 人工审核**（凭证与访问范围必须人工确认）。
**纪律**（ADR-A7）：sqllineage 解析失败的 SQL 进「待人工确认」队列，**禁止静默丢弃**。

> 📋 **接源当天照着走**：见《AI数据治理平台_单机版_接源前检查清单.md》——按"T-3~T-1 提前拿 / T-1 准备 / 当天 S1~S11 / T+1 对账"组织，含 **G0/G1/G2 三道 Go/No-Go**（**G0＝门禁④ 两份原件归档，硬约束 4：未归档不动生产库**）与"当前 9 项空白与缺口"表。

### S2-3 SSO 对接（第 2 周）

oauth2-proxy + Traefik ForwardAuth 对接企业 IdP。前置：回调域名、客户端 ID/密钥（P0-A 申请）。联调 Agent 自主执行 + 人工验证登录。

### S2-4 【门禁】T-M0-1：OM × PgBouncer transaction 实测

| 项 | 内容 |
|---|---|
| 步骤 | OM 登录 → 浏览元数据 → 搜索（触发 OS 链路）→ 跑完整 ingestion → 打开血缘与质量页 → 收集 PgBouncer 与 OM server 日志 |
| 通过 | 日志**零命中** `prepared statement .* does not exist` / `transaction aborted` |
| 失败 | **不自动降级** —— 输出证据包（日志 + 复现步骤），DBA + DE 决策 session 还是直连 |
| 成本 | 降级改一个配置项 ≈0，最多损失 1~2 天 |
| 确认点 | 结论写入部署基线文档后，PgBouncer 配置定版 |

> **本环境执行状态（2026-09-17）**：Agent 以等价 API 调用完成该动作序列（读/搜索/写/完整 ingestion/30 并发，共 27 项），
> **OM 侧与 PgBouncer 侧对两条判据均 0 命中**，`SHOW POOLS` 无等待（`cl_waiting=0`、`maxwait=0`），**未触发降级**；
> 完整证据与人工判定栏见 **《AI数据治理平台_单机版_S2-4门禁③证据包.md》**（含 5 次 `unexpected eof` 的良性分类、`server_reset_query=DISCARD ALL` 观测项、复现命令）。
> **判定与签字为人工专属（DBA + DE）**，Agent 不代签。

### S2-5 Presidio + OPA + gov_metrics（第 3 周）

三件事并行：

1. **Presidio 中国证件自定义识别规则**（身份证/手机号/银行卡，官方识别器不含，需业务样本验证）
2. **置信度分层调优**：高置信自动打标签 / 中置信走 OM 原生 Tasks 工作流生成复核任务 / 低置信仅记录
3. **gov_metrics schema + GA 指标 ETL 首版**（ADR-A6，约 3~5 人天）
   - 首版只做**资产覆盖率 + 质量趋势**；**告警趋势 Phase 2 再补**
   - 每小时 ETL，自持表结构；分区仅适用自研表

**OPA 纪律**：Rego 入 Git；**fail-closed**（不可用时默认拒绝高风险操作）。

> **本环境落地（2026-09-17，详见《AI数据治理平台_单机版_S2-5OPA策略与部署.md》）**：
> ① OPA **1.20.2**（`openpolicyagent/opa:latest`，**distroless 无 shell**）已上线并 healthy，`127.0.0.1:8181` 仅回环，资源 0.5 vCPU / 512MB；
> ② **healthcheck 的覆盖边界**：镜像无 HTTP 客户端，故用 `opa eval --data /policies 'true'`（进程可用 + 策略可编译），**不等价于 HTTP 服务可用**，HTTP 监听由宿主侧 `/health` 探测补充验证；
> ③ 策略入 Git：仓库 **`opa-policies/`**（`authz.rego`、`access_audit.rego`、`data.json`），只读挂载进容器，**改策略后须重启容器**（未启用 bundle 自动拉取）；
> ④ **编译通过 ≠ 逻辑正确**：Rego 的"规则冲突"只在运行期表现为 `undefined`，故**策略变更必须重跑决策用例**（本轮实测踩到并已修复）；
> ⑤ 授权/告警决策用例 8/8 通过，空输入与 OPA 不可用均**拿不到"允许"**（fail-closed 实测）。

> **Presidio 落地（2026-09-17，详见《AI数据治理平台_单机版_S2-5Presidio部署与规则.md》）**：
> ① analyzer + anonymizer 已上线并 healthy（1 vCPU/2GB、0.5 vCPU/1GB），**仅绑回环、未接 Traefik**（设计明确"仅离线批量识别，移出在线路径"）；
> ② 中国证件识别规则入 Git（仓库 **`presidio-recognizers/`**）：身份证 18/15 位、手机号（含 +86）、银行卡（常见号段），**纯正则不依赖 NLP 模型**；
> ③ 实测：有上下文时 身份证 **1.00** / 手机 **0.95** / 银行卡 **0.75**；无上下文时 **0.85 / 0.60 / 0.40** —— 该差值即 S2-5 第 2 件「置信度分层」的量化依据（阈值在 S3 定义并须人工确认）；
> ④ **运维要点（踩坑）**：gunicorn 25 的**控制套接字与 fork 竞争**会导致 worker 间歇性不启动（`running` 但无 `Booting worker`、所有请求挂起）——须设 `GUNICORN_CMD_ARGS="--no-control-socket"`；镜像用 poetry 且 `presidio_analyzer` 是 `/app` 下源码目录，自定义脚本须 `PYTHONPATH=/app`。

> **gov_metrics 与 ETL 落地（2026-09-17，详见《AI数据治理平台_单机版_S2-5gov_metrics与ETL.md》）**：
> ① **独立库 `gov_metrics` + schema `gov_metrics`**（库级与 OM 内库隔离），三张自持表：`asset_coverage_snapshot` / `quality_trend_snapshot` / `etl_run`；
> ② 角色：`gov_metrics_writer`（仅本库写入）、`superset_ro`（**仅 gov_metrics 的 SELECT**）；并**收回 PUBLIC 的库级 CONNECT**，只放行各自角色；
> ③ ETL 每小时 `:25`（与备份错峰）：**只走 OM REST API 取数**（`search` 聚合 + `dataQuality`），**写库经 PgBouncer 6432**；首跑成功 9 行、`数据截至` 由 `run_ts` 提供；
> ④ **反向验证通过**：`superset_ro` 连 `openmetadata_db`/`airflow_db` 均 `permission denied for database` —— ADR-A6「禁止直连 OM 内表」在权限层被强制；
> ⑤ **运维要诀**：**PgBouncer 会保留空闲服务端连接**，库级/角色级权限变更**必须回收池连接**（重启 pgbouncer）才生效——否则会出现"直连被拒、经池却可连"的假象（本轮实测踩到）。

### S2-6 Superset 看板（第 4 周）

**硬约束（ADR-A6）**：数据源**只读 `gov_metrics`**，**禁止直连 OM 内表**；看板标注「数据截至」时间戳（每小时 ETL，最长延迟 1h）。

**验收**：Superset 全部查询落在 gov_metrics，无 OM 内表直查。

> **本环境落地（2026-09-17，详见《AI数据治理平台_单机版_S2-6Superset与指标看板.md》）**：
> ① Superset **6.1.0** 上线 healthy，**仅回环 `127.0.0.1:8088` + SSH 隧道**（阶段选择，443 分配与 SSO 一并规划）；
> ② **官方 lean 镜像不含 PG 驱动**，故**自建派生镜像**（`superset-image/`，固定基础 tag `6.1.0` + 用镜像自带 `uv` 把 `psycopg2-binary` 装进 `/app/.venv`）；元数据库 `superset` 经 **PgBouncer 6432**（alembic 实测 53 张表）；
> ③ 数据源**唯一且只读**（`gov_metrics`／`superset_ro`／`allow_dml=false`）；**验收正反均过**：Superset 执行的查询返回 `max(run_ts)`＝数据截至，查 `pipeline_entity` 返回 **404 Table not found**；DB 侧 `superset_ro` 连 OM 库被**库级拒绝**；
> ④ **API 要点**：写操作需 **CSRF token**；`/sqllab/execute/` 在 6.x **不接受 `json` 字段**；图挂看板用 **`PUT /api/v1/chart/{id}`**。

### S2-7 治理门户（第 2~4 周，可并行）

Agent 未就绪时降级显示。

> **本环境落地（2026-09-17，详见《AI数据治理平台_单机版_S2-7治理门户.md》）**：
> ① 自研 **Streamlit 门户**上线 healthy（0.5 vCPU / 512MB、**仅回环 8501 + SSH 隧道**），4 个页面：首页/能力入口、治理指标、智能问答（场景 D）、访问审计与审批（场景 E）；
> ② **降级已实测**：Governance Agent 未就绪时 `health()` 返回 `ready=False`，页面显示降级提示并指向 OpenMetadata（S3 Agent 上线后自动脱离降级，无需改代码）；
> ③ **只读 gov_metrics**（角色 `portal_ro`），**反向验证通过**：连 `openmetadata_db`/`airflow_db`/`superset` 均被库级拒绝；
> ④ **构建加速（重要，后续自建镜像通用）**：公网 PyPI 镜像在**构建容器内**仅 ~100 kB/s（构建挂住 15 分钟），改用**阿里云内网镜像 `http://mirrors.cloud.aliyuncs.com/pypi/simple/` + `--trusted-host`** 后 **17 秒**完成；大镜像构建应前台 `--progress=plain` 落日志，避免 SSH 超时丢证据；
> ⑤ **S2 段 7 个容器全部就位**（OM server/ingestion、presidio×2、opa、superset、governance-portal），与设计方案 S2 清单一致。

> ✅ S2 准出门禁：覆盖率 100%；PII >90%；血缘 Top50 ≥90%；Superset 零查 OM 内表；**合规边界文件归档**（未归档不得进 S3）。

> **量测口径已预置（2026-09-18）**：上述五项判据"怎么量、抽多少、谁来判、记什么"见《AI数据治理平台_单机版_S2准出量测口径与抽检模板.md》——含覆盖率分子/分母口径（**以 `gov_metrics/schema.sql` 已声明口径为准**，并澄清"治理完备度/质量检测覆盖率"不得混称）、源侧分母 SQL、PII 分层抽样与查准/查全双口径、血缘 Top 50 列级抽检法、四张记录模板与 **4 项待人工确认的口径**。
> 目的：**源库（S2-2）一到即可直接取数判定**，避免验收现场口径分歧。**判读与签字仍为人工专属。**

---

## S3 实时链路部署（Phase 2，3~4 周）

### S3-1 【门禁】接生产源库前的两项书面确认

| 项 | 内容 | 责任人 |
|---|---|---|
| 合规边界 | 场景 E「POC 期只做事后审计 + 主动告警，不做事前拦截」书面确认 | SEC + 合规方 |
| 续接窗口 | **MySQL** `binlog_expire_logs_seconds` ≥ 3 × 停机预算且默认 ≥48h（例：停工 16h → 48h）；**PG** `wal_keep_size` ≥ 3 × 停机预算内 WAL 增量 | DBA |

**未签不动生产库。**

> **就绪材料（2026-09-18，草案）**：两份书面文件的**起草件**见《AI数据治理平台_单机版_S3-1门禁④材料包.md》——§4 文件 A《合规边界确认书（草案）》、§5 文件 B《续接窗口签约定（草案）》（含按源库填报表、PG 参数推导模板与示例、磁盘余量约束、监控需求、作业下线 checklist）。
> **门禁④ 状态（2026-09-18）：人工（项目决策方）确认通行。** 本节的判据文字**不变**；Agent 不代签、不背书，两份文件的原件归档由 SEC + 合规方 / DBA 完成。
> 通行后：可接触源库，但**每次源库变更仍按 S3-4 "Agent 起草 + DBA 审核后执行"**；**文件 B 取值未落定前不得在源库设置 `max_slot_wal_keep_size` / `wal_keep_size`**。

### S3-2 部署 Kafka（第 1 周）

**Topic 规划**（ADR-A5 + A1）：

| Topic | 用途 |
|---|---|
| `governance.alerts` | 增量窗口异常告警 |
| 日志事件类 | 日志/埋点接入（可重放） |

> **默认直连，不加 `cdc.*` Topic**。仅当 ADR-A1 三个切换信号之一命中（有其他 CDC 消费方 / 续接窗口不满足 / 频繁背压）才加 —— **切换只加 Topic，不引入新组件**。

> **本环境落地（2026-09-18）**：Kafka 已上线 —— `apache/kafka:4.0.0`（**按 digest 固定**）KRaft 单节点（broker+controller 合一、无 ZooKeeper），`KAFKA_HEAP_OPTS=-Xms512m -Xmx512m`（4.1.3），**不发布任何端口**，`KAFKA_AUTO_CREATE_TOPICS_ENABLE=false`（Topic 显式创建）。
> `CLUSTER_ID` 显式固定（`2d8dca59-…`）以免重建后集群身份漂移；`governance.alerts` 已建（**2 分区**与 Flink slots=2 对齐 / RF 1 / 保留 7d）；**日志事件类 Topic 未预建**（命名待接入方确认，不臆造）；收发往返实测通过，测试消息已清理。
> 证据与命令见《AI数据治理平台_单机版_S3前置部署记录.md》§2。

### S3-3 部署 Flink（内置 Flink CDC）（第 1~2 周）

- 自建镜像：CDC JAR 入 `/opt/flink/lib`，**固定 digest**
- **Checkpoint（ADR-A4 单机形态）**：`/data/flink-checkpoint` 本地盘 + 每日出机
- **边界声明**：单机形态不存在跨节点恢复问题，**不得宣称 Flink HA**
- **镜像供应链**：固定 digest + 上线前扫描，无高危漏洞

> **本环境落地（2026-09-18）**：Flink 已上线 —— 自建派生镜像 `ai-governance/flink:1.20.1-cdc3.6.0`（源码入 Git：`flink-image/`；基础镜像 `flink:1.20.1-scala_2.12-java17` **按 digest 固定**），内置 **CDC 3.6.0-1.20（MySQL/PG）+ `flink-connector-kafka 3.4.0-1.20` + `flink-connector-base 1.20.1` + `kafka-clients 3.4.0`**（JAR 坐标与 sha256 全部登记，构建期强制校验）。
> JM 1vCPU/2G、TM 3vCPU/6G，**task slots=2**（4.1.3）；`state.backend=hashmap`，Checkpoint 落 `/data/flink-checkpoint`；JM UI 仅回环 `127.0.0.1:8081`（SSH 隧道）。
> **实测踩坑（重要）**：`flink-connector-kafka` 是瘦包，只装 CDC + Kafka 连接器会 `ClassNotFoundException: org.apache.kafka.clients.consumer.OffsetResetStrategy` ⇒ 必须补 `flink-connector-base` **与 `kafka-clients`**（后者官方基础镜像也不含）。
> 冒烟已通过：Kafka source → print sink 作业 RUNNING、marker 被消费、**Checkpoint 落数据盘**；作业已取消、临时 topic 与 checkpoint 目录已清理。
> **未纳入**：Oracle CDC（商业插件与授权评估）、OpenLineage 上报 JAR（属 S3-5）。**镜像漏洞扫描未执行**。证据见《…S3前置部署记录.md》§3。
### S3-4 【门禁】PG 源库复制槽安全保护（第 1 周）

**仅 PG 源库需要（MySQL 无此项）。** P1 级风险：作业停机期间 slot 阻止 WAL 回收，**撑爆源库磁盘**（比断流严重一个量级）。

| # | 措施 | 配置 |
|---|---|---|
| ① | 兜底上限 | 源库 `max_slot_wal_keep_size`（超限 slot 自动失效，**宁可断流重快照、不撑爆磁盘**） |
| ② | 水位告警 | `restart_lsn` 与当前 WAL 差值 → **最高优先级告警**，阈值 = 兜底值 **70%** |
| ③ | 运维纪律 | 作业下线**必须先删 slot**，写入 checklist 与 runbook |

```sql
-- ① 兜底（数值按签约定推算，DBA 核定 —— 待确认）
ALTER SYSTEM SET max_slot_wal_keep_size = '<按签约定计算>';
SELECT pg_reload_conf();

-- ② 水位查询（接入 exporter）
SELECT slot_name, restart_lsn,
       pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS retained_bytes
FROM pg_replication_slots;

-- ③ 下线前必做
SELECT pg_drop_replication_slot('<slot_name>');
```

**执行方**：参数 **Agent 起草 + DBA 审核后执行**。

> **参数推导模板与机制预验证（2026-09-18）**：兜底 `= ceil(3 × T_stop × R_WAL)`、告警阈值 = 兜底 × 70%、**兜底未配置时 `safe_wal_size` 返回 NULL**（不可作告警基准）——推导与示例见《…S3-1门禁④材料包.md》§5.3；判据② 的**采集 → 规则 → 通知三环节当前全缺**（实测：无 `postgres_exporter`、`prometheus.yml` 无 `rule_files`、Alertmanager 仅 `default` 无通道）见同文件 §6。
> 机制侧已在 POC 本机 PG 16.15 上预验证（建槽 → `pg_switch_wal()×3` 水位增长 → 删槽无残留）——**该验证不替代源库侧执行**，源库参数与演练仍按 DBA 流程办。**取值 `待确认`**（附 C 第 3 项未收敛）。

### S3-5 CDC 作业开发与部署（第 2 周）

三块：CDC Source（快照+增量）、窗口规则引擎、**作业图级血缘上报**（OpenLineage，作业启动/变更时上报，**非事件级**）。

**质量口径拆分（ADR-A5）—— 两类产出走不同通道**：

| 产出 | 来源 | 去向 |
|---|---|---|
| 权威质量指标（全表空值率/唯一值/分布） | CDC **快照阶段** + 离线 Profiler | OM 质量测试 → **进质量看板** |
| 异常告警（窗口内突增/越界/骤降） | CDC **增量阶段** | `governance.alerts` → 告警 → **不进质量看板** |

两类在 OM 中以**不同 test definition 类型**区分。
**执行方**：作业开发部署 Agent 自主；**规则逻辑上线前人工审核**。

> ⚠️ **OpenLineage 血缘上报：已查证为"当前形态不可行"（2026-09-19）**——**须人工决策，Agent 未自行改形态或升版本**。
> **官方口径**（文档源文件 `website/docs/integrations/flink/`，经 GitHub API 取到）：连接器**按 Flink 大版本分两条实现**——
> **Flink 1.x 走 `JobListener`**：**必须在作业代码里注册**、**不支持 Flink SQL**、仅 **Application Mode** 且需 `execution.attached: true`；
> **Flink 2.x 走原生 `JobStatusChangedListenerFactory`（FLIP-314）**：**支持 SQL、无需改代码**。
> **本地实测**：① fat jar 的 `Premain-Class` 声明**是错的**（全 JAR 仅 shaded Javassist 含 premain）⇒ **`-javaagent` 路线永久关闭，官方从未要求**；② **Flink 1.20.1 的 `flink-dist` 中该 SPI 0 命中**（只有老的 `JobListener`）。
> ⇒ **我们站在"1.20.1 + Flink SQL"，两条路都不通**。三选一（**均属组件/架构层，须人工批准**）：**A** 升 Flink 2.x（支持 SQL/不改代码；但 2.x 目前仅 Kafka connector 提供 lineage，**Flink CDC JDBC 源须再核**，且属组件版本变更）；**B** 作业改 DataStream API + Application Mode（需重写作业）；**C** 暂不上报（**Agent 建议短期取 C，与 A 一并在 M-Prod 立项评估**）。
> ⚠️ **A 的前提已核实：不成立（2026-09-19，见《…S3前置部署记录.md》§12.9）**——`flink-cdc` 源码树**含 lineage 的路径 = 0**（未实现 FLIP-314 的 `LineageVertexProvider`），Flink 官方文档亦写明"连接器覆盖是 **gradually** 的、自定义连接器须**自行实现**该接口"。⇒ **仅升 Flink 2.x 拿不到 CDC 源的血缘**。
> 因此新增两条路：**A′** 让血缘边界落在 Kafka（Kafka 连接器已实现血缘；但**与 ADR-A1"Kafka 不承担 CDC 传输"冲突**，须先改 ADR）；**D** 由**自研侧（GA）在作业启动/变更时向 OM 注册作业图级血缘**（**最贴合 ADR-A7 原文**——原文只说"作业启动/变更时上报"，未限定必须由 OpenLineage 上报；属**机制变更，需人工批准**）。**Agent 建议：若本期必须有作业图级血缘，D 的可行性高于 A；否则维持 C。**
> 完整依据（官方文档原文要点、4 份文档与 jar 的 sha256、SPI 实测、三个仓库 git tree 过滤结果）见《…S3前置部署记录.md》§12.7 / §12.8 / §12.9。

> ✅ **D1 已落地（2026-09-19，人工选定简版）**：**不依赖 Flink 血缘接口，改为 GA 自研上报**。
> **链路**：调用方 POST 作业 SQL → GA 解析 `CREATE TABLE … WITH` + `INSERT INTO … SELECT … FROM` → 按 FQN 在 OM 建/删血缘边（`source=PipelineLineage`，并把**作业 SQL 原文**写进 `sqlQuery`）。
> **口径**：**宁缺勿错**（解析/映射不确定即 400 且**不落任何行**）；映射显式（`table_service`/`topic_service`/`default_schema`，不猜）；幂等键 `(job_key, from, to)`；变更时自动删旧边；Bearer `GA_INGEST_TOKEN` 强制校验。
> **验收（端到端，非仅单元）**：解析 4/4（含两个负例）；dry-run/幂等/`entity_missing` 容错；**建临时实体 → `status=applied` → OM 读回下游边（含 sqlQuery）→ 幂等不重复建边 → 换 sink 后旧边删除新边生效 → 清理后 tables/schemas/databases/services 全为 0**；**镜像重扫 Total 13（HIGH 10/CRIT 3）与改动前一致**。
> **边界（不得当成"血缘已可用"）**：**OM 无表实体时边必然 404**（真实业务边**待 S2-2 接源**）；**只做表/主题级边，不建作业实体**（D2）；**无周期对账**——OM 删实体会连带删边而 GA 状态仍 `applied`，属**已知漂移**；触发靠调用方显式 POST；门户无血缘页；OM 写权限仍用 admin token（待收口）。
> ✅ **D1 对账 + 自主触发（2026-09-19 同日追加）**：① **对账** `POST /api/v1/lineage/reconcile`（`repair` 自愈）+ `lineage_reconcile.sh`；**只新增/修复，绝不删 OM 上的边**；漂移闭环实测通过（删边→`drift=1`→自愈→OM 可见→不重复修复→零残留）；**新增规则 `LineageReconcileStale`（>30h）⇒ 规则总数 21 → 22**。② **自主触发（提交即上报）**：`submit_flink_job.sh`（**干跑门禁 → 登记 → 提交 → 上报**，门禁不过**不提交**）+ `lineage_autoreport.sh`（遍历登记目录 `/data/ai-governance/jobs/*.sql`，全部成功才对账写成功戳；cron 每日 04:10 跑它）。验收：好作业写入 OM（`edge_exists=True`）、坏作业 `rc=3` 且**未调用提交**、登记为空时直接对账、零残留。
> ⚠️ **同轮修复三个真缺陷**（都属"会静默或误导"类）：① 采集器空值 ⇒ node_exporter 拒收整个 textfile（所有新鲜度指标被打哑）；② `getLineageEdge` 判据错（存在时返回 `{"edge":{…}}`）⇒ 对账误报漂移；③ **dry-run 写 `planned` 导致 `apply=true` 跳过推送 ⇒ 边永远进不了 OM**（"先干跑再提交"正是主路径）。
> ⚠️ **一处重要更正**：GA 镜像重建时**取用了更新后的基础**（`python:3.12-slim@sha256:78387bc3881b…`，debian 13.6 → 13.7）⇒ **HIGH/CRIT 由 10/3 降为 0/0**（对照实验：门户/superset 未重建，仍分别 13 与 100）。**连带暴露偏差：基础镜像未固定 digest ⇒ 重建可能静默换基础**；建议固定（属构建方式变更，需人工决定）。详见《…S3-5血缘上报D1记录.md》§4.4 与《…供应链漏洞处置清单.md》。
> ⚠️ **同轮修掉一个严重回归**：新任务的成功戳缺失时指标行写成**空值** ⇒ **node_exporter 拒收整个 textfile 文件**，**所有新鲜度指标一度全部消失**（日志实证每 30s 一条 ERROR）。已改为全量 `num_or0()` 兜底 + 落盘自检（非法行保留旧文件）+ 补 `# HELP`；修复后 `promtool check metrics` rc=0、指标数恢复 6、node_exporter 零错误。
> **待人工**：~~ADR-A7 的实现方式回写~~ **已完成（2026-09-19，人工授权）**——《设计方案》升 **v3.3**，ADR-A7 新增"上报机制"行、§1 总体架构/§3.3/场景 C 同步修正；ADR 的**决策要点与组件清单未变**。详见新增《AI数据治理平台_单机版_S3-5血缘上报D1记录.md》§5.5。

### S3-6 作业自动拉起 + 告警打通（第 3 周）

> ✅ **告警汇入 GA 落地注（2026-09-19）**：**补上"有判定、无留痕"的缺口**。
> **背景**：实测 Alertmanager 的 `receivers` 只有一个空的 `default`（无 URL）⇒ 21 条规则的告警**只存在于 AM UI**，既不落库也不可审计。
> **改动**：AM 新增接收器 `ga-ingest` → GA 新端点 `POST /api/v1/alerts/ingest` → 落 `ga.alert_event`（`source='alertmanager'`，幂等键 `alertmanager:<fingerprint>:<status>`）。**未引入新组件、未改规则与阈值**。
> **鉴权**：`GA_INGEST_TOKEN`（Bearer，48 hex，服务器 `.env`）；**部署即带令牌**。配置模板 `alerting/alertmanager.yml.example`（占位符，真值不入库）。
> **验收**：无令牌 401 ✔；合成 payload `stored=1` ✔；重复投递 `duplicates=1`（不新增行）✔；resolved 变体新增行 ✔；**真实端到端**（向 AM 注入告警 → 30s 后 GA 收到 `firing`、AM 无投递错误）✔；**镜像重扫 Total 13（HIGH 10/CRIT 3）与改动前一致** ✔。
> **三条踩坑**：① 容器网内按容器名互访（`http://governance-agent:8080`）**不需要改端口发布**，暴露面未扩大；② `docker compose build` 前**必须去掉 `image:` 的 digest**，构建后重新钉（新 `09421ae7…`）；③ AM **未启用 lifecycle API**，配置生效用 `SIGHUP` 热加载而非 `/-/reload`。
> **边界**：**留痕 ≠ 通知**——IM/Webhook 推送仍待地址；门户当前无告警页，本次交付是"**落库 + API 可查**"，未承诺门户可见。详见《…S3-6治理Agent首版实现记录.md》§7。

- `restart: unless-stopped` + healthcheck（S3-3 已配）
- 作业状态、Checkpoint 成功率、binlog/slot 水位 → **最高优先级告警**
- GA 告警增强模块 + Alertmanager/IM 打通

> **本环境落地（2026-09-18）：GA 首版已上线**（迭代式交付，先落"告警落库 + 开放 API + 审批网关"，IM 通道待人工给地址）。
> | 项 | 内容 |
> |---|---|
> | 镜像/端口 | `ai-governance/governance-agent:0.1.0`（自建）；**不发布公网端口**，仅回环 **`127.0.0.1:8085`**（宿主 8080 已被 Airflow 占用）+ SSH 隧道；容器内监听 8080 以匹配门户的 `GOVERNANCE_API_URL` |
> | 库与权限 | 独立库 `governance_agent` + `ga` schema（`alert_event` / `approval_request` / `audit_log`）；`ga_writer`（读写）+ `ga_ro`（只读）；**经 PgBouncer 6432** |
> | 接口 | `GET /health`（门户判就绪）、`/ready`、`/api/v1/alerts[...]`、`/api/v1/approvals[...]`、`/api/v1/audit`、`POST /chat`（**S4 前降级**） |
> | 验收 | **门户降级结束**（实测 `agent.health() → True`）；告警幂等落库（重复投递不重现）；审批流 create→decide→list→审计闭环；`ga_ro` 不可写；`ga_writer` 越库被拒；18 容器 healthy |
> | **新纪律（实测得出）** | ① **每个新库都必须做库级 CONNECT 收敛**——PG 默认给 PUBLIC 授权，新角色否则可连任意库（本轮已把 `governance_agent`/`openmetadata_db`/`postgres`/`template1` 一并收敛）；② **权限变更后必须回收 PgBouncer 池连接**（管理台 `RECONNECT`），否则 idle 服务端连接会让变更"看起来没生效" |
> 证据见《AI数据治理平台_单机版_S3-6治理Agent首版实现记录.md》。

### S3-7 【门禁】三项演练（第 4 周）

| 演练 | 通过标准 |
|---|---|
| CDC 断流恢复 | 停机 ≤ 续接窗口 **1/3** 内恢复，**无重新快照** |
| PG 复制槽保护 | 兜底生效 + 水位告警触发 + 删 slot 演练通过 |
| 重快照预案 | 大表限速完成，源库负载在约定阈值内（低峰、限 chunk、跳非关键表） |

**执行方**：脚本与采集 Agent 自主；**判读与源库负载评估人工专属（DBA 签字）**。

> ✅ S3 准出门禁：P95<30s；断流恢复 + 重快照演练通过；PG slot 三项验证通过；质量看板无增量数据混入；连续 1 周无未恢复故障。

---

## S4 AI 能力与试运行（Phase 3+4，5~7 周）

### S4-1 Ollama 部署（Phase 3 第 1 周）

**前置门禁**：M0 实测第 6 项 —— Qwen 目标模型**流式首 token <10s**。
**不达标**：触发 GPU 评估，或量化版换小模型。
**选型**：Qwen2.5/Qwen3（Apache-2.0）；嵌入模型 BGE-M3 / nomic-embed-text。
**注意**：请求需并发限流与批处理，**不得拖慢实时告警链路**；实时规则检测**不依赖 LLM**。

> **本环境落地（2026-09-18）：Ollama 已上线，M0 第 6 项实测出结论**。
> | 项 | 内容 |
> |---|---|
> | 镜像/端口 | `ollama/ollama@sha256:da6e0dc5…`（版本 0.34.2，**镜像 9.19 GB**）；**仅回环 `127.0.0.1:11434`** + SSH 隧道 |
> | 资源/纪律 | **8 vCPU / 16 GB**（4.2 基线，实测峰值 CPU 62.8%、内存 1.26 GiB）；`OLLAMA_MAX_LOADED_MODELS=1`、`NUM_PARALLEL=1`、`KEEP_ALIVE=10m`（**并发受限 + 空闲卸载**，不拖慢实时链路） |
> | 模型 | `qwen2.5:3b`(1.9GB) / `qwen2.5:1.5b`(986MB) / `nomic-embed-text`(274MB)；模型仓库**实测可达 9 MB/s** |
> | **M0 第 6 项** | 短 prompt 双档通过（3b 0.08s / 1.5b 0.02s）；**首次长上下文（≈5.2k tokens，类 RAG）**：**3b 12.61s ❌ 不通过**、**1.5b 6.55s ✅ 通过（余量不足 1/3）** ⚠️ **2026-09-19 口径更正**：Ollama **默认 `num_ctx` 仅 2050 tokens**，该样本**实际只处理了 2050 tokens**（超长 prompt 被静默截断）；结论不变、口径收窄，详见《…S4-1…》§3.3 |
> | **判读口径（关键）** | **复测的 0.08~0.10s 是 prompt cache 复用，不代表真实 RAG**；门禁须以"**首次处理该上下文**"为准 |
> | 建议（待人工决策） | 本环境默认取 **1.5b**；或把 **RAG 上下文预算限到 ~3k tokens** 后用 3b 复测；或按门禁原文**触发 GPU 评估**。**准确率在本环境不可判读**（4.1.3 明文） |
> | 容器数 | 18 → **19**，**单机版全量组件部署完成（本环境口径满额）** |
> 详见《AI数据治理平台_单机版_S4-1Ollama部署与首token实测.md》。**磁盘预算更正**：Ollama 实际 ~12.2 GB（镜像 9.19 + 模型 3.0），而非 0.3.1 估的 ~2 GB。
>
> **运行面告警（2026-09-19 补）**：Ollama **无 `/metrics`**（实测 404）、Prometheus 抓取目标 0，且本环境 **cAdvisor 取不到逐容器指标**（Docker 29.8.1 用 containerd 镜像存储、layerdb 的 `mount-id` 为空，cAdvisor v0.45.0 建不出容器 ⇒ **「目标 up」≠「有数据」**）⇒ 改由 **`ollama_probe.sh`（cgroup v2 + `/api/ps`，每分钟，独立 textfile 文件）** 采集，并新增 **`ollama-runtime` 组 7 条规则**（API 不可达 / CPU 饱和 / 内存高 / **OOM kill** / 模型不卸载 / 探针停摆 / 指标缺失）⇒ **规则总数 27 → 34**；真实 fire/resolve 与模型加载/卸载均已实测。**cAdvisor 的修复属组件版本变更，需人工决定**。

### S4-2 pgvector 向量索引 + RAG（第 2~3 周）

- pgvector 向量索引
- 元数据/血缘摘要向量化管道（离线定期刷新）
- LangGraph RAG 问答链路
- **引用程序化校验**：回答引用的表/字段名必须在 OM 中真实存在，否则标注「未核实」或拒绝返回
- 门户聊天界面对接 GA API

> **本环境落地（2026-09-18）：最小 RAG 闭环已上线**（迭代式交付）。
> | 项 | 内容 |
> |---|---|
> | 向量存储 | **pgvector 0.8.6**；`governance_agent` 库的 **`rag` schema**（`rag.doc`，`vector(768)` + **HNSW 余弦索引**）——**GA 自持库**，不碰 `gov_metrics`、**不读 OM 内表** |
> | 模型 | 嵌入 `nomic-embed-text`（768 维）；生成 **`qwen2.5:1.5b`**（档位依据 S4-1 首 token 实测：3b 长上下文不达标） |
> | 主链 | `retrieve()`（pgvector 余弦 top-k）→ `generate()`（强约束 prompt）→ **`verify_citations()`**（引用 FQN 必须真实存在，否则**标注「引用未核实」**） |
> | 定期刷新 | cron **每小时 :40**（滞后 ETL :25 避抢 IO）；**最长滞后 1 小时** |
> | 验收 | 正例正确引用资产；**负例（问不存在的表）→ 答「元数据中未找到」（不编造）**；引用校验**真引用 0 未核实 / 假引用 3 个全部标注**；19 容器 healthy |
> | **LangGraph（按设计）** | ✅ **已按设计引入**：`retrieve → generate → verify → END` 图（`/chat` 回答尾部可见节点轨迹）；依赖 `langgraph 1.2.11` / `langchain-core 1.6.3` / `langchain-ollama 1.1.0`（**均 MIT**）；镜像 294MB、**重扫未新增 HIGH/CRITICAL** |
> | **上下文预算（2026-09-19 新增）** | ✅ **整条 prompt ≤ 3000 tokens**（`GA_RAG_CTX_BUDGET_TOKENS`）、显式 **`num_ctx=4096`**（`GA_RAG_NUM_CTX`）、保守换算 1.5 字符/token、按相关度裁剪；`/chat` 返回预算口径行（估算/**实测 token**/采用篇数/丢弃篇数）。实测：裁剪后 prompt **2,491 tokens（>2050，证明默认上限已被抬高）**、**预算内流式首 token 冷启动 7.01 s**；GA 镜像重建 `2c8b39e9…`、**重扫 HIGH+CRITICAL = 0** |
> | 局限 | 当前仅 **1 篇**向量（OM 自举资产）⇒ **接源后价值才体现**；**问答准确率在本环境不可判读**（4.1.3） |
> 详见《AI数据治理平台_单机版_S4-2RAG最小闭环记录.md》。

### S4-3 CI/CD 血缘「提示」（第 4 周）

PR 评论列影响面。**连续 4 周达标后才升级「阻断」**。

### S4-4 试运行与水位周报（Phase 4）

- 真实负载压测，对照 4.1.2 容量表校准（**容量数值为经验估算，作压测校准依据，不作 SLA 承诺**）
- **单机水位周报**：CPU/内存/磁盘，对照 M-Prod 触发条件 2（>70% 连续两周）
- 年度化备份恢复演练（PG PITR、OS snapshot、Flink Checkpoint 出机恢复）
- 试运行 90 天三问基线：周活用户数 / 识别准确率 / 告警处置率

> ✅ **水位周报落地注（2026-09-18）**：机制已建立并跑通。
> **交付**：`watermark-report/watermark_report.sh`（Prometheus API 取数 → 控制台 + `/data/ai-governance/reports/watermark-<YYYY-Www>.md`）与幂等安装器 `install_cron.sh`；cron `0 8 * * 1` 落在 `/etc/cron.d/ai-governance-backup`（**与 S1-8 备份任务同一文件**，连跑两次本任务仍只有 1 行、原 5 条备份/采集任务未受影响）。
> **口径**：触发条件 2 按《实施执行计划》原文——**两个相邻 7 天窗口（近 7 天 / 第 8–14 天）都 >70%** 才算触发；磁盘 ≥80% 记"记录、停止、转人工"；加盘阈值"可用 < 20 GiB"。
> **两次实测纠偏（重要）**：① **长窗 `rate()` 不可用于窗口均值**——`avg(rate(node_cpu_seconds_total{mode="idle"}[7d]))` 实测 **0.315**，同窗口子查询法为 **0.992**（相差近 80 倍，`rate` 会按外推补足窗口）；现统一用 `avg_over_time(rate(metric[5m])[d:5m])`。② **必须先实测数据跨度再判定**——本环境 Prometheus 实测只有 **2.22 天**数据，首版据此把 `[7d]`/`[14d]` 失真值当"未触发"上报属**用不足数据下倾向性结论**；现新增 `SPAN` 门槛，跨度 <14d 一律记 **「数据不足，无法判定（需连续 14 天）」**。
> **磁盘**：不做线性外推（镜像拉取是台阶式，`deriv`/`predict_linear` 会给出 −252 GiB 这类无物理意义的值），只报**实测净变化**（近 24h −20.7 GiB、全窗口 −34.2 GiB；主因 **29 个镜像 34.08 GB**）与折算速率。
> **首次报告（2026-W38）**：CPU 1.2% / 内存 10.7% / 磁盘 47.1%，可用 **51.9 GiB**（**未命中**加盘阈值与 80% 线）；平台面 19 容器 0 非 healthy、规则 21、目标 5/5。三项判定均为**数据不足**，**Agent 未做触发与否的判读**。证据见《AI数据治理平台_单机版_S4-4水位周报机制记录.md》。

> ✅ **容量基线（自压）落地注（2026-09-19）**：S4-4 的"真实负载压测"**前置自压骨架**已建立并跑出首版基线（`capacity-baseline/`：主脚本 + OpenSearch 子项 + README）。
> **首版结果（标准模式）**：PG 读写 **1342 tps** / 只读 **12,102 tps**（均**经 PgBouncer 6432**）；Kafka 生产 **60,423 records/s（59.0 MB/s）**；OpenSearch 写入 **100,806 docs/s**（`errors=false`、落库 50000/50000）；Ollama 1.5b **33 tok/s** / 3b **17 tok/s**；服务 API 全 200、0.5~33 ms。
> **现场自查**：临时库/Topic/索引**零残留**、压测后**零非 healthy 容器**。
> **四条实测踩坑**：① 经 PgBouncer 建库后 **DROP DATABASE 会被池内连接挡住**（须先 `RECONNECT`），否则留下残留库；② bulk 的**索引必须出现在 URL 里**（只写 `/_bulk` 会报 index is missing）；③ **被拒的 bulk 依然"很快"**，只看耗时会把 docs/s 算虚高（曾误得 18 万）；④ `refresh=false` 时 `_count` 为 0，需显式 `_refresh` 后再计数。
> **边界**：这是**自压基线、不是真实业务负载**；门禁① 已人工豁免，故**不得用于准出判读**。真实负载压测仍待业务源。证据见《AI数据治理平台_单机版_S4-4容量基线记录.md》。

> ✅ **年度化恢复演练（本地段）落地注（2026-09-19）**：`restore-annual-drill/run_annual_drill.sh`（**复用** S1-8 既有 PG/OS 演练脚本，新增 §0 新鲜度、§3 配置归档校验、§4 Flink Checkpoint 可测性、§5 汇总）。
> **实测**：PG **6 库全部对象数一致**（合计 6s，临时库残留 `(none)`）；OpenSearch 快照**恢复后文档数一致**（2s）；配置归档 31 条目、`.env` 非空、compose 含 services、**密钥键名 24/24 与现网一致**；对象级恢复合计 **8s**（RTO 目标 4h）；PG 备份年龄 0.02h（RPO ≤1h）。
> **如实记录未覆盖 5 项**：**PG PITR 在本环境不存在**（未启用 WAL 归档 ⇒ 不得把 dump 恢复说成 PITR）、**Flink Checkpoint 出机恢复**（0 checkpoint / 0 作业，且出机介质未配）、**出机副本恢复**（`OFFSITE_TARGET` 未配置）、**主机级重建 RTO**、真实业务量恢复。详见《AI数据治理平台_单机版_S1-8备份恢复脚本.md》§5.2。

> ✅ S4 准出门禁：问答准确率抽样达标；CI/CD 提示在 ≥1 仓库生效；完成一次真实备份恢复演练；M-Prod 评审有书面结论（**无论是否触发**）。

---

## 附 A：单机版门禁速查（四道）

| # | 门禁 | 位置 | 不通过怎么办 |
|---|---|---|---|
| ① | 服务器验收（fio / 容量） | S1-1 前 | 拒收，重提采购 |
| ② | 备份可恢复（**RPO≤1h**/RTO≤4h） | S1-8 | 修脚本重验，人工签字 |
| ③ | **T-M0-1** OM×PgBouncer transaction | S2-4 | **不自动降级**，DBA+DE 决策 session/直连 |
| ④ | 合规边界 + 续接窗口签约定 | S3-1 | **不动生产库** |

另有一项收口在 S3：**PG 复制槽保护三项验证**（兜底 + 告警 + 删 slot 演练）。

## 附 B：单机版四道书面产物（归档要求）

| 产物 | 产出段 | 责任人 |
|---|---|---|
| 服务器验收报告（fio 结果） | S1-1 | INFRA |
| 恢复演练记录（DBA 签字） | S1-8 | DBA |
| 合规边界确认书 | S3-1 | SEC + 合规方 |
| 续接窗口签约定（按源库分列） | S3-1 | DBA |

> **S1 两份已整理为待签署材料**：见《AI数据治理平台_单机版_S1准出材料包.md》文档 B（恢复演练记录）与文档 C（服务器验收/降级豁免，本环境因无数据盘走"人工豁免"路径）。S3-1 两份仍待跨组织签署。

## 附 C：待确认项（占位符，不作承诺）

| # | 项 | 卡在哪 |
|---|---|---|
| 1 | 全部镜像精确版本号 | 设计方案 6.2.3 声明「需按官方文档核对」；CDC 版本矩阵由 M0 第 2 项实测锁定 |
| 2 | ~~PgBouncer 是否 ≥1.21~~ | **已收敛（2026-09-16 S1 实测）**：实拉 1.24.1 ✓，`max_prepared_statements=200` 已启用，transaction 模式前提满足 |
| 3 | `max_slot_wal_keep_size` / `wal_keep_size` 数值 | 由续接窗口签约定结果推算，DBA 核定（**推导模板已备**，见《AI数据治理平台_单机版_S3-1门禁④材料包.md》§5.3；取值仍 `待确认`） |
| 4 | 数据盘设备名 | 服务器到货后 `lsblk` 确认 |
| 5 | 企业 IdP 回调域名 / 客户端 ID | P0-A SSO 申请结果 |
| 6 | Ollama 首 token 是否 <10s | M0 实测第 6 项，S4-1 前置 |

> **S1 段执行结果**：2026-09-16 已在实测环境（本环境最小测试环境形态）完成 S1 部署并通过可编程校验（7/7 healthy、`SHOW POOLS` 无 waiting、OpenSearch green、Prometheus 3/3 up、TLS 握手成功）；
> 2026-09-17 完成**安全面复查**（公网仅 22/443 可达、SSH 认证面高风险）与 **S1-8 备份链路落地 + 首次恢复演练**（RPO 口径已定 ≤1h）。
> **门禁②（备份可恢复）待人工签字放行**：出机链路未闭环、S1 阶段无业务数据两项限制见 S1-8 段落。完整证据与 12 项执行偏差见《AI数据治理平台_单机版_S1部署记录.md》，脚本见《AI数据治理平台_单机版_S1-8备份恢复脚本.md》。

---

## 附 D：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-16 | 首次产出：由《实施执行计划 v2.0-r1》与《设计方案 v3.2》拆解 S1~S4 单机版部署步骤，含四道门禁、四道书面产物与待确认清单 |
| v1.1 | 2026-09-16 | 新增 **0.3 本环境实测配置与降级声明**（实测规格、门禁① 人工豁免、磁盘低于 4.1.3 门槛、容器数 19 口径）、**0.3.1** 参数与保留期收紧、**0.3.2** 门禁差异表；S1-1 增加本环境适配注记（跳过 `mkfs.xfs`、内核参数与句柄上限持久化）。无架构与组件变更，设计方案/执行计划无需同步 |
| v1.2 | 2026-09-16 | S1 段执行后回写：链路图 S4 容器数 17→18、compose 附录标题 17→18 并加"以服务器实际 compose 为准"声明；附 C 第 2 项（PgBouncer ≥1.21）**标记已收敛**（实测 1.24.1）；附 C 增加 S1 执行结果与《AI数据治理平台_单机版_S1部署记录.md》指引 |
| v1.3 | 2026-09-17 | 0.3.2 表回写「主机安全」行（公网实测仅 22/443 可达、ufw 无第二道防线、SSH 口令爆破与根因、处置顺序）并新增「镜像可复现性」行（5 服务 `:latest`、注释版本与实际不符）；依据为 S1 记录 §3.7 复查补录。无架构与组件变更，设计方案/执行计划无需同步 |
| v1.4 | 2026-09-17 | **RPO 口径 15min→1h 全面回填**（人工决策，源文档《实施执行计划 v2.0》已发 v2.0-r2）：S1 准出表、0.3.2 门禁② 行、附 A 门禁速查；**S1-8 段落重写**为小时级三路备份设计（PG 逐库 dump + OS fs 快照 + 出机 hook）+ 指向新增《AI数据治理平台_单机版_S1-8备份恢复脚本.md》；附 C S1 执行结果补 2026-09-17 进展与门禁②未签字原因 |
| v1.5 | 2026-09-17 | 0.3.2「主机安全」行按 **§3.7.5 限源后核查**更新：22 已限源、近 24h 成功登录仅本机来源、非本机成功登录 0、失败次数精确值 37、**修正"fail2ban 未装 ⇒ 无自动封禁"的表述**（OpenSSH 自带 `PerSourcePenalties` 已生效）；"优先级最高"降为"残留待放行"。无架构变更 |
| v1.6 | 2026-09-17 | 新增**运维接入方式（方案 C）**：S1-7 补 SSH 隧道访问 Prometheus/Alertmanager UI 的说明与命令；compose 附录把两个服务的 `ports` 固定为 `127.0.0.1` 回环绑定，并注明 Alertmanager 需加 `frontend-net` 才使回环发布生效（S1 记录偏差 4 在本轮实测复现）。**对外暴露面零新增**（仍仅 443） |
| v1.7 | 2026-09-17 | 附 B（四道书面产物）指向新增《AI数据治理平台_单机版_S1准出材料包.md》：S1 两份（恢复演练记录、服务器验收/降级豁免）已整理为待签署格式 |
| v1.8 | 2026-09-17 | **S2-1 回写**：S1-4 增 **多角色认证陷阱**（新角色须设 `PGBOUNCER_AUTH_USER`）与 compose 片段；S2-1 增本环境落地注（采用 2.0.1 "自带基础设施"变体、镜像用 Docker Hub 名、OpenSearch JKS 信任库 + SAN 别名、口令 Base64、必须关闭自助注册），指向《S2接入方案》§8。无架构与组件变更 |
| v1.9 | 2026-09-17 | **S2-2 前置回写（ingestion）**：新增 5 条落地要点（必须带官方 entrypoint/command、Airflow 3 API 走 JWT 非 basic、**FERNET_KEY 须迁移前定版且两边一致**、Airflow 库可经 PgBouncer transaction（方案 A 通过）、8080 不发布）。指向《S2接入方案》§9 |
| v2.0 | 2026-09-17 | S1-6 增**自签证书的浏览器信任要求**：证书未受信 ⇒ Chrome/Edge 禁用 Service Worker ⇒ OpenMetadata 2.0.x 新 UI 登录后 401 循环（已实测复现并定位）；处置=导入受信任根证书 + 完全重启浏览器 + 清站点数据 |
| v2.1 | 2026-09-17 | S2-4（门禁③）增本环境执行状态注：27 项动作、双侧判据 0 命中、无等待、未降级，指向新增《AI数据治理平台_单机版_S2-4门禁③证据包.md》，并注明判定签字为人工专属 |
| v2.2 | 2026-09-17 | S2-5 增 OPA 本环境落地注（1.20.2 / distroless / healthcheck 覆盖边界 / `opa-policies/` 入 Git / 编译≠逻辑正确 / 8-8 决策用例 + fail-closed 实测），指向新增《AI数据治理平台_单机版_S2-5OPA策略与部署.md》 |
| v2.3 | 2026-09-17 | S2-5 增 **Presidio 本环境落地注**（analyzer+anonymizer healthy / 仅回环不接 Traefik / `presidio-recognizers/` 规则入 Git / 有-无上下文分数对照作为分层依据 / 两个运维要点：gunicorn 控制套接字与 fork 竞争、poetry 源码目录导入），指向新增《AI数据治理平台_单机版_S2-5Presidio部署与规则.md》 |
| v2.4 | 2026-09-17 | S2-5 增 **gov_metrics 与 ETL 落地注**（独立库+三张自持表 / 角色与库级 CONNECT 收敛 / ETL 只走 API + 经 PgBouncer 写入 + 每小时 :25 / 反向验证通过 / **PgBouncer idle 连接导致权限变更不即时生效**的运维要诀），指向新增《AI数据治理平台_单机版_S2-5gov_metrics与ETL.md》 |
| v2.5 | 2026-09-17 | S2-6 增 **Superset 本环境落地注**（6.1.0 仅回环+隧道 / **自建派生镜像补 PG 驱动**（lean 镜像无驱动、须装进 `/app/.venv`）/ 元数据库经 PgBouncer / 唯一只读数据源 + **正反验收通过** / API 要点：CSRF、sqllab 6.x 字段、图挂看板用 PUT），指向新增《AI数据治理平台_单机版_S2-6Superset与指标看板.md》 |
| v2.6 | 2026-09-17 | S2-7 增 **治理门户落地注**（自研 Streamlit、仅回环 8501、4 页面、**降级实测**、只读 gov_metrics + 反向权限通过、**构建用内网 PyPI 镜像 + trusted-host 的加速要诀**、S2 段 7 容器全部就位），指向新增《AI数据治理平台_单机版_S2-7治理门户.md》 |
| v2.7 | 2026-09-18 | **S3-1 门禁④ 就绪材料 + S3-4 参数推导模板**：S3-1 增"起草件已成稿"指引（**门禁口径不变**，仍以本节为准）；S3-4 增参数推导模板（兜底 `ceil(3×T_stop×R_WAL)`、阈值 70%、`safe_wal_size` 在兜底未配时为 NULL）、机制预验证注（本机 PG 16.15：建槽/取水位/删槽无残留，**不替代源库执行**）与判据② 三环节缺口（采集/规则/通知全缺，实测证据）；附 C 第 3 项补推导模板指引（取值仍 `待确认`）。指向新增《AI数据治理平台_单机版_S3-1门禁④材料包.md》。**未改架构、组件与门禁判据** |
| v2.8 | 2026-09-18 | **S3-1 门禁④ 通行回写（人工决定）**：S3-1 增状态行——人工（项目决策方）2026-09-18 确认通行；**门禁判据文字不变**（附 A 速查表只列位置与不通过处置、不带状态列，故未改）；写明通行后口径（源库变更仍按"Agent 起草 + DBA 审核后执行"；文件 B 取值未落定前不得在源库设 `max_slot_wal_keep_size` / `wal_keep_size`）。**Agent 未代签、未背书**；原件归档由 SEC+合规方 / DBA 完成。同步《…S3-1门禁④材料包.md》v1.1 与《单机版部署计划》v2.9 |
| v2.9 | 2026-09-18 | **S3 前置落地回写（Kafka / Flink / 告警链路 / 镜像固定）**：① S3-2 增落地注（KRaft 单节点、heap 512m、零端口发布、`CLUSTER_ID` 固定、`governance.alerts` 2 分区/RF1/7d、日志类 Topic 不预建）；② S3-3 增落地注（自建镜像含 base digest + 5 个 JAR 与 sha256、**`kafka-clients` 缺失的实测踩坑**、slots=2、冒烟与 Checkpoint 验证、Oracle CDC 与 OpenLineage 未纳入）；③ S1-7 增"告警规则载体补齐"注（`rule_files` 曾为空 ⇒ SLO 阈值无载体，现已补 3 条 SLO + 5 条复制槽规则；投递链路已证实、通知地址待定）；④ 0.3.2「镜像可复现性」行由"未达"改为"**15 个镜像已固定 digest**、校验和 `b70ae7a9…`"（**未重建容器**；漏洞扫描仍未执行）。指向新增《AI数据治理平台_单机版_S3前置部署记录.md》与 `alerting/`、`flink-image/` 两个代码目录。**未改架构、组件清单与门禁判据** |
| v2.10 | 2026-09-18 | **安全面与配置面收口回写**：① 0.3.2「主机安全」行由"残留待放行"改为**已闭环**（公钥登录 + `PasswordAuthentication no` + `PermitRootLogin prohibit-password`（实测密钥可用、口令被拒）、root 口令轮换、`fail2ban` 启用、**node_exporter 收紧为 `172.17.0.1:9100`**）；② S1-8 段补**配置出机**要求（新增 `backup_config.sh`：`.env` + `docker-compose.yml` + `config/`，0600/14d，cron 02:55，并入出机 rsync）——**FERNET_KEY 自持后 `.env` 必须随备份，否则恢复后 secrets 不可解密**；③ 附 C/正文说明 FERNET_KEY 已重做为自持 key（compose 校验和现行 `e35b560c…`）。证据见《AI数据治理平台_单机版_S3前置部署记录.md》§7~§9。**门禁判据与 RPO/RTO 口径未变** |
| v2.11 | 2026-09-18 | **443 收敛（SSO 前）+ OM 改走隧道**：① S1-6 增本环境变更注——**无固定企业 VPN/出口 IP ⇒ 不做限源而撤回暴露**：撤下 OM 的 Traefik 动态路由（保留 `.disabled-<ts>` 回滚副本）、**注释掉 traefik 的 `ports: ["443:443"]`**（宿主无 443 监听）、OM 增加 `127.0.0.1:8585:8585` 回环端口并给出合并隧道命令；**附带修复** gov_metrics ETL 基址（`https://localhost/api/v1` → `http://127.0.0.1:8585/api/v1`）与门户 OM 链接；② 0.3.2「主机安全」行更新为"**当前对外仅 22（已限源），无任何对外业务端口**"。compose 校验和现行 **`3ada7146…`**。**未改架构与门禁判据** |
| v2.12 | 2026-09-18 | **S3-6 GA 首版落地回写**：S3-6 段增落地注（自建镜像 `ai-governance/governance-agent:0.1.0`、仅回环 8085、独立库 `governance_agent` + `ga` schema 三表、`ga_writer`/`ga_ro`、经 PgBouncer、9 个接口、**门户降级结束**的验收）与**两条实测新纪律**（① 每个新库必须做库级 CONNECT 收敛，PG 默认给 PUBLIC 授权；② 权限变更后必须 `RECONNECT` 回收 PgBouncer 池连接）。容器数由"实时链路 3/4"补齐为 **4/4**（累计 **18** 容器 = 19 − Ollama）。指向新增《AI数据治理平台_单机版_S3-6治理Agent首版实现记录.md》。**未改架构、组件清单与门禁判据** |
| v2.13 | 2026-09-18 | **S4-1 Ollama 落地回写**：S4-1 段增落地注（镜像 `ollama/ollama@sha256:da6e0dc5…` 版本 0.34.2、**镜像 9.19GB**、仅回环 11434、8vCPU/16G、并发受限 + 空闲卸载）与 **M0 第 6 项实测**（短 prompt 双档通过；**首次长上下文 5.2k tokens：3b 12.61s 不通过 / 1.5b 6.55s 通过**；**prompt cache 导致复测失真，不得用于判读**）；容器数 18 → **19（本环境口径满额）**；0.3.1 磁盘预算中 Ollama 一行按实测更正（**~12.2 GB** 而非 ~2 GB）。指向新增《…S4-1Ollama部署与首token实测.md》 |
| v2.14 | 2026-09-18 | **S4-2 RAG 最小闭环落地回写**：S4-2 段增落地注（pgvector 0.8.6 + `rag.doc`(vector 768 / HNSW 余弦索引) 在 **GA 自持库**、不碰 gov_metrics 与 OM 内表；嵌入 `nomic-embed-text`、生成 `qwen2.5:1.5b`；主链 retrieve→generate→**verify_citations**；cron :40 定期刷新；正/负例与引用校验真假两态实测）与 **LangGraph 实现偏差声明（待人工确认）**。指向新增《AI数据治理平台_单机版_S4-2RAG最小闭环记录.md》。**未改架构与组件清单** |
| v2.15 | 2026-09-18 | **按设计引入 LangGraph 回写**：S4-2 落地注中的"设计偏差（待确认）"改为**已按设计引入**（`retrieve → generate → verify → END` 图、`ChatOllama` 客户端、依赖 `langgraph 1.2.11`+`langchain-core 1.6.3`+`langchain-ollama 1.1.0` 均 MIT）；镜像重建 205MB→294MB 并更新 compose digest；**重扫 Total 13（HIGH 10 / CRIT 3）与引入前一致，未新增高/严重漏洞**。同步《…S4-2RAG最小闭环记录.md》v1.2。**未改架构与组件清单** |
| v2.16 | 2026-09-18 | **S4-4 水位周报机制落地回写**：S4-4 段增落地注（脚本 + 幂等 cron 安装器 + 首份报告；触发条件 2 明确为**两个相邻 7 天窗口都 >70%**）+ **两条实测纠偏**：① 长窗 `rate()` 不可用于窗口均值（实测 0.315 vs 子查询法 0.992，差近 80 倍）；② **必须先实测数据跨度再判定**——本环境实测仅 **2.22 天**，首版把失真值当"未触发"属用不足数据下倾向性结论，现跨度 <14d 一律记"数据不足"。磁盘改为**不做线性外推**（台阶式增长，`deriv`/`predict_linear` 产出 −252 GiB 无意义值）；实测磁盘 47.1% / 可用 51.9 GiB **未命中**加盘阈值。指向新增《AI数据治理平台_单机版_S4-4水位周报机制记录.md》与代码目录 `watermark-report/`。**未改架构、组件清单与门禁判据；未做触发与否的判读（人工专属）** |
| v2.17 | 2026-09-19 | **S4-4 容量基线（自压）落地回写**：S4-4 段增落地注（`capacity-baseline/`：主脚本 + OpenSearch 子项 + README），首版基线 PG 1342 tps 读写 / 12,102 tps 只读（经 PgBouncer）、Kafka 60,423 rec/s（59.0 MB/s）、OpenSearch 100,806 docs/s（errors=false、落库 50000/50000）、Ollama 1.5b 33 tok/s / 3b 17 tok/s、服务 API 全 200。**四条实测踩坑**：PgBouncer 池连接挡住 `DROP DATABASE`（须先 RECONNECT）、bulk 索引须在 URL、被拒 bulk 仍"很快"会算虚高 docs/s、`refresh=false` 下 `_count` 为 0。**边界**：自压基线**不是真实业务负载**，门禁① 已豁免 ⇒ **不得用于准出判读**；真实负载压测仍待业务源。指向新增《…S4-4容量基线记录.md》与代码目录 `capacity-baseline/`（AGENTS §1 地图 + §7 表同步登记）。**未改架构、组件清单与 S4 准出门槛** |
| v2.18 | 2026-09-19 | **S4-4 年度化恢复演练（本地段）回写**：S4-4 段增落地注（`restore-annual-drill/`，**复用** S1-8 既有 PG/OS 演练脚本，新增新鲜度/配置归档/Flink CP 可测性/汇总四段）。实测 PG 6 库全一致（6s，残留 none）、OS 快照文档数一致（2s）、配置归档密钥键名 24/24 与现网一致、对象级合计 8s。**如实记录 5 条未覆盖**：**PG PITR 在本环境不存在**（未启用 WAL 归档 ⇒ 不得把 dump 恢复说成 PITR）、Flink CP 出机恢复、出机副本恢复、主机级重建 RTO、真实业务量恢复。证据见《…S1-8备份恢复脚本.md》v1.6 §5.2；AGENTS §1 地图 + §7 表登记 `restore-annual-drill/`。**未改架构、组件清单与门禁判据；判读仍属人工** |
| v2.19 | 2026-09-19 | **两项：OpenLineage 查证结论 + 出机介质暂缓（人工决定）**。① **S3-5 段增 OpenLineage 结论注**：官方文档源文件（`website/docs/integrations/flink/`）经 GitHub API 取到正文 —— **Flink 1.x 走 `JobListener`（须改作业代码、**不支持 Flink SQL**、须 Application Mode + `attached:true`）**，**Flink 2.x 走原生 SPI（FLIP-314，支持 SQL、无需改代码）**；本地实测：fat jar 的 `Premain-Class` 声明**是错的**（⇒ **javaagent 永久关闭**）、**Flink 1.20.1 中该 SPI 0 命中** ⇒ **当前 1.20.1 + Flink SQL 拿不到血缘**；给出 A/B/C 三选一与 **Agent 建议（短期取 C）**，**属人工决策，Agent 未升版本未改形态**。② **S1-8 段增⏸️人工决定注**：出机介质暂缓、备份类事项本轮不推进 ⇒ **出机仍未闭环，门禁② 该项判据继续挂账**（不视为完成）。证据见《…S3前置部署记录.md》v1.13 §12.7/§12.8 与《…S1-8备份恢复脚本.md》v1.7 §9。**未改架构、组件清单与门禁判据** |
| v2.20 | 2026-09-19 | **S3-6 告警汇入 GA 落地回写**：S3-6 段增落地注——AM 空 receiver 导致 21 条规则告警"有判定、无留痕"，故新增接收器 `ga-ingest` → GA `POST /api/v1/alerts/ingest` → `ga.alert_event`（`source='alertmanager'`，幂等键 `alertmanager:<fingerprint>:<status>`，Bearer 令牌强制校验）。验收：401 未授权 / stored=1 / duplicates=1 不新增行 / resolved 变体新增行 / **真实端到端**（AM 注入 → GA 收到 firing，AM 无错误）/ 镜像重扫 Total 13 与改动前一致。**三条踩坑**：容器网内互访不需改端口发布、`build` 前必须去 digest（新 `09421ae7…`）、AM 未启用 lifecycle API 须 `SIGHUP`。**边界：留痕 ≠ 通知；门户无告警页**。指向新增 `alerting/alertmanager.yml.example` 与《…S3-6治理Agent首版实现记录.md》v1.1 §7。**未引入新组件、未改规则与阈值、未改架构与门禁判据** |
| v2.21 | 2026-09-19 | **S3-5 OpenLineage：A 选项前提核实为"不成立"**——`flink-cdc` 源码树**含 lineage 的路径 = 0**（未实现 FLIP-314 的 `LineageVertexProvider`）；Flink 官方血缘文档原文写明连接器覆盖是"**gradually**（Kafka、JDBC、Cassandra、Hive）"且"**customized connector 需自行实现 `LineageVertexProvider`**"；`flink-connector-kafka` 则有整包 `.../connector/kafka/lineage/`（**Kafka 已实现**）⇒ **仅升 Flink 2.x 拿不到 CDC 源血缘**。S3-5 段据此增补 **A′**（血缘边界落 Kafka，与 ADR-A1 冲突须先改 ADR）与 **D**（自研侧向 OM 注册作业图级血缘，最贴合 ADR-A7 原文、属机制变更需批准）两条路，并更新 Agent 建议（**硬需求走 D，否则维持 C**）。证据见《…S3前置部署记录.md》v1.14 §12.9。**未升版本、未改架构与门禁判据；属人工决策** |
| v2.22 | 2026-09-19 | **S3-5 作业图级血缘 D1 落地回写**：S3-5 段增落地注 —— **不再依赖 Flink 血缘接口，改为 GA 自研上报**（解析 Flink SQL → OM 建/删边，`source=PipelineLineage` + `sqlQuery`）。口径：**宁缺勿错**（不确定即 400 且不落行）、映射显式、幂等键 `(job_key, from, to)`、变更删旧边、Bearer 令牌。**端到端验收**：建临时实体 → `applied` → OM 读回下游边（含 sqlQuery）→ 幂等不重复 → 换 sink 后旧边删/新边生效 → **清理零残留**（tables/schemas/databases/services 全 0）；**镜像重扫 Total 13 与改动前一致**。**边界：OM 无实体时边必然 404（真实业务边待接源）；只做表/主题级边；无周期对账（OM 删实体连带删边 ⇒ 已知漂移）**。**待人工：ADR-A7 实现方式回写**（现文写 OpenLineage）。指向新增《…S3-5血缘上报D1记录.md》；AGENTS §1/§7 同步。**未改架构组件清单与门禁判据** |
| v2.23 | 2026-09-19 | **两项：D1 对账任务落地 + ADR-A7 回写完成**。① **对账**：新增 `POST /api/v1/lineage/reconcile`（`repair` 自愈）+ `lineage_reconcile.sh` + cron 每日 04:10；**只新增/修复、绝不删 OM 上的边**；漂移闭环实测（无漂移不误报 → 删边后 `drift=1` → 自愈 `repaired=1` 且 OM 可见 → 不重复修复 → 零残留）；**新增规则 `LineageReconcileStale`（>30h）⇒ 规则 21 → 22**；新镜像 `b4d3f29b…`、compose `21168b4b…`；镜像重扫仍未变（Total 13）。② **修掉一个严重回归**：采集器新增任务的成功戳缺失 ⇒ 指标行空值 ⇒ **node_exporter 拒收整个 textfile，所有新鲜度指标一度全部消失**（日志实证）⇒ 全量 `num_or0()` 兜底 + 落盘自检 + 补 `# HELP`，修复后 `promtool check metrics` rc=0、指标数恢复 6、零错误。③ **ADR-A7 回写**（人工授权）：《设计方案》**v3.2 → v3.3**，ADR-A7 新增"上报机制"行并同步 §1/§3.3/场景 C，**决策要点与组件清单未变**；《实施执行计划》无需同步。指向《…S3-5血缘上报D1记录.md》v1.1 与《…S3-6治理Agent接口规格.md》v1.4 |
| v2.24 | 2026-09-19 | **D1 自主触发（提交即上报）落地**：新增 `submit_flink_job.sh`（干跑门禁 → 登记 → 提交 → 上报；门禁不过**不提交**）与 `lineage_autoreport.sh`（遍历登记目录 `/data/ai-governance/jobs/*.sql`，全部成功才对账写成功戳），cron 改为每日跑自动上报。验收：好作业写入 OM（`edge_exists=True`）、坏作业 `rc=3` 且**未调用提交**、登记为空时直接对账、零残留。**同轮修复第三个真缺陷**：dry-run 写 `planned` 导致 `apply=true` 跳过推送 ⇒ **边永远进不了 OM**（"先干跑再提交"正是主路径）——改为"只有 `status='applied'` 才算已在 OM"。**另更正镜像漏洞数字**：GA 重建取用了更新后的基础（debian 13.6→13.7）⇒ **HIGH/CRIT 由 10/3 降为 0/0**（门户/superset 未重建仍 13 与 100，构成对照）；**连带暴露"基础镜像未固定 digest"的偏差，建议固定（需人工决定）**。指向《…S3-5血缘上报D1记录.md》v1.2、《…供应链漏洞处置清单.md》 |
| v2.25 | 2026-09-19 | **基础镜像固定 digest + 门户重建（人工批准）**：把 `governance-agent` 与 `governance-portal` 的 `FROM python:3.12-slim`（浮动 tag）改为 **`python:3.12-slim@sha256:23b5dc88c7dd…`**（debian 13.7）——动因是 GA 重建时发现**浮动 tag 会静默换基础**（debian 13.6→13.7，HIGH/CRIT 由 10/3 变 0/0）。**结果**：两镜像重扫均 **0/0**（门户 13 → 0、GA 保持 0）；两容器重建后 healthy、GA `/ready` 正常、门户 `/_stcore/health` 200 且 `portal_ro` 经 PgBouncer 读到 gov_metrics（392 行），日志无异常。新 digest：GA `86cab9c6b590d…`、门户 `d86b159fc93f4…`；compose 校验和现行 **`05da87bd5e9b3f2f…`**。**踩坑留痕**：本地 tag 与"新 digest"须分别核实——曾误钉旧的 `78387bc3881b…`（重扫又回 13），`docker pull` 后才拿到真正的 `23b5dc88c7dd…`；**钉完必须重扫验证**。同步《…供应链漏洞处置清单.md》《…S2-7治理门户.md》v1.2 与两个代码目录 README。**未改架构、组件清单与门禁判据** |
| v2.26 | 2026-09-19 | **S2-6 Superset 派生镜像重建 + 钉版（人工批准）**：① `FROM apache/superset:6.1.0` 改为**按 digest 固定**（`@sha256:16b50bbef664…`；`docker pull` 实测该 tag 已是最新 ⇒ 属"写死现状"）；② `uv pip install psycopg2-binary` **原先未钉版本** ⇒ 改为 **`==2.9.13`**；③ **更正重建方式**：superset 是**手工 `docker build`**、compose **无 `build` 段**（`docker compose build superset` 实测 no-op）。**结果**：新镜像 `0b42de36db7d…`；**漏洞数不变 100（HIGH 100/CRIT 0）**——逐目标与重建前一致（debian 67 + python-pkg 33）⇒ **重建无收益**；构成中 **64 条来自 `linux-libc-dev`（内核头文件包）**，33 条 Python 依赖（pillow 13 为主）。**验收复跑通过**：healthy、`/health` 200、`superset_ro → gov_metrics` 可读（400 行）、**反向连 `openmetadata_db` 被拒**、API 登录取到看板 1 个。compose 校验和 **`b154e94b6f212459…`**。**要真正降漏洞须人工批准**：升 Superset 版本，或显式升级个别 Python 依赖。同步《…S2-6Superset与指标看板.md》v1.3、《…供应链漏洞处置清单.md》与 `superset-image/README.md` |
| v2.27 | 2026-09-19 | **新增《接源前检查清单》**（S2-2 段加指引）：把"接第一个业务源当天"要做/要确认的事列全，分四段——**T-3~T-1 提前拿**（源库连接信息、只读与复制权限账号、网络可达、`T_stop`、**实测 `R_WAL`**、**S2 准出 4 项口径拍板**、凭证落地）/ **T-1 Agent 可自主准备**（分母工具就绪、基线快照、备份链验证、告警阈值待算项标注、**D1 血缘登记准备**、磁盘余量、演练窗口、缺口书面确认）/ **当天 S1~S11**（G0 复核 → 源库参数落值 → OM 建连 → 首次 ingestion **建出表实体** → 分母盘点 → CDC 作业提交（走 `submit_flink_job.sh`）→ 血缘自动生成与对账 → PII 批量识别 → topic 规划 → S2 三项指标取数 → S3-7 三项演练）/ **T+1 对账**。含 **G0/G1/G2 三道 Go/No-Go**（**G0＝门禁④ 两份原件归档**，硬约束 4）与**当前 9 项空白与缺口**（源库信息、原件归档、`T_stop`/`R_WAL`、4 项口径、出机暂缓、IM 未配、门禁②③ 原件、8 条风险接受、镜像钉版已闭环）。**未改架构、组件清单与门禁判据** |
| v2.28 | 2026-09-19 | **Flink 作业 / Checkpoint 告警链落地**：S3-6 段所述"最高优先级告警"的另一半（此前**整条链空着**：reporter 未启用、无抓取、无规则）已补齐 —— JM/TM 启用 **Prometheus reporter（9249）**、`prometheus.yml` 加 `job_name: flink`（容器网内、**不发布宿主端口**）、新增 **`flink-jobs` 组 5 条规则**（抓取失败 / 登记了作业但没在跑 / 作业重启 / Checkpoint 失败 / Checkpoint 停摆）⇒ **规则总数 22 → 27**；采集器新增 `ai_governance_lineage_registered_jobs`。**真实触发验证**：datagen 演练作业 RUNNING（Checkpoint 4/0）→ 取消 → 规则 `pending`（`for` 到点 firing）。**实测踩坑**：跨源 `A and B` 因标签集不同**永不匹配**（规则永远 inactive）⇒ **必须写 `on()`**；纪律：新规则必须真实触发验证。**未改架构、组件清单与门禁判据** |
| v2.29 | 2026-09-19 | **接源预演并回写检查清单 v1.1**：在 POC 自建库建 mock 源（4 表 + 1 视图 + 分区父子 + 合成数据），把"建实体 → 血缘 → 覆盖率分母"真跑一遍后**全部清理**（mock 库 0、OM 实体 0、GA 血缘行 none）。**五条发现**：① 源库 `wal_level` 默认 `replica` 而 **Flink CDC PG 源必须 `logical`**；② 改它**需重启 PG ⇒ 属停机窗口**（接源当天才发现即作废）；③ **OM 有表实体后 D1 能产真实血缘边**（`applied` + OM 读回）；④ 覆盖率分母端到端 **100%（6/6）**；⑤ `coverage_denominator.py` 的 **`--exclude` 只按 schema**（排除不了分区子表）且**宿主侧须显式 `OM_BASE`**。已回写《…接源前检查清单.md》§1/§2/§3/§7。**未改架构与门禁判据** |
| v2.30 | 2026-09-19 | **GA 补齐 Flink 5 条规则的 `ADVICE` 增强模板**：此前日报与告警视图的"增强摘要"列为空。**验收**：合成 AM payload → 落库 `enhanced_summary` 已填充 → **告警日报摘要列显示该建议**；探针行已清（残留 0）。新镜像 `1e64eb59…`、compose 校验和 **`d87f85ba6237d09a…`**、**重扫 0/0**（基础未变）。**注意**：`enhanced_summary` 入库时写入、**不回填历史行**；新增规则须同步加模板（已写入 `governance-agent/README.md` 纪律）。同步《…S3-6治理Agent首版实现记录.md》v1.2 与 `alerting/README.md`。**未改架构、组件清单与门禁判据** |
| v2.31 | 2026-09-19 | **新增《组件部署状态》一页归档**：0.3 段加指引。该页给出"该部署的组件是否都已部署"的**逐项对照**——19 容器全部 healthy（覆盖设计组件清单全部条目）、宿主侧 4 件（3 exporter + fail2ban）全 active、**未部署三类**（触发式组件按纪律不启用 / M-Prod 三节点未到阶段 / **`oauth2-proxy` 是唯一"该有还没有"的，缺 IdP 回调域名与客户端 ID**），并附运行面证据（27 规则 / 7 抓取目标 / 6 新鲜度指标 / 4 份周期报告 / **对外仅 22**）与**复核触发条件 5 条**。**结论：组件层面无缺口**。AGENTS §1 文档地图同步登记。**未改架构、组件清单与门禁判据** |
| v2.32 | 2026-09-19 | **Ollama 运行面告警（C 项）+ RAG 上下文预算（B 项）**。① **C**：Ollama 0.34.2 **无 `/metrics`**、本环境 **cAdvisor 取不到逐容器指标**（根因实测：Docker 29.8.1 用 **containerd 镜像存储**，`layerdb/mounts/*/mount-id` 为空，而 cAdvisor 是 **v0.45.0**）⇒ 新增 **`ollama_probe.sh`**（宿主 cgroup v2 + `GET /api/ps`，**独立 textfile 文件 + 独立 cron，每分钟**）与 **`ollama-runtime` 组 7 条规则**（API 不可达/CPU 饱和/内存高/**OOM kill**/模型不卸载/探针停摆/指标缺失；阈值沿用 S1-7 的 80%/85%，分母取容器限额）⇒ **规则总数 27 → 34**。验收：fire→AM→GA 落库→resolved 全周期、模型加载/卸载路径、幂等与 401、7 条规则还原后全 `inactive/ok`、探针分钟级刷新零报错。② **B**：实测 **Ollama 默认 `num_ctx` 仅 2050、超长 prompt 被静默截断**（8,158/24,558 字符都只处理 2050）⇒ **更正 S4-1 的"≈5.2k tokens"口径**；落地**整条 prompt ≤3000 tokens + `num_ctx=4096` + 1.5 字符/token 保守裁剪**，`/chat` 暴露预算口径行；实测 2,491 tokens（>2050 证明生效）、**流式首 token 冷启动 7.01 s**。GA 镜像重建 **`2c8b39e9…`**（296.9 MB）、**重扫 HIGH+CRITICAL = 0**；compose 校验和 **`82c9ebb00aa670a5…`**。**未改架构、组件清单与门禁判据**；**cAdvisor 修复与档位/准确率判读仍属人工决策** |
| v2.33 | 2026-09-19 | **cAdvisor 修复尝试定稿（外部阻塞）+ 通用容器运行面自采（①）+ 镜像回收（③）**。① **修复尝试**：升级镜像不可行（DaoCloud 拦非白名单仓库、gcr/quay 不可达、可用镜像站上 cadvisor 最高仅 **v0.45.0**）、`--docker_only=false` 有序列但 **name 标签全空**、v0.45 的 containerd 集成无效 ⇒ 记为**外部阻塞**，cAdvisor 保留部署但容器级指标**以自采为准**。② **新增 `container_probe.sh`**（遍历 `docker ps` + cgroup v2：CPU/内存/anon/file/OOM，标签 `container`+`name`；独立 textfile 文件 + 独立 cron `ai-governance-containers`，每分钟）+ **`container-runtime` 组 6 条规则**（指标缺失/采集器停摆/**序列数不足**/**OOM kill**/内存近上限/CPU 持续饱和）⇒ **规则总数 34 → 40**；**Ollama 探针同步移除容器指标**（同一 series 单生产者）。**验收**：19/19 容器有指标且带真实名；**OOM 真实触发**（一次性 64MB 容器 → `oom_kill=1` → firing 落库 `13:35:32Z` → 清理 → resolved `13:40:32Z`，零残留）；**踩坑**：窗口式 `increase()` 会漏报"首次采集前就 OOM"，改累计计数判据。③ **镜像回收**：删未使用镜像与构建缓存（镜像 34.26→**29.01 GB**、缓存 2.93→**1.554 GB**，释放≈**6.63 GB**；`/` 可用 51→**57 GB**），**保留**自建镜像的三个基础镜像与 kafka_exporter 来源镜像。GA 镜像重建 **`394700abe6ed…`**、**重扫 0/0**；compose 校验和 **`6f7d40c6bc0fc1f6…`**。同步《…组件部署状态.md》v1.2、《…S3-6…》v1.4、`alerting/README.md` v1.2、《…供应链漏洞处置清单.md》v1.5。**未改架构、组件清单与门禁判据** |
| v2.34 | 2026-09-19 | **S4-1 档位复测（人工要求"尝试用 3b"）**：按 M0 第 6 项口径、`num_ctx=4096`、**每样本换上下文规避 prompt cache**，在预算内 **2,847 tokens** 下实测——**3b 冷启动 20.68 s / 热态 17.3~17.4 s ⇒ 不通过**（decode 15 tok/s、模型 1.84 GB）；**1.5b 冷启动 11.31 s（越线）/ 热态 8.3 s（余量 1.7 s）** ⇒ **更正 B 项早前的"冷启动 7.01 s"为单次乐观样本**，`GA_RAG_CTX_BUDGET_TOKENS=3000` 实为**贴线取值**。走生产 RAG 链的质量样例：两者都不编造、1.5b 更简洁准确。**本次未改任何部署配置**（模型测后卸载、`/api/ps` loaded=0）。**待人工决策**：档位（1.5b / 为 3b 触发 GPU 评估）、以及"压预算 ≤2,000 vs 保持 3,000 但消除冷启动（须同步调 `OllamaModelNotUnloading` 口径）"二选一。同步《…S4-1…》v1.3、《…S4-2…》v1.4。**未改架构、组件清单与门禁判据** |
| v2.35 | 2026-09-19 | **S4-1/S4-2 定档落地（人工决定：1.5b + 压预算 1800）**：① **取值比选**（判据=冷启动首 token <8.0 s，对 10 s 门禁留 20% 余量）——1,500 tokens 冷 6.88 s / **1,800 冷 6.93 s、热 4.85 s** / 2,000 冷 7.70 s（三者达标）⇒ 取 **1,800**（余量最充足；2,000 留作接源后上调备选）。② **落地**：compose `GA_RAG_CTX_BUDGET_TOKENS: "1800"` + `rag.py` 代码默认值同步（避免口径漂移）；GA 重建 **`44ca36b1e519…`**、**重扫 0/0**、compose 校验和 **`62aeeef899d2bede…`**。③ **验收**：`/health` `/ready` 200、`/chat` 正例 3.10 s / 负例 2.70 s、口径行 `261/1800 tokens`。④ **代价如实记录**：预算 1800 时同一批 12 篇检索结果只装得下 7 篇（3000 时 11 篇）。⑤ **未启动 GPU**（3b 达标需 GPU，属采购/评审）；"延长 keep_alive"方案未采用（与 `OllamaModelNotUnloading` 口径冲突）。同步《…S4-2…》v1.5、《…S4-1…》v1.4、《…组件部署状态.md》v1.3、《…供应链漏洞处置清单.md》v1.6。**未改架构、组件清单与门禁判据** |
| v2.36 | 2026-09-19 | **新增人工自测入口**：因"无固定企业 VPN + 未发布 443"，沿用既定口径走 **SSH 隧道**——本机一键脚本 `open-tunnel.ps1` 把 9 个只绑回环的服务映射到本机 127.0.0.1（**实测 9/9 通**、跨会话存活、`-Stop` 精确关闭），**不新增对外暴露**。新增《AI数据治理平台_单机版_测试入口与用例.md》（v1.0）：入口表 + 五组用例（场景 D 问答含防幻觉负例/引用校验、场景 E 审批、Superset、OM 与运维面、GA API）与期望结果；**口令只从服务器 `.env` 取、不写入仓库**。实测踩坑回写 AGENTS §7：**PS 5.1 读 `.ps1` 必须 UTF-8 with BOM**、**`curl.exe` 传中文 JSON 会被引号吃掉**。**未改架构、组件清单与门禁判据** |
| v2.37 | 2026-09-19 | **门户首页布局/交互修复（人工实测反馈）**：首页「智能问答（场景 D）」此前**只有就绪提示、没有提问框**，且把 GA `/health` 原始 JSON 直接贴出（看似报错）⇒ ① 状态行改**人话** + 原始返回移入折叠详情；② **首页就地可提问**（直接调 GA `/chat`）；③ 修正 OM 入口兜底默认值（→ `http://localhost:8585/`）。**验收**：门户重建 **`308e50f8ff4c…`**（重扫 0/0、compose `03d0ebf8014e4ee0…`）、healthy、`/_stcore/health` 200、深链 `?page=chat` 200；**Streamlit `AppTest` 无头跑四页异常数全 0** + 断言首页就绪行与提问框存在。同步《…S2-7治理门户.md》v1.3、《…测试入口与用例.md》v1.2、《…组件部署状态.md》、《…供应链漏洞处置清单.md》v1.7。**只改前端展示与交互** |
| v2.38 | 2026-09-19 | **门户首页区块拆分（人工第二轮反馈）**：把**系统状态（运维视角）**与**智能问答（场景 D）**拆成独立区块（首页顺序：能力入口 → 系统状态 → 智能问答），问答区块只放输入与回答、未就绪时只给一行指引；**问答页不再显示就绪状态**。代码拆分 `_agent_health()`（探测）/ `_render_agent_status()`（渲染，仅系统状态区块调用）。**验收**：门户重建 **`e1fe07781a23…`**（重扫 0/0、compose `3d09d579b0628d28…`）、healthy、`/_stcore/health` 200、首页 200；**`AppTest` 四页异常数全 0** + 断言首页三区块 subheader 与"问答页 success 为空"。同步《…S2-7治理门户.md》v1.4、《…测试入口与用例.md》v1.3、《…组件部署状态.md》、《…供应链漏洞处置清单.md》v1.8。**只改前端布局** |
| v2.39 | 2026-09-20 | **治理门户视觉重构上线（人工要求"按 `mockup.html` 重新设计"）**：按 `design/design-system.md`（含 **Streamlit 落地映射** A/B/C 类归属）落地——`theme.py`（`--gv-*` 令牌 + 组件 CSS + `emit_config_toml()`）、`.streamlit/config.toml`、`app.py` v1.5（原生控件 + `.gv-*` 自绘组件，**零硬编码色值**）、`design/` 三件套、`tools/` 4 个校验脚本（令牌/结构/解析/AppTest）。**IA**：导航 **5 页**、首页改**入口网格**（2 站内模块 + 5 外部深链），系统状态与智能问答各自成页（系统状态在**末位**）。**验收**：令牌 64 项一致 + 深色 31 项覆盖 + **WCAG AA 28 组（最低 4.14:1）**；mockup **42/42**；解析 26 项；**AppTest 降级 62/62、就绪 57/57**；容器 healthy、5 深链 200；镜像 **`650a57b42570…`**、**重扫 0/0**、compose **`d030657ae80c5bd1…`**。**同时修掉回归脚本的环境依赖缺陷**（`setdefault` → 强制覆盖 + `PORTAL_TEST_AGENT_URL` 开关）。**只改前端与设计资产，未改数据口径/暴露方式/门禁判据** |
| v2.40 | 2026-09-20 | **门户首页入口卡隐藏 URL（人工要求）**：卡片原显示 `localhost:8585` 一类地址 ⇒ 站内卡 hint 改 `站内模块`、外部卡改 `外部界面 · 需隧道`（非隧道为 `外部界面`），移除每卡重复的"需隧道"小字，改由首页底部口径条统一说明；**地址细节只保留在「系统状态」页的依赖服务清单**；`design/mockup.html` 同步（7 处 hint）以保持"设计稿 = 视觉基准"。**新增回归断言**：剥离 `href` 后检查入口卡**可见文案**不含 `localhost/127.0.0.1/http(s)://`。**验收**：镜像 **`ba381aa10dad…`**、compose **`ca2d8c791bce6391…`**、**重扫 HIGH+CRITICAL = 0**（用 `--skip-db-update`，2 秒；trivy 默认更新库在本环境常达数分钟甚至超时）、5 深链 200、首页 `URL 命中 = (无)`；`check_tokens` ✅ / `check_mockup_nav` 42/42 / `verify_mockup_parse` 26 / **`AppTest` 降级 63/63、就绪 58/58**。同步《…S2-7治理门户.md》v1.6、《…组件部署状态.md》v1.6、《…供应链漏洞处置清单.md》v1.10。**只改前端与设计资产** |
| v2.41 | 2026-09-20 | **「需隧道」改为徽标（人工要求）**：按设计稿把该信息由 hint 文案改为 `gv-badge`（`gv-badge--info`，挂在 `gv-entry__name`），外部卡 hint 只留 `外部界面`；去掉冗余的 `打开 ↗` 徽标（卡片右侧已有 ↗）。`design/mockup.html` 同步收敛 5 处 hint。**新增 2 条回归断言**：徽标内须有「需隧道」、hint 文案内不得有。**验收**：首页 5 个 `需隧道` 徽标（共 21 badge）、hint `['站内模块','外部界面'…]`；`AppTest` **降级 65/65 / 就绪 60/60**；`check_tokens` ✅ / `check_mockup_nav` 42/42 / `verify_mockup_parse` 26；镜像 **`a8ac7d049c7d…`**、compose **`bc24dd9171aff05e…`**、**重扫 0/0**、5 深链 200、容器 19/19。同步《…S2-7治理门户.md》v1.7、《…组件部署状态.md》v1.7、《…供应链漏洞处置清单.md》v1.11。**只改前端与设计资产** |
| v2.42 | 2026-09-21 | **脱敏整改（同步 GitHub 前）**：把服务器**公网 IP** 与**主机名**替换为占位符（`<POC服务器公网IP>` / `<POC服务器主机名>`），以满足 AGENTS §9「域名/敏感信息一律占位符」；**未改任何口径、结论与判据**（仓库已推送 GitHub，历史提交中的原值另行处理） |

---

*本文为单机版（POC）部署步骤，由《实施执行计划 v2.0-r1》与《设计方案 v3.2》拆解而成，不含新增架构决策。三节点 M-Prod 部署见《AI数据治理平台_部署步骤.md》S5。若执行中与源文档冲突，以源文档为准。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；命令与版本号执行前请按各组件官方文档核对。
