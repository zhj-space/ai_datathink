# AI 数据治理平台 · 单机版 S3 前置部署记录（Kafka / Flink / 告警链路 / 镜像固定 / FERNET_KEY 重做）

> **执行日期**：2026-09-18 ｜ **执行方**：Agent（部署 + 脚本 + 采集）
> **依据**：《部署步骤_单机版》S3-2 / S3-3 / S3-4 与 0.3.1；《设计方案 v3.2》ADR-A1 / A2 / A4 / A5 / A7、3.3、4.1.3、4.2、4.3；《实施执行计划 v2.0-r2》P2 第 1 周
> **授权范围**：2026-09-18 人工决定——门禁④ 通行 + 允许"S3 前置与 S2 并行"，**先做不接触源库的部分**；源库信息仍待人工提供（S2-2 未开始）
> **本记录不含**：任何对生产源库的动作、任何架构/组件选型变更
> **版本**：v1.0

---

## 1. 结论摘要

| # | 事项 | 结果 |
|---|---|---|
| 1 | **S3-2 Kafka** | ✅ 已上线：KRaft 单节点 `apache/kafka:4.0.0`，heap 512m，**不发布端口**；`governance.alerts` 已建（2 分区 / RF1 / 保留 7d）；收发往返实测通过 |
| 2 | **S3-3 Flink** | ✅ 已上线：自建镜像 `ai-governance/flink:1.20.1-cdc3.6.0`（内置 CDC MySQL/PG + Kafka 连接器）；JM+TM healthy，**2 task slots**；**Kafka→Flink 冒烟作业成功消费**，**Checkpoint 落 `/data/flink-checkpoint`** |
| 3 | **告警链路补齐**（S1-7 补 + S3-4 判据② 前提） | ✅ **采集 + 规则已通**：`postgres_exporter` + Prometheus 规则（8 条）；**投递链路已用临时 sink 端到端证实**（Prometheus firing → Alertmanager → sink 收到真实 payload）；⏳ **通知通道地址待定**（S3-6） |
| 4 | **S1-7 遗留修正** | ✅ 此前 `rule_files` 为空、SLO 阈值无规则载体（实测 `groups: []`），**本轮已补 3 条 SLO 规则**（CPU<80% / 内存<85% / 磁盘>85%） |
| 5 | **镜像可复现性**（S1 偏差 12） | ✅ compose 中 **15 个镜像固定到 digest**（含 5 个原 `:latest`）；**未重建容器**，故"下次重建生效"；compose 校验和重取为 `b70ae7a9…` |
| 6 | 容器数 | **18**（S1 6 + cAdvisor 1 + S2 7 + S3 4）；**GA 已于同日上线**（见《…S3-6治理Agent首版实现记录.md》）；待 S4 Ollama 上线后达本环境口径 **19** |
| 7 | **FERNET_KEY 重做**（门禁③ 判定同窗口） | ✅ OM 元数据库以**自持 key** 重建（195 表、迁移退出码 0）；两侧 key `sha256` 一致；链路复建 + DagRun success；**门禁③ 判据复跑仍 0 命中**。见 §7 |
| 8 | **配置出机补齐** | ✅ 新增 `backup_config.sh`（`.env` + compose + `config/`，0600，保留 14d）、cron 02:55、并入出机 rsync。**FERNET_KEY 自持后此项为必需**（缺 .env 则恢复后无法解密）。见 §8.1 |
| 9 | **node_exporter 监听收紧** | ✅ `*:9100` → `172.17.0.1:9100`；Prometheus 抓取 4/4 仍 up。见 §8.2 |
| 10 | **镜像漏洞扫描** | ✅ **已完成（16/16 镜像）**：**四层卡点**全部排除（① 二进制截断 → 断点续传；② vuln DB 源 `mirror.gcr.io` 不可达 → `ghcr.io`；③ 默认超时与**中断残留缓存锁**（复现两次）→ `--timeout 60m` + **脚本内置每轮清锁**；④ **Java DB 同源不可达** → `--java-db-repository ghcr.io/...`）。结果见 §9.2：`OM ingestion 149/5`、`pgbouncer 117/8`、`superset 100/0`、`cadvisor 99/6`、`presidio×2 86~88/8`、`traefik 77/5`、`kafka 70/7`、`opensearch 35/6`、`flink 31/0`、`pgvector 24/1`、`门户 10/3`、`alertmanager 8/0`、`prometheus 6/0`、**`OM server 0/0`、`opa 0/0`**。**计数≠可利用、处置方式属人工决策，不得写成"已扫清"** |
| 11 | **443 收敛（SSO 前）** | ✅ **已完成**：本环境无固定企业 VPN/出口 IP ⇒ 不做限源而**撤回暴露**——443 不再发布（宿主无监听）、OM 改回环 `127.0.0.1:8585` + SSH 隧道；ETL 与门户链接同步改。见 §10 |
| 12b | **Kafka 消费滞后告警**（设计方案 4.5） | ✅ **已完成**：kafka_exporter 1.10.0（宿主二进制）+ 仅回环 EXTERNAL 监听 + `job_name: kafka` 抓取 + 3 条规则；**端到端验证**（造 lag → firing → 恢复 → inactive）。见 §4.6 |
| 12c | **Flink 作业 / Checkpoint 告警**（设计方案把"作业状态 / Checkpoint 成功率"列为**最高优先级**） | ✅ **已完成（2026-09-19）**：启用 Flink 官方 Prometheus reporter（JM/TM `9249`）+ `job_name: flink` 抓取（目标 up）+ `flink-jobs` 组 **5 条规则**（抓取失败 / **登记了作业但没在跑** / 作业重启 / Checkpoint 失败 / Checkpoint 停摆）⇒ **规则总数 22 → 27**；用演练作业做真实触发验证（含一个**静默失效的坑**：跨源 `and` 必须写 `on()`）。见 `alerting/README.md` |
| 12 | **GA 首版（S3-6）** | ✅ **已完成**（同日另行记录）：`ai-governance/governance-agent:0.1.0` 上线、**门户降级结束**、告警幂等落库 + 审批流闭环；**容器数 17 → 18**。见《AI数据治理平台_单机版_S3-6治理Agent首版实现记录.md》 |

> **未做（声明）**：Ollama（S4）、Flink CDC 作业（S3-5，依赖源库）、源库侧 exporter 实例与复制槽参数（依赖门禁④ 文件 B 取值）、IM/Webhook 通知通道（人工"暂时不处理"）。**Governance Agent 首版已于同日上线**（另行记录）。

---

## 2. S3-2 Kafka（KRaft 单节点）

### 2.1 落地参数

| 项 | 取值 | 依据 |
|---|---|---|
| 镜像 | `apache/kafka:4.0.0@sha256:3f7b939115cd4872e9cee9369d80bd69712fde55f9902f46d793f64848dedc75` | 实拉可用（Docker Hub 经 registry-mirrors） |
| 部署形态 | KRaft 单节点（`broker,controller` 合一，**无 ZooKeeper**） | 设计方案 2.2（KRaft 自 3.3 GA） |
| `CLUSTER_ID` | `2d8dca59-7148-4565-a165-72936c9895fb`（显式固定，保证重建后集群身份一致） | 运维纪律（随机 ID 会破坏持久卷复用） |
| JVM heap | `-Xms512m -Xmx512m` | 4.1.3 最小测试环境 |
| 监听 | 容器内 `9092`（PLAINTEXT）/ `9093`（CONTROLLER）；**宿主不发布任何端口** | 仅暴露 443（安全基线④）；客户端只走 `kafka:9092` |
| Topic 自动创建 | **关闭**（`KAFKA_AUTO_CREATE_TOPICS_ENABLE=false`） | 治理纪律：Topic 显式创建、纳入 IaC，禁止隐式漂移 |
| Topic 保留 | 默认 168h（7d） | 3.3「日志类事件保留 ≥7 天用于回放」 |
| 副本因子 | `offsets` / `transaction.state` 均 1 | 4.1.3「单节点、不开副本，测试专用」 |
| 资源上限 | 2 vCPU / 4G | 设计方案 4.2 |

### 2.2 Topic 规划（现状）

| Topic | 用途 | 现状 |
|---|---|---|
| `governance.alerts` | 增量窗口异常告警分发（ADR-A5） | ✅ 已建：**2 分区**（与 Flink task slots=2 对齐，设计方案 3.4）/ RF 1 / `retention.ms=604800000`（7d） |
| 日志事件类 Topic | 应用日志/埋点接入 | ⏳ **未预建**：命名需与实际接入方对齐，**不臆造 Topic 名**；接入时按 S3-5 建 |
| `cdc.*` | 两段式开关（ADR-A1） | ❌ **默认不建**：仅当 ADR-A1 三个切换信号命中才加（且只加 Topic，不引入新组件） |

### 2.3 验证证据

```text
cluster_id      Cluster ID: 2d8dca59-7148-4565-a165-72936c9895fb   （与配置一致）
server version  4.0.0                                              （kafka-topics.sh --version）
容器健康        ai-governance-poc-kafka-1 | Up (healthy)
暴露面          docker port → 无宿主映射；宿主无 9092 监听
topic 创建      Created topic governance.alerts（PartitionCount 2 / ReplicationFactor 1）
                Configs: cleanup.policy=delete, retention.ms=604800000
收发往返        producer → consumer 收到 {"test":"round-trip","scenario":"E","at":"2026-09-18T04:42:27Z"}
自动创建守卫    --list 仅 __consumer_offsets + governance.alerts
测试残留清理    删除含测试消息的 topic 并重建；kafka-get-offsets → 0:0 / 1:0（零消息）
```

---

## 3. S3-3 Flink（standalone，内置 Flink CDC）

### 3.1 自建镜像（源码入 Git：`flink-image/`）

| 项 | 取值 |
|---|---|
| 基础镜像 | `flink@sha256:d51a693da76581f2483d3f83d5997d3226b076a9f990a7f7d1eed144434b8e1e`（= `flink:1.20.1-scala_2.12-java17`，**按 digest 固定**） |
| 产物镜像 | `ai-governance/flink:1.20.1-cdc3.6.0@sha256:5681153a3742b5af66048f6c123b4bfe24e905cf690f9e4eeea1453d0c102cbe`（构建于 2026-09-18 12:49） |
| 已装 JAR | `flink-sql-connector-mysql-cdc-3.6.0-1.20`（`864ec202…`）<br>`flink-sql-connector-postgres-cdc-3.6.0-1.20`（`e3d7f91d…`）<br>`flink-connector-kafka-3.4.0-1.20`（`0deab094…`）<br>`flink-connector-base-1.20.1`（`a0332645…`）<br>`kafka-clients-3.4.0`（`48f38ded…`） |
| 校验方式 | `jars.sha256` 入 Git，构建期 `sha256sum -c` 强制校验（换版本不同步该文件 ⇒ 构建失败，属预期保护） |
| 未纳入 | **Oracle CDC**（需商业插件 + 授权评估，设计方案 3.3）；**OpenLineage 上报 JAR**（属 S3-5，届时按官方兼容矩阵选定） |

**踩坑记录（重要）**：首次构建只装了 CDC 与 Kafka 连接器，冒烟作业立即失败：

```text
java.lang.ClassNotFoundException: org.apache.kafka.clients.consumer.OffsetResetStrategy
```

根因：`flink-connector-kafka` 是**瘦包**（239 个 class），运行还需要 `flink-connector-base` **与 `kafka-clients`**；Flink 基础镜像 `/opt/flink/lib` 两者都没有。
处置：补装 `kafka-clients`，版本取自官方父 POM `flink-connector-kafka-parent:3.4.0-1.20` 的 `<kafka.version>3.4.0</kafka.version>`（**不臆造版本**），重建镜像。第一次构建的镜像（Id `8e6f2778…`）已作废。

### 3.2 运行参数

| 项 | 取值 | 依据 |
|---|---|---|
| JobManager | 1 vCPU / 2G，`jobmanager.memory.process.size: 1600m` | 计划附录 compose |
| TaskManager | 3 vCPU / 6G，`taskmanager.memory.process.size: 4g` | 同上 |
| **task slots** | **2** | 4.1.3「调到 1~2，只验证规则逻辑是否跑通，不代表真实吞吐能力」 |
| `state.backend` | `hashmap` | ADR-A4 单机形态 |
| Checkpoint 目录 | `/data/flink-checkpoint`（本地盘；每日出机见 S1-8） | ADR-A4 |
| JM UI | `127.0.0.1:8081` + SSH 隧道（加 `frontend-net` 使回环发布生效） | 方案 C / 运维接入 |
| 边界 | **单机形态不宣称 Flink HA** | S3-3 边界声明 |

### 3.3 验证证据（含端到端冒烟）

```text
容器健康        flink-jobmanager Up (healthy) / flink-taskmanager Up (healthy)
集群总览        taskmanagers=1  slots-total=2  slots-available=2  flink-version=1.20.1 (commit cb1e7b5)
TaskManager     pekko.tcp://flink@172.18.0.9:40459/.../taskmanager_0  slots=2 free=2

冒烟作业（临时 topic flink-smoke-test，Kafka source → print sink，detached 提交）
  提交        Job ID: b647aab8f9ffcb932d2d8bd7d35dbd53
  状态        state=RUNNING  tasks.running=1
  数据贯通    TaskManager 日志出现：+I[2026-09-18T04:50:17Z, flink-smoke-marker]
  Checkpoint  宿主 /data/flink-checkpoint/b647aab8…/chk-3（+ _metadata / shared）  ← ADR-A4 本地盘路径实测可用
  收尾        作业 CANCELED（cancel http=202）；临时 topic 删除；冒烟 checkpoint 目录删除
  终态        jobs-running=0  jobs-failed=0  slots-available=2
```

> **意义**：这条冒烟同时证明了 ① Kafka 连接器 classpath 完整 ② Flink→Kafka 网络与认证可用 ③ 作业提交/调度链路可用 ④ Checkpoint 真的落在数据盘。源库 CDC 作业（S3-5）尚未开发，依赖源库信息。

---

## 4. 告警链路补齐（S1-7 遗留修正 + S3-4 判据② 前提）

### 4.1 补齐前实测缺口（本记录 §1 已引用）

| 环节 | 补齐前 | 补齐后 |
|---|---|---|
| 采集 | `postgres_exporter` **未安装**；Prometheus 仅 3 target | ✅ 宿主二进制 + systemd，绑定 `172.17.0.1:9187`，Prometheus job `postgres` 抓取，`pg_up=1`（667 条 `pg_` 指标） |
| 规则 | `prometheus.yml` **无 `rule_files`**；`/api/v1/rules` → `groups: []` | ✅ **8 条规则**生效（`poc-slo` 3 + `postgres-replication-slot` 5），`promtool check rules` SUCCESS |
| 通知 | Alertmanager `receivers` 仅 `default` 且**无通道** | ⏳ 链路已证实可用（§4.5），**真实 IM/Webhook 地址待定**（S3-6） |

### 4.2 postgres_exporter 落地

| 项 | 取值 |
|---|---|
| 版本 | **0.20.1**（宿主二进制 `/usr/local/bin/postgres_exporter`，systemd 单元 `postgres_exporter.service`） |
| 监听 | `172.17.0.1:9187`（docker0 网关；公网不可达，不占 443） |
| **连接路径** | **经 PgBouncer `127.0.0.1:6432` 回环 → PG**（硬约束②：任何服务不得直连 5432） |
| PG 只读角色 | `prom_exporter`：仅 `pg_monitor`（无 SUPERUSER/CREATEDB/CREATEROLE）；口令存 `/etc/pg_exporter.env`（0600 root，**不入 Git**，已登记到仓库外敏感文件） |
| 自定义查询 | `alerting/pg_exporter_queries.yml` → `/etc/pg_exporter_queries.yml`（复制槽水位） |
| 数据源保护 | 宿主侧仅回环发布 6432；PgBouncer 需加 `frontend-net` 才使回环发布生效（**S1 记录偏差 4 的第三次复现**） |

### 4.3 四个实测踩坑（供复用）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | `CREATE ROLE pg_exporter` 报 `role name "pg_exporter" is reserved` | PostgreSQL 保留 `pg_` 前缀角色名 | 改名 `prom_exporter` |
| 2 | 宿主 exporter 连 `127.0.0.1:5432` → connection refused | **硬约束②：PG 不发布端口**，宿主进程本就无法直连 | 改经 `127.0.0.1:6432`（PgBouncer）——**合规且更安全** |
| 3 | 给 pgbouncer 加 `ports: 127.0.0.1:6432:6432` 后宿主仍无监听 | 只挂 **internal** 网络的容器，回环端口发布不生效 | pgbouncer 增加 `frontend-net`（与 Prometheus/Alertmanager/Airflow/Superset/门户/Flink 同一做法） |
| 4 | `--config.file` 加载自定义查询报 `field queries not found in type config.Config` | v0.20.1 的 `--config.file` 键名与新格式不符 | 沿用 `--extend.query-path`（仅 DEPRECATED 警告）；**已记入 `alerting/README.md`，待官方键名明确后迁移** |

### 4.4 规则清单（`alerting/poc-alerts.yml`，入 Git）

| 组 | 规则 | 表达式要点 | 级别 |
|---|---|---|---|
| `poc-slo` | `HostCpuUsageHigh` | `100 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))*100 > 80`（for 10m） | warning |
| `poc-slo` | `HostMemoryUsageHigh` | `(1 - MemAvailable/MemTotal)*100 > 85`（for 10m） | warning |
| `poc-slo` | `HostFilesystemUsageHigh` | 文件系统使用率 > 85%（for 15m）——100G 单盘形态的关键护栏 | critical |
| `postgres-replication-slot` | `PgReplicationSlotNoSafetyCap` | `max_slot_wal_keep_size < 0` **且该实例存在复制槽** | critical |
| `postgres-replication-slot` | `PgReplicationSlotWatermarkHigh` | `retained_pct_of_budget > 70`（for 5m）——**判据②** | critical |
| `postgres-replication-slot` | `PgReplicationSlotInactiveHoldingWal` | `active==0 且 retained_bytes>0`（for 15m） | warning |
| `postgres-replication-slot` | `PgReplicationSlotInvalidated` | `wal_status='lost'` | critical |
| `postgres-replication-slot` | `PgReplicationSlotBeyondCap` | `wal_status='extended'`（超兜底但未丢） | critical |
| `poc-slo` | `HostDiskHeadroomLow`（2026-09-18 新增） | **可用空间 < 20 GiB**（只盯 `/` 与 `/data`）——绝对余量护栏，命中即"该考虑加盘" | critical |

**三条设计纪律（避免"看似有告警、实际告不掉"）**：

> **2026-09-18 补充（A1）**：新增 `HostDiskHeadroomLow`（可用 < 20 GiB）后，实测**踩到一次假阳性**——不加 mountpoint 过滤时，规则命中了 **`/boot/efi`（约 200 MB 的 vfat ESP 分区）**、直接进 pending。已把**两条磁盘规则都改为只盯 `mountpoint=~"/|/data"`**（`/data` 为将来加数据盘预留），此后两条均为 `inactive`。**教训**：写文件系统类规则**必须限定 mountpoint**，否则小分区必然误报。

1. **阈值不硬编码**：水位阈值 = `retained_bytes / max_slot_wal_keep_size`，**由 SQL 现场计算**（分母是源库实际兜底值）。文件 B 取值未定前规则照样可用，DB 侧一改参数阈值即生效。
2. **"没配兜底"必须单独告警**：兜底为 `-1` 时百分比指标为 NULL，水位规则**永远不会触发**——故单独设 `NoSafetyCap`（fail-visible）。
3. **告警范围收窄**：`NoSafetyCap` 仅在"该实例确实有复制槽"时报（`and on (instance,job) count by(...) (pg_replication_slot_reserved) >= 1`）。平台自身 PG（无槽）不会刷屏——**实测：收窄前该规则 pending，收窄后 inactive**。

### 4.5 投递链路端到端自检（临时物，验证后已清理）

```text
① 临时 sink             python http.server 监听 172.17.0.1:9099，落 /tmp/am_sink.log
② Alertmanager 临时配置  default 接收器 → webhook http://172.17.0.1:9099/
③ 临时自检规则           zz-selfcheck.rules.yml（expr: vector(1) > 0, for 0s）
④ Prometheus 热加载      POST /-/reload → HTTP 200（同时确认 --web.enable-lifecycle 已生效）
⑤ 规则状态              zz-selfcheck PocAlertPipelineSelfCheck → firing
⑥ Alertmanager          /api/v2/alerts → PocAlertPipelineSelfCheck | state=active
⑦ **sink 实收**         1045 字节真实通知体：
   {"receiver":"default","status":"firing","alerts":[{"labels":{"alertname":"PocAlertPipelineSelfCheck",...}],
    "notification_reason":"first notification",...}
⑧ 清理                 删临时规则 + 热加载（回到 8 条）；还原 alertmanager.yml；停 sink；删临时文件
⑨ 终态                 8 条规则（health=ok）、4 targets 全 up、Alertmanager 活动告警 0、容器全 healthy
```

**判定**：三环中的**采集与规则已达成**，**投递链路已证实可用**；判据②（"水位告警触发"）只差**真实通知地址**（S3-6 落地），届时用同一自检方法复验即可。

### 4.6 Kafka 运维接入与消费滞后告警（2026-09-18 新增）

**依据**：设计方案 4.5「Kafka 消费滞后 | `kafka_exporter` | lag 持续增长 5 分钟以上告警」。

**① Kafka 增加仅回环的 EXTERNAL 监听**（宿主侧 exporter 用；与 postgres_exporter 的 6432 回环同一思路）：

| 项 | 变更 |
|---|---|
| 监听 | `KAFKA_LISTENERS` 增 `EXTERNAL://:9094`；`KAFKA_ADVERTISED_LISTENERS` 增 `EXTERNAL://127.0.0.1:9094`（**内部客户端仍用 `kafka:9092`，不变**） |
| 发布 | `ports: ["127.0.0.1:9094:9094"]`（**仅回环**）+ 容器加 `frontend-net`（使回环发布生效，同既有做法） |
| 回归 | Kafka healthy；topic 列表正常；**GA `/ready` → kafka=true**（内部消费链路未受影响） |

**② kafka_exporter（宿主二进制，不占容器数）**：

| 项 | 值 |
|---|---|
| 版本/来源 | `kafka_exporter` **1.10.0**；sha256 `67ad3c7c47a19b914292043a4bc8915955bec941ae0ba6915b673d21b4dc84ee` |
| **获取方式的实测教训** | GitHub Release 在本环境**下载归零失败**（90s 0 字节后 SSL EOF；与 trivy 同类问题）⇒ 改为**从 Docker Hub 镜像 `danielqsj/kafka-exporter:latest` 提取二进制**（镜像走加速器很快）：`docker create` + `docker cp /bin/kafka_exporter` ⇒ 仍保持"**exporters 作为宿主二进制**"的容器数口径 |
| 监听/连接 | HTTP `172.17.0.1:9308`（仅 docker0 网关）；`--kafka.server=127.0.0.1:9094` |
| 过滤 | `--topic.filter=^governance\..*`（只暴露治理相关 topic） |
| 关键指标（实测） | `kafka_brokers 1`、`kafka_topic_partitions{topic="governance.alerts"} 2`、`kafka_consumergroup_lag{consumergroup="governance-agent-poc",...}`、`kafka_consumergroup_lag_sum` |
| Prometheus | 新增 `job_name: kafka` → `host.docker.internal:9308`（抓取 **up**） |

**③ 新增 3 条规则**（`alerting/poc-alerts.yml` 的 `kafka-lag` 组，规则总数 8 → **11**，`promtool` SUCCESS）：

| 规则 | 表达式要点 | 级别 |
|---|---|---|
| `KafkaConsumerLagGrowing` | `(kafka_consumergroup_lag > 0) and (deriv(kafka_consumergroup_lag[10m]) > 0)`，`for: 5m` —— **"lag 持续增长 5 分钟以上"** 的字面落地 | warning |
| `KafkaBrokerDown` | `kafka_brokers < 1`（单节点无冗余 ⇒ 实时链路中断） | critical |
| `KafkaExporterScrapeFailed` | `up{job="kafka"} == 0` —— **"滞后不可观测"本身也要告警**（不是"没有滞后"） | warning |

**④ 端到端验证（真实制造 lag，验证后已收敛并清理）**：

```text
前置            targets: kafka=up；规则组 [kafka-lag 3, poc-slo 3, postgres-replication-slot 5]；初始 lag_sum=0
制造 lag        停 Governance Agent（消费者暂停）→ 每 45s 投 1 条告警，共 9 条
                 lag_sum: 1→2→3→4→4→5→6→7（deriv 恒 >0）
                 规则状态：pending（前 7 条，处于 5m for 窗口）→ **firing**（第 8 条，累计超 5 分钟）
恢复            启动 GA → lag_sum 归 0 → 规则 **inactive**（自动解除）
清理            删除演练消息（`ga.alert_event` 中 `lagtest-*` 共 9 行）；GA `/ready` kafka=true、consumed=9
终态            11 条规则全部 health=ok；18 容器 healthy；kafka_exporter/postgres_exporter/node_exporter/fail2ban 均 active
```

**⑤ 一个必须知道的口径细节**：`kafka_consumergroup_lag` 的 **`-1` 是"未取到偏移"的哨兵值**（分区无消息/未提交偏移），**不是"滞后 0"**。规则按 `> 0` 判定 ⇒ 既不会把 `-1` 误报成滞后，也意味着**"不可观测"不会有专门的 lag 告警**（故另加 `KafkaExporterScrapeFailed`）。排障时不要把 `-1` 读成"没问题"。

**⑥ 新增 compose 变更**：kafka 监听/端口/网络（见 ①）⇒ compose 校验和 `3ada7146…` → **`1e6398f87634f01a5f96557871f4be50aa8c36090f96504b969b1a3c6754d6da`（现行）**。

---

## 5. 镜像与可复现性收口（S1 偏差 12）

### 5.1 处置方式

把 compose 中的浮动 tag **固定到当前运行镜像的 digest**（`name:tag@sha256:…`），**不重建容器** ⇒ 运行态零变更，**下次重建即复现**。

| 原引用 | 固定后（digest 短号） |
|---|---|
| `pgvector/pgvector:pg16` | `ccc6e83d…` |
| `bitnami/pgbouncer:latest` | `823c47c0…` |
| `opensearchproject/opensearch:latest` | `fafe3fc3…` |
| `traefik:v3.0` | `a208c74f…` |
| `prom/prometheus:latest` | `5ce7540c…` |
| `prom/alertmanager:latest` | `690c7b52…` |
| `zcube/cadvisor:latest` | `f5d9acd3…` |
| `openmetadata/server:2.0.1` | `55d6df72…` |
| `openmetadata/ingestion:2.0.1` | `00bfe3fe…` |
| `openpolicyagent/opa:latest` | `1b9ca9be…` |
| `mcr.microsoft.com/presidio-analyzer:latest` | `286e3fa7…` |
| `mcr.microsoft.com/presidio-anonymizer:latest` | `a10a12a2…` |
| `ai-governance/superset:6.1.0-pg` | `c8e65ec4…`（本地构建） |
| `ai-governance/governance-portal:0.1.0` | `73790e0f…`（本地构建） |
| `ai-governance/flink:1.20.1-cdc3.6.0` | `5681153a…`（本地构建，含 `--force-recreate` 后用新镜像） |
| `apache/kafka:4.0.0` | `3f7b9391…`（新建即固定） |

**校验和链路**：`aee7c2c4…`（S2-1）→ … → **`b70ae7a9cb19c31c5a1d9d0326ddaac510727a905de858dfb9b688d6ae1c6d92`（现行）**；变更前均有 `docker-compose.yml.bak-20260918-*` 备份。

**注意（自建镜像）**：`ai-governance/*` 三个镜像是本地构建，其 digest 即本地镜像标识；**若本地镜像被 prune，需按 `flink-image/`（及各自 Dockerfile）重建**——构建输入（基础 digest + JAR 坐标 + sha256）均已入 Git。

**镜像漏洞扫描**：S3-3 要求"固定 digest + 上线前扫描"。此前只完成"固定 digest"；**2026-09-18 尝试用 `trivy` 扫描但未完成**（带宽与加速器白名单限制），原因、可复现命令与替代路径见 §9。

---

## 6. compose 变更点（本次）

| # | 变更 | 说明 |
|---|---|---|
| 1 | 新增 `kafka` | S3-2，无端口发布，2VCPU/4G |
| 2 | 新增 `flink-jobmanager` / `flink-taskmanager` | S3-3，JM 回环 8081，slots=2 |
| 3 | `pgbouncer` 增加 `ports: 127.0.0.1:6432:6432` | 宿主侧运维/exporter 经 PgBouncer（**不直连 5432**） |
| 4 | `pgbouncer` 网络加 `frontend-net` | 使回环端口发布生效（内部网络容器的已知行为） |
| 5 | `prometheus` 增挂 `rules/` 并加 `--web.enable-lifecycle` | 规则热加载（`POST /-/reload`） |
| 6 | 全部镜像固定 digest | §5 |

**未改**：网络拓扑（仍三网）、对外暴露面（仍仅 443；22 维持限源）、任何组件的架构定位。

---

## 7. FERNET_KEY 重做（官方默认值 → 自持密钥）

### 7.1 为什么现在做（时机论证）

`FERNET_KEY` 用于加解密 OpenMetadata 里保存的**连接凭证**。此前沿用**官方默认值**（公开已知），当时记为"已知风险 + 接源前重评"（《…S2接入方案.md》§9.3）。

**窗口选择**：门禁③ 判定与本次重做**放在同一窗口**——理由是重做会重置 `openmetadata_db`，而门禁③ 的证据包描述的是"当时的部署状态"，两者分开做会让证据与状态错位两次。现在做，代价是**重跑一次迁移 + 重建 1 条 ingestion 管道**；等接了含真实凭证的业务源再做，就要连采集配置一起重做，且期间凭证是用公开已知的 key 加密的。

| 项 | 处置前 | 处置后 |
|---|---|---|
| 密钥来源 | 官方默认值（**公开已知**） | **自持随机 key**（`base64url(32 随机字节)`，44 字符） |
| 存放 | 明文写在 compose 两处 | `.env` 的 `OPENMETADATA_FERNET_KEY`（**0600**），compose 以 `${...}` 引用 |
| OM 侧生效 | — | server 与 ingestion 两侧 `sha256` 一致（实测 `272ba5a95f197cfc`，与期望值相同） |

### 7.2 执行步骤（可复现）

```text
① 生成新 key：python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())'
② 备份：openmetadata_db 逻辑 dump + compose + .env → /data/backup/fernet-reset-<ts>/（0700）
③ 接线：写入 .env；compose 两处 FERNET_KEY 改为 ${OPENMETADATA_FERNET_KEY}
④ 停服：docker compose stop openmetadata-server openmetadata-ingestion
⑤ 重建库：DROP DATABASE openmetadata_db; CREATE DATABASE ... OWNER <OM_DB_USER>
⑥ 迁移：docker compose up --abort-on-container-exit --exit-code-from execute-migrate-all execute-migrate-all
⑦ 起服：up -d openmetadata-server openmetadata-ingestion（等 healthy）
⑧ 复建链路：pipelineService + ingestionPipeline + 部署 DAG + 触发（复用 S2 期间的 API 脚本）
⑨ 清搜索残留：删除重置前实体的 `*_search_index` 文档（见 7.4 坑 2）
⑩ 复跑门禁③ 判据 + 刷新 gov_metrics
```

### 7.3 验证证据

```text
备份             /data/backup/fernet-reset-20260918-132713/openmetadata_db.sql.gz（778,353 B）+ compose + .env
处置前           openmetadata_db 195 表 / pipeline_entity 1 行 / entity_extension_time_series 69 行
迁移             容器退出码 0；迁移后仍 195 表
服务             openmetadata_server healthy、openmetadata_ingestion healthy；OM / → HTTP 200
key 生效         期望 sha256[:16]=272ba5a95f197cfc；server=272ba5a95f197cfc；ingestion=272ba5a95f197cfc  ✔ 两侧一致
链路复建         pipelineService HTTP 201（id 9346da84…）/ ingestionPipeline HTTP 201（3db798c7…）
                 deploy DAG HTTP 200（Workflow has been created）/ deployed=True
                 触发后 DagRun manual__2026-09-18T05:41:37… → **state=success**（end 05:41:44Z）
OM 侧资产        pipelines total=1；pipelineState=Pending（**已知非缺陷**：终态由下一次运行回填，见证据包 §5 第 4 项）
DB 计数          pipeline_entity=1、entity_extension_time_series=8
门禁③ 判据复跑   OM 侧 prepared-statement 0 / transaction aborted 0；PgBouncer 侧同样 0；池 cl_waiting=0、maxwait=0
gov_metrics      手动重跑 ETL 成功；pipeline 资产数在清理幽灵文档后回到 1
compose 校验和   b70ae7a9… → **e35b560cf2b1fe5775d50f04aa69b95c1eefc2f11c396622ac9774786f374574**（现行）
```

### 7.4 三个踩坑（复现步骤已修正）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 重置后 OM 登录 **401**，连带 gov_metrics ETL 报 `HTTP 401` | 新库由 OM bootstrap 种入的 admin 口令哈希（`$2a$12$…`）**与 `.env` 的 `OPENMETADATA_ADMIN_PASSWORD` 不一致**（重置前的库是当时"精准写库定版"的产物，重建后退回 bootstrap 默认） | 用 ingestion 镜像内 `bcrypt` 生成 `$2b$12$…`（经 **stdin** 传口令，不走命令行参数），`UPDATE user_entity` 精准写回 → 登录 HTTP 200（方法同《…S2接入方案.md》§8.3） |
| 2 | OM 搜索 `airflow_metadata_poc` 命中 **2 条**，且 `gov_metrics` 的 `asset_total_count(pipeline)=2` | **仪表库重置不会清 OpenSearch**：重置前实体的文档（旧 UUID）仍在 `pipeline_search_index` / `pipeline_service_search_index` / `ingestion_pipeline_search_index` 里 → 幽灵资产并**污染指标** | 按"文档 id 是否仍在库里"逐条删除陈旧文档（3 条，HTTP 200）→ 三个索引 total 均回到 1、指标回到 1。**结论：重置 OM 元数据库必须同时清理搜索文档**（已写入本记录，勿再踩） |
| 3 | 尝试从**宿主**查 OpenSearch 索引，返回空、看似"没有索引" | OpenSearch **未发布宿主端口**（S1 偏差 4 的既定状态），宿主查不到 | 一律经容器内发起（`docker exec … curl -sk -u admin:$OPENSEARCH_INITIAL_ADMIN_PASSWORD https://localhost:9200/…`） |

### 7.5 回滚点

| 项 | 位置 |
|---|---|
| OM 数据库 dump | `/data/backup/fernet-reset-20260918-132713/openmetadata_db.sql.gz` |
| 处置前 compose / .env | 同目录（`.env` 0600） |
| 回滚做法 | 还原两个配置文件 → 停 OM → `pg_restore` 该 dump 到 `openmetadata_db` → 起 OM；**同时须还原 OpenSearch 中被清理的文档或接受搜索重建** |

---

## 8. 备份与暴露面收口（本轮顺带补齐）

### 8.1 配置出机（新增 `backup_config.sh`）——FERNET_KEY 自持后的必要补丁

**发现的缺口**：S1-8 的备份链只覆盖 **PG 逻辑 dump + OpenSearch 快照 + 出机**，**不含** `/data/ai-governance/.env`、`docker-compose.yml`、`config/`。FERNET_KEY 一旦自持，**只恢复数据库而无 `.env` ⇒ OM 的加密 secrets 无法解密**（Superset `SECRET_KEY`、Airflow `FERNET_KEY`、各库口令同理）。

| 项 | 内容 |
|---|---|
| 新脚本 | `/data/ai-governance/scripts/backup_config.sh`（源码见《…S1-8备份恢复脚本.md》） |
| 备份范围 | `.env` + `docker-compose.yml` + `config/`（traefik / openmetadata JKS / prometheus 规则 / alertmanager 等） |
| 落点与权限 | `/data/backup/config/config_<ts>.tar.gz`，**0600**（含密钥）；保留 14 天 |
| 调度 | `/etc/cron.d/ai-governance-backup` 新增 **02:55**（先于 03:00 出机） |
| 出机 | `backup_offsite.sh` 的 rsync 已增加 `$BASE/config/` |
| 首次执行 | ✅ 2026-09-18 13:45：28 个文件 / 20,077 B，含 `.env`、`docker-compose.yml`、26 个 `config/` 条目 |

### 8.2 node_exporter 监听收紧

| # | 项 | 处置前 | 处置后 |
|---|---|---|---|
| 1 | 监听地址 | `*:9100`（**全网卡**，S1 记录 §3.7 曾建议收紧） | `172.17.0.1:9100`（仅 docker0 网关） |
| 2 | 依据 | — | 与 `postgres_exporter` 同一口径；Prometheus 经 `host.docker.internal:9100` 抓取不受影响（实测抓取 4/4 up） |
| 3 | 回滚点 | — | `/root/node_exporter.service.bak-<ts>` |

---

## 9. 供应链：镜像漏洞扫描（**部分完成，执行中**）

> 状态：**部分完成**（2026-09-18 晚）。此前"固定 digest"已完成但扫描未做（S3-3 要求"固定 digest + 上线前扫描"）。本节如实记录**三层卡点、已出结果、剩余进度**。

### 9.1 三层卡点（逐层排除，全部实测）

| 层 | 现象 | 根因 | 处置 |
|---|---|---|---|
| ① 二进制 | 首次下载的 trivy `--version` **段错误 rc=139**，`file` 报 `too large section header offset` | **下载被超时截断**（GitHub Release 在本环境 12~200 KB/s，48.08 MB 需 >30 分钟） | 改**断点续传**（`curl -C -` 循环）后台跑完；解压后**必须先验 `trivy --version`** 再使用 |
| ② 漏洞库源 | `FATAL failed to download vulnerability DB … repo="mirror.gcr.io/aquasec/trivy-db:2" → connect: connection timed out` | trivy **默认走 `mirror.gcr.io`**，该域名在本环境**不可达**（mirror.gcr.io 与 Docker Hub 直连均不可达，属既有环境约束） | 显式指定 `--db-repository ghcr.io/aquasecurity/trivy-db`（**ghcr.io 实测可达**，HTTP 401 属正常鉴权） |
| ③ 超时与锁 | 前两个镜像 `context deadline exceeded`（5 分钟默认超时）；随后**全部镜像**报 `cache may be in use by another process: timeout` | ② 之后是**默认 DB 下载超时**；③ 是**上一轮被我 `pkill` 留下的陈旧缓存锁** | 用 `--timeout 30m`；清掉陈旧锁（`find /root/.cache/trivy -name '*.lock' -delete`）后单镜像复验 **rc=0** |
| ④ **Java 依赖库同源问题** | 全部移除后仍有 5 个镜像 rc=1：`Unable to initialize the Java DB: … failed to download artifact from mirror.gcr.io/aquasec/trivy-java-db:1 → connection timed out` | **trivy 的 Java 依赖库（`trivy-java-db`）也默认走 `mirror.gcr.io`**；凡含 Java 制品的镜像（Kafka/Flink/OM/OpenSearch）都需要它 | 追加 `--java-db-repository ghcr.io/aquasecurity/trivy-java-db`（**两个 DB 都要显式指定**） |

> **两条教训**：① **中断 trivy 后必须清缓存锁**（本轮**复现两次**：`pkill` 后所有扫描报 `cache may be in use by another process`，看起来像并发冲突，实际是残留锁）⇒ 扫描脚本**内置每轮清锁**；② **vuln DB 与 Java DB 是两个独立来源**，都要从 `mirror.gcr.io` 改到 `ghcr.io`，否则 Java 系镜像永远扫不出来。

### 9.2 扫描结果（**16/16 镜像全部完成**；HIGH/CRITICAL，`--ignore-unfixed --severity HIGH,CRITICAL`）

> **口径**：只统计**可修复（`--ignore-unfixed`）**的高/严重项；`openmetadata/server` 与 `opa` 为 0。命令：
> `trivy image --timeout 60m --db-repository ghcr.io/aquasecurity/trivy-db --java-db-repository ghcr.io/aquasecurity/trivy-java-db --scanners vuln --ignore-unfixed --severity HIGH,CRITICAL <image>`

| 镜像 | HIGH | CRITICAL | 备注 |
|---|---|---|---|
| `openmetadata/ingestion:2.0.1` | **149** | **5** | 数量最高（镜像 7.9 GB） |
| `bitnami/pgbouncer:latest` | **117** | **8** | |
| `ai-governance/superset:6.1.0-pg` | **100** | 0 | 派生镜像（基于 apache/superset 6.1.0） |
| `zcube/cadvisor:latest` | **99** | **6** | 社区镜像（gcr.io 不可达的替代品，供应链风险已在 S1 记录登记） |
| `mcr.microsoft.com/presidio-anonymizer:latest` | 88 | **8** | 官方镜像（`latest` 按 digest 固定） |
| `mcr.microsoft.com/presidio-analyzer:latest` | 86 | **8** | 同上 |
| `traefik:v3.0` | **77** | **5** | |
| `apache/kafka:4.0.0` | 70 | **7** | |
| `opensearchproject/opensearch:latest` | 35 | **6** | |
| `ai-governance/flink:1.20.1-cdc3.6.0`（自建） | 31 | 0 | 含 CDC/Kafka 连接器 JAR |
| `pgvector/pgvector:pg16` | 24 | **1** | |
| `ai-governance/governance-portal:0.1.0`（自研） | 10 | **3** | python:3.12-slim 基线 |
| `prom/alertmanager:latest` | 8 | 0 | |
| `prom/prometheus:latest` | 6 | 0 | |
| **`openmetadata/server:2.0.1`** | **0** | **0** | 无 HIGH/CRITICAL |
| **`openpolicyagent/opa:latest`** | **0** | **0** | 无 HIGH/CRITICAL |

> **如何解读（重要）**：① 这些是**可修复（`--ignore-unfixed`）**的高/严重漏洞计数，**不等于**"已确认可利用"；② 上线前处理方式属**人工决策**（升基础镜像 tag / 等待上游修复 / 记录风险接受）；③ 本记录**只报事实**，不替人工判定风险。
>
> **处置清单已产出（2026-09-18）**：逐镜像的"上游是否有更新（实拉 digest 比对）+ 建议动作 + 升级步骤与回滚 + 风险接受勾选表"见 **《AI数据治理平台_单机版_供应链漏洞处置清单.md》**。要点：**多数镜像已是最新 digest（只能等上游）**；可行动作集中在三处——**重建 4 个自建派生镜像**（门户/GA/Superset/Flink）、**alertmanager 有新 digest 可升**、**kafka 有 4.0.1 补丁（4.1.0 属大版本另行评估）**。

| 项 | 内容 |
|---|---|
| 工具 | `trivy`（GitHub Releases v0.74.0，`trivy_0.74.0_Linux-64bit.tar.gz`） |
| 范围（待扫） | compose 中 **16 个镜像引用**（15 个确定性 digest + 1 个本地构建镜像） |
| 口径 | `--scanners vuln --ignore-unfixed --severity HIGH,CRITICAL`（只看**可修复**的高/严重） |
| **卡点 1** | **二进制下载速率过低**：实测 GitHub Release 下载在 **12~200 KB/s** 之间抖动，48.08 MB 需 >30 分钟；首两次尝试均因超时被截断（tar 仅 9~13 MB，解出的二进制 `trivy --version` **段错误 rc=139**，`file` 报 "too large section header offset"）。**结论：截断包不可用，必须先确认完整性再用** |
| **卡点 2** | **容器路径被挡**：`docker pull aquasecurity/trivy:latest` 经加速器返回 `这镜像不在白名单`（DaoCloud 白名单限制）⇒ 走不了"容器化扫描器"这条路 |
| **已排除的怀疑** | **漏洞库源可达**：`ghcr.io` 返回 **HTTP 401（正常鉴权响应，非不可达）**；`mirror.gcr.io` 与 Docker Hub 直连不可达（属既有环境约束） |
| 当前处置 | 已改为**断点续传后台任务**（`curl -C -` 循环重试），记录在 `/tmp/trivy_dl.log`；截至 2026-09-18 14:09 已下 16.9 MB。**下载完成后即可直接扫描，无需重下** |

**可复现命令（网络允许时）**：

```bash
# 1) 取二进制并**校验完整性**（截断包会导致段错误，务必比对解压后能否 --version）
curl -fSL --retry 3 -C - -o /tmp/trivy.tar.gz \
  https://github.com/aquasecurity/trivy/releases/download/v0.74.0/trivy_0.74.0_Linux-64bit.tar.gz
tar xzf /tmp/trivy.tar.gz -C /tmp trivy && install -m 0755 /tmp/trivy /usr/local/bin/trivy
trivy --version          # 必须正常打印版本，段错误即包不完整

# 2) 逐镜像扫描（镜像已固定在 compose 中）
while read -r IMG; do
  trivy image --scanners vuln --ignore-unfixed --severity HIGH,CRITICAL --no-progress "$IMG"
done < <(grep -oE '^[[:space:]]+image:[[:space:]]+\S+' /data/ai-governance/docker-compose.yml | awk '{print $2}' | sort -u)
```

**替代路径（任选其一，均需人工定）**：

| # | 方案 | 说明 |
|---|---|---|
| 1 | 继续后台续传后扫描 | 已在跑；完成后 Agent 可直接补做并回填本节（无需人工） |
| 2 | 离线投放扫描器与库 | 从可联网环境下载 trivy 二进制 + 漏洞库（`trivy-db` OCI 包），拷入内网后 `--skip-db-update` 使用 |
| 3 | 用既有平台扫描 | 若已有镜像扫描能力（如企业 Harbor/镜像仓库自带扫描、云安全中心），在那边扫并归档报告 |

> **本轮结论**：**扫描未完成**（网络带宽与加速器白名单限制），风险敞口是"15 个固定 digest 镜像未做漏洞复核"——不是"已扫过且无高危"。**不得**在准出材料里写成已扫描。

---

## 10. 443 收敛：OM 改走 SSH 隧道（SSO 前）

### 10.1 决策与动因

原计划是"SSO 上线前把 443 按源 IP 收敛"。**人工 2026-09-18 确认：本环境没有固定的企业 VPN / 出口 IP** ⇒ 限源会因出口 IP 变化把自己锁死；而 443 上的**唯一路由就是 OpenMetadata**（Traefik `PathPrefix('/')` → `openmetadata-server:8585`，已核对动态配置）。

**结论**：不做限源，改为**撤回暴露**——443 不再对外发布，OM 与 Superset/Airflow/Prometheus/门户统一为"仅回环 + SSH 隧道"口径。

### 10.2 变更内容

| # | 变更 | 说明 |
|---|---|---|
| 1 | Traefik 动态路由撤下 | `config/traefik/dynamic/openmetadata.yml` → `openmetadata.yml.disabled-<ts>`（Traefik 只加载 `*.yml`，故不再有 OM 路由）；**回滚副本保留** |
| 2 | 443 不再发布 | traefik 的 `ports: ["443:443"]` 已注释（容器仍运行，供 SSO 上线时复用） |
| 3 | OM 增加回环端口 | `ports: ["127.0.0.1:8585:8585"]`（OM 已在 frontend-net，回环发布生效） |
| 4 | **附带修复：ETL 基址** | `gov_metrics_etl.py` 由 `https://localhost/api/v1`（走 443）改为 `http://127.0.0.1:8585/api/v1`——**这是本次唯一的隐藏依赖**，不改会导致每小时 ETL 全挂 |
| 5 | 门户链接 | `PORTAL_LINK_OPENMETADATA` 由 `https://localhost/` 改为 `http://localhost:8585/` |

### 10.3 验证证据

```text
宿主监听          443：**无任何监听** ✔        127.0.0.1:8585：docker-proxy ✔
OM 可达（回环）   http://127.0.0.1:8585/ → HTTP 200
OM 登录（回环）   /api/v1/users/login → HTTP 200，token 718 字节
Traefik           容器 healthy；OM 路由已不存在（无 443 监听即为决定性判据）
ETL              手动执行成功（新路径 8585），gov_metrics 写入 `2026-09-18 06:23:46 | pipeline | 1`
门户              governance-portal healthy（链接已改为隧道地址）
容器             17/17 healthy
compose 校验和    e35b560c… → **3ada714647383364b3c4d958fcd23d3c23da05b2b7cf364a45ae39f89d42ebe1（现行）**
```

**人工访问方式**（合并隧道，与运维界面一条命令）：

```bash
ssh -N -L 8585:127.0.0.1:8585 -L 8080:127.0.0.1:8080 -L 9090:127.0.0.1:9090 \
       -L 9093:127.0.0.1:9093 -L 8088:127.0.0.1:8088 -L 8501:127.0.0.1:8501 root@<POC服务器IP>
# 浏览器：http://localhost:8585（OM）｜8080 Airflow｜8088 Superset｜8501 门户｜9090 Prometheus｜9093 Alertmanager
```

### 10.4 预期附带收益与遗留

| # | 项 | 说明 |
|---|---|---|
| 1 | **自签证书的 401 循环问题应随之消失** | `http://localhost` 属浏览器"安全上下文"，Service Worker 可用（此前是"证书未受信 → SW 被禁 → OM 新 UI 登录态切换失败"）。**待人工首次浏览器使用确认**，确认后可关闭《部署步骤_单机版》S1-6 的该条适配注 |
| 2 | 安全组 443 规则 | 宿主已无监听，**可选**把安全组入方向 443 规则也删除（纵深防御，非必需；SSO 上线时再放开） |
| 3 | **22 的限源与"无固定出口 IP"是同一类风险** | 22 目前限源 IP。若办公出口 IP 会变，可能出现 **SSH 也进不去** —— 兜底是阿里云控制台 VNC（口令已轮换并登记）。建议：把常用出口 IP 都加进 22 的白名单，或明确以控制台 VNC 为应急通道 |
| 4 | 证书 | `/data/tls/server.crt` 与 Traefik TLS 配置保留（SSO 上线时复用），不再承载 OM 访问 |

---

## 11. 未做 / 待确认

| # | 项 | 说明 |
|---|---|---|
| 1 | **Governance Agent** | 唯一自研组件，S3-6 范围；仓库尚无代码目录，**本轮未部署**（`/data/ai-governance/governance-portal` 的门户已在等它上线后自动结束降级） |
| 2 | **Flink CDC 作业** | S3-5，依赖源库信息与门禁④ 文件 B（复制槽参数）落值 |
| 3 | **源库侧 exporter 实例** | 需源库 DSN + **只读账号**（DBA 提供）；届时新增一个 systemd 实例/单元，规则无需改动（阈值由 SQL 计算） |
| 4 | **复制槽兜底与水位阈值** | 源库 `max_slot_wal_keep_size` 取值仍 `待确认`（挂账项 #3，DBA 核定）；本机 POC 库**故意不设**（非源库） |
| 5 | **通知通道** | IM/Webhook 地址待定（S3-6）；当前 Alertmanager 仅 `default` 空接收器 |
| 6 | 日志类事件 Topic | 命名待接入方确认，未预建 |
| 7 | 镜像漏洞扫描 | **未完成**：工具下载受带宽限制（12~200 KB/s）+ 容器路径被加速器白名单挡住；**漏洞库源 ghcr.io 实测可达**。已改后台断点续传，可复现命令与三条替代路径见 §9。**风险敞口=15 个镜像未做漏洞复核，不得写成"已扫描"** |
| 8 | ~~`node_exporter` 监听面~~ | **已收紧**为 `172.17.0.1:9100`（见 §8.2） |
| 9 | Oracle CDC / OpenLineage JAR | 见 §3.1（授权与兼容矩阵） |
| 9b | ~~OpenLineage 接入方式~~ **已查证（2026-09-19，§12.7/§12.8）** | 结论：**`-javaagent` 路线永久关闭**（该 fat jar 的 `Premain-Class` 声明是错的，官方文档从未要求 javaagent）；**Flink 1.x 路径不支持 Flink SQL / 须改代码 / 须 Application Mode**，**Flink 2.x 路径所需 SPI 在 1.20.1 中不存在（实测 0 命中）** ⇒ **当前形态拿不到血缘**。要拿需三选一：**A 升 Flink 2.x**（组件版本变更，须批准；且 2.x 目前仅 Kafka connector 提供 lineage，CDC JDBC 源须再核）／**B 作业改 DataStream API + Application Mode**／**C 暂不上报（建议：短期取 C，与 A 一并在 M-Prod 立项时评估）**。**属人工决策** |
| 10 | 并行期的阶段纪律 | 本轮按人工授权做了"S3 前置（不碰源库）"；**S2 准出与门禁③ 判定仍未完成**，S2 三项指标（覆盖率/PII/血缘）待接源 |

---

## 12. S3-5 作业骨架演练（datagen，**不接源库**）

### 12.1 目的

S3-5（CDC 作业开发）的自然前置是源库。但**作业框架本身的风险不必等源库**——本轮用 `datagen` 造变更流，把"作业提交 → 窗口规则聚合 → Checkpoint 落盘 → Savepoint 触发 → **从 Savepoint 恢复**"整条链路先跑通，接源后只剩"换 source"。

### 12.2 演练骨架（Flink SQL）

```text
datagen 变更流（event_id / table_name / op / null_cols / event_time + WATERMARK 5s）
   → 5 秒 TUMBLE 窗口聚合：changed=COUNT(*)、null_rows=SUM(null_cols>0)
   → print sink
   （口径：ADR-A5「增量窗口产出 → 告警通道，**不进质量看板**」）
配置：execution.checkpointing.interval=10s、EXACTLY_ONCE、state.backend=hashmap
```

### 12.3 验证证据（实测）

```text
提交             Job ID 25271a2d… state=RUNNING name=insert-into_…rule_out
Checkpoint       counts: completed=6 failed=0；宿主落盘 /data/flink-checkpoint/<jid>/chk-6|chk-7（含 _metadata / shared / taskowned）
Savepoint        POST /jobs/<jid>/savepoints {cancel-job:true} → request-id fdc9c6e5…
                 状态 IN_PROGRESS → **COMPLETED**；路径 file:/opt/flink/checkpoints/savepoints/savepoint-25271a-6f0594b9d07c（宿主可见）
从 Savepoint 恢复 以 SET 'execution.savepoint.path'=… 重新提交 → 新 Job ID bf739082…
                 **REST 恢复证据**：{"restored": {"id": 8, "is_savepoint": true,
                                   "external_path": "file:/opt/flink/checkpoints/savepoints/savepoint-25271a-6f0594b9d07c"}}  ✅
窗口输出         TaskManager 日志：+I[2026-09-18T10:07:20, 2026-09-18T10:07:25, 02a412, 1, 1]
清理             两个作业均 CANCELED；slots-available=2；checkpoint/savepoint 目录清空；演练 SQL 删除
终态             Flink 1.20.1，jobs-running=0、jobs-failed=0；18 容器 healthy；磁盘 25G/70G 可用
```

### 12.4 两个实测踩坑（对后续作业脚本都适用）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 首轮提交**语法解析失败**（`"-" "(" ... "INTERVAL"`），作业根本没起来 | **Flink SQL 的字符串必须用单引号**（双引号是标识符）；我为了绕开嵌套 shell 引号改用了双引号 | 改为「**SQL 写成文件 → 上传 → `docker cp` 进容器 → `sql-client -f`**」，彻底避开嵌套引号（脚本模板已落地） |
| 2 | 取消作业时**取消错了对象**（恢复态作业仍在跑，占 1 个 slot） | 脚本用 `jobs[0]` 取 jobid，取到的是"已取消的旧作业" | **必须按状态过滤**：`[j["jid"] for j in jobs if j["state"]=="RUNNING"]`；这条对后续所有作业运维脚本都成立 |

### 12.5 这次演练证明了什么 / 没证明什么

| 已证明 | **未证明（明确声明）** |
|---|---|
| 作业提交与调度、窗口聚合与 Sink 输出可用 | **真实 CDC source 未验证**：Flink CDC 连源库、快照+增量、位点与 Checkpoint 绑定（依赖源库） |
| **Checkpoint 落本地数据盘**（ADR-A4 单机形态）；6 次完成、0 失败 | **OpenLineage 作业图级血缘上报未验证**：镜像**未装 OL JAR**（属 S3-5 待加，需按官方兼容矩阵选版本） |
| Savepoint 触发成功并落在可回滚路径 | **断流恢复 / 重快照演练未做**（S3-7，须源库或副本） |
| **从 Savepoint 恢复**：REST 明确返回 `is_savepoint=true` 与恢复源路径（ADR-A2 第 3 件机制） | 规则逻辑的**业务正确性**（需人工审核，且当前只有 datagen 假数据） |

### 12.6 结论

**作业框架侧无需等源库**：接源后 S3-5 只剩三件事 —— ① 换 source 为 Flink CDC（含只读+复制权限账号）；② 按真实业务口径细化窗口规则并**人工审核**；③ ~~加 OpenLineage JAR 并验证血缘上报~~ → **2026-09-19 查证：当前形态（Flink 1.20.1 + Flink SQL）下不可行**，须先做 A/B/C 选择（见 §12.7/§12.8），**属人工决策**。**单机形态不得宣称 Flink HA**（本次演练也未触碰跨节点恢复）。

### 12.7 OpenLineage 接入预研结论（A4，2026-09-18）

**两端先摸清，再定版本**：

| 端 | 结论 | 证据 |
|---|---|---|
| Flink 侧 JAR | 制品 `io.openlineage:openlineage-flink` 可用，最新 **1.53.0**（`lastUpdated=2026-09-01`，活跃维护）；**33.6 MB / 5122 classes ⇒ fat jar，可直接进 `/opt/flink/lib`** | Maven Central 实查 + 实下 jar 统计 |
| **版本对应** | ✅ **已查证（2026-09-19，官方文档源文件，见 §12.8）**：连接器**按 Flink 大版本分两条实现**——**Flink 1.x 用 `JobListener`（须改作业代码、**不支持 Flink SQL**、仅 Application Mode + `execution.attached:true`）**；**Flink 2.x 用原生 `JobStatusChangedListenerFactory`（FLIP-314，支持 SQL、无需改代码）**。**该版本号是 OpenLineage 自己的线，不能当作 Flink 版本号** | 官方 `website/docs/integrations/flink/{about,flink1,flink2}.md`（sha256 见 §12.8） |
| **OM 接收端** | ✅ **已实测可用**（见下方原始输出） | 2026-09-18 三次探测 |

**接收端探测原始结果（决定性）**：

```text
POST /api/v1/openlineage/lineage  无认证            → HTTP 401（端点存在且**必须认证**）
POST /api/v1/openlineage/lineage  Bearer JWT、缺参数 → HTTP 400：
     {"code":400,"message":"[query param producer must not be null, query param schemaURL must not be null]"}
POST …?producer=…&schemaURL=…     Bearer JWT、合规事件体 → **HTTP 200**：
     {"status":"success","message":"Event processed, no lineage edges created","failedEvents":[],"lineageEdgesCreated":0}
```

**接入方案（版本核定后执行，步骤与纪律见 `flink-image/README.md`）**：① Dockerfile 增 OL jar（下载 + **sha256 校验**，同现有 5 个 JAR 的纪律）；② 重建镜像并重跑"JM/TM healthy + 作业提交 + Checkpoint"验收；③ 作业配置 OL **HTTP transport** → OM 该端点（`producer`/`schemaURL` + 认证）；④ **用已有 datagen 演练作业先验证上报（不需要源库）**。

> **待确认**：① 与 Flink 1.20.1 的官方兼容结论（人工查官方页）；② **长期凭据形态**——本次探测用 Bearer JWT，长期上报需按官方文档确认 API Key/服务账号做法，避免 token 轮换导致上报中断。

**A4 附加实验（同日，**失败并已回滚**，证据完整）**：

```text
尝试方式      按 MANIFEST 声明用 -javaagent 挂载 openlineage-flink-1.53.0（compose：FLINK_PROPERTIES 加 env.java.opts.all + OPENLINEAGE_CONFIG + 只读挂载）
结果          ❌ JM/TM **双双 unhealthy**：NoSuchMethodException: io.openlineage.flink.OpenLineageFlinkJobListener.premain(String, Instrumentation)
              FATAL ERROR in native method: processing of -javaagent failed, processJavaStart failed
处置          **立即回滚**（删 javaagent/OPENLINEAGE_CONFIG/恢复 volumes）→ 重建 JM/TM → **恢复 healthy**、slots 2/2、无残留作业
静态核查      1.20.5 / 1.21.1 / 1.24.2 的 listener class（均 8649 B）**同样不含 premain/agentmain** ⇒ 非版本偶发
制品清单      io.openlineage 组下**无独立 java-agent 制品**（仅 openlineage-flink / -java / -spark …）
已同时证实    接收端可用（OM 端点 200，见上）
已闭合（0919）  **Flink 侧接入方式已查证**：非配置问题，而是"Flink 1.x 不支持 SQL / Flink 2.x 才有 SQL 用的 SPI"两条互斥（见 §12.8）
```

**⇒ 结论修正（2026-09-19，已查证；取代上条"待人工查文档"）**：

1. **`-javaagent` 这条路本身是错的，不是配置问题**：该 fat jar 的 `MANIFEST.MF` 里 `Premain-Class` / `Agent-Class` 指向 `io.openlineage.flink.OpenLineageFlinkJobListener`，但**全 JAR 只有 shaded Javassist 的 `HotSwapAgent` 含 `premain`**（实测常量池扫描：1 命中，且是第三方工具类）⇒ `NoSuchMethodException` 是**必然**。**官方文档从未要求 javaagent**，该 MANIFEST 声明是**过时/错误的**。⇒ **此路永久关闭，不再尝试**。
2. **在 Flink 1.20.1 + Flink SQL 的当前形态下，OpenLineage 无法上报血缘**。三条路径逐条排除，见 §12.8。
3. 要用，必须三选一，**均属组件/架构层决定，须人工批准**（Agent 不自行升级或改形态）：

| 选项 | 做法 | 代价 / 风险 |
|---|---|---|
| **A. 升级 Flink 到 2.x** | 走原生 SPI：jar 进 `lib/` + `execution.job-status-changed-listeners = io.openlineage.flink.listener.OpenLineageJobStatusChangedListenerFactory`，**支持 SQL、无需改作业代码** | **属组件版本变更**（AGENTS §6：须核许可证、更新设计方案组件表与 ADR；v3.2 的"零新增零替换"纪律会被打破）；且 2.x 的 lineage **目前只有 Kafka connector 提供**，我们是 Flink CDC JDBC 源，**是否支持需再核**；Flink 2.x 的 CDC 连接器兼容矩阵须重做 |
| **B. 作业改用 DataStream API（Java）+ Application Mode** | 在作业代码里 `streamExecutionEnvironment.registerJobListener(...)` | 需把现有 SQL 作业重写为 Java 作业；**部署形态要从 session 改 application mode**；开发量与回归成本高；仍受"仅支持列表内 source/sink"限制 |
| **C. 暂不上报血缘（维持现状）** | 不引入 OpenLineage；ADR-A7 的"作业图级血缘"顺延到接源后按新信息重评 | 血缘覆盖从"作业图级"退到"仅 OM 采集/SQL 解析可得的部分"；**须在源文档层面明示该偏差**（当前文档仍写"作业启动/变更时上报"） |

> **Agent 建议（供人工判读，不代决定）**：从"不打破 v3.2 零新增零替换、且本环境无压测资质"出发，**短期取 C、并与 A 一起在 M-Prod 立项时评估**更稳；若业务上必须本期就有作业图级血缘，则 **A 的投入产出比高于 B**（不改代码、支持 SQL），但**必须先核实 CDC JDBC 源在 Flink 2.x 下能否产出 lineage**，否则升完仍然拿不到血缘。
>
> ⚠️ **该前提已于 2026-09-19 核实：不成立（见 §12.9）**——`flink-cdc` 源码树 **0 命中**血缘接口，Flink 官方文档亦写明"连接器需自行实现 `LineageVertexProvider`、覆盖是逐步的"。⇒ **仅升 Flink 2.x 拿不到 CDC 源的血缘**；新增 **D 选项（由自研侧上报作业图级血缘）**，其可行性高于 A。

**纪律（本轮教训，保留）**：**javaagent 类试点必须在隔离环境先验证，不得直接挂到在跑的集群上**——本次直接改 compose 挂 agent，导致 JM/TM 同时 unhealthy（约 4 分钟窗口、无业务作业在跑）；处置为立即回滚 compose 并重建。证据、sha256 与复用命令见 `/data/ai-governance/experiments/openlineage/README.md`。

### 12.8 OpenLineage 官方依据（2026-09-19 查证，取代"文档站 JS 渲染抓不到"）

**为什么上轮没查清**：`openlineage.io` 是 JS 渲染站点，静态抓不到正文；但**文档的源文件就在官方仓库里**——`website/docs/integrations/flink/`。本轮经 **GitHub contents API** 取到正文（`raw.githubusercontent.com` 当时未返回，改用 `api.github.com/.../contents/...` + base64 解码成功）。

**取到的官方正文（关键结论原文）**：

| 文源 | 关键原文 |
|---|---|
| `about.md` | "The Flink 1.x connector is built on the JobListener interface … **modifications to the Flink job code are necessary** … **this implementation does not support Flink SQL**."；"Conversely, the Flink 2.0 connector leverages Flink's native interfaces … **requires no changes to the job code and does support Flink SQL**." |
| `flink2.md` | 启用方式 = jar 进 classpath + 配置 `execution.job-status-changed-listeners = io.openlineage.flink.listener.OpenLineageJobStatusChangedListenerFactory`；"**Currently, only the Kafka connector supports this functionality**"（指 connector 需实现 lineage 接口） |
| `flink1.md` | 用法 = 代码里 `OpenLineageFlinkJobListener.builder().executionEnvironment(env).build()` + `registerJobListener`；"limited to getting information from jobs running in **Application Mode**"、"requires running in **application mode** with setting **`execution.attached: true`**"；支持的 Source/Sink 仅 Kafka / Iceberg / **JDBC** / Cassandra 等列表内项 |
| `configuration.md` | 配置经 `OPENLINEAGE_CONFIG`（yml）或 Flink 配置项；含 `openlineage.flink.enableDetachedJobTracking`（**Flink 2.x 专用**）等 |

**本地实测复核（三条，逐条可复现）**：

| # | 检查 | 结果 |
|---|---|---|
| 1 | fat jar `MANIFEST.MF` | `Premain-Class`/`Agent-Class` = `io.openlineage.flink.OpenLineageFlinkJobListener`（**错误声明**） |
| 2 | 全 JAR 常量池扫 `premain`/`agentmain` | **仅 1 命中**：`io/openlineage/flink/shaded/javassist/util/HotSwapAgent.class`（第三方 shaded 工具，不是入口）⇒ 该 JAR **不是 java agent** |
| 3 | fat jar `META-INF/services` | 注册了 **`org.apache.flink.core.execution.JobStatusChangedListenerFactory` → `io.openlineage.listener.OpenLineageJobStatusChangedListenerFactory`** ⇒ 这是**给 Flink 2.x 用的 SPI 自动装配** |
| 4 | **Flink 1.20.1 是否有该 SPI** | `docker cp` 出 `flink-dist-1.20.1.jar`（125,887,715 B）用 python 扫：`JobStatusChangedListener*` **0 命中**；只有 `org/apache/flink/core/execution/JobListener.class` ⇒ **SPI 在 1.20.1 不存在**（FLIP-314 属 Flink 2.0） |

**产物与 sha256（证据归档，服务器 `/data/ai-governance/experiments/openlineage/`）**：

| 制品 / 文档 | sha256 |
|---|---|
| `openlineage-flink-1.53.0.jar`（33,652,234 B；**核查后已删除以省空间**，需要时按下述命令重下） | `bd4daf546c3c6433002fd31e854f67d7b432a2e561fba0352b1d6c627e70f248` |
| `docs/about.md` | `b799f86e7338a0b5813d93921d010d91a473d20088f795120a8a552869f413d3` |
| `docs/flink1.md` | `d0a946acfe50c936333453f9c8eafcfa7123ab3d29cf66a49d401cddd685371b` |
| `docs/flink2.md` | `0b269c1c5961efdb7639f13a3c386d2ba31dde97d1bba16a299d4c39e33d2552` |
| `docs/configuration.md` | `626f5992e106d045abfa89062563da446ccfe682e8c1383724e63ff663829e7f` |
| `EVIDENCE-manifest.txt`（MANIFEST + services 原文） | 见服务器目录 |

```bash
# 重下 jar（需要复现时）
curl -fsSL -o openlineage-flink-1.53.0.jar \
  https://repo1.maven.org/maven2/io/openlineage/openlineage-flink/1.53.0/openlineage-flink-1.53.0.jar
sha256sum -c <<< "bd4daf546c3c6433002fd31e854f67d7b432a2e561fba0352b1d6c627e70f248  openlineage-flink-1.53.0.jar"
```

> **结论一句话**：**不是我们配错了，是"Flink 1.x + 不支持 SQL"与"Flink 2.x + SQL"这两条互斥**——我们站在 1.20.1 + SQL，所以现阶段**拿不到 OpenLineage 血缘**；要拿只能动组件版本或作业形态（§12.7 的 A/B/C 三选一，属人工决策）。

### 12.9 A 选项前提核实：CDC JDBC 源在 Flink 2.x 下**能否**产出 lineage（2026-09-19）

**问的是什么**：§12.7 的选项 A（升 Flink 2.x）能否拿到血缘，取决于"**Flink CDC 的 JDBC 源在 2.x 下是否实现血缘接口**"。这条不核实清楚，升级有可能白升。

**结论：不能。仅升级 Flink 2.x 拿不到 CDC 源的血缘。**

**证据（三条，均可复现）**：

| # | 检查 | 结果 |
|---|---|---|
| 1 | **Flink 官方血缘文档**（`apache/flink` master `docs/content/docs/internals/data_lineage.md`） | 原文："The Flink community will **gradually** support all of the common connectors, such as **Kafka, JDBC, Cassandra, Hive**. If you have a **customized connector** defined, you need to have **customized source/sink implementations of the `LineageVertexProvider` interface**." ⇒ **血缘由连接器自己实现**，且覆盖是"逐步"的（Kafka 先行） |
| 2 | **`apache/flink-connector-kafka`（main）** | 存在整包 `flink-connector-kafka/src/main/java/org/apache/flink/connector/kafka/lineage/`（`KafkaDatasetIdentifier` / `KafkaDatasetFacet` / `LineageUtil` …）⇒ **Kafka 连接器已实现** |
| 3 | **`apache/flink-cdc`（master）** | git tree 3925 条路径中**含 "lineage" 的路径 = 0**；`LineageVertexProvider` / `LineageGraph` / `JobStatusChangedListener` **均 0 命中** ⇒ **flink-cdc 完全没实现 FLIP-314 血缘接口**（CDC 的 PostgreSQL/MySQL 源同理） |

**复核方法（可重跑）**：拉三个仓库的 git tree 按路径关键字过滤 + 取 Flink 文档正文，脚本与输出见本节命令记录（GitHub API 通道，`raw.githubusercontent.com` 当时未返回）。

**对选项 A 的影响**：**A 的收益前提不成立**——升级 Flink 2.x 后，我们的 **PostgreSQL CDC 源仍不会产出血缘**（只有 Kafka 源/汇会）。

**因此出现第四条路（**新增，属设计决策**）**：

| 选项 | 做法 | 评价 |
|---|---|---|
| **A′ 让血缘边界落在 Kafka** | 若将来引入 CDC→Kafka→Flink(Kafka 源) 的形态，则 Kafka 连接器可产出血缘；**但这等于改数据链路形态**（ADR-A1 明确 Kafka 只承担日志类事件与 `governance.alerts`，**不承担 CDC 传输**）⇒ **与现有 ADR 冲突，需先改 ADR** | 代价大，除非 M-Prod 另有规划 |
| **D 由自研侧上报作业图级血缘** | 不再依赖 Flink 血缘接口：**GA（自研）在作业启动/变更时向 OM 注册作业图级血缘**（ADR-A7 的原文只说"作业启动/变更时上报"，**未限定必须由 OpenLineage 上报**） | **最贴合 ADR-A7 的语义**、不引入新组件；但属**机制变更**（原设计用 OpenLineage），须人工批准并回写设计方案 |

> **Agent 建议（供人工判读）**：若"作业图级血缘"是本期的硬需求，**选项 D 的实际可行性高于 A**（A 已证伪、A′ 与 ADR-A1 冲突、B 需重写作业）；否则维持 **C（暂不上报）**，把 D 与 A′ 一并放到 M-Prod 立项时按当时链路形态定。

> ✅ **人工决定与落地（2026-09-19）：选定 D1 并已实施**——GA 自研上报（解析 Flink SQL → OM 建/删边，`source=PipelineLineage` + `sqlQuery`）。
> **端到端验收通过**：临时实体 → `status=applied` → OM 读回下游边（含作业 SQL）→ 幂等不重复建边 → 换 sink 后旧边删除新边生效 → **清理零残留**（tables/schemas/databases/services 全 0）；镜像重扫 **Total 13 与改动前一致**。
> **边界**：**OM 无表实体时边必然 404（真实业务边待 S2-2 接源）**；只做表/主题级边（不建作业实体 = D2）；**无周期对账**（OM 删实体连带删边而 GA 状态仍 `applied` ⇒ **已知漂移**）。
> **待人工**：**ADR-A7 的实现方式回写**（现文写"官方 OpenLineage 集成"，须改为自研上报；《设计方案》3.3 与场景 C 有同类表述）。详见《AI数据治理平台_单机版_S3-5血缘上报D1记录.md》v1.0。

---

## 13. 影响面与需同步的文档

| 文档 | 是否同步 | 说明 |
|---|---|---|
| 设计方案 v3.2 / 实施执行计划 v2.0 | **否** | 无架构、口径、门禁变更 |
| 《部署步骤_单机版》 | **是** | S3-2 / S3-3 落地注；0.3.2「镜像可复现性」行；0.3.1 无需改（参数取值沿用 4.1.3） |
| 《单机版部署计划》 | **是** | S3-2 / S3-3 段状态注 + 版本记录 |
| 《…S3-1门禁④材料包.md》 | **是** | §6 三环状态由"全缺"更新为"采集/规则已补、通知待地址" |
| 《…S1部署记录.md》 | **是** | 偏差表补"S1-7 SLO 阈值无规则载体"一行（本轮已修正） |
| 《…S1-8备份恢复脚本.md》 | **是** | 新增 `backup_config.sh` 原文（配置出机）+ 出机 rsync 变更；说明"FERNET_KEY 自持后 .env 必须随备份" |
| 《…S2接入方案.md》 | **是** | §9.3 遗留决策 #1（FERNET_KEY）由"保持官方默认值"改为"**已重做为自持 key**" |
| 仓库外敏感文件 `server-info.local.md` | **是** | SSH 加固、root 口令轮换、`prom_exporter` 口令、FERNET_KEY 位置、**OM 隧道访问方式（8585）** |
| AGENTS.md | **是** | §1 文档地图（本文件、`alerting/`、`flink-image/`）+ §7 新增两个代码目录 |

---

## 附：复现命令清单（要点）

```bash
# Kafka 健康与 topic
docker exec ai-governance-poc-kafka-1 /opt/kafka/bin/kafka-cluster.sh cluster-id --bootstrap-server localhost:9092
docker exec ai-governance-poc-kafka-1 /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --describe --topic governance.alerts

# Flink 集群与作业
curl -s http://127.0.0.1:8081/overview          # 需 127.0.0.1（回环 + SSH 隧道）
curl -s http://127.0.0.1:8081/taskmanagers
ls -l /data/flink-checkpoint

# 采集与规则
systemctl is-active postgres_exporter node_exporter
curl -s http://172.17.0.1:9187/metrics | grep -E '^pg_up|^pg_replication_slot|^pg_settings_max_slot_wal_keep_size'
curl -s localhost:9090/api/v1/rules | python3 -m json.tool | head -40
curl -s localhost:9090/api/v1/targets | python3 -c 'import json,sys;[print(t["labels"]["job"],t["health"]) for t in json.load(sys.stdin)["data"]["activeTargets"]]'

# 规则改动后的热加载（无需重启）
curl -s -X POST -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9090/-/reload

# 规则语法/表达式校验（权威）
docker run --rm -v /data/ai-governance/config/prometheus:/p --entrypoint /bin/promtool prom/prometheus:latest \
  check rules /p/rules/poc-alerts.yml

# 镜像 digest 与 compose 校验和
grep -nE '^\s+image:' /data/ai-governance/docker-compose.yml
sha256sum /data/ai-governance/docker-compose.yml
```

**执行脚本**（落操作机仓库外 `~/.ai-datathink/remote/`）：`162`~`192` 系列（含侦察、Kafka 部署/验证/清理、Flink 制品与构建、冒烟、exporter、PgBouncer 回环、Prometheus 接线、告警投递自检 A/B、digest 固定）。

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-18 | 首次产出：① S3-2 Kafka（KRaft 单节点、`governance.alerts` 2 分区 / RF1 / 7d、零端口发布、收发往返通过、测试消息清理）；② S3-3 Flink（自建镜像 `1.20.1-cdc3.6.0` 含 base digest + 5 个 JAR 的 sha256、**实测缺 `kafka-clients` 的踩坑与修复**、JM/TM healthy、slots=2、**Kafka→Flink 冒烟 + Checkpoint 落数据盘**）；③ 告警链路补齐（postgres_exporter 0.20.1 + 只读角色 `prom_exporter` 经 PgBouncer、8 条规则、promtool 通过、**投递链路临时 sink 端到端证实**、4 个踩坑记录、**S1-7 SLO 规则载体缺口修正**）；④ 15 个镜像固定 digest + compose 校验和 `b70ae7a9…`（**未重建容器**）；⑤ 未做清单（GA / CDC 作业 / 源库 exporter / 通知地址 / 漏洞扫描 / node_exporter 监听面）与影响面。**未触碰生产源库；未改架构与门禁** |
| v1.1 | 2026-09-18 | **同批完成（人工放行四项 + Agent 三项）**：① 新增 **§7 FERNET_KEY 重做**——官方默认值 → 自持 key（`.env` 0600 + compose `${...}` 引用），195 表迁移退出码 0、两侧 key `sha256` 一致、链路复建（201/200、DagRun success）、**门禁③ 判据复跑 0 命中**；记录三个踩坑（bootstrap 口令哈希与 .env 不一致 → bcrypt 精准写库；**重置元数据库必须同时清 OpenSearch 幽灵文档**，否则污染 `asset_total_count`；宿主查不到 9200 需经容器）与回滚点；② 新增 **§8.1 配置出机**（`backup_config.sh`：`.env`+compose+`config/`，0600/14d，cron 02:55，并入出机 rsync；首次执行 28 文件 20,077 B）——FERNET_KEY 自持后此项为**必需**；③ **§8.2 node_exporter 收紧**为 `172.17.0.1:9100`（抓取 4/4 仍 up）；④ 新增 **§9 镜像漏洞扫描**（trivy，HIGH/CRITICAL + ignore-unfixed）；⑤ 更新未做清单与影响面。**门禁④ 通行的原件归档、S2 准出、通知通道地址仍待人工** |
| v1.2 | 2026-09-18 | **扫描状态如实修正 + GA 契约草案产出**：① §1 第 10 行、§5 末尾、§9、§10 第 7 项统一改为**"镜像漏洞扫描未完成"**并写明原因（trivy 二进制下载 **12~200 KB/s**，两次尝试被超时截断 → 二进制段错误 `rc=139`/`too large section header offset`；`docker pull aquasecurity/trivy` 被加速器白名单挡；**漏洞库源 ghcr.io 实测 HTTP 401 可达**），给出**可复现命令**（含"必须 `trivy --version` 验证包完整"）与**三条替代路径**（后台续传 / 离线投放扫描器与库 / 用既有平台扫），并明确纪律：**不得在准出材料里写成"已扫描"**；② 新增《AI数据治理平台_单机版_S3-6治理Agent接口规格.md》——GA 与门户的既有契约（`GET /health` 200 判就绪、`POST /chat`）、首版 9 个接口、`ga` schema 三表与最小权限角色、消费增强链路与 ADR-A3 边界、6 项验收判据、3 项待人工确认；AGENTS 文档地图同步登记 |
| v1.3 | 2026-09-18 | **新增 §10「443 收敛：OM 改走 SSH 隧道」**（原 §10/§11 顺延为 §11/§12）：人工确认**无固定企业 VPN/出口 IP** ⇒ 限源不可行，改为**撤回暴露** —— 撤下 OM 的 Traefik 动态路由（保留 `.disabled-<ts>` 回滚副本）、注释 traefik `ports: ["443:443"]`（**宿主无 443 监听**）、OM 增 `127.0.0.1:8585` 回环端口并给出合并隧道命令；**抓出并修复唯一隐藏依赖**：`gov_metrics_etl.py` 原经 443 调 OM ⇒ 改 `http://127.0.0.1:8585/api/v1`（并同步门户的 OM 链接），ETL 手动复跑成功；验证含"宿主无 443 监听 / OM 回环 200+登录 200 / 17 容器 healthy"，compose 校验和 `e35b560c…` → **`3ada7146…`**；记录两项遗留（自签证书 401 循环预期消失待浏览器确认、**22 的限源与"无固定出口 IP"是同源风险**） |
| v1.4 | 2026-09-18 | **GA 首版上线（S3-6）→ 本记录 §1 更新**：容器数 **17 → 18**（S3 段 4/4 齐）；§1 新增第 12 项；未做清单移出 GA、补"IM/Webhook 暂不处理"。GA 的详细实现与验收见新增《AI数据治理平台_单机版_S3-6治理Agent首版实现记录.md》（含两条实测新纪律：**新库必须做库级 CONNECT 收敛**、**权限变更后必须 `RECONNECT` 回收 PgBouncer 池连接**）。compose 校验和 `3ada7146…` → **`ec542f9c…`** |
| v1.5 | 2026-09-18 | **§9 漏洞扫描：三层卡点逐层排除 + 部分结果**：① 二进制**截断下载**（段错误 rc=139）→ 断点续传补齐；② trivy **默认库源 `mirror.gcr.io` 本环境不可达** → 显式 `--db-repository ghcr.io/aquasecurity/trivy-db`；③ **默认 5 分钟 DB 超时**与**中断残留缓存锁**（`cache may be in use by another process`）→ `--timeout 30m` + 清锁，单镜像复验 rc=0。已出 6 个镜像结果（superset 100 HIGH、门户 10 HIGH/3 CRIT、kafka 21/1、pgbouncer 21/1、presidio-analyzer 86/8、presidio-anonymizer 88/8），其余 10 个后台执行中；§1 第 10 行同步。**只报事实，不替人工判定风险** |
| v1.6 | 2026-09-18 | **新增 §4.6 Kafka 运维接入与消费滞后告警**：① Kafka 增**仅回环 EXTERNAL 监听 9094**（内部客户端 `kafka:9092` 不变，回归通过）；② 装 `kafka_exporter` 1.10.0（宿主二进制）——**GitHub 下载再次失败 ⇒ 改从 Docker Hub 镜像提取二进制**，保持"exporters 为宿主二进制"口径；③ `job_name: kafka` 抓取（up）+ **新增 `kafka-lag` 规则组 3 条**（lag 持续增长 5m / broker 不可用 / exporter 抓取失败），规则总数 8 → **11**；④ **端到端验证**：停 GA 造 lag（1→7）→ 规则 pending → **firing** → 恢复后归零 → **inactive**，演练消息已清理；⑤ 记录 `kafka_consumergroup_lag=-1` 哨兵值口径。compose 校验和 `3ada7146…` → **`1e6398f8…`** |
| v1.7 | 2026-09-18 | **新增 §12 S3-5 作业骨架演练（datagen，不接源库）**（原 §12 顺延为 §13）：Flink SQL 骨架（datagen 变更流 → 5s 窗口聚合 → print，ADR-A5 口径）；实测 **Checkpoint 6 次完成/0 失败落本地数据盘**、**Savepoint 触发成功**、**从 Savepoint 恢复**（REST 返回 `is_savepoint=true` 与恢复源路径）；记录两个对后续作业脚本通用的踩坑（**Flink SQL 字符串必须单引号** ⇒ 改"SQL 文件 + docker cp"；**取 jobid 必须按 state 过滤**）；明确"已证明 / 未证明"边界（未验证真实 CDC source、OpenLineage 上报、断流与重快照演练）。另：清 `/tmp` 冗余 224 MB（磁盘 25G/70G 可用） |
| v1.8 | 2026-09-18 | **§9 漏洞扫描：第四层卡点 + 11 个镜像结果**：新增卡点④——**trivy 的 Java 依赖库（`trivy-java-db`）同样默认走 `mirror.gcr.io`**，凡含 Java 制品的镜像（Kafka/Flink/OM/OpenSearch）都因它 rc=1 ⇒ 追加 `--java-db-repository ghcr.io/aquasecurity/trivy-java-db`（**两个 DB 都要显式指定**）；并强化"**中断后必须清缓存锁**"（本轮复现两次，扫描脚本改为**每轮内置清锁**）。§9.2 表补齐 **11 个镜像结果**（新增 cadvisor 99/6、traefik 77/5、pgvector 24/1、alertmanager 8/0、prometheus 6/0），5 个 Java 系镜像补扫中；§1 第 10 行同步 |
| v1.9 | 2026-09-18 | **§9 漏洞扫描完成（16/16）**：补扫出 5 个 Java 系镜像结果（**OM ingestion 149/5**、kafka 70/7、OpenSearch 35/6、flink 31/0、**OM server 0/0**），§9.2 表补齐并按严重度排序 + 附统一命令口径；§1 第 10 行由"部分完成"改为**已完成**，并保留纪律（**计数≠可利用、处置属人工决策**） |
| v1.10 | 2026-09-18 | **A1/A3 收口**：① §4.4 规则清单增 `HostDiskHeadroomLow`（可用 < 20 GiB，只盯 `/` 与 `/data`），规则总数 11 → **12**；并记录实测**假阳性教训**（未限 mountpoint 会命中 `/boot/efi` 约 200 MB 分区 → pending）；② §9 增**处置清单指引**，指向新增《AI数据治理平台_单机版_供应链漏洞处置清单.md》（逐镜像"上游是否有更新"实拉比对结论 + 三类建议动作 + 升级步骤/回滚 + 8 条风险接受勾选表）。另：**恢复演练已扩展为逐库**（含 Superset，证据见《…S1-8备份恢复脚本.md》§5） |
| v1.11 | 2026-09-18 | **A4：OpenLineage 接入预研（§12.7）**：① Flink 侧制品 `io.openlineage:openlineage-flink` 可用（最新 1.53.0、2026-09-01 更新、**fat jar 33.6 MB 可直接进 lib**）；**版本号是 OpenLineage 自己的线，不可当作 Flink 版本号**；② **官方兼容矩阵未取到**（文档站 JS 渲染）⇒ 标"须人工在浏览器核定"；③ **OM 接收端已实测可用**：`POST /api/v1/openlineage/lineage` **必须认证**（无 token→401）、**必须带 `producer`/`schemaURL`**（缺→400 且报明参数名）、合规事件 → **HTTP 200 success**；④ 给出四步接入方案（Dockerfile 加 jar + sha256、重建验收、HTTP transport 指向该端点、**用 datagen 作业先验证上报**）与两项待确认（Flink 兼容结论、长期凭据形态）。同步 `flink-image/README.md` |
| v1.12 | 2026-09-18 | **A4 附加实验：javaagent 方式实测失败并已回滚（§12.7 末）**：按 MANIFEST 的 `Premain-Class` 用 `-javaagent` 挂载 → **JM/TM 双双 unhealthy**（`NoSuchMethodException …premain`、`FATAL ERROR processing of -javaagent failed`）⇒ **立即回滚**（删 javaagent/OPENLINEAGE_CONFIG/恢复 volumes → 重建 → 恢复 healthy、slots 2/2）；静态核查 **1.20.5/1.21.1/1.24.2 的 listener 类同样不含 premain/agentmain**（非版本偶发），且 `io.openlineage` 组**无独立 agent 制品**。**更正 flink-image README**（"直接进 lib"的写法作废）。**新增待确认项：OL 的"正确接入方式 + 版本矩阵"须人工查官方文档；核对前不得再以 javaagent 接入**。**新增纪律：javaagent 类试点必须先在隔离环境验证，不得直接挂到在跑的集群上**。实验制品与 sha256 存 `/data/ai-governance/experiments/openlineage/README.md`（jar 已删，省约 100 MB） |
| v1.13 | 2026-09-19 | **OpenLineage 接入方式查证完成（新增 §12.8；§12.7 结论修正；关闭待确认项 9b）**：官方文档源文件其实在仓库内（`website/docs/integrations/flink/`），经 **GitHub contents API** 取到正文 —— **Flink 1.x 走 `JobListener`（须改作业代码、不支持 Flink SQL、须 Application Mode + `execution.attached:true`）**；**Flink 2.x 走原生 `JobStatusChangedListenerFactory`（FLIP-314，支持 SQL、无需改代码）**。本地复核三条：① fat jar 的 `Premain-Class` 声明**是错的**（全 JAR 仅 shaded Javassist 含 premain ⇒ **javaagent 路线永久关闭**，官方从未要求）；② jar 的 `META-INF/services` 注册的是 **Flink 2.x 的 SPI**；③ **Flink 1.20.1 的 `flink-dist` 中该 SPI 0 命中**（实测：无 `JobStatusChangedListener*`，只有老的 `JobListener`）⇒ **当前 1.20.1 + Flink SQL 形态下拿不到 OpenLineage 血缘**。给出 A/B/C 三条路径（升 Flink 2.x／作业改 DataStream + Application Mode／暂不上报）与 **Agent 建议（短期取 C，与 A 一并在 M-Prod 立项评估）**，**属人工决策**。附 4 份官方文档与 jar 的 sha256；33MB jar 核查后已删除（附重下命令） |
| v1.14 | 2026-09-19 | **新增 §12.9：A 选项前提核实（CDC JDBC 源在 Flink 2.x 下能否产出 lineage）——结论"不能"**。三条证据：① **Flink 官方血缘文档**（`apache/flink` master `docs/content/docs/internals/data_lineage.md`）原文写明"community will **gradually** support … Kafka, JDBC, Cassandra, Hive"且"**customized connector 需自行实现 `LineageVertexProvider`**"；② `flink-connector-kafka` 有整包 `.../connector/kafka/lineage/`（**Kafka 已实现**）；③ **`apache/flink-cdc` 源码树 3925 路径中含 lineage 者 = 0**（`LineageVertexProvider`/`LineageGraph`/`JobStatusChangedListener` 全 0）⇒ **flink-cdc 未实现 FLIP-314**。**影响**：**仅升 Flink 2.x 拿不到 CDC 源血缘，A 的收益前提不成立**；据此新增两条路——**A′ 让血缘边界落在 Kafka**（与 ADR-A1"Kafka 不承担 CDC 传输"冲突，须先改 ADR）与 **D 由自研侧（GA）在作业启动/变更时向 OM 注册作业图级血缘**（**最贴合 ADR-A7 原文，未限定必须由 OpenLineage 上报**；属机制变更需批准）。**Agent 建议：若本期必须有作业图级血缘，D 的可行性高于 A；否则维持 C**。**未改架构、未升版本、未改门禁判据** |
| v1.15 | 2026-09-19 | **D1 选定并落地（§12.9 追加）**：人工决定走 **D1 自研上报**（GA 解析 Flink SQL → OM 建/删边，`source=PipelineLineage` + `sqlQuery`）。**端到端验收**：临时实体 → `applied` → OM 读回下游边（含作业 SQL）→ 幂等不重复建边 → 换 sink 后旧边删/新边生效 → **清理零残留**（tables/schemas/databases/services 全 0）；镜像重扫 **Total 13 与改动前一致**。记录 5 条踩坑（**窗口 TVF 被"含括号"误判**、JDBC sink 无 database-name 需从 url 反解、**清理排序写反留残留**、**OM 删实体连带删边而 GA 状态仍 applied ⇒ 已知漂移（缺对账）**、OM lineage 响应 `toEntity` 为 id 字符串）。**待人工：ADR-A7 实现方式回写**（现文写 OpenLineage）。指向新增《…S3-5血缘上报D1记录.md》 |
| v1.16 | 2026-09-19 | **Flink 作业 / Checkpoint 告警落地（结论摘要 12c）**：设计把"作业状态 / Checkpoint 成功率"列为**最高优先级告警**，此前**整条链都空着**（reporter 未启用、`/metrics` 404、无抓取、无规则）。本轮补齐：① JM/TM 的 `FLINK_PROPERTIES` 加 `metrics.reporter.prom.*`（端口 **9249**；容器内 200）；② `prometheus.yml` 加 `job_name: flink`（走 backend-net 容器名，**不发布宿主端口**；两个目标 **up**）；③ `flink-jobs` 组 **5 条规则**（抓取失败 / **登记了作业但没在跑** / 作业重启 / Checkpoint 失败 / Checkpoint 停摆）⇒ **规则总数 22 → 27**；④ 采集器新增 `ai_governance_lineage_registered_jobs`（D1 登记目录作业数），供"登记了但没在跑"跨源比对。**真实触发验证**：提交 datagen 演练作业（RUNNING、Checkpoint 4 完成 / 0 失败）→ 取消 → 规则 `pending`（`for` 到点后 firing）。**实测踩坑（会静默失效）**：跨源组合 `A and B` 因**标签集不同永不匹配**，规则永远 `inactive` ⇒ **必须写 `on()`**；纪律：新规则必须做真实触发验证，不能只看 promtool 通过。详见 `alerting/README.md` |

---

*本文件为 S3 前置（不接触源库部分）的部署与验证记录，由《部署步骤_单机版》S3-2 / S3-3 / S3-4 展开，不含新增架构决策；与源文档冲突时以源文档为准并回报修订。*
*门禁判定与签字为人工专属，Agent 不代签、不背书。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；版本、参数与扫描结论执行前请按官方文档核对。
