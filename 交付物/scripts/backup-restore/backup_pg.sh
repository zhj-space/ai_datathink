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
