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
