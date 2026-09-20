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
