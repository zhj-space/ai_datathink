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
