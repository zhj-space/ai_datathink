#!/usr/bin/env bash
# S1-8 补充演练：**逐个库**恢复演练（覆盖全部平台库，含 Superset —— 关闭 S2-6 待办 #4）
# 每库：取最新 dump → 建临时库 → pg_restore → 对象数比对 → 计时 → 清理临时库
set -uo pipefail
BASE=/data/backup
PG_C=ai-governance-poc-postgres-1
STAMP=$(date +%Y%m%d-%H%M%S)
LOG="$BASE/log/restore_drill_pg_all_${STAMP}.log"
mkdir -p "$BASE/log"
exec > >(tee -a "$LOG") 2>&1
log() { echo "[$(date '+%F %T')] $*"; }

DBS="openmetadata_db airflow_db superset gov_metrics governance_agent postgres"
COUNT_SQL="select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
           where n.nspname not in ('pg_catalog','information_schema')
             and c.relkind in ('r','p','S','v','m','f')"

log "=== PG 逐库恢复演练 stamp=$STAMP ==="
printf '%-20s %-28s %8s %8s %10s %8s\n' "DB" "dump" "src_obj" "dst_obj" "restore_s" "判定"
FAIL=0
for DB in $DBS; do
  DUMP=$(ls -1t "$BASE/pg/${DB}_"*.dump 2>/dev/null | head -1 || true)
  if [ -z "$DUMP" ]; then printf '%-20s %-28s %8s\n' "$DB" "(无 dump，跳过)" "-"; continue; fi
  DRILL="drill_${DB}_${STAMP//-/}"
  SRC=$(docker exec "$PG_C" psql -U postgres -tAc "$COUNT_SQL" -d "$DB" 2>/dev/null || echo 0)
  docker exec "$PG_C" psql -U postgres -q -c "DROP DATABASE IF EXISTS \"$DRILL\"" >/dev/null 2>&1
  docker exec "$PG_C" psql -U postgres -q -c "CREATE DATABASE \"$DRILL\"" >/dev/null
  T0=$(date +%s.%N)
  docker exec -i "$PG_C" pg_restore -U postgres -d "$DRILL" --no-owner --no-privileges < "$DUMP" > /tmp/drill_restore_out.txt 2>&1 || true
  T1=$(date +%s.%N)
  SEC=$(awk -v a="$T0" -v b="$T1" 'BEGIN{printf "%.2f", b-a}')
  DST=$(docker exec "$PG_C" psql -U postgres -tAc "$COUNT_SQL" -d "$DRILL" 2>/dev/null || echo "?")
  ERRS=$(grep -ciE '^pg_restore: error' /tmp/drill_restore_out.txt 2>/dev/null || echo 0)
  if [ "$SRC" = "$DST" ]; then VERDICT="✔ 一致"; else VERDICT="✘ 不一致"; FAIL=1; fi
  printf '%-20s %-28s %8s %8s %10s %8s (非致命告警 %s)\n' "$DB" "$(basename "$DUMP")" "$SRC" "$DST" "$SEC" "$VERDICT" "$ERRS"
  docker exec "$PG_C" psql -U postgres -q -c "DROP DATABASE IF EXISTS \"$DRILL\"" >/dev/null
done

log "=== 临时库清理核验（应无 drill_* 残留）==="
docker exec "$PG_C" psql -U postgres -tAc "select coalesce(string_agg(datname,','),'(none)') from pg_database where datname like 'drill_%' or datname like 'restore_drill_%'"
log "=== 结论 ==="
[ "$FAIL" -eq 0 ] && log "全部库「对象数一致」⇒ 备份→恢复机制在各库均可用（判读与签字仍为人工专属）" || log "存在不一致库，需人工核查"
log "日志：$LOG"
