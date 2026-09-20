# AI 数据治理平台 · 单机版 S1-8 备份与恢复脚本（含运行手册）

> **定位**：`执行步骤/` 下的**部署脚本产物**，由《部署步骤_单机版》S1-8 展开，**不含新增架构决策**。
> **依据**：《设计方案 v3.2》4.6（RPO ≤1h / RTO ≤4h）+ ADR-A4（单机形态 = 本地数据盘 + 每日出机备份）+《实施执行计划 v2.0》P0-B 第 3 周（v2.0-r2 已回填 RPO 口径）。
> **口径声明**：**RPO ≤1h**（2026-09-17 人工决策，消解源文档冲突）。单机 POC 由**小时级逻辑备份/快照**承接；**不引入 WAL 归档 PITR**（分钟级手段，属口径变更须走 ADR）。
> **落地状态**：2026-09-17 已在 POC 服务器 `<POC服务器主机名>` 上线并通过首次恢复演练；证据见《AI数据治理平台_单机版_S1部署记录.md》§7。
> **判读边界**：Agent 产出脚本与演练证据；**恢复演练结果的验证与签字为人工专属（DBA）**。

---

## 1. 备份设计

| 对象 | 手段 | 落地位置 | 频率 | 保留 |
|---|---|---|---|---|
| PostgreSQL | `pg_dump -Fc`（逐库）+ `pg_dumpall --globals-only` | `/data/backup/pg/` | 每小时 `:05` | 小时级 26h；每日档 14 天 |
| OpenSearch | snapshot（`fs` 仓库 `poc_fs`） | `/data/opensearch/snapshots/` | 每小时 `:15` | 小时级 26 份 |
| Flink Checkpoint | 目录出机 | `待 S3 部署后启用` | — | — |
| 出机（全部对象） | `rsync` 到出机介质 | `OFFSITE_TARGET`（**待确认**） | 每日 `03:00` | 随目标侧策略 |

**RPO 推导**：每小时备份一次 ⇒ 最坏丢数据 = 1 小时（故障发生在两次备份之间），满足 RPO ≤1h。
**RTO 推导**：恢复 = 建库 + `pg_restore` + 校验；2026-09-17 实测 `pg_restore` 耗时 0.07s（S1 空库），远低于 4h 目标；**S2 起有真实数据后须重新测时并由 DBA 判读**。

**磁盘预算（本机 99G 系统盘同盘 /data）**：当前备份占用 60KB、快照 1.7MB；按治理元数据量级属可忽略，保留期已设上限，磁盘水位仍按《部署步骤_单机版》0.3 声明（达 80% 记录、停止、转人工）。

**已知缺口（不隐瞒）**：

1. **出机链路未启用**——出机介质地址是挂账待确认项；脚本在未配置时返回退出码 2 并记录，**不伪造成功**。ADR-A4 要求的"每日出机"在介质确认前**未闭环**。
2. **无主动告警**——备份失败目前只落日志与退出码；「备份成功率 100%（失败即时告警）」是设计方案 4.5 的指标口径，落地在 S3-6 告警打通，本阶段为**已知缺口**。
3. **OpenSearch 快照与索引数据同盘**——单机形态下两者都在 `/data`（同一故障域），靠"每日出机"兜底；这是 ADR-A4 单机形态的既定取舍。

---

## 2. 脚本（4 个，部署于 `/data/ai-governance/scripts/`，root 可执行）

### 2.1 `backup_pg.sh`

```bash
#!/usr/bin/env bash
# S1-8 · PostgreSQL 逻辑备份（RPO ≤1h 口径：整点后 5 分执行，最坏丢 1 小时数据）
# 依据：《部署步骤_单机版》S1-8、《实施执行计划 v2.0》P0-B 第 3 周（PG 逻辑备份）、ADR-A4（本地盘 + 每日出机）
# 说明：单机形态不启用 WAL 归档（分钟级手段）；如需收窄 RPO 属口径变更，须走 ADR。
set -euo pipefail

BASE=/data/backup
PG_C=ai-governance-poc-postgres-1
RET_HOURS=26                      # 小时级保留窗口（略大于 24h，容忍一次执行失败）
RET_DAYS=14                       # 日级保留份数
LOG="$BASE/log/backup_pg.log"

mkdir -p "$BASE/pg" "$BASE/pg/daily" "$BASE/log"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

stamp=$(date +%Y%m%d-%H%M)
hour=$(date +%H)
fail=0

log "=== 备份开始 ==="

# 1) 全局对象（角色/权限），体积小，随每次备份留存
if docker exec "$PG_C" pg_dumpall -U postgres --globals-only > "$BASE/pg/globals_${stamp}.sql" 2>>"$LOG"; then
  log "globals OK -> globals_${stamp}.sql"
else
  log "globals 失败"; fail=1
fi

# 2) 逐个非模板库逻辑备份 + 可读性校验（TOC 条目数 > 0）
for db in $(docker exec "$PG_C" psql -U postgres -tAc \
    "select datname from pg_database where datistemplate=false order by 1"); do
  out="$BASE/pg/${db}_${stamp}.dump"
  tmp="${out}.tmp"
  if docker exec "$PG_C" pg_dump -U postgres -Fc -Z6 "$db" > "$tmp" 2>>"$LOG"; then
    # 可读性校验：归档头能被 pg_restore 解析，且 TOC Entries > 0
    # 注意：空库的归档只有注释级条目，"Selected TOC Entries" 下无编号行，故不能用行首数字计数
    if toc_out=$(docker exec -i "$PG_C" pg_restore -l < "$tmp" 2>>"$LOG"); then
      toc=$(printf '%s\n' "$toc_out" | sed -n 's/^; *TOC Entries: *\([0-9][0-9]*\).*/\1/p' | head -1)
      if [ "${toc:-0}" -gt 0 ]; then
        mv "$tmp" "$out"
        log "pg_dump OK  db=$db  toc_entries=$toc  size=$(stat -c%s "$out")B  file=$(basename "$out")"
      else
        rm -f "$tmp"; log "校验失败（TOC Entries 解析为 0）：db=$db"; fail=1
      fi
    else
      rm -f "$tmp"; log "校验失败（pg_restore 无法读取归档）：db=$db"; fail=1
    fi
  else
    rm -f "$tmp"; log "pg_dump 失败：db=$db"; fail=1
  fi
done

# 3) 每日档：03 点那次额外留一份，保留 RET_DAYS 天
if [ "$hour" = "03" ]; then
  for f in "$BASE"/pg/*_"${stamp}".dump; do [ -e "$f" ] && cp -a "$f" "$BASE/pg/daily/"; done
  find "$BASE/pg/daily" -type f -name '*.dump' -mtime +"$RET_DAYS" -print -delete >>"$LOG" 2>&1 || true
  log "日级档已留存（保留 ${RET_DAYS} 天）"
fi

# 4) 保留期清理（小时级）
find "$BASE/pg" -maxdepth 1 -type f -name '*.dump' -mmin +$((RET_HOURS * 60)) -print -delete >>"$LOG" 2>&1 || true
find "$BASE/pg" -maxdepth 1 -type f -name 'globals_*.sql' -mmin +$((RET_HOURS * 60)) -print -delete >>"$LOG" 2>&1 || true

# 5) 退出码 + 状态文件（供后续 Prometheus/告警接入；S3-6 打通前无主动告警）
if [ "$fail" -eq 0 ]; then
  date '+%F %T' > "$BASE/log/pg_last_success"
  log "=== 备份成功 ==="
else
  log "=== 备份存在失败项，退出码 1 ==="
fi
exit "$fail"
```

### 2.2 `backup_opensearch.sh`

```bash
#!/usr/bin/env bash
# S1-8 · OpenSearch 快照备份（fs 仓库，落在 /data/opensearch/snapshots，即本地数据盘）
# 依据：《部署步骤_单机版》S1-8、ADR-A4（单机形态 = 本地数据盘 + 每日出机）
# 前置：docker-compose.yml 的 opensearch.environment 已加 path.repo（2026-09-17 变更）
set -euo pipefail

BASE=/data/backup
OS_C=ai-governance-poc-opensearch-1
OS_URL=https://localhost:9200
REPO=poc_fs
RET_HOURS=26                      # 保留小时级快照份数窗口
LOG="$BASE/log/backup_opensearch.log"

mkdir -p "$BASE/log"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

OS_PW="$(grep -E '^OPENSEARCH_INITIAL_ADMIN_PASSWORD=' /data/ai-governance/.env | cut -d= -f2-)"
os() { docker exec -e OS_PW="$OS_PW" "$OS_C" sh -c "curl -sk -u \"admin:\$OS_PW\" $*"; }

log "=== 快照开始 ==="

# 1) 注册仓库（幂等）：fs 仓库根目录 = 容器内 /usr/share/opensearch/data/snapshots
os "-X PUT $OS_URL/_snapshot/$REPO -H 'Content-Type: application/json' \
     -d '{\"type\":\"fs\",\"settings\":{\"location\":\"/usr/share/opensearch/data/snapshots\",\"compress\":\"true\"}}'" >>"$LOG" 2>&1 || true

snap="poc-snap-$(date +%Y%m%d-%H%M)"

# 2) 打快照（等待完成；不勾选 global state，避免把安全配置一并卷入）
os "-X PUT \"$OS_URL/_snapshot/$REPO/$snap?wait_for_completion=true\" -H 'Content-Type: application/json' -d '{\"indices\":\"*\",\"ignore_unavailable\":true,\"include_global_state\":false}'" > /tmp/_os_snap.json 2>>"$LOG" || true

state=$(grep -o '"state":"[A-Z]*"' /tmp/_os_snap.json | head -1 | cut -d'"' -f4 || true)
if [ "${state:-}" = "SUCCESS" ]; then
  shards=$(grep -o '"successful":[0-9]*' /tmp/_os_snap.json | head -1 | cut -d: -f2 || true)
  log "snapshot OK  name=$snap  state=$state  shards=${shards:-?}"
else
  log "snapshot 失败或未完成：name=$snap  state=${state:-未知}  详见 /tmp/_os_snap.json 与上方输出"
  sed -n '1,5p' /tmp/_os_snap.json >>"$LOG" 2>&1 || true
  exit 1
fi

# 3) 保留期清理：删除超过 RET_HOURS 的小时级快照（只删本脚本命名的 poc-snap-*）
os "-s \"$OS_URL/_snapshot/$REPO/_all\"" > /tmp/_os_list.json 2>>"$LOG" || true
for name in $(grep -o '"poc-snap-[0-9-]*"' /tmp/_os_list.json | tr -d '"' | sort -u | head -n -"$RET_HOURS"); do
  os "-X DELETE \"$OS_URL/_snapshot/$REPO/$name\"" >>"$LOG" 2>&1 || true
  log "已清理过期快照 $name"
done

date '+%F %T' > "$BASE/log/opensearch_last_success"
log "=== 快照成功 ==="
```

### 2.3 `backup_offsite.sh`

```bash
#!/usr/bin/env bash
# S1-8 · 每日出机备份（ADR-A4：单机形态 = 本地数据盘 + 每日出机备份）
# 出机介质地址为挂账待确认项（《部署步骤_单机版》附 C / S1 记录 §5 第 2 项）：
#   - 未配置时：仅告警式记录，不伪造成功；退出码 2 以便人工识别
#   - 配置后：rsync 同步 /data/backup 到目标（对象存储需先挂载或走 rclone，按介质定）
set -uo pipefail

BASE=/data/backup
CONF=/data/ai-governance/backup-offsite.conf
LOG="$BASE/log/backup_offsite.log"
mkdir -p "$BASE/log"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

TARGET=""
[ -f "$CONF" ] && TARGET="$(grep -E '^OFFSITE_TARGET=' "$CONF" | cut -d= -f2- || true)"

if [ -z "${TARGET:-}" ]; then
  log "出机未配置（$CONF 缺 OFFSITE_TARGET）——本地备份已完成，出机链路待介质确认后启用（退出码 2）"
  exit 2
fi

log "=== 出机开始 target=$TARGET ==="
if rsync -a --delete --stats "$BASE/pg/" "$TARGET/pg/" >>"$LOG" 2>&1 \
   && rsync -a --delete "$BASE/log/" "$TARGET/log/" >>"$LOG" 2>&1 \
   && rsync -a --delete "$BASE/config/" "$TARGET/config/" >>"$LOG" 2>&1; then
  date '+%F %T' > "$BASE/log/offsite_last_success"
  log "=== 出机成功 ==="
else
  log "=== 出机失败（退出码 1） ==="
  exit 1
fi
```

> **配置文件路径（唯一权威）**：`/data/ai-governance/backup-offsite.conf`，内含一行 `OFFSITE_TARGET=<介质路径>`。
> **踩坑留痕（2026-09-18）**：`job_freshness.sh` 早期默认读的是 `/data/ai-governance/offsite.conf`（**文件名不一致**），
> 后果是人工按本文档配好介质后，`ai_governance_job_configured{task="backup_offsite"}` 仍为 0 ⇒ `OffsiteBackupStale` **永不触发**（静默失效）。
> 已将两处统一为 `backup-offsite.conf`，并把新鲜度取值改为读**本地成功戳** `$BASE/log/offsite_last_success`（仅 rsync 全成功才写），
> 不再每次采集去扫描远端介质（网络挂载点可能挂死/被限流）。
> `offsite_last_success` 缺失时指标值取 **0**（"从未成功"）——**宁可误报，不静默**。

### 2.4 `backup_config.sh`（2026-09-18 新增：配置出机；同日追加周报目录）

> **为什么必须新增**：FERNET_KEY 等密钥已改为**自持**并落在 `/data/ai-governance/.env`（见《…S3前置部署记录.md》§7）。
> 原来的备份链只覆盖**数据库逻辑 dump + OpenSearch 快照**，**不含 `.env` / `docker-compose.yml` / `config/`**；
> 一旦在别处恢复，"有数据没有钥匙"——OpenMetadata 的加密 secrets、Superset `SECRET_KEY`、Airflow `FERNET_KEY`、各库口令全部不可用。
> 本脚本把这三样打成一个**含密钥的 tar.gz（0600）**并纳入出机 rsync。
> **2026-09-18 追加 `reports/`**：S4-4 水位周报是 **M-Prod 触发条件 2 的书面证据**，而 Prometheus 本地只保留 15d —— 报告不落备份就等于"判定依据只活两周"。故把 `/data/ai-governance/reports/` 一并纳入同一归档（不新增频率、不改保留策略，**RPO/RTO 口径不变**）。

```bash
#!/usr/bin/env bash
# 配置出机（2026-09-18 新增）：备份 .env / docker-compose.yml / config / reports
# 为什么必须：FERNET_KEY 等密钥自持后落在 /data/ai-governance/.env；
# 只恢复数据库而不恢复 .env ⇒ OpenMetadata 加密 secrets 无法解密（Superset SECRET_KEY、Airflow FERNET_KEY、各库口令同理）。
# reports/：S4-4 水位周报（M-Prod 触发条件 2 的书面证据）；目录可能不存在，故按存在性拼接清单。
set -euo pipefail
SRC=/data/ai-governance
BASE=/data/backup
DST="$BASE/config"
KEEP_DAYS=14
stamp="$(date +%Y%m%d-%H%M%S)"
log() { echo "[$(date -u +%FT%TZ)] $*"; }

mkdir -p "$DST"; chmod 700 "$DST"
umask 077
tmp="$DST/config_${stamp}.tar.gz.tmp"
out="$DST/config_${stamp}.tar.gz"

items=(.env docker-compose.yml config)
[ -d "$SRC/reports" ] && items+=(reports)
tar -czf "$tmp" -C "$SRC" "${items[@]}"
chmod 600 "$tmp"
mv "$tmp" "$out"
n=$(tar -tzf "$out" | wc -l)
log "config backup OK  files=$n  size=$(stat -c%s "$out")B  file=$(basename "$out")"
find "$DST" -type f -name 'config_*.tar.gz' -mtime +"$KEEP_DAYS" -print -delete || true
```

**首次执行（2026-09-18 13:45）**：`files=28 size=20077B`，含 `.env`、`docker-compose.yml`、26 个 `config/` 条目；权限实测 `600`。
**追加 `reports/` 后验证（2026-09-18 23:23）**：`files=31 size=25346B`（`.env` + `docker-compose.yml` + `config/` 27 条目 + `reports/` 2 条目 = 31）；归档内已含 `reports/watermark-2026-W38.md`；权限仍 `600`，14 天滚动删除逻辑未动。**出机侧无需改动**——`backup_offsite.sh` 早已 rsync `$BASE/config/`，故周报随之出机。
**出机**：`backup_offsite.sh` 的 rsync 已追加 `$BASE/config/`（见 2.3 的对应改动）。
**安全提醒**：该归档**含真实密钥**，落点 0600、出机介质须同样受控（对象存储私有桶 / 加密卷）。

### 2.5 `restore_drill.sh`

```bash
#!/usr/bin/env bash
# S1-8 · 恢复演练（门禁② 证据生成；判读与签字为人工专属）
#   PG 段：从最新 dump 恢复到临时库 → 对象数比对 → 计时 → 清理临时库
#   OS 段：造索引 → 打快照 → 删索引 → 恢复 → 文档数比对 → 清理
# 输出：控制台 + /data/backup/log/restore_drill_<时间戳>.log
set -euo pipefail

BASE=/data/backup
PG_C=ai-governance-poc-postgres-1
OS_C=ai-governance-poc-opensearch-1
OS_URL=https://localhost:9200
REPO=poc_fs
STAMP=$(date +%Y%m%d-%H%M%S)
LOG="$BASE/log/restore_drill_${STAMP}.log"
SEP="----------------------------------------------------------------"

mkdir -p "$BASE/log"
exec > >(tee -a "$LOG") 2>&1
log() { echo "[$(date '+%F %T')] $*"; }

echo "S1-8 恢复演练  stamp=$STAMP"
echo "$SEP"

##################### PG 段 #####################
log "PG 段：选取最新 dump"
DUMP=$(ls -1t "$BASE"/pg/*.dump | head -1)
log "dump 文件：$DUMP（$(stat -c%s "$DUMP")B，生成于 $(stat -c%y "$DUMP")）"

DB_NAME=$(basename "$DUMP" | sed -E 's/_[0-9]{8}-[0-9]{4}\.dump$//')
DRILL_DB="restore_drill_${STAMP//-/}"
log "源库=$DB_NAME  演练临时库=$DRILL_DB"

src_obj=$(docker exec "$PG_C" psql -U postgres -tAc \
  "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
    where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','S','v','m','f')" \
  -d "$DB_NAME")
log "源库对象数=$src_obj"
if [ "${src_obj:-0}" -eq 0 ]; then
  log "⚠ 注意：源库当前无用户对象（S1 阶段尚未部署业务/元数据表）。本次演练只证明【备份→恢复机制】可跑通，"
  log "   不构成「业务数据可恢复」的证明；S2 起（OpenMetadata 落库后）须用真实数据复演并由 DBA 判读。"
fi

docker exec "$PG_C" psql -U postgres -q -c "DROP DATABASE IF EXISTS \"$DRILL_DB\"" >/dev/null
docker exec "$PG_C" psql -U postgres -q -c "CREATE DATABASE \"$DRILL_DB\"" >/dev/null
log "已创建临时库，开始 pg_restore（计时）"

T0=$(date +%s.%N)
docker exec -i "$PG_C" pg_restore -U postgres -d "$DRILL_DB" --no-owner --exit-on-error < "$DUMP" 2>&1 | tail -5 || true
T1=$(date +%s.%N)
PG_SEC=$(awk -v a="$T0" -v b="$T1" 'BEGIN{printf "%.2f", b-a}')
log "pg_restore 完成，耗时 ${PG_SEC} 秒"

dst_obj=$(docker exec "$PG_C" psql -U postgres -tAc \
  "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
    where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','S','v','m','f')" \
  -d "$DRILL_DB" 2>/dev/null || echo "?")
log "恢复库对象数=$dst_obj"

if [ "$src_obj" = "$dst_obj" ]; then
  log "PG 段结论：对象数一致 ✔（pg_restore 观测耗时 ${PG_SEC}s；含建库/清理的端到端 RTO 远低于 4h 目标）"
else
  log "PG 段结论：对象数不一致 ✘（源 $src_obj / 恢复 $dst_obj）"
fi

log "清理临时库 $DRILL_DB"
docker exec "$PG_C" psql -U postgres -q -c "DROP DATABASE \"$DRILL_DB\"" >/dev/null
echo "$SEP"

##################### OpenSearch 段 #####################
OS_PW="$(grep -E '^OPENSEARCH_INITIAL_ADMIN_PASSWORD=' /data/ai-governance/.env | cut -d= -f2-)"
os() { docker exec -e OS_PW="$OS_PW" "$OS_C" sh -c "curl -sk -u \"admin:\$OS_PW\" $*"; }

IDX=drill_src_$STAMP
SNAP=drill-snap-$STAMP
log "OS 段：造索引 $IDX（3 条文档）"
os "-X PUT \"$OS_URL/$IDX\" -H 'Content-Type: application/json' -d '{\"settings\":{\"number_of_shards\":1,\"number_of_replicas\":0}}'" >/dev/null
for i in 1 2 3; do
  os "-X POST \"$OS_URL/$IDX/_doc\" -H 'Content-Type: application/json' -d '{\"n\":$i}'" >/dev/null
done
os "-X POST \"$OS_URL/$IDX/_refresh\"" >/dev/null
before=$(os "-s \"$OS_URL/$IDX/_count\"" | grep -o '"count":[0-9]*' | cut -d: -f2)
log "快照前文档数=$before"

log "打快照 $SNAP 并等待完成"
os "-X PUT \"$OS_URL/_snapshot/$REPO/$SNAP?wait_for_completion=true\" -H 'Content-Type: application/json' -d '{\"indices\":\"$IDX\",\"include_global_state\":false}'" > /tmp/_drill_snap.json
log "快照结果：$(grep -o '\"state\":\"[A-Z]*\"' /tmp/_drill_snap.json | head -1)"

log "删除索引 $IDX（模拟数据丢失）"
os "-X DELETE \"$OS_URL/$IDX\"" >/dev/null
gone=$(os "-s \"$OS_URL/$IDX/_count\"" | head -c 120)
log "删除后查询索引：$gone"

t0=$(date +%s.%N)
log "从快照恢复"
os "-X POST \"$OS_URL/_snapshot/$REPO/$SNAP/_restore?wait_for_completion=true\" -H 'Content-Type: application/json' -d '{\"indices\":\"$IDX\",\"include_global_state\":false}'" > /tmp/_drill_restore.json
t1=$(date +%s.%N)
OS_MS=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.0f", (b-a)*1000}')
after=$(os "-s \"$OS_URL/$IDX/_count\"" | grep -o '"count":[0-9]*' | cut -d: -f2)
log "恢复后文档数=$after（快照前 $before；恢复耗时 ${OS_MS} ms）"

if [ "${before:-x}" = "${after:-y}" ] && [ -n "${after:-}" ]; then
  log "OS 段结论：恢复后文档数与快照前一致 ✔"
else
  log "OS 段结论：文档数不一致 ✘"
fi

log "清理演练产物：删快照 $SNAP、删索引 $IDX"
os "-X DELETE \"$OS_URL/_snapshot/$REPO/$SNAP\"" >/dev/null || true
os "-X DELETE \"$OS_URL/$IDX\"" >/dev/null || true

echo "$SEP"
echo "演练日志：$LOG"
```

---

## 3. 调度（`/etc/cron.d/ai-governance-backup`，root:root 644）

```
# AI 数据治理平台 POC · S1-8 备份调度
# 口径：RPO ≤1h（2026-09-17 人工决策，见《实施执行计划 v2.0》v2.0-r2）
# 依据：《部署步骤_单机版》S1-8；卸载方式：删本文件即可，不影响容器
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
MAILTO=""

# PG 逻辑备份：整点后 5 分（最坏丢 1 小时数据）
5 * * * * root /data/ai-governance/scripts/backup_pg.sh >> /data/backup/log/cron.log 2>&1
# OpenSearch 快照：整点后 15 分
15 * * * * root /data/ai-governance/scripts/backup_opensearch.sh >> /data/backup/log/cron.log 2>&1
# 每日出机：03:00（介质未配置时脚本退出码 2 并记录，不伪造成功）
0 3 * * * root /data/ai-governance/scripts/backup_offsite.sh >> /data/backup/log/cron.log 2>&1
```

---

## 4. 运行手册

| 场景 | 命令 | 预期 |
|---|---|---|
| 手工触发全量备份 | `/data/ai-governance/scripts/backup_pg.sh && /data/ai-governance/scripts/backup_opensearch.sh` | 退出码 0；`/data/backup/pg/` 出现新 `.dump` |
| 查看备份日志 | `tail -50 /data/backup/log/backup_pg.log`（或 `_opensearch.log`、`cron.log`） | 含 `pg_dump OK ... toc_entries=` / `snapshot OK ... state=SUCCESS` |
| 判断最近一次成功 | `cat /data/backup/log/pg_last_success /data/backup/log/opensearch_last_success` | 时间戳 |
| 手动跑恢复演练 | `/data/ai-governance/scripts/restore_drill.sh` | 生成 `/data/backup/log/restore_drill_<stamp>.log` |
| 触发一次出机 | `/data/ai-governance/scripts/backup_offsite.sh` | 介质未配置时**退出码 2**（预期行为，不是故障） |
| 查看快照仓库与快照 | 见 2.2 的 `os` 封装；`GET _snapshot/poc_fs/_all` | `state: SUCCESS` |

**失败处置（硬约束 5：不自动降级、不自行改架构）**：脚本退非 0 → 记录日志 → 停止 → 转人工；**不得**为让门禁通过而调低频率、改保留期或跳过校验。

**变更记录要求**：任何对保留期、频率、校验判据的修改都属**口径相关变更**，须同步《部署步骤_单机版》S1-8 与本文件版本记录；若涉及 RPO 数值，须回填源文档（设计方案 / 执行计划）并走 ADR。

---

## 5. 恢复演练（门禁②）

**执行**：`/data/ai-governance/scripts/restore_drill.sh`（PG 段 + OpenSearch 段，自带产物清理）

### 5.1 逐库恢复演练（2026-09-18 新增，关闭 S2-6 待办 #4）

**问题**：原 `restore_drill.sh` 只取**最新一个 dump**（通常只有 1 个库），**Superset 库的 dump 从未被恢复验证过**（S2-6 待办 #4）。

**新增脚本** `/data/ai-governance/scripts/restore_drill_pg_all.sh`：**逐个平台库**取最新 dump → 建临时库 → `pg_restore --no-owner --no-privileges` → **对象数比对** → 计时 → 删临时库。

**实测结果（2026-09-18 18:48，日志 `/data/backup/log/restore_drill_pg_all_20260918-184822.log`）**：

| 库 | dump | 源对象数 | 恢复后对象数 | 耗时(s) | 判定 | 非致命告警 |
|---|---|---|---|---|---|---|
| `openmetadata_db` | `openmetadata_db_20260918-1805.dump` | 203 | 203 | 2.59 | ✔ 一致 | 0 |
| `airflow_db` | `airflow_db_20260918-1805.dump` | 101 | 101 | 0.67 | ✔ 一致 | 0 |
| **`superset`** | `superset_20260918-1805.dump` | **103** | **103** | 0.63 | ✔ 一致 | 0 |
| `gov_metrics` | `gov_metrics_20260918-1805.dump` | 4 | 4 | 0.10 | ✔ 一致 | 0 |
| `governance_agent` | `governance_agent_20260918-1805.dump` | 5 | 5 | 0.10 | ✔ 一致 | 0 |
| `postgres`（空库） | `postgres_20260918-1805.dump` | 0 | 0 | 0.07 | ✔ 一致 | 0 |

**清理核验**：`drill_*` / `restore_drill_*` 临时库**无残留**；单库端到端恢复 **≤ 2.6 s**（远低于 RTO ≤ 4h）。

> **判读边界（须写进门禁② 判读）**：① 本轮恢复的是**平台自身各库**（已含 OM 195 表、Airflow、Superset、gov_metrics、GA 等真实结构），**仍不含业务源数据**（S2-2 未接源）；② `--no-privileges` 跳过了 GRANT/REVOKE（权限由 `schema.sql` 与 bootstrap 重建），故本演练证明的是**数据可恢复**，不是**权限自动恢复**；③ **出机介质仍未配置**（门禁② 的既有未闭环项），本演练只覆盖"本机 dump → 本机库"。**判读与签字仍为人工专属。**

**2026-09-17 首次实测结果**（日志 `/data/backup/log/restore_drill_20260917-004650.log`）：

| 段 | 动作 | 结果 |
|---|---|---|
| PG | 取最新 dump（`postgres_20260917-0046.dump`，1084B）→ 建临时库 → `pg_restore` → 对象数比对 | 对象数 0 = 0 ✔，`pg_restore` 耗时 **0.07s** |
| OpenSearch | 建索引写 3 条 → 打快照 SUCCESS → 删索引 → 从快照恢复 | 恢复后文档数 3 = 3 ✔，恢复耗时 **158ms** |
| 清理 | 临时库、演练索引、演练快照 | 均已清理（复查见 S1 记录 §7.4） |

**证明力边界（必须写进判读）**：S1 阶段 PG 库**尚无用户对象**（OpenMetadata 在 S2 才落库），因此本次演练证明的是**备份→恢复机制可跑通、产物可读、校验判据有效**，**不构成"业务数据可恢复"的证明**。S2 起须用真实数据复演。

**门禁判定（人工专属）**：RPO ≤1h ｜ RTO ≤4h ｜ 演练记录归档 → **由 DBA 签字后 S1-8 方可放行**。Agent 只提供证据。

### 5.2 年度化恢复演练（本地段）（2026-09-19 新增，对应 S4-4）

**为什么新增**：S4-4 要求"年度化备份恢复演练"，而 §5.1 是**单点**演练（按库、按对象）。需要一次**跨对象、带计时、带 RPO/RTO 对照**的复核，并把"测不了的部分"明确写出来。

**新增执行器**：仓库 `restore-annual-drill/run_annual_drill.sh` → 服务器 `/data/ai-governance/scripts/restore_annual_drill.sh`。
**复用而非重写**：§1 直接调 `restore_drill_pg_all.sh`，§2 直接调 `restore_drill.sh`；新增的是 §0 新鲜度、§3 配置归档校验、§4 Flink Checkpoint 可测性、§5 汇总与声明。

**实测结果（2026-09-19 00:06，报告 `/data/ai-governance/reports/restore-annual-drill-20260919-000605.md`）**：

| 段 | 结果 |
|---|---|
| §0 备份新鲜度 | PG **0.02 h**（阈值 1.5h）✔；配置归档 **0.71 h**（阈值 30h）✔；OS 快照 **0.01 h**（阈值 30h）✔ |
| §1 PG 逐库恢复（6 库） | **全部对象数一致**（`openmetadata_db` 203、`airflow_db` 101、`superset` 103、`gov_metrics` 4、`governance_agent` 6、`postgres` 0）；合计 **6 s**；残留核验 `(none)` |
| §2 OpenSearch 快照恢复 | **恢复后文档数与快照前一致 ✔**；用时 **2 s** |
| §3 配置归档恢复校验 | 归档 **31 条目**；`.env` 存在且非空；`docker-compose.yml` 含 `services:`；`config/` **16 文件**；**密钥键名集合与现网逐一比对 24/24 一致** |
| §4 Flink Checkpoint | **磁盘 0 个 checkpoint、Flink 0 个作业 ⇒ 本环境不可演练**（如实记录，未伪造） |
| §5 计时 | 对象级恢复合计 **8 s**（RTO 目标 4h） |

**§3 为什么要比对密钥键名**：配置归档的动因是"只有数据没有钥匙的恢复是无效恢复"（见 §2.4）。仅检查 `.env` 存在还不够——**要证明归档里的密钥集合与现网一致**，才排除"备份时漏了某个密钥"的情形。本次 24 个键名**完全一致**。

**明确未覆盖（已写入报告 §7，不得据此推断"全灾备可用"）**：

| # | 未覆盖项 | 原因 |
|---|---|---|
| 1 | **PG PITR（时间点恢复）** | 本环境按 S1 设计**未启用 WAL 归档**，该能力不存在；实际保证是「最近一次小时级逻辑 dump」（RPO ≤1h）。**不得把 dump 恢复说成 PITR** |
| 2 | **Flink Checkpoint 出机恢复** | 出机介质未配置；且当前无运行中作业（演练产物已按纪律清理） |
| 3 | **出机副本恢复**（所有对象） | `OFFSITE_TARGET` 未配置 ⇒ 出机链路未闭环（见 §9） |
| 4 | **主机级灾难重建 RTO** | 只测对象级恢复耗时，未做"从零重建整机"演练 |
| 5 | **真实业务数据量下的恢复** | 未接业务源（S2-2 挂账） |

> **判读归属**：上表结果与"是否通过门禁②"均属**人工专属**（DBA 签字）；Agent 只产出证据与核对。

---

## 6. 部署记录（2026-09-17 实际落地）

| # | 项目 | 内容 |
|---|---|---|
| 1 | 脚本 | `/data/ai-governance/scripts/{backup_pg,backup_opensearch,backup_offsite,restore_drill}.sh`，`chmod +x`，`bash -n` 通过 |
| 2 | 调度 | `/etc/cron.d/ai-governance-backup`（644）已装，`systemctl is-active cron` = active，3 条调度 |
| 3 | 目录 | `/data/backup/{pg,pg/daily,log}`、快照根 `/data/opensearch/snapshots` |
| 4 | **compose 变更（唯一）** | `opensearch.environment` 增加 `path.repo: "/usr/share/opensearch/data/snapshots"`；仅重建 opensearch 容器（实测 25s 内 healthy，其余 6 个容器未动） |
| 5 | 校验和 | 本次（加 `path.repo`）：`3cec4d63…` → `895cb47130980283165d48c093d580304752b2e4a524004a8aa7e9a9e48ac30a`；**其后 2026-09-17 运维接入（方案 C）再变为 `07654a18…`（现行）**，见《S1部署记录》§8。备份文件 `/data/ai-governance/docker-compose.yml.bak-20260917-*` |
| 6 | 未做 | 出机介质未配置（脚本退出码 2）；未启用 WAL 归档；~~未接入备份成功率告警~~ → **2026-09-18 已接入**（见 §8） |

---

## 8. 备份/ETL 成功率告警（2026-09-18 新增，关闭本文件旧版"未做"第 6 项之一）

**问题**：备份与 ETL 失败是**静默的**（cron 写日志但无人看）——RPO ≤1h 的承诺实际上没有监控兜底。

**做法**：新增 `/data/ai-governance/scripts/job_freshness.sh`（源码入 Git：`alerting/job_freshness.sh`）+ node_exporter **textfile 收集器**，把 5 类作业的"最近一次成功时间"暴露为指标，由 Prometheus 规则判定过期：

| 作业 | 成功判据 | 阈值 |
|---|---|---|
| PG 逻辑备份 | 最新 `*.dump` 的 mtime | 5400s（1.5× 小时周期） |
| OpenSearch 快照 | 快照目录最新条目 | 5400s |
| 配置出机（`.env`/compose/config） | 最新 `config_*.tar.gz` | 30h（日级） |
| gov_metrics ETL | `gov_metrics.etl_run` 最近 `success` | 5400s |
| RAG 向量刷新 | `rag.doc.max(updated_at)` | 5400s |
| 每日出机 | 仅在 `OFFSITE_TARGET` **已配置**时判定；时间取**本地成功戳** `/data/backup/log/offsite_last_success`（仅 rsync 全成功才写；缺失记 0＝"从未成功"） | 30h |
| 采集器自身 | 自身运行时间戳 | 900s（3× 5min 周期） |

**实测（2026-09-18）**：规则 21 条全部 `health=ok`；五个作业年龄均在阈值内；**触发路径实测**（临时降阈值）`pending → firing`、Alertmanager 出现活动告警、还原后 `inactive`。

> **重要教训（已写入 alerting/README）**：自定义指标**不要用 `job` 作标签名**——与 Prometheus 自身的 `job` 冲突会被改名 `exported_job`，规则永远匹配不到、**告警静默失效**；统一改用 `task`，并以 `JobFreshnessSeriesIncomplete`（`count < 5`）作为护栏。这与本文件反复出现的"不伪造成功"同一精神：**告警也要能自证在生效**。

---

## 9. 出机介质配置单（待人工提供信息）

> ⏸️ **人工决定（2026-09-19）：出机介质暂缓，备份类事项本轮不推进。**
> 本节**保留为待办**（不是取消）：链路已在 §9.4 演练通过，介质就绪后按 §9.3 四步直接启用。
> **门禁② 的该项判据继续挂账**，不得记为已完成。本文件其它内容（小时级备份、快照、配置归档、逐库/年度化恢复演练）**不受影响、照常运行**。

> **为什么停在人工**：介质选型、容量与凭据属人工专属边界（AGENTS §11）。本机出机链路已**演练通过**，缺的只有一个真实的异地目标。

### 9.1 需要人工提供的信息

| # | 项 | 说明 | 备注 |
|---|---|---|---|
| 1 | 介质类型 | 对象存储 / NFS / 加密卷 / 异地主机 | 对象存储需先挂载或走 rclone |
| 2 | 目标路径 | 建议 `<介质挂载点>/ai-governance-backup` | **不得落在本机同一物理盘**，否则不构成"出机" |
| 3 | 凭据 | 不写入仓库；走 KMS/Vault 或 0600 配置文件 | 安全基线① |
| 4 | 容量与保留 | 需承载 ≥14 天：PG dump（每小时）+ config（每日）+ log | 随目标侧策略 |
| 5 | 加密 | 传输加密 + 静态加密 | `.env`/config 归档**含真实密钥**，介质必须受控 |

### 9.2 配置模板（服务器 `/data/ai-governance/backup-offsite.conf`，权限 0600）

```ini
# S1-8 出机目标（唯一权威路径；backup_offsite.sh 与 job_freshness.sh 共用此文件）
OFFSITE_TARGET=<介质挂载点>/ai-governance-backup
```

### 9.3 启用与验收（介质就绪后由 Agent 执行，人工判读结果）

```bash
# 1) 可写性探测（不要跳过）
touch <介质挂载点>/ai-governance-backup/.write_test && rm -f <介质挂载点>/ai-governance-backup/.write_test
# 2) 写入 conf（0600）
install -m 600 /dev/stdin /data/ai-governance/backup-offsite.conf <<< 'OFFSITE_TARGET=<介质挂载点>/ai-governance-backup'
# 3) 手动跑一次出机，须 rc=0 且出现成功戳
/data/ai-governance/scripts/backup_offsite.sh; echo "rc=$?"
cat /data/backup/log/offsite_last_success
# 4) 采集器应转为 configured=1 且时间戳新鲜
/data/ai-governance/scripts/job_freshness.sh
grep offsite /var/lib/node_exporter/textfile/ai_governance_jobs.prom
# 5) 确认 OffsiteBackupStale 不处于 firing
```

**验收判据（门禁② 的实质条件）**：出机成功戳新鲜；目标在**不同故障域**；并且**从出机副本**（不是本机 `/data/backup`）恢复一次成功、对象数一致。

**回滚**：删除 `/data/ai-governance/backup-offsite.conf` 即回到"未配置"（脚本 `rc=2`，`ai_governance_job_configured` 归 0），不要靠改脚本来关掉。

### 9.4 本次链路演练（2026-09-18，**不代表出机闭环**）

| 步骤 | 结果 |
|---|---|
| 指向同机专用目录 `/data/backup/_offsite_rehearsal` 并写 conf | `rc=0`，日志 `=== 出机成功 ===` |
| 目标侧产物 | `pg/`、`log/`、`config/` 三棵树齐全（含 `config_20260918-232331.tar.gz`） |
| 成功戳 | `/data/backup/log/offsite_last_success` = `2026-09-18 23:54:19` |
| 指标侧 | `ai_governance_job_configured{task="backup_offsite"}` 由 **0 → 1**，`time()-last_success` = **46.8s** ⇒ 告警不触发（符合预期） |
| 还原 | 删除 conf / 演练目录 / 成功戳 ⇒ 重新采集回到 **configured=0**、无 offsite 时间戳；目录已清除 |

> ⚠️ **该演练只证明"链路能跑"**：目标落在**本机同一物理盘**，属**同一故障域**，**不满足 ADR-A4 的"每日出机"语义**，也**不能**用于门禁② 的判读。成功戳与演练目录已删除，未给后续留下假信号。

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-17 | 首次产出：按 RPO ≤1h 口径（人工决策）落地 S1-8 四脚本 + cron + 运行手册 + 首次恢复演练证据（PG 0.07s / OS 158ms）+ 三条已知缺口（出机未闭环、无主动告警、快照与索引同盘）+ compose `path.repo` 变更记录 |
| v1.1 | 2026-09-18 | **新增 `backup_config.sh`（配置出机，§2.4）**：`.env` + `docker-compose.yml` + `config/` → `/data/backup/config/config_<ts>.tar.gz`（0600，保留 14 天）；cron 增 **02:55**（先于 03:00 出机）；`backup_offsite.sh` 的 rsync 追加 `$BASE/config/`。**动因**：FERNET_KEY 已改为自持并落在 `.env`（见《…S3前置部署记录.md》§7）——"只有数据、没有钥匙"的恢复是无效恢复。首次执行：28 文件 / 20,077 B / 权限 600。**RPO/RTO 口径未变**（本项只补齐"配置面"覆盖，不改备份频率与保留策略） |
| v1.2 | 2026-09-18 | **新增 §5.1 逐库恢复演练**（脚本 `restore_drill_pg_all.sh`）：覆盖 **6 个平台库**（openmetadata_db / airflow_db / **superset** / gov_metrics / governance_agent / postgres），全部"**对象数一致**"、单库恢复 ≤ 2.6 s、临时库无残留 ⇒ **关闭 S2-6 待办 #4（Superset 库未覆盖）**；同时写明三条判读边界（仍无业务源数据、`--no-privileges` 跳过权限、出机介质仍未配置） |
| v1.3 | 2026-09-18 | **新增 §8 备份/ETL 成功率告警**（关闭旧版"未做"第 6 项之一）：`alerting/job_freshness.sh` + node_exporter **textfile 收集器** + cron 每 5 分钟；5 类作业的新鲜度阈值与成功判据（含"出机仅在已配置时判定"）；**触发路径实测**（pending → firing → Alertmanager active → 还原 inactive）。附**重要教训**：自定义标签**不能用 `job`**（与 Prometheus 冲突被改名 `exported_job` ⇒ 规则静默失效），统一改 `task` 并加 `count<5` 护栏 |
| v1.4 | 2026-09-18 | **`backup_config.sh` 采集范围追加 `reports/`（§2.4）**：S4-4 水位周报是 **M-Prod 触发条件 2 的书面证据**，而 Prometheus 本地仅保留 15d ⇒ 报告不落备份等于"判定依据只活两周"。实现为按目录存在性拼接 tar 清单（`items=(.env docker-compose.yml config) [ -d reports ] && items+=(reports)`），**未改频率与保留策略，RPO/RTO 口径不变**。实测：`files=31 size=25346B`，归档内含 `reports/watermark-2026-W38.md`；权限 600；出机侧无需改（`backup_offsite.sh` 已 rsync `$BASE/config/`）。原脚本已备份为 `backup_config.sh.bak-20260918-2325` |
| v1.5 | 2026-09-18 | **出机链路修复 + 介质配置单（§9）**：① 修复**第二处告警静默失效**——`backup_offsite.sh` 读 `backup-offsite.conf`、`job_freshness.sh` 却读 `offsite.conf`，人工配好介质后 `ai_governance_job_configured` 仍为 0 ⇒ `OffsiteBackupStale` 永不触发；统一为 `backup-offsite.conf`，且取值从"扫远端介质"改为读**本地成功戳** `offsite_last_success`（网络挂载点无挂死风险），缺失记 0＝"从未成功"（宁可误报不静默）。② 修正 §2.3 代码块与服务器脚本的漂移（补 `config/` rsync，**现已与服务器逐字节一致**）。③ 新增 §9 出机介质配置单（待人工信息 5 项 + 模板 + 启用/验收/回滚步骤）与**同机链路演练**记录（目标 `_offsite_rehearsal`，rc=0、三棵树齐全、指标 0→1、年龄 46.8s；**已完整还原**，成功戳与目录删除，**该演练不满足"出机"语义、不得用于门禁② 判读**）。**RPO/RTO 口径未变；介质仍未配置（门禁② 该条件仍挂账）** |
| v1.6 | 2026-09-19 | **年度化恢复演练（本地段）（§5.2）**：新增执行器 `restore-annual-drill/run_annual_drill.sh`（**复用**既有 PG/OS 演练脚本，新增 §0 新鲜度、§3 配置归档校验、§4 Flink Checkpoint 可测性、§5 汇总）。实测：PG 6 库**全一致**（合计 6s，残留 `(none)`）、OS 快照**文档数一致**（2s）、配置归档 31 条目且**密钥键名 24/24 与现网一致**、对象级恢复合计 **8s**（RTO 4h）。**§4 如实记录"0 checkpoint、0 作业 ⇒ 不可演练"，未伪造结果**。报告 §7 固定声明 5 条未覆盖（**PG PITR 在本环境不存在**——未启用 WAL 归档，不得把 dump 恢复说成 PITR；Flink CP 出机恢复；出机副本恢复；主机级重建 RTO；真实业务量恢复）。新增 `restore-annual-drill/` 并入 AGENTS 文档地图与 §7 代码目录表 |
| v1.7 | 2026-09-19 | **记录人工决定：出机介质暂缓（备份类事项本轮不推进）**：§9 顶部加⏸️状态说明 —— 本节**保留为待办而非取消**（链路已在 §9.4 演练通过，介质就绪后按 §9.3 四步启用），**门禁② 的该项判据继续挂账、不得记为已完成**；本地备份链与逐库/年度化恢复演练**照常运行**。**未改脚本、未改 RPO/RTO 口径、未改门禁判据** |
| v1.8 | 2026-09-21 | **补齐仓库漂移：备份/恢复脚本入仓库**（人工要求"把相关部署脚本打包成交付物"时发现）：`backup_pg.sh` / `backup_opensearch.sh` / `backup_config.sh` / `backup_offsite.sh` / `restore_drill.sh` / `restore_drill_pg_all.sh` / `restore_annual_drill.sh` 共 **7 个脚本此前只存在于服务器** `/data/ai-governance/scripts/`（仓库无副本）⇒ 现已取回归档到 **`backup-restore/`**（AGENTS §7 同步登记）并收录进交付包 `交付物/scripts/backup-restore/`。**完整性核对**：服务器 ↔ 归档副本 **sha256 逐一一致**（示例 `backup_pg.sh 18e6cda8…`、`restore_drill_pg_all.sh a12cbf90…`、`restore_annual_drill.sh d1a31ce5…`）；**无硬编码口令**（`OS_PW="$(grep …)"` 等均读 `/data/ai-governance/.env`）。同批回填：`capacity-baseline/capacity_baseline.sh`、`watermark-report/install_watermark_cron.sh`。**未改脚本内容、未改调度与 RPO/RTO 口径** |
| v1.9 | 2026-09-21 | **脱敏整改（同步 GitHub 前）**：把服务器**公网 IP** 与**主机名**替换为占位符（`<POC服务器公网IP>` / `<POC服务器主机名>`），以满足 AGENTS §9「域名/敏感信息一律占位符」；**未改任何口径、结论与判据**（仓库已推送 GitHub，历史提交中的原值另行处理） |

---

*本文件为 S1-8 部署脚本产物，由《部署步骤_单机版》S1-8 展开，不含新增架构决策；与源文档冲突时以源文档为准并回报修订。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；命令与版本号执行前请按各组件官方文档核对。恢复演练结果的验证与签字为人工专属。
