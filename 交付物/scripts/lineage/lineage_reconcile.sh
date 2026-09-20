#!/usr/bin/env bash
# D1 作业图级血缘 · 每日对账（cron 04:10）
#
# 为什么必须有：对账的价值就是"发现漂移"——**对账自己静默失败等于没做**。
#   例：OM 侧实体被删除会连带删掉血缘边，而 GA 库里那条边仍是 applied（本环境 2026-09-19 实测）。
#   本脚本调用 GA 的 `POST /api/v1/lineage/reconcile`（repair=true：发现缺失就重新上报），
#   成功时写**成功戳**，由 `job_freshness.sh` 采集 → `LineageReconcileStale` 规则兜底告警。
#
# 判定口径：
#   * 成功：curl 成功 且 响应里 `failed == 0`；
#   * `entity_missing > 0` 属**尚未接源建表**的正常态（不计失败）；`checked == 0` 同理；
#   * `repaired > 0` 记日志（说明确实发生过漂移并被自愈）——这本身是有价值的观测。
set -uo pipefail
GA=${GA_URL:-http://127.0.0.1:8085}
ENVF=/data/ai-governance/.env
MARK=/data/ai-governance/state/lineage_reconcile_last_success
LOG=/data/backup/log/lineage_reconcile.log
mkdir -p "$(dirname "$MARK")" "$(dirname "$LOG")"

TOKEN=$(grep -m1 '^GA_INGEST_TOKEN=' "$ENVF" | cut -d= -f2-)
TS=$(date '+%F %T')

RESP=$(curl -s -S --max-time 180 -X POST -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" -d '{"repair":true}' "$GA/api/v1/lineage/reconcile" 2>&1)
RC=$?
echo "[$TS] rc=$RC resp=$RESP" >> "$LOG"

VERDICT=$(printf '%s' "$RESP" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("badjson"); raise SystemExit
if d.get("failed", 0) > 0:
    print("failed")
elif d.get("checked", 0) == 0:
    print("nocases")
elif d.get("repaired", 0) > 0:
    print("repaired")
elif d.get("entity_missing", 0) > 0:
    print("pending-source")
else:
    print("ok")' 2>/dev/null)

echo "[$TS] verdict=${VERDICT:-unknown}" >> "$LOG"
case "${VERDICT:-unknown}" in
  ok|repaired|nocases|pending-source)
    date +%s > "$MARK"
    echo "[$TS] OK（成功戳已更新）" >> "$LOG"
    exit 0
    ;;
  *)
    echo "[$TS] FAIL —— 未更新成功戳，将由 LineageReconcileStale 告警暴露（不静默）" >> "$LOG"
    exit 1
    ;;
esac
