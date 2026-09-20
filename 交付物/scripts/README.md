# AI 数据治理平台 · 部署脚本交付包

> **版本**：v1.0（2026-09-21）
> **内容**：本项目**部署与运维脚本的完整拷贝**（34 个文件）+ cron 定义 + 镜像构建文件，按用途分组。
> **口径**：脚本以仓库为**唯一真相源**；本目录是**交付拷贝**，与仓库同名文件内容一致（服务器落点见下表）。
> **本包不含任何口令**：全部脚本的口令都从 `/data/ai-governance/.env` 或环境变量读取（已逐文件核对：`OS_PW="$(grep …)"`、`TOKEN=$(grep … "$ENVF")`）。

---

## 0. 目录结构

```
scripts/
├── cron/                     # 6 个 cron 定义（/etc/cron.d/ai-governance-*）
├── backup-restore/           # 备份 4 + 恢复演练 3（门禁② 相关）
├── monitor/                  # 告警日报 + 三个采集器（新鲜度 / Ollama / 容器）
├── metrics-etl/              # 治理指标 ETL + 覆盖率分母工具 + 建表 SQL
├── lineage/                  # D1 血缘：提交包装 / 对账 / 自动上报
├── watermark-capacity/       # 水位周报（含 cron 安装）+ 容量基线（含 OS 压测）
└── images/                   # 4 个自建镜像的 Dockerfile + 依赖/jars 校验
```

**服务器落点统一为**：`/data/ai-governance/scripts/`（cron 文件为 `/etc/cron.d/`）；镜像构建目录为 `/data/ai-governance/<name>/`。

---

## 1. 备份与恢复（`backup-restore/`）——**门禁② 的载体**

| 脚本 | 用途 | 调度（cron） | 落点/产物 | 依据 |
|---|---|---|---|---|
| `backup_pg.sh` | PostgreSQL 逐库逻辑备份 | **每小时 :05** | `/data/backup/pg/*.dump`（14 天滚动） | S1-8 |
| `backup_opensearch.sh` | OpenSearch 快照 | **每小时 :15** | `/data/opensearch/snapshots` | S1-8 |
| `backup_config.sh` | 配置归档（`.env`＋compose＋`config/`，**含密钥**） | **每日 02:55** | `/data/backup/config/config_*.tar.gz`（600） | S1-8 |
| `backup_offsite.sh` | 出机（异地 rsync，**本环境暂缓**） | 每日 03:00 | 异地介质；成功戳 `/data/backup/log/offsite_last_success` | S1-8 |
| `restore_drill.sh` | 单品恢复演练（PG/OS 各自） | 手动（演练用） | 演练输出 | S1-8 §5 |
| `restore_drill_pg_all.sh` | **逐库恢复演练**（6 库） | 手动 | 一致性 + 耗时 | S1-8 §5.1 |
| `restore_annual_drill.sh` | **年度化恢复演练**（本地段） | 手动（年度） | `reports/restore-annual-drill-*.md` | S4-4 |

> ⚠️ 恢复纪律：**配置归档含真实密钥** ⇒ 解包必须在 `/tmp`、用毕即删、删除前**断言路径前缀**；`DROP DATABASE` 前**必须回收 PgBouncer 池连接**；**PG PITR 在本环境不存在**（未启用 WAL 归档），**不得把 dump 恢复说成 PITR**。

## 2. 监控与采集（`monitor/`）

| 脚本 | 用途 | 调度 | 产物 | 依据 |
|---|---|---|---|---|
| `job_freshness.sh` | 备份/ETL/RAG/对账"最近成功时间"→ textfile 指标 | 每 5 分钟 | `ai_governance_jobs.prom` | S1-8 / S3-6 |
| `ollama_probe.sh` | Ollama API 面（`/api/ps`：模型驻留、卸载期限、可达性） | **每分钟** | `ai_governance_ollama_runtime.prom` | S4-1 |
| `container_probe.sh` | **通用**容器运行面（cgroup v2：CPU/内存/anon/file/OOM） | **每分钟** | `ai_governance_containers.prom` | 本轮（cAdvisor 替代） |
| `alert_digest.sh` | 当日告警日报（GA 落库 → Markdown） | 每日 08:30 | `reports/alert-digest-<date>.md` | S3-6 |

> 采集纪律（血泪）：**文本类指标一行非数字 ⇒ node_exporter 拒收整文件**；自定义标签**不得用 `job`**；三个采集器**各写各的文件**，避免互相打哑。

## 3. 治理指标 ETL（`metrics-etl/`）

| 文件 | 用途 | 调度 | 说明 |
|---|---|---|---|
| `gov_metrics_etl.py` | 从 **OM REST API** 取资产/质量指标 → 写 `gov_metrics`（经 **PgBouncer 6432**） | 每小时 :25 | ADR-A6：**只走 API，不读 OM 内表** |
| `coverage_denominator.py` | 资产覆盖率**分母盘点**（源侧计数 ↔ OM 比对，`--mode apply` 写参考表） | 手动（盘点时） | **分母只由它写、ETL 只读**；`--exclude` **只按 schema 过滤** |
| `schema.sql` | `gov_metrics` 建 schema/4 表/索引/角色 | 一次性 | Superset 只读该 schema |

## 4. D1 血缘（`lineage/`）

| 脚本 | 用途 | 调度 | 说明 |
|---|---|---|---|
| `submit_flink_job.sh` | **作业提交包装**：干跑门禁 → 登记 → 提交 → 血缘上报 | 提交作业时手动 | **干跑不过不提交**；登记目录 `/data/ai-governance/jobs/*.sql` 是唯一真相源 |
| `lineage_autoreport.sh` | 按登记目录批量上报 + 对账（写成功戳） | 每日 04:10 | 全部成功才写成功戳 |
| `lineage_reconcile.sh` | 血缘对账（`repair` 自愈：**只新增/修复，绝不删 OM 的边**） | 由 autoreport 调用 | 缺失置 `drift`；`LineageReconcileStale` 兜底告警 |

## 5. 水位周报与容量（`watermark-capacity/`）

| 脚本 | 用途 | 调度 | 说明 |
|---|---|---|---|
| `watermark_report.sh` | 资源水位周报（CPU/内存/磁盘 + 平台面，对照 M-Prod 触发条件） | 周一 08:00 | **判定必须两个相邻 7 天窗口**；跨度 <14 天一律"数据不足"；**磁盘不做线性外推** |
| `install_watermark_cron.sh` | 安装上条 cron（幂等） | 一次性 | 写入 `/etc/cron.d/ai-governance-backup` |
| `capacity_baseline.sh` | 容量基线（PG/Kafka/OS/Ollama 自压） | 手动 | **自压数据，不得用于准出判读** |
| `os_bulk_bench.py` | OpenSearch bulk 压测（索引必须在 URL 里，必须校验 `errors`） | 由上面调用 | 被拒的 bulk 依然"很快"，只看耗时会虚高 |

## 6. 镜像构建（`images/`）

| 文件 | 对应镜像 | 纪律 |
|---|---|---|
| `governance-agent__Dockerfile` / `__requirements.txt` | `ai-governance/governance-agent:0.1.0` | 基础镜像**按 digest 固定**；依赖**钉版本**；改依赖须重建 → 重跑验收 → **重扫** |
| `governance-portal__Dockerfile` / `__requirements.txt` | `ai-governance/governance-portal:0.1.0` | 同上；**改 UI 还要跑 `governance-portal/tools/` 四个校验** |
| `superset-image__Dockerfile` | `ai-governance/superset:6.1.0-pg` | 官方 lean 无 PG 驱动；**手工 `docker build`**（compose 无 build 段） |
| `flink-image__Dockerfile` / `__jars.sha256` | `ai-governance/flink:1.20.1-cdc3.6.0` | 连接器 JAR 走 Maven Central 并**按 sha256 校验** |

## 7. cron 定义（`cron/`）

| 文件 | 覆盖内容 |
|---|---|
| `ai-governance-backup` | PG/OS/配置/出机备份 + 新鲜度采集 + 水位周报 |
| `ai-governance-gov-metrics` | 指标 ETL(:25) + RAG 向量刷新(:40) |
| `ai-governance-lineage` | 血缘自动上报 + 对账(04:10) |
| `ai-governance-alert-digest` | 告警日报(08:30) |
| `ai-governance-containers` | 容器探针（每分钟） |
| `ai-governance-ollama` | Ollama 探针（每分钟） |

> 安装方式：直接落到 `/etc/cron.d/`（`chmod 644`、`chown root:root`）；**cron 文件即改即生效**，无需 reload。

---

## 8. 使用与验证

```bash
# 1) 落位（脚本统一放这里；权限可执行）
install -m 755 backup_pg.sh /data/ai-governance/scripts/

# 2) 先手动跑一次，看输出与退出码
/data/ai-governance/scripts/backup_pg.sh; echo "rc=$?"

# 3) 再装 cron（注意错峰：备份 :05/:15、ETL :25、RAG :40）
install -m 644 ai-governance-backup /etc/cron.d/ai-governance-backup

# 4) 观察一轮（日志 + 指标文件刷新 + Prometheus 侧序列）
tail -f /data/backup/log/cron.log
ls -la --time-style=+%H:%M:%S /var/lib/node_exporter/textfile/
```

**验收要点**：脚本改动必须**同步仓库**（本包就是同期拷贝）；`job_freshness.sh` 等采集脚本改动后要确认 textfile 文件**仍在刷新且无拒收**（任一非法行会打哑整表）。

---

## 9. 本次归档说明（重要）

| 项 | 说明 |
|---|---|
| **仓库漂移已修** | `backup_pg/opensearch/config/offsite.sh`、`restore_drill*.sh`、`restore_annual_drill.sh`、`install_watermark_cron.sh`、`capacity_baseline.sh` **此前只存在于服务器**（仓库无副本）⇒ 本次一并归档：交付包全量收录，并**回填仓库**（新增 `backup-restore/`，补齐 `capacity-baseline/`、`watermark-report/`） |
| **已核对无硬编码口令** | 逐脚本检查：口令均来自 `.env`（`OS_PW="$(grep …)"`、`TOKEN=$(grep … "$ENVF")`） |
| **未纳入** | `*.bak-*`（历史备份）、`__pycache__/`（构建产物）、三个镜像里的**业务代码**（`governance-agent/*.py`、`governance-portal/*.py` 属源码交付，见各自目录） |

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-21 | 首次产出部署脚本交付包：按用途分 7 组（cron 6 / 备份恢复 7 / 监控采集 4 / 指标 ETL 3 / 血缘 3 / 水位容量 4 / 镜像构建 7），逐脚本给出**用途 + 调度 + 服务器落点 + 依据文档**，并写明关键纪律（含密钥的配置归档、textfile 拒收、干跑门禁、周报判定口径、自压数据不得用于准出）。**同时修掉仓库漂移**：9 个只存在于服务器的脚本已取回、收录进本包并回填仓库（新增 `backup-restore/`）。**已核对包内无硬编码口令** |

---

*本包为部署脚本的交付拷贝，脚本以仓库为唯一真相源；不含新增架构决策。*
*门禁判读、准出签字属人工专属，Agent 不代判。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；脚本执行前请按实机环境核对路径与变量。
