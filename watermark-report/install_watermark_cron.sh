#!/usr/bin/env bash
# 安装 / 卸载单机水位周报（S4-4）的 cron 任务 —— 幂等，可重复执行
#
# 用法（服务器上以 root 执行）：
#   bash install_cron.sh            # 安装：每周一 08:00 出报告
#   bash install_cron.sh --remove   # 卸载
#
# 落点：/etc/cron.d/ai-governance-backup（与 S1-8 备份任务同一文件，保持"一处看全"）
# 说明：脚本只增删自己那两行（注释 + 任务），不动其它备份任务。

set -eu

CRONFILE=/etc/cron.d/ai-governance-backup
SCRIPT=/data/ai-governance/scripts/watermark_report.sh
LOGFILE=/data/backup/log/watermark_report.log
MARK=watermark_report.sh

[ "$(id -u)" = "0" ] || { echo "须以 root 执行"; exit 1; }
[ -f "$CRONFILE" ] || { echo "缺少 $CRONFILE（S1-8 备份任务文件），先确认 S1-8 已完成"; exit 1; }

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

# 先剔除本任务自己的行（含注释），保证幂等
grep -v "$MARK" "$CRONFILE" | grep -v '单机水位周报' > "$TMP" || true

if [ "${1:-}" = "--remove" ]; then
  install -m 644 "$TMP" "$CRONFILE"
  echo "已卸载水位周报 cron 任务"
  exit 0
fi

[ -f "$SCRIPT" ] || { echo "缺少脚本 $SCRIPT，请先上传 watermark_report.sh"; exit 1; }
mkdir -p "$(dirname "$LOGFILE")"

{
  echo '# 单机水位周报（S4-4）：每周一 08:00 出报告 → /data/ai-governance/reports/'
  echo "0 8 * * 1 root $SCRIPT >> $LOGFILE 2>&1"
} >> "$TMP"

install -m 644 "$TMP" "$CRONFILE"

echo "已安装。当前水位周报相关行："
grep -n "$MARK" "$CRONFILE"
echo
echo "校验（应能看到 '0 8 * * 1 root ...'）："
cat "$CRONFILE" | tail -3
