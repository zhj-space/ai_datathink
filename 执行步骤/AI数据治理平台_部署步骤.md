# AI 数据治理平台 · 部署步骤（由实施执行计划 v2.0-r1 拆解）

> **依据**：《实施执行计划 v2.0-r1》（阶段/门禁/任务卡）+《设计方案 v3.2》（4.1.2 单机容量档、4.2 单容器资源基线、6.3 单机 Compose 部署、6.2 三节点 k3s、第 9 章 8 条 ADR、第 11 章验收标准）。
> **口径纪律**：本文所有数字均回溯自上述两份文档，未出现的新数字标 `待确认`。命令中的版本号一律标 `需按官方文档核对`——设计方案 6.2.3 已就此声明。
> **执行前提**：POC 服务器 1 台（24 vCPU / 96GB / 2TB NVMe），到位并按《POC 服务器采购需求》第 4 节通过验收。**未通过验收不得进入 S1。**

---

## 0. 部署总览

### 0.1 四段部署链路

```
S1 单机底座 ──▶ S2 离线治理 ──▶ S3 实时链路 ──▶ S4 AI 与试运行 ──▶ S5 三节点生产化
   P0-B            Phase 1         Phase 2          Phase 3+4        M-Prod（触发式）
   2~3 周          3~4 周          3~4 周           3~4 + 2~3 周     4~6 周
   ≈6 容器          +OM/OS          +Flink          +Ollama          +3 节点 k3s
```

### 0.2 容器展开节奏（对应 4.2 资源基线，共 17 个）

| 段 | 新增容器 | 累计 |
|---|---|---|
| S1 单机底座 | PostgreSQL+pgvector、PgBouncer、OpenSearch、Traefik、Prometheus、Alertmanager | 6 |
| S2 离线治理 | OpenMetadata server、OpenMetadata ingestion(Airflow)、Presidio analyzer、Presidio anonymizer、OPA、Superset、治理门户(Streamlit) | 13 |
| S3 实时链路 | Kafka(KRaft)、Flink JobManager、Flink TaskManager、Governance Agent | 17 |
| S4 AI 与试运行 | Ollama（Governance Agent 已在 S3 部署，此处只加推理后端） | 17 |

> 与执行计划 §1「约 17 容器」一致。

### 0.3 部署期间的五条硬约束（违反即埋缺陷）

| # | 约束 | 来源 |
|---|---|---|
| 1 | Compose 是 POC 期**唯一部署真相源**；不得提前引入任何 K8s 配置 | 执行计划 §8 单机档纪律 |
| 2 | 所有服务经 PgBouncer 访问 PG，不允许有服务直连 5432 | 设计方案 4.3 |
| 3 | 全部容器 `restart: unless-stopped` + healthcheck | 执行计划 任务卡 P0-B-1 |
| 4 | S3 接生产源库前，合规边界书面文件与续接窗口签约定必须已归档 | 执行计划 Phase 1/2 门禁 |
| 5 | 任何 `unhealthy` 不自动降级、不自行改架构，记录停止并转人工 | 执行计划 任务卡 T-M0-1 |

---

## S1 单机底座部署（P0-B，建议 2~3 周）

### S1-1 服务器初始化（第 1 周）

**前置**：服务器到位并通过第 4 节验收（`nproc`=24 / `free -g`=96 / `lsblk` 系统盘 480G + 数据盘 2TB / fio 顺序写 ≥1GB/s、4K 随机读 ≥100k IOPS）。

```bash
# OS：Ubuntu 22.04 LTS（或 Rocky Linux 9）
# 1. 数据盘挂载到 /data（独立于系统盘）
lsblk -d -o NAME,ROTA,SIZE            # 确认数据盘 ROTA=0（NVMe）
mkfs.xfs /dev/nvme1n1                  # 设备名以实际为准，待确认
mkdir -p /data && mount /dev/nvme1n1 /data
echo '/dev/nvme1n1 /data xfs defaults 0 0' >> /etc/fstab

# 2. 目录规划（全部容器数据卷落在 /data）
mkdir -p /data/{postgres,opensearch,kafka,flink-checkpoint,backup,superset}

# 3. 内核与文件句柄（PG/OpenSearch 需要）
sysctl -w vm.max_map_count=262144      # OpenSearch 要求
sysctl -w vm.swappiness=1
ulimit -n 65535
```

**验收**：`df -h /data` 显示 2TB 已挂载；`sysctl vm.max_map_count` = 262144。
**失败处理**：磁盘容量/性能不达标 → 拒收，走采购流程重提（对应采购需求第 4 节）。

### S1-2 Docker 与 Compose 安装（第 1 周）

```bash
curl -fsSL https://get.docker.com | sh
docker --version && docker compose version   # 版本号需按官方文档核对
systemctl enable --now docker
```

**验收**：`docker compose version` 可执行；`docker run --rm hello-world` 成功。

### S1-3 三网隔离（第 1 周）

按设计方案 6.3 与安全基线第 ③ 条，建三条 Docker 网络：

```bash
docker network create --internal backend-net    # OM↔PG↔OS↔GA 后端
docker network create --internal data-net       # PG↔PgBouncer↔Flink 数据面
docker network create frontend-net              # Traefik↔门户/OM/Superset
```

| 网络 | 加入的服务 |
|---|---|
| `frontend-net` | Traefik、治理门户、OM server、Superset |
| `backend-net` | OM server、OM ingestion、Presidio、OPA、Governance Agent、Superset |
| `data-net` | PostgreSQL、PgBouncer、OpenSearch、Kafka、Flink、Governance Agent |

**验收**：`docker network ls` 三条网络存在；`frontend-net` 与 `backend-net`/`data-net` 之间无直连通路（容器跨网 ping 不通）。

### S1-4 部署 PostgreSQL + pgvector 与 PgBouncer（第 2 周）

**这是全链路的第一个门禁点**——PgBouncer 参数直接决定后续所有服务能否正常工作。

`docker-compose.yml`（片段，内存 limits 取自 4.2 基线）：

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16          # 版本需按官方文档核对
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
      retries: 5
    deploy:
      resources:
        limits: { cpus: "2", memory: 4G }

  pgbouncer:
    image: bitnami/pgbouncer:latest        # 版本需按官方文档核对
    environment:
      PGBOUNCER_POOL_MODE: transaction     # 单机档起配（见下）
      PGBOUNCER_DEFAULT_POOL_SIZE: "25"
      PGBOUNCER_MAX_CLIENT_CONN: "200"
    depends_on: [postgres]
    networks: [data-net, backend-net]
    restart: unless-stopped
    deploy:
      resources:
        limits: { cpus: "0.25", memory: 256M }
```

**关键参数依据**（全部来自设计文档，不得改动）：

| 参数 | 值 | 依据 |
|---|---|---|
| `pool_mode` | `transaction` | 执行计划 P0-B 第 2 周「transaction 模式起配」 |
| `default_pool_size` | 25 | 同上 |
| PG `max_connections` | 80 | 执行计划「收至 60~80，省出内存给 shared_buffers / effective_cache_size」 |
| PG 内存 limit | 4GB | 4.2 基线 |

**transaction 模式必须在 S2 前实测**（陷阱已知，非假设）：
- 会话级 `prepared statements` 需 PgBouncer 1.21+ 的 `max_prepared_statements` —— **版本待确认**
- `SET` 会话变量、`advisory lock`、`LISTEN/NOTIFY` 在 transaction 模式下行为异常
- 实测与降级链见 `T-M0-1`（S2-4）

**验收**：`docker exec <pgbouncer> psql -h localhost -p 6432 -U postgres pgbouncer -c "SHOW POOLS;"` 可查询且 **waiting 列为 0**（执行计划 P0-B 准出门槛）。

### S1-5 部署 OpenSearch（第 2 周）

```yaml
  opensearch:
    image: opensearchproject/opensearch:latest   # 版本需按官方文档核对
    environment:
      discovery.type: single-node
      OPENSEARCH_JAVA_OPTS: "-Xms4g -Xmx4g"      # 官方建议 heap ≥4GB（4.2 说明）
      DISABLE_SECURITY_PLUGIN: "false"           # 不关闭安全插件（安全基线）
    volumes: ["/data/opensearch:/usr/share/opensearch/data"]
    ulimits: { memlock: { soft: -1, hard: -1 }, nofile: { soft: 65536, hard: 65536 } }
    networks: [data-net, backend-net]
    restart: unless-stopped
    deploy:
      resources:
        limits: { cpus: "2", memory: 8G }        # 4.2 基线
```

**验收**：`curl -k https://localhost:9200/_cluster/health` 返回 `status: green` 或 `yellow`（单节点单副本 yellow 属预期）。

### S1-6 Traefik 入口 + TLS（第 3 周）

```yaml
  traefik:
    image: traefik:v3.0                    # 版本需按官方文档核对
    ports: ["443:443"]                     # 仅暴露 443（安全基线第 ④ 条）
    networks: [frontend-net]
    restart: unless-stopped
    deploy:
      resources:
        limits: { cpus: "0.5", memory: 256M }
```

**验收**：`openssl s_client -connect <host>:443` 握手成功；`curl -I https://<host>` 返回 Traefik 路由响应。
**注**：SSO（oauth2-proxy + ForwardAuth）在 S2-3 对接，S1 阶段只验 TLS 终止。

### S1-7 Prometheus + Alertmanager（第 3 周）

```yaml
  prometheus:
    image: prom/prometheus:latest
    volumes: ["/data/prometheus:/prometheus"]
    networks: [backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "1", memory: 2G } } }
  alertmanager:
    image: prom/alertmanager:latest
    networks: [backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "0.25", memory: 256M } } }
```

采集范围：所有容器 + 宿主机（含 node_exporter、cAdvisor）。建议 SLO 阈值见设计方案 4.5（CPU<80%、内存<85%）。

**验收**：`/targets` 页面显示全部容器与宿主机为 UP（执行计划任务卡 P0-B-1 验收标准）。

### S1-8 备份脚本 + 首次恢复演练（第 3 周）

三路备份（对应 ADR-A4 单机形态「本地数据盘 + 每日出机备份」）：

| 对象 | 方式 | 目的地 |
|---|---|---|
| PostgreSQL | 逻辑备份（pg_dump） | `/data/backup` + 出机 |
| OpenSearch | snapshot | `/data/backup` + 出机 |
| Flink Checkpoint | 目录每日出机 | M-Prod 前无 Flink，S3 起启用 |

```bash
# 示例：PG 小时级逻辑备份（RPO ≤1h 口径的最小实现；完整脚本见《部署步骤_单机版》S1-8）
0 * * * * docker exec postgres pg_dump -U postgres -Fc openmetadata > /data/backup/pg_$(date +\%Y\%m\%d\%H).dump
```

**门禁**：**RPO ≤ 1h / RTO ≤ 4h 的恢复演练记录**（执行计划 P0-B 准出门槛，口径经 2026-09-17 人工决策回填，见该文件 v2.0-r2 修订记录）。
**执行方**：脚本为 Agent 自主执行；**恢复演练结果验证为人工专属（DBA 签字）**。

---

## S2 离线治理闭环部署（Phase 1，建议 3~4 周）

### S2-1 部署 OpenMetadata（第 1 周）

**做法**：直接采用 OpenMetadata 官方 quickstart docker-compose 作为治理内核（设计方案 6.3），在其基础上增补，**不手写配置**。

**唯一必须改的地方**：OM 连接串**指向 PgBouncer**（执行计划 Phase 1 第 1 周），而非直连 postgres。

```yaml
  openmetadata-server:
    image: openmetadata/server:latest       # 版本需按官方文档核对
    environment:
      DB_HOST: pgbouncer                     # ← 关键：不是 postgres
      DB_PORT: "6432"
      ELASTICSEARCH_HOST: opensearch
    networks: [backend-net, frontend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "2", memory: 4G } } }   # 4.2 基线

  openmetadata-ingestion:
    # 内置 Airflow，来自官方 quickstart
    networks: [backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "2", memory: 4G } } }
```

**部署后立即执行 T-M0-1 实测**（见 S2-4），实测通过后才继续接入业务源。

### S2-2 接入业务源（第 1~2 周）

- 第 1 周：接入第 1 个核心业务源（schema / 血缘抽取）
- 第 2 周：接入第 2 个业务源 + sqllineage 补充复杂 SQL 解析

**执行方**：业务源连接器配置为 **Agent 起草 + 人工审核**（凭证与访问范围必须人工确认——执行计划明确划界）。

**纪律**（ADR-A7）：sqllineage 解析失败的 SQL 进「待人工确认」队列，**禁止静默丢弃**。

**验收**：试点源资产目录覆盖率 100%（Phase 1 准出门槛）。

### S2-3 SSO 对接（第 2 周）

oauth2-proxy + Traefik ForwardAuth 对接企业 IdP。

**前置**（P0-A 应已完成）：回调域名、客户端 ID/密钥已申请。

**执行方**：联调为 Agent 自主执行 + 人工验证登录。

### S2-4 【门禁】T-M0-1：OM × PgBouncer transaction 兼容性实测

**必须做，且不自动降级。**

| 项 | 内容 |
|---|---|
| 步骤 | ① OM 登录 → ② 浏览元数据 → ③ 搜索（触发 OpenSearch 链路）→ ④ 跑一次完整 ingestion → ⑤ 打开血缘与质量页 → ⑥ 收集 PgBouncer 与 OM server 日志 |
| 通过标准 | 日志**零命中** `prepared statement .* does not exist` / `transaction aborted` 类错误 |
| 失败处理 | **不自动降级**——输出失败证据包（日志 + 复现步骤），由 DBA + DE 决策走 session 还是直连 |
| 降级成本 | 改一个配置项，≈0；最多损失 1~2 天排期 |
| 人工确认点 | 实测结论写入部署基线文档后，PgBouncer 配置定版 |

> 此项同时是执行计划风险表第 1 条（OM × PgBouncer transaction 不兼容）的前置消解手段。

### S2-5 Presidio + OPA + gov_metrics（第 3 周）

```yaml
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
    image: openpolicyagent/opa:latest
    networks: [backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "0.5", memory: 512M } } }
```

三件事并行推进：

1. **Presidio 中国证件自定义识别规则**（身份证/手机号/银行卡——官方识别器不含，需业务样本验证）
2. **置信度分层调优**：高置信自动打标签 / 中置信走 OM 原生 Tasks 工作流生成人工复核任务 / 低置信仅记录
3. **gov_metrics schema 建立 + Governance Agent 指标 ETL 首版**（ADR-A6，约 3~5 人天）
   - 首版只做**资产覆盖率 + 质量趋势**两个指标；**告警趋势 Phase 2 再补**（执行计划风险表第 4 条）
   - 每小时 ETL，自持表结构；分区仅适用于自研表（ADR-A6）

**OPA 策略纪律**：Rego 入 Git 管理；**fail-closed**（引擎不可用时默认拒绝高风险操作，设计方案 4.4）。

### S2-6 Superset 看板（第 4 周）

```yaml
  superset:
    image: apache/superset:latest           # 版本需按官方文档核对
    depends_on: [pgbouncer]
    environment:
      SUPERSET_SECRET_KEY: "<从密钥管理注入>"   # 不得硬编码（安全基线第 ① 条）
    networks: [backend-net, frontend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "1", memory: 2G } } }
```

**硬约束（ADR-A6）**：Superset 数据源**只读 `gov_metrics`**，**禁止直连 OM 内表**。
看板必须标注「数据截至」时间戳（每小时 ETL，最长延迟 1h）。

**验收**：Superset 全部查询落在 gov_metrics，无 OM 内表直查（第 11 章验收项）。

### S2-7 治理门户（第 2~4 周，可并行）

```yaml
  governance-portal:
    build: ./governance-portal
    depends_on: [governance-agent]
    environment: { GOVERNANCE_API_URL: "http://governance-agent:8080" }
    networks: [frontend-net, backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "0.5", memory: 512M } } }
```

> Governance Agent 本身在 S3 部署（其依赖 Kafka）；门户可先起，Agent 未就绪时降级显示。

### S2 准出门禁（全部满足才进 S3）

- [ ] 试点源资产目录覆盖率 **100%**
- [ ] PII 抽检准确率 **>90%**
- [ ] 血缘 Top 50 列级抽检 **≥90%**
- [ ] Superset 全部查询落在 gov_metrics
- [ ] **合规边界文件归档**（场景 E「POC 期事后审计」书面确认）——**未拿到不得进入 S3 对生产库的接入**

---

## S3 实时链路部署（Phase 2，建议 3~4 周）

### S3-1 【门禁】接生产源库前的两项书面确认

**在动生产库之前必须已完成**：

| 项 | 内容 | 责任人 |
|---|---|---|
| 合规边界文件 | 场景 E「POC 期只做事后审计 + 主动告警，不做事前拦截」书面确认 | SEC + 合规方 |
| 续接窗口签约定 | **MySQL**：`binlog_expire_logs_seconds` ≥ 3 × 停机预算且默认 ≥48h（例：停机 16h → 48h）；**PG**：`wal_keep_size` ≥ 3 × 停机预算内 WAL 增量 | DBA |

**未签订不动生产库**（执行计划 Phase 2 第 1 周硬约束）。

### S3-2 部署 Kafka（第 1 周）

```yaml
  kafka:
    image: apache/kafka:latest              # 版本需按官方文档核对
    environment:
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_NODE_ID: 1
      KAFKA_CONTROLLER_QUORUM_VOTERS: "1@kafka:9093"
      KAFKA_LISTENERS: "PLAINTEXT://:9092,CONTROLLER://:9093"
      KAFKA_ADVERTISED_LISTENERS: "PLAINTEXT://kafka:9092"
      KAFKA_HEAP_OPTS: "-Xms2g -Xmx2g"      # 4.2 说明「JVM heap ~2GB + 页缓存」
    volumes: ["/data/kafka:/var/lib/kafka/data"]
    networks: [data-net, backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "2", memory: 4G } } }   # 4.2 基线
```

**Topic 规划**（ADR-A5 + ADR-A1）：

| Topic | 用途 | 备注 |
|---|---|---|
| `governance.alerts` | 增量窗口命中的异常告警 | POC 期治理平台自带；若复用企业集群用 P0-A 申请的 topic |
| 日志事件类 | 日志/埋点接入 | 可重放 |

> **默认直连，不加 `cdc.*` Topic**。仅当 ADR-A1 三个切换信号之一命中（存在其他 CDC 消费方 / 续接窗口无法满足 / 频繁背压），才加 `Flink CDC → Kafka cdc.<source>` —— **切换只加 Topic，不引入新组件**。

### S3-3 部署 Flink（内置 Flink CDC）（第 1~2 周）

**关键**：自建镜像需把 CDC connector JAR 放入 `/opt/flink/lib`（设计方案 6.3.1 注释）。

```yaml
  flink-jobmanager:
    image: <自建镜像>:<固定 digest>          # 镜像固定 digest（安全基线第 ⑤ 条）
    command: jobmanager
    environment:
      FLINK_PROPERTIES: |
        jobmanager.rpc.address: flink-jobmanager
        state.backend: hashmap             # 单机形态（ADR-A4）
        state.checkpoints.dir: file:///opt/flink/checkpoints
    volumes: ["/data/flink-checkpoint:/opt/flink/checkpoints"]
    networks: [data-net, backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "1", memory: 2G } } }

  flink-taskmanager:
    image: <自建镜像>:<固定 digest>
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
```

**Checkpoint 存储（ADR-A4 单机形态）**：本地数据盘 `/data/flink-checkpoint` + **每日出机备份**。
**边界声明**：单机形态不存在跨节点恢复问题；**不得宣称 Flink HA**（单机无容错）。

**镜像供应链**：自建镜像固定 digest + 上线前扫描，扫描无高危漏洞（执行计划 M0 第 5 项）。

### S3-4 【门禁】PG 源库复制槽安全保护（第 1 周）

**仅 PG 源库需要（MySQL 无此项）。** 这是 P1 级风险——作业停机期间 slot 持续阻止 WAL 回收，**会撑爆源库磁盘**（比断流严重一个量级）。

三道防线，缺一不可：

| # | 措施 | 具体配置 |
|---|---|---|
| ① | 兜底上限 | 源库设 `max_slot_wal_keep_size`（超限 slot 自动失效，**宁可断流重快照、不撑爆磁盘**） |
| ② | 水位告警 | `pg_replication_slots` 的 `restart_lsn` 与当前 WAL 差值，纳入**最高优先级告警**，阈值 = 兜底值的 **70%** |
| ③ | 运维纪律 | 作业下线/长期停用**必须先删 slot**（`pg_drop_replication_slot`），写入 checklist 与 runbook |

**执行方**：参数为 **Agent 起草 + DBA 审核后执行**。

```sql
-- ① 兜底（示例值，具体大小按续接窗口签约定推算，待 DBA 核定）
ALTER SYSTEM SET max_slot_wal_keep_size = '<按签约定计算>';
SELECT pg_reload_conf();

-- ② 水位查询（接入 Prometheus exporter）
SELECT slot_name, restart_lsn,
       pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS retained_bytes
FROM pg_replication_slots;

-- ③ 下线前必做
SELECT pg_drop_replication_slot('<slot_name>');
```

### S3-5 CDC 作业开发与部署（第 2 周）

作业三块内容：

1. **CDC Source**（快照 + 增量）
2. **窗口规则引擎**（空值率突增 / 范围越界 / 行数环比骤降）
3. **作业图级血缘上报**（OpenLineage 集成，作业启动/变更时上报——**非事件级血缘**，ADR-A7）

**质量口径拆分落地（ADR-A5）——两类产出走不同通道，不得混用**：

| 产出 | 来源 | 去向 |
|---|---|---|
| 权威质量指标（全表空值率/唯一值/分布） | CDC **快照阶段** + 离线 Profiler | OpenMetadata 质量测试结果 → **进质量看板** |
| 异常告警（窗口内突增/越界/骤降） | CDC **增量阶段** | `governance.alerts` → 告警通道 → **不进质量看板** |

两类在 OM 中以**不同 test definition 类型**区分。

**执行方**：作业开发部署 Agent 自主执行；**规则逻辑上线前人工审核**。

### S3-6 作业自动拉起 + 告警打通（第 3 周）

- `restart: unless-stopped` + healthcheck（已在 S3-3 配置）
- 作业运行状态、Checkpoint 成功率、binlog/slot 水位 → **最高优先级告警**（ADR-A2 第 3 件）
- Governance Agent 告警增强模块 + Alertmanager/IM 打通

```yaml
  governance-agent:
    build: ./governance-agent
    depends_on: [kafka, pgbouncer, opa]
    networks: [backend-net, data-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "2", memory: "4G" } } }   # 4.2 基线
```

### S3-7 【门禁】三项演练（第 4 周）

| 演练 | 通过标准 | 判读 |
|---|---|---|
| **CDC 断流恢复** | 停机 ≤ 续接窗口 **1/3** 内恢复，**无重新快照** | Agent 采集，DBA 签字 |
| **PG 复制槽保护验证** | 兜底生效 + 水位告警触发 + 删 slot 演练通过 | 同上 |
| **重快照预案** | 大表限速完成，源库负载在约定阈值内（低峰、限 chunk、跳非关键表） | 同上 |

**执行方**：脚本与采集 Agent 自主执行；**结果判读与源库负载评估为人工专属（DBA 签字）**。

### S3 准出门禁

- [ ] 端到端延迟 **P95 < 30s**
- [ ] 断流恢复（≤ 续接窗口 1/3）+ 重快照演练通过
- [ ] PG 复制槽保护三项验证通过
- [ ] 质量看板**无增量窗口数据混入**
- [ ] 连续运行 **1 周无未恢复故障**

---

## S4 AI 能力与试运行部署（Phase 3 + Phase 4）

### S4-1 Ollama 部署（Phase 3 第 1 周）

```yaml
  ollama:
    image: ollama/ollama:latest             # 版本需按官方文档核对
    volumes: ["/data/ollama:/root/.ollama"]
    networks: [backend-net]
    restart: unless-stopped
    deploy: { resources: { limits: { cpus: "8", memory: "16G" } } }   # 4.2 基线 4~8 vCPU / 8~16GB
```

**前置门禁**：M0 实测第 6 项 —— Qwen 目标模型**流式首 token < 10s**。
**不达标**：触发 GPU 评估（Phase 3 按需）；或量化版换小模型。
**选型**：Qwen2.5 / Qwen3（Apache-2.0，中文场景主流）；嵌入模型 BGE-M3 / nomic-embed-text。

**注意**：Ollama 请求需并发限流与批处理，**不得拖慢实时告警链路**（设计方案 4.3）；实时规则检测**不依赖 LLM**。

### S4-2 pgvector 向量索引 + RAG（Phase 3 第 2~3 周）

- pgvector 向量索引构建
- 元数据/血缘摘要向量化管道（离线定期刷新）
- LangGraph RAG 问答链路
- **引用程序化校验**：回答中引用的表/字段名必须在 OM 中真实存在，否则标注「未核实」或拒绝返回（防引用幻觉）
- 门户聊天界面对接 Governance Agent API

### S4-3 CI/CD 血缘「提示」能力（Phase 3 第 4 周）

PR 评论列影响面（ADR-A7）。**连续 4 周达标后才升级为「阻断」**。

### S4-4 试运行与水位周报（Phase 4）

- 真实负载压测，对照 4.1.2 容量表校准（**容量数值为经验估算，Phase 4 压测校准，不作 SLA 承诺**）
- **单机水位周报机制**：CPU/内存/磁盘，直接对照 M-Prod 触发条件 2（>70% 连续两周）
- 年度化备份恢复演练（PG PITR、OS snapshot、Flink Checkpoint 出机恢复）
- 试运行 90 天三问基线：周活用户数 / 识别准确率 / 告警处置率

---

## S5 三节点生产化部署（M-Prod，触发式，建议 4~6 周）

> **启动前提**：M-Prod 四条触发信号任一命中，且 ARCH + PM 评审有书面结论。**未触发不得部署**——不预购 3 台、不预装 k3s、不为「将来迁移」提前适配。

### S5-1 k3s 集群 + ADR-A4 三件事（第 1~2 周）

```bash
# 1. 第一台 init（etcd 3 副本，stacked 拓扑）
curl -sfL https://get.k3s.io | sh -s - server --cluster-init

# 2. 其余两台 join
curl -sfL https://get.k3s.io | K3S_URL=https://<node-1>:6443 K3S_TOKEN=<token> sh -s - server
```

**ADR-A4 随附三件事（缺一不可）**：

| # | 事项 | 原因 |
|---|---|---|
| ① | kube-vip / keepalived 给 API Server 配 VIP | 消除控制面单点 |
| ② | **引入 MetalLB 前先禁用 Klipper** | 两者冲突 |
| ③ | 文档明示「local-path PV 带节点亲和，状态 Pod **不可漂移**，可用性由副本提升保证」 | 避免误以为能漂移 |

**验收**：3 节点 Ready；**VIP 漂移测试通过**（关停持有节点，VIP 30s 内漂移）——任务卡 M-PROD-1。
**人工确认点**：集群健康 + VIP 漂移人工复核后，方可进入第 3 周。

### S5-2 Operator 与服务部署（第 3 周）

```bash
helm install cnpg cloudnative-pg/cloudnative-pg
helm install strimzi-kafka-operator strimzi/strimzi-kafka-operator
helm install opensearch-operator opensearch-project/opensearch-operator
helm install flink-kubernetes-operator flink-operator-repo/flink-kubernetes-operator
helm install metallb metallb/metallb
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack
helm install openmetadata open-metadata/openmetadata
helm install superset superset/superset
# 版本号需按官方文档核对（设计方案 6.2.3 已声明）
```

**与单机形态的四处关键差异**：

| 项 | 单机 | 3 节点 |
|---|---|---|
| **PgBouncer** | 独立容器 | **由 CNPG 内置 Pooler 替代**（不部署独立 PgBouncer） |
| **Checkpoint** | 本地盘 + 每日出机 | **SeaweedFS（必需）** + `state.backend: rocksdb` + **增量 Checkpoint** |
| Kafka | KRaft 单节点 | Strimzi 管理，3 broker，副本因子 3 |
| 入口 | Traefik 容器 | MetalLB 提供跨节点漂移 VIP |

**SeaweedFS（ADR-A4 唯一组件动作）**：从「触发式可选」提升为 **3 节点形态必需**。

### S5-3 迁移演练（第 4 周，不动单机）

在 3 节点集群上完整走一遍：PG 逻辑备份恢复 → OM 元数据校验 → OS 索引重建 → Flink 从 Savepoint 恢复 → Kafka 位点迁移。
**故障注入**：模拟单节点宕机，验证 CNPG / OpenSearch 自动切换。

**门禁**：**数据校验 100% 通过**；校验不过 → **不切流**，回 S5-3 重演，单机继续承载业务（无损失）。

### S5-4 切流窗口（第 5 周）

```
低峰停写 → 最终备份 → 迁移 → 校验 → DNS/入口切换 → 观察 48h
```

**切流期纪律**（执行计划 §8，比其他阶段更严）：
- 所有操作从「Agent 起草 + 人工审核」**升级为「Agent 起草 + 人工逐步放行」**（每步执行前人工确认）
- **回滚触发权归 PM**；Agent 发现异常只上报建议，**不得自行回滚**
- 回滚预案：入口指回单机，**单机保留 2 周不拆**

### S5-5 收尾（第 6 周）

单机降级为测试环境或回收；3 节点监控告警全量迁移；运维手册定稿（含 ADR-A2/A4 全部边界条款）。

**准出门槛**：3 节点均 Ready；模拟单节点故障自动转移生效且无数据丢失；迁移演练数据校验 100% 通过；切流后 48h 无 P1 级故障。

---

## 附 A：部署顺序依赖图

```
S1-1 服务器初始化
   └─▶ S1-2 Docker
        └─▶ S1-3 三网隔离
             └─▶ S1-4 PG + PgBouncer ──┬─▶ S2-1 OpenMetadata ─▶ S2-4 【门禁】T-M0-1
             └─▶ S1-5 OpenSearch ─────┘                              │
             └─▶ S1-6 Traefik                                          ▼
             └─▶ S1-7 Prometheus                              S2-2/3/5/6/7 离线治理
             └─▶ S1-8 【门禁】备份可恢复                            │
                                                                    ▼
                                          S3-1 【门禁】签约定+合规确认
                                                  └─▶ S3-2 Kafka ─▶ S3-3 Flink
                                                                    └─▶ S3-4 【门禁】PG slot
                                                                         └─▶ S3-5/6 作业
                                                                              └─▶ S3-7 【门禁】三演练
                                                                                   └─▶ S4 AI/试运行
                                                                                        └─▶ S5 M-Prod（触发式）
```

## 附 B：部署期五道门禁速查

| 门禁 | 位置 | 不通过怎么办 |
|---|---|---|
| 服务器验收（fio/容量） | S1-1 前 | 拒收，重提采购 |
| 备份可恢复（RPO≤1h/RTO≤4h） | S1-8 | 修脚本重验，人工签字 |
| **T-M0-1** OM×PgBouncer | S2-4 | **不自动降级**，DBA+DE 决策 session/直连 |
| 合规边界 + 续接窗口签约定 | S3-1 | **不动生产库** |
| PG 复制槽保护三项验证 | S3-4/S3-7 | 补齐后重验，DBA 签字 |

## 附 C：待确认项（占位符，不作承诺）

| # | 项 | 说明 |
|---|---|---|
| 1 | 全部镜像精确版本号 | 设计方案 6.2.3 声明「需按官方文档核对」；CDC 版本矩阵由 M0 第 2 项实测锁定 |
| 2 | PgBouncer 版本是否 ≥1.21 | 关系到 `max_prepared_statements` 是否可用（transaction 模式前提） |
| 3 | `max_slot_wal_keep_size` / `wal_keep_size` 具体数值 | 由续接窗口签约定结果推算，DBA 核定 |
| 4 | POC 服务器数据盘设备名 | 服务器到货后 `lsblk` 确认 |
| 5 | 企业 IdP 回调域名 / 客户端 ID | P0-A SSO 申请结果 |

## 附 D：修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-15 | 首次产出：由《实施执行计划 v2.0-r1》与《设计方案 v3.2》拆解 S1~S5 部署步骤（含三节点 M-Prod），含硬约束、门禁速查与待确认项 |
| v1.1 | 2026-09-17 | **P0-B/S1-8 门禁 RPO 口径 15min→1h**（人工决策，同《实施执行计划 v2.0》v2.0-r2），备份示例改小时级并指向《部署步骤_单机版》S1-8 完整脚本；补本修订记录表（原文件缺，AGENTS §10.2）。无架构与组件变更 |

---

*本文由《实施执行计划 v2.0-r1》与《设计方案 v3.2》机械拆解而成，不含新增架构决策。若执行中出现与两份源文档冲突的情况，以源文档为准并回报修订。*
