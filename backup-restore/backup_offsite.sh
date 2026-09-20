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
