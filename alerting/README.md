# alerting/ · 告警规则与采集查询

> **内容**：Prometheus 告警规则（`poc-alerts.yml`）+ postgres_exporter 自定义查询（`pg_exporter_queries.yml`）+ 采集脚本（`job_freshness.sh`、`ollama_probe.sh`、`container_probe.sh`、`alert_digest.sh`）。
> **部署落点**：服务器 `/data/ai-governance/config/prometheus/rules/poc-alerts.yml`（只读挂载进 `prometheus` 容器 `/etc/prometheus/rules`）、`/etc/pg_exporter_queries.yml`（宿主 `postgres_exporter` 服务用 `--extend.query-path` 加载）、`/data/ai-governance/scripts/*.sh`（cron → node_exporter **textfile** 收集器）。
> **依据**：S1-7（SLO 阈值 CPU<80% / 内存<85%）、S3-4（复制槽兜底 + 水位 70% 告警）、S3-6（作业状态/Checkpoint 告警，待作业上线后补）。

## 为什么放在一起

规则与采集口径是一体的：**没有对应指标，规则就是摆设**。本目录同时固定"指标从哪来"（exporter 查询）与"怎么判"（规则），避免两边漂移。

## 纪律

1. **阈值不硬编码**：复制槽水位阈值 = `retained_bytes / max_slot_wal_keep_size`，**由 SQL 按源库实际兜底值现场计算**（`retained_pct_of_budget`）。改源库参数即改阈值，规则文件无需动；也避免在规则里写下一个未经 DBA 核定的数值。
1b. **文件系统类规则必须限定 `mountpoint`**：只盯 `/` 与 `/data`。不加过滤会命中 **`/boot/efi`（约 200 MB 的 ESP 分区）** 造成假阳性——2026-09-18 实测踩到过一次（`HostDiskHeadroomLow` 直接进 pending）。
2. **"没配兜底"必须单独告警**：兜底为 `-1` 时百分比指标为 NULL，水位规则永远不会触发 ⇒ 必须保留 `PgReplicationSlotNoSafetyCap`（fail-visible）。
3. **告警范围收窄**：`NoSafetyCap` 仅在"该实例确实存在复制槽"时报，避免平台自身 PG（无槽）刷屏。
4. **改规则后必须**：`promtool check rules` → `POST /-/reload`（Prometheus 已带 `--web.enable-lifecycle`）→ 核对 `/api/v1/rules` 里 `health=ok` → 在本文件与《…S3前置部署记录.md》追加版本记录。
5. **`--extend.query-path` 是 deprecated**：v0.20.1 的 `--config.file` **不接受 `queries:` 键**（实测 `field queries not found in type config.Config`），故暂时沿用；官方键名明确后迁移，并在部署记录中留痕。
6. 规则与查询**入 Git**；服务器上的文件由本目录同步，禁止只改服务器。
7. **Kafka lag 的哨兵值**：`kafka_consumergroup_lag = -1` 表示**未取到偏移**（不是"滞后 0"）；规则按 `> 0` 判定，故"不可观测"另有 `KafkaExporterScrapeFailed` 兜底，排障时不要把 `-1` 读成"没问题"。
8. `kafka_exporter` 为**宿主二进制**（`172.17.0.1:9308`），连接 Kafka 的**仅回环 EXTERNAL 监听** `127.0.0.1:9094`（内部客户端仍用 `kafka:9092`）；**GitHub Release 在本环境下载不可靠，二进制从 Docker Hub 镜像提取**（方法与 sha256 见《…S3前置部署记录.md》§4.6）。

## 已覆盖 / 未覆盖

| 覆盖 | 未覆盖（待办） |
|---|---|
| 宿主机 CPU / 内存 / 文件系统（S1-7 SLO）+ **磁盘绝对余量 < 20 GiB**（2026-09-18 新增） | 作业状态与 Checkpoint 成功率（S3-6，依赖 Flink 作业上线） |
| PG 复制槽：无兜底 / 水位 70% / 无消费者持有 WAL / 已失效 / 超兜底（S3-4） | —— |
| **Kafka 消费滞后**（`kafka-lag` 组 3 条：lag 持续增长 5m / broker 不可用 / exporter 抓取失败） | ——（2026-09-18 补齐，设计方案 4.5） |
| **Flink 作业 / Checkpoint**（`flink-jobs` 组 **5** 条：抓取失败 / **登记了作业但没在跑** / 作业重启 / Checkpoint 失败 / Checkpoint 停摆） | ——（**2026-09-19 新增**；数据来自 Flink 官方 Prometheus reporter，见下） |
| **作业新鲜度**（`job-freshness` 组 **10** 条：PG/OS/配置备份 + gov_metrics ETL + RAG 刷新 + **血缘对账**是否过期；出机仅在已配置时判定；采集器自身 + 指标完整性护栏） | ——（2026-09-18 补齐、2026-09-19 增 `LineageReconcileStale`） |
| **Ollama 运行面**（`ollama-runtime` 组 **7** 条：API 不可达 / 容器 CPU 饱和 / 容器内存高 / **OOM kill** / 模型不卸载 / 采集器停摆 / 指标整体缺失） | ——（**2026-09-19 新增**；数据来自 `ollama_probe.sh`（API 面）+ `container_probe.sh`（容器面）→ textfile，见下；Ollama 0.34.2 **无 /metrics 端点**，cAdvisor 在本环境**取不到逐容器指标**） |
| **通用容器运行面**（`container-runtime` 组 **6** 条：指标整体缺失 / 采集器停摆 / **序列数不足** / **OOM kill** / 内存近上限 / CPU 持续饱和） | ——（**2026-09-19 新增**；数据来自 `container_probe.sh` → textfile，**替代 cAdvisor 在本环境失效的能力**，见下） |

> **规则总数 40**（`poc-slo` 4 + `postgres-replication-slot` 5 + `kafka-lag` 3 + `flink-jobs` 5 + `job-freshness` 10 + `ollama-runtime` 7 + `container-runtime` 6）。

### Flink 指标接入（2026-09-19 新增，S3-6「最高优先级告警」的另一半）

设计把「作业状态 / Checkpoint 成功率 / slot 水位」列为**最高优先级告警**：slot 水位 5 条早已就位，**Flink 这一半此前整条链都空着**（reporter 未启用、无抓取、无规则）。补齐方式：

| 环节 | 做法 |
|---|---|
| Flink 侧 | `FLINK_PROPERTIES` 加 `metrics.reporter.prom.factory.class: org.apache.flink.metrics.prometheus.PrometheusReporterFactory` + `metrics.reporter.prom.port: 9249`（JM/TM 都加） |
| 抓取 | `prometheus.yml` 加 `job_name: flink` → `flink-jobmanager:9249` 与 `flink-taskmanager:9249`（走 **backend-net 容器名**，**不发布宿主端口**） |
| 规则 | `flink-jobs` 组 5 条（见上）；**除"抓取失败"外都只在确有作业/确有登记时才可能命中**，避免接源前长期误报 |
| 跨源联动 | `FlinkRegisteredJobNotRunning` 用 `ai_governance_lineage_registered_jobs`（D1 登记目录，由 `job_freshness.sh` 采集）与 Flink 的 `numRunningJobs` 比对 ⇒ **"登记了作业但没在跑"** |

> ⚠️ **实测踩坑（会静默失效，务必记住）**：跨源规则的 `and` **要求两侧标签集一致**——
> `ai_governance_lineage_registered_jobs`（node_exporter，`job="node"`）与 `flink_jobmanager_numRunningJobs`（`job="flink"`）**标签不同 ⇒ 永不匹配**，
> 规则永远 `inactive`（条件明明满足也不报）。**必须用 `and on()`**（本仓库 offsite 规则同样写法）。
> 本轮就是先写成 `A and B`，实测状态 `inactive` 才发现。

### 指标类采集的硬纪律（2026-09-19 实测踩坑，代价很大）

**任何一行的值非数字，node_exporter 会拒收整个 textfile 文件** —— 不是丢那一行，是**整张表一起消失**。
实证：`job_freshness.sh` 新增任务后，成功戳不存在时指标行写成空值 ⇒ 日志
`text format parsing error in line 8: expected float as value, got ""`（每 30s 一条），
Prometheus 里 `count(ai_governance_job_last_success_timestamp_seconds)` 直接返回**空** ⇒ **所有**备份/ETL 新鲜度告警静默失效。

因此采集器必须做到：

1. **取值兜底**：取不到写 `0`（含义="从未成功"），让**对应规则**把问题明确报出来，而不是把整张表打哑；
2. **落盘自检**：写入前逐行校验格式，发现非法行则**保留上一版文件**并报错退出（绝不覆盖成坏文件）；
3. **带 `# HELP`/`# TYPE`**，并保证 `promtool check metrics` 通过（本环境用它做落盘前/后的格式校验）。

## Alertmanager 接收器（2026-09-19 起）：告警汇入 GA 落库

**改动**：`route.receiver` 由 `default`（**空接收器**）改为 `ga-ingest`，把 AM 的告警 webhook 投给 GA 的 `POST /api/v1/alerts/ingest`，
落进 `ga.alert_event`（`source='alertmanager'`，幂等键 `alertmanager:<fingerprint>:<status>`）。

**为什么必须改**：空 receiver 意味着 21 条规则的告警**只在 AM UI 里留存**（UI 一关就没了），既不落库也不可审计。
本改动**不引入新组件、不动规则与阈值**，只补一个接收端。

**配置模板**：`alerting/alertmanager.yml.example`（令牌用占位符；真值在服务器 `/data/ai-governance/.env` 的 `GA_INGEST_TOKEN`）。
**鉴权**：`Authorization: Bearer $GA_INGEST_TOKEN`——**部署即带令牌**，不裸奔。
**生效方式**：`amtool check-config` 通过后 `docker kill --signal=HUP <am容器>`（本环境**未启用** lifecycle API，不能用 `/-/reload`）。
**边界**：这是"**留痕 / 可审计**"，**不等于"通知到人"**；IM/Webhook 地址到位后仍需再加一个 receiver。

### 告警日报（`alert_digest.sh`，2026-09-19 新增）

**为什么**：IM/Webhook 一直没给 ⇒ 告警**没有主动通知**（现只在 Alertmanager UI + GA 落库可见）。日报是"不依赖 IM 的最低成本可见性"：
每天 08:30 把 GA 落库的当日告警汇总成一份 Markdown，落到 `/data/ai-governance/reports/alert-digest-<date>.md`（**该目录已随配置备份出机**）。

**边界**：**不代替通知**——IM 到位后仍要配 receiver；同日重跑覆盖同一文件（幂等）。
**首次验收**：生成当日日报，恰好捕到 `FlinkRegisteredJobNotRunning` 的 **firing + resolved** 两条（critical / 来源 alertmanager）⇒ 与 `flink-jobs` 规则的触发验证互为佐证。

> **增强模板（同日补齐）**：Flink 那 5 条规则已在 GA 的 `ADVICE` 里加了对应模板（重建镜像 `1e64eb59…`，重扫仍 **0/0**）。
> **注意**：`enhanced_summary` 是**入库时写入**的，**不会回填历史行**——补齐前落库的告警摘要仍为空（属预期，不是缺陷）。

### 作业新鲜度采集的硬纪律（两条，均为实测踩坑）

1. **自定义指标标签不得用 `job`**：与 Prometheus 自身 `job` 冲突会被改名 `exported_job` ⇒ 规则永远匹配不到、**告警静默失效**。统一用 `task`，并保留 `count<预期` 护栏。
2. **`job_freshness.sh` 与 `backup_offsite.sh` 必须读同一个配置文件**：`/data/ai-governance/backup-offsite.conf`。曾因两处文件名不同（`backup-offsite.conf` vs `offsite.conf`）导致"介质配好了但 `ai_governance_job_configured` 仍是 0"，出机过期告警**永不触发**。出机新鲜度取**本地成功戳** `/data/backup/log/offsite_last_success`（仅 rsync 全成功才写），不扫远端介质（网络挂载点可能挂死），缺失记 `0`（"从未成功"）——**宁可误报，不静默**。

## 作业新鲜度采集（`job_freshness.sh`，169/2026-09-18）

**要解决的问题**：备份与 ETL 的失败是**静默的**——cron 会把错误写进日志，但没人看。本脚本把"最近一次成功时间"暴露成指标，由规则判定是否过期。

| 项 | 内容 |
|---|---|
| 采集方式 | **node_exporter textfile 收集器**（宿主二进制已加 `--collector.textfile.directory=/var/lib/node_exporter/textfile`）；脚本原子写入 `ai_governance_jobs.prom` |
| 调度 | cron **每 5 分钟**（`/etc/cron.d/ai-governance-backup`） |
| 成功判据（每类作业） | PG 备份=最新 `*.dump` mtime；OS 快照=快照目录最新条目；配置归档=最新 `config_*.tar.gz`；gov_metrics ETL=`gov_metrics.etl_run` 最近 `success`；RAG=`rag.doc.max(updated_at)` |
| 阈值口径 | 小时级作业 **1.5×周期=5400s**；配置备份 **30h**；采集器自身 **900s（3×周期）** |
| **实测教训（务必遵守）** | ⚠️ **不要用 `job` 作为自定义标签名**——它与 Prometheus 自身的 `job` 标签冲突，会被重命名为 `exported_job`，导致规则里 `{job="..."}` **永远匹配不到、告警静默失效**。本脚本统一用 **`task`**；并加 `JobFreshnessSeriesIncomplete`（`count(...) < 5`）作为这类"静默失效"的护栏 |
| 触发路径实测 | 临时把 `PgBackupStale` 降为 `>60s`/`for 30s` → **pending → firing** → Alertmanager 出现活动告警 → 还原后 **inactive**（全程可逆） |
| —— | 通知通道（IM/Webhook 地址，S3-6；当前 Alertmanager 仅空 `default` 接收器） |

## Ollama 运行面采集（`ollama_probe.sh`，2026-09-19 新增）

**要解决的问题**：S4-1 的纪律是「并发限流，**不得拖慢实时告警链路**」，但 Ollama **0.34.2 没有 Prometheus 端点**
（实测 `GET /metrics` = **404**），Prometheus 侧 **ollama 抓取目标 = 0** ⇒ 运行面此前**完全不可观测**：
推理把 CPU 吃满、被 cgroup OOM kill、模型长期不卸载，都不会有任何信号。

### 为什么不走 cAdvisor（**本环境实测的坑，务必记住**）

| 事实 | 证据（2026-09-19 实机） |
|---|---|
| cAdvisor 容器 `healthy`、抓取目标 `up`，但**只有根 cgroup** | `/api/v1/label/id/values` 仅 `["/","1","ubuntu"]`；`count(count by (id) (container_memory_working_set_bytes))` = **1** |
| 逐容器指标**一条都没有** | `container_memory_working_set_bytes{name=~".*ollama.*"}` 与 `{container_label_com_docker_compose_service="ollama"}` **均为空**；`docker stats` 却能看到该容器 ⇒ 不是容器问题 |
| 根因 | cAdvisor 日志 `Failed to create existing container … failed to identify the read-write layer ID … /rootfs/var/lib/docker/image/overlayfs/layerdb/mounts/<id>/mount-id: no such file or directory`；本机 Docker **29.8.1** 使用 **containerd 镜像存储**（`driver-type: io.containerd.snapshotter.v1`），`layerdb/mounts/` 下 **`mount-id` 计数 = 0**，而 cAdvisor 是 **v0.45.0（2022-09-23 构建）** |
| 后果 | **「抓取目标 up」≠「有数据」**：cAdvisor 在本环境对业务容器**零诊断价值**，且此前会让人误以为容器级监控已就位 |

> 口径：**这是 cAdvisor 侧的缺陷，不是 Ollama 的问题**。修复需换 cAdvisor 版本/调整挂载并实测验证（属组件版本变更：须人工批准 + 重扫 + 同步文档），
> **本次不擅自更换**；Ollama 运行面改用下面的自采方式先落地。

### 采集方式与指标

| 项 | 内容 |
|---|---|
| 数据源① | 宿主 **cgroup v2**：`/sys/fs/cgroup/system.slice/docker-<容器ID>.scope/{cpu.stat,cpu.max,memory.current,memory.max,memory.stat,memory.events}` |
| 数据源② | **Ollama REST** `GET /api/ps`（仅回环 `127.0.0.1:11434`） |
| 落盘 | `ai_governance_ollama_runtime.prom`（**独立文件**，与 `job_freshness.sh` 分开：一个采集器的坏行不会打哑另一个） |
| 调度 | cron `/etc/cron.d/ai-governance-ollama` **每分钟**（独立 cron 文件，不动 `ai-governance-backup`） |
| 容器侧指标 | `ai_governance_container_{cpu_usage_seconds_total,cpu_limit_cores,memory_current_bytes,memory_max_bytes,memory_anon_bytes,memory_file_bytes,oom_kill_total}`（标签 `container="ollama"`，**不用 `job`**） |
| API 侧指标 | `ai_governance_ollama_{up,loaded_models,model_loaded{model},model_size_bytes{model},model_vram_bytes{model},model_expires_in_seconds{model},probe_last_run_timestamp_seconds}` |

**7 条规则与阈值口径**（`ollama-runtime` 组；CPU 80% / 内存 85% **沿用 S1-7 的 SLO 阈值**，分母取**容器自身 cgroup 限额**而非宿主 ⇒ 不新增臆造数值）：

| 规则 | 判据 | 级别 |
|---|---|---|
| `OllamaApiDown` | `ai_governance_ollama_up == 0`（5m） | critical |
| `OllamaContainerCpuSaturated` | `rate(cpu_usage_total[10m]) / cpu_limit_cores > 0.8`（10m） | warning |
| `OllamaContainerMemoryHigh` | `memory_current / memory_max > 0.85`（10m；**带 `>0` 除零护栏**） | warning |
| `OllamaContainerOomKilled` | `increase(oom_kill_total[30m]) > 0` | critical |
| `OllamaModelNotUnloading` | `max(model_expires_in_seconds) > 3600` | warning |
| `OllamaProbeStale` | `time() - probe_last_run > 300`（=5×周期） | warning |
| `OllamaRuntimeMetricsMissing` | `absent(probe_last_run_timestamp_seconds)` | warning |

**实测取证（2026-09-19）**：

1. **采集可用**：手工与 cron 均 exit 0；`promtool check metrics` 通过；指标文件每分钟刷新、cron 日志**零报错**；
   cgroup 限额读数 **8.0000 核 / 17,179,869,184 B（16 GiB）**，与 compose 一致。
2. **`/api/ps` 解析路径**：真实推理（`qwen2.5:1.5b`、`keep_alive=2m`）→ `model_loaded{model="qwen2.5:1.5b"}=1`、
   `model_size_bytes=1169980128`、`model_vram_bytes=0`（纯 CPU）、`model_expires_in_seconds=120`（与 keep_alive 一致）；
   显式卸载（`keep_alive: 0`）后**模型序列消失、`loaded_models=0`** ⇒ 空闲卸载链路可观测。
3. **内存构成可判读**：卸载后 `memory.current = 3.30 GB`，其中 `memory_file = 3.19 GB`（**页缓存，可回收**）、`memory_anon = 16 MB`
   ⇒ 只看 `memory.current` 会误判「占满」，故另导 anon/file 两个序列供排障区分。
4. **fire/resolve 全周期**（临时把阈值降为 `>0`、`for: 30s`，**全程可逆**）：
   Prometheus `firing` → Alertmanager `active`（`startsAt 11:40:45Z`）→ GA 落库 `firing`（11:41:15Z）→ GA 落库 `resolved`（11:46:15Z）；
   还原后 **7 条规则全部 `inactive` 且 `health=ok`**、Prometheus/AM **无活动告警**、规则文件哈希与基线**一致**
   （`b38c25279e5cc87b1e593ab220196bebe0fa7b569a314da1cf31b659d2828c46`）。
   > 说明：`resolved` 比 `firing` 晚约 5 分钟，是 Alertmanager 的 `group_interval` 节流，不是丢数据。
5. **增强模板**：GA 的 `ADVICE` 已补 7 条 Ollama 处置建议；**重建后**落库的行 `enhanced_summary` 非空
   （重建前落库的历史行为空，属预期，不回填）。另用**合成 AM 投递**（标签 `container="selftest-20260919"`，可区分）
   验证：首次 `stored=1`、同键重投 `duplicates=1`、无令牌 **401**。

> ⚠️ 幂等键是 `alertmanager:<fingerprint>:<status>` ⇒ **同一条告警第二次 firing 不会新增行**（判重属预期）。
> 因此"模板是否生效"必须看**重建之后**产生的行。

## 通用容器运行面采集（`container_probe.sh`，2026-09-19 新增）

### 为什么需要：cAdvisor 在本环境**修不了**（已实测到尽头）

**问题**：cAdvisor 容器 `healthy`、抓取目标 `up`，但**逐容器指标一条都没有**（只导出根 cgroup）。

**根因（已定位）**：Docker **29.8.1** 使用 **containerd 镜像存储**（`driver-type: io.containerd.snapshotter.v1`），
`/var/lib/docker/image/overlayfs/layerdb/mounts/<id>/mount-id` **不存在**（`mount-id` 计数 = 0），
而现役 cAdvisor 是 **v0.45.0（2022-09-23 构建）**，日志持续报 `failed to identify the read-write layer ID`；
`docker inspect` 侧 `GraphDriver` 直接是 **null**（containerd 快照不通过 graphdriver 暴露）。

**三条修复路径都试过，均不通**（2026-09-19 实测）：

| 路径 | 实测结果 |
|---|---|
| ① 升级 cAdvisor 镜像 | **拉不到**：`docker.m.daocloud.io`（含显式路径）被仓库策略拦（`🚫 …DaoCloud/p…`）、`gcr.io` 不可达、`quay.io`/`dockerpull.org`/`hub.rat.dev`/`docker.1ms.run` 无法解析、`docker.xuanyuan.me` 需付费、`registry.cn-hangzhou.aliyuncs.com/google_containers/cadvisor` denied。**可用的 `docker.1panel.live`** 上：`zcube/cadvisor` 最高 **v0.45.0**（与现役同版本）、`mirrorgooglecontainers/cadvisor` / `anjia0532/google-containers.cadvisor` 最高 **v0.33.0**（更旧）⇒ **无更高版本可拉**（基线：`alpine:3.20` 与 `zcube/cadvisor:latest` 可拉，说明不是全局断网） |
| ② 改 `--docker_only=false`（退化为纯 cgroup 口径） | 序列数 **1 → 55**，但**`name` 标签全为空**，`id` 是 cgroup 路径（`/`、`/aegis` …）⇒ 无法按容器名告警，运维价值不足 |
| ③ 启用 containerd 集成（挂 `containerd.sock` + `--containerd-namespace=moby`） | v0.45 下**无效**：仍只有根 cgroup（与现役配置复现结果一致） |

> **结论**：cAdvisor 在本环境属**外部阻塞**（缺可用镜像源 + 版本兼容），**保留部署但不可用于容器级监控**；
> 容器级可视性由**自采**（与 `job_freshness.sh` 同一套机制）替代，**不引入新组件**。
> 一旦镜像源放开或能拿到 ≥ v0.5x 的 cAdvisor，可再评估替换（复核触发点见《…组件部署状态.md》§5）。

### 采集方式、口径与规则

| 项 | 内容 |
|---|---|
| 数据源 | 宿主 **cgroup v2**：`/sys/fs/cgroup/system.slice/docker-<容器ID>.scope/{cpu.stat,cpu.max,memory.current,memory.max,memory.stat,memory.events}`，**遍历 `docker ps` 全部运行中容器** |
| 落盘 | `ai_governance_containers.prom`（**独立文件**，坏行不牵连 job_freshness / ollama 两个文件） |
| 调度 | cron `/etc/cron.d/ai-governance-containers` **每分钟** |
| 指标 | `ai_governance_container_{cpu_usage_seconds_total,cpu_limit_cores,memory_current_bytes,memory_max_bytes,memory_anon_bytes,memory_file_bytes,oom_kill_total}` + `ai_governance_container_probe_{containers,last_run_timestamp_seconds}` |
| 标签 | `container`（compose 服务名，无则容器名）+ `name`（容器名）；**不用 `job`** |
| 阈值 | 内存 85% / CPU 90% 均取**容器自身 cgroup 限额**为分母（内存 85% 沿用 S1-7 口径；CPU 因含批处理场景放宽到 90% 且要求持续 30m），并各带 `>0` 除零护栏 |

**6 条规则**：`ContainerMetricsMissing`（`absent`）、`ContainerProbeStale`（>5×周期）、`ContainerSeriesIncomplete`（运行容器数 > 实际序列数 ⇒ "看着在跑其实没数据"的护栏）、`ContainerOomKilled`（critical）、`ContainerMemoryNearLimit`（>85%，15m）、`ContainerCpuSaturated`（>90%，30m）。

### 实测取证

| # | 验证 | 结果 |
|---|---|---|
| 1 | 采集覆盖 | 19/19 容器均有序列且带真实容器名；`promtool check metrics` 通过；cron 每分钟刷新、日志零报错 |
| 2 | 与 Ollama 现有规则兼容 | 容器指标迁到通用探针后，`ai_governance_container_*{container="ollama"}` 仍可查 ⇒ `ollama-runtime` 的 4 条容器类规则**无需改动**（同一 series 仍只有**一个生产者**——Ollama 探针已同步去掉这部分） |
| 3 | **OOM 真实触发** | 自建一次性容器（`--memory=64m`，子进程申请 200MB）⇒ 子进程 `exit=137`、cgroup `oom_kill=1` ⇒ `ContainerOomKilled` **pending → firing**（GA 落库 `13:35:32Z`，含处置建议）⇒ 删除容器后 **resolved**（`13:40:32Z`）。**零残留**（容器数与序列数回到 19） |
| 4 | **踩坑（判据选错会永远不报）** | 最初写成 `increase(ai_governance_container_oom_kill_total[30m]) > 0`：容器若在**首次采集之前**就 OOM，序列第一次出现值即为 1、此后不再变化 ⇒ **`increase()` 恒为 0、规则永不触发**（实战中就是这次测试发现的）。已改为**累计计数**判据 `oom_kill_total > 0`——计数随容器重启清零，语义是"本实例发生过 OOM"，可自愈 |

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-19 | 本文件此前**无版本记录表**，自本版起补记（历史内容见各节日期标注）。**新增 `ollama-runtime` 组 7 条规则**（Ollama API/容器 CPU/内存/OOM/模型不卸载/采集器护栏）与 `ollama_probe.sh` 采集（cgroup v2 + `/api/ps` → 独立 textfile 文件、每分钟 cron）；**记录 cAdvisor 在本环境取不到逐容器指标的实测根因**（containerd 镜像存储 vs cAdvisor v0.45.0）；规则总数 **27 → 34**；补 fire/resolve、模型加载/卸载、幂等与令牌的实测取证 |
| v1.2 | 2026-09-19 | **新增通用容器运行面采集 `container_probe.sh` 与 `container-runtime` 组 6 条规则**（规则总数 **34 → 40**），用于替代在本环境失效的 cAdvisor：① 定位并复现 cAdvisor 失效根因（Docker 29.8.1 = containerd 镜像存储、layerdb 的 mount-id 不存在、GraphDriver 为 null vs cAdvisor v0.45.0）；② **三条修复路径实测均不通**（镜像源被拦/不可达、可用镜像站上 cadvisor 最高仅 v0.45.0、`--docker_only=false` 无容器名、v0.45 的 containerd 集成无效）⇒ 记为**外部阻塞**；③ 容器级指标改自采（cgroup v2、遍历 `docker ps`、带 `container`/`name` 标签、独立 textfile 文件与独立 cron），**Ollama 探针同步移除容器指标**（同一 series 单生产者）；④ 验收含 **OOM 真实触发**（64MB 容器 → `oom_kill=1` → firing 落库 → 清理 → resolved 零残留）；⑤ **踩坑**：窗口式 `increase()` 会漏报"首次采集前就 OOM"，改用累计计数判据 |

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；阈值与告警级别执行前请按源文档与官方文档核对。
