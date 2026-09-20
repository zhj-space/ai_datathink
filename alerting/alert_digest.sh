#!/usr/bin/env bash
# 告警日报（2026-09-19）：把 GA 落库的告警汇总成一份 Markdown，落到 reports/（随配置备份出机）
#
# 为什么需要：IM/Webhook 地址一直未提供 ⇒ 告警**没有主动通知**。日报是"不依赖 IM 的最低成本可见性"：
#   每天一份，值班至少能按天看到今天都报了什么，而不是只在 Alertmanager UI 里翻。
#
# 口径与边界：
#   * 只汇总 **GA 已落库**的告警（来源含 alertmanager 与 governance.alerts 两条链）；
#   * **不代替通知**——IM 到位后仍应配置 receiver；本脚本只是把"没有通知"的可见性缺口缩小；
#   * 窗口 = 北京时间自然日（`since` 取当日 00:00 CST 对应的 UTC）。
#
# 用法：alert_digest.sh [--date YYYY-MM-DD]   （默认今天；同日重跑覆盖同一文件，幂等）
set -uo pipefail
GA=${GA_URL:-http://127.0.0.1:8085}
ENVF=/data/ai-governance/.env
OUTDIR=${OUTDIR:-/data/ai-governance/reports}
LOG=/data/backup/log/alert_digest.log
mkdir -p "$OUTDIR" "$(dirname "$LOG")"
TOKEN=$(grep -m1 '^GA_INGEST_TOKEN=' "$ENVF" | cut -d= -f2-)

DAY=$(date +%F)
if [ "${1:-}" = "--date" ]; then DAY=${2:-$DAY}; fi
SINCE=$(TZ=Asia/Shanghai date -d "$DAY 00:00" -u +%Y-%m-%dT%H:%M:%SZ)
OUT="$OUTDIR/alert-digest-$DAY.md"
TMPJSON=$(mktemp)
trap 'rm -f "$TMPJSON"' EXIT
TS=$(date '+%F %T')

if ! curl -s -S --max-time 60 -H "Authorization: Bearer $TOKEN" \
     "$GA/api/v1/alerts?limit=500&since=$SINCE" -o "$TMPJSON"; then
  echo "[$TS] FAIL 取 GA 告警失败（未写日报）" >> "$LOG"
  exit 1
fi

python3 - "$DAY" "$SINCE" "$OUT" "$TMPJSON" <<'PY'
import json
import sys
from collections import Counter

day, since, out, jf = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    d = json.load(open(jf, encoding="utf-8"))
except Exception as exc:
    print("FAIL 响应不是 JSON：%s" % exc)
    raise SystemExit(1)
rows = d.get("data") or []

by_sev = Counter((r.get("severity") or "unknown") for r in rows)
by_name = Counter((r.get("alert_name") or "unknown") for r in rows)
by_src = Counter((r.get("source") or "unknown") for r in rows)


def fmt(counter, top=None):
    items = counter.most_common(top) if top else sorted(counter.items())
    return ", ".join("%s=%d" % kv for kv in items) or "（无）"


with open(out, "w", encoding="utf-8") as f:
    f.write("# 告警日报 · %s\n\n" % day)
    f.write("> 窗口（CST 当日）：%s 起 ｜ 数据来源：Governance Agent `ga.alert_event`"
            "（含 alertmanager 与 governance.alerts 两条链）\n" % since)
    f.write("> **本报告不代替通知**：这是 IM/Webhook 未配置前的「每日可见性」兜底；"
            "报告落在 `/data/ai-governance/reports/`，已随配置备份出机。\n\n")
    f.write("## 1. 概览\n\n| 项 | 值 |\n|---|---|\n")
    f.write("| 当日告警条数 | %d |\n" % len(rows))
    f.write("| 按严重度 | %s |\n" % fmt(by_sev))
    f.write("| 按来源 | %s |\n" % fmt(by_src))
    f.write("| 按告警名（Top 10） | %s |\n" % fmt(by_name, 10))
    f.write("\n## 2. 明细（最近 50 条）\n\n")
    if rows:
        f.write("| 时间(UTC) | 严重度 | 来源 | 告警名 | 增强摘要 |\n|---|---|---|---|---|\n")
        for r in rows[:50]:
            summ = (r.get("enhanced_summary") or "").replace("|", "/")[:90]
            f.write("| %s | %s | %s | %s | %s |\n" % (
                r.get("received_at"), r.get("severity"), r.get("source"),
                r.get("alert_name"), summ))
    else:
        f.write("_当日无告警。_\n")
print("OK 日报已生成：%s（%d 条）" % (out, len(rows)))
PY
RC=$?
if [ "$RC" != "0" ]; then
  echo "[$TS] FAIL 汇总失败 rc=$RC" >> "$LOG"
  exit 1
fi
echo "[$TS] OK 日报写入 $OUT（since=$SINCE）" >> "$LOG"
cat "$OUT"
