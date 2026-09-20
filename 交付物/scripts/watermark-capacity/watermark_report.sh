#!/usr/bin/env bash
# 单机水位周报（S4-4）：CPU / 内存 / 磁盘 + 平台运行面，对照 M-Prod 触发条件 2
#
# 依据（口径全部取自源文档，未自造）：
#   * 《部署步骤_单机版》S4-4「单机水位周报：CPU/内存/磁盘，对照 M-Prod 触发条件 2（水位 >70% 连续两周）」
#   * 《部署步骤_单机版》0.3「磁盘水位纳入最高优先级告警，达 80% 记录、停止、转人工」
#   * 加盘触发条件（本环境口径）：可用空间 < 20 GiB（见《…S3前置部署记录.md》）
#
# 数据来源：Prometheus API（node_exporter 指标）。脚本会**实测数据实际跨度**，
# 跨度不足 7 天 / 14 天时不给出"未触发"结论，而是明确写"数据不足"——
# 长窗 `rate()` 在样本有缺口时会被 Prometheus 外推放大，直接取 `rate(idle[7d])` 会得到假值。
#
# 输出：控制台 + /data/ai-governance/reports/watermark-<YYYY-Www>.md
# 纪律：本报告由 Agent 生成；**触发与否、是否推 M-Prod 属人工判读**；容量趋势为工程经验估算，不作 SLA。

set -u
PROM=${PROM:-http://127.0.0.1:9090}
OUTDIR=${OUTDIR:-/data/ai-governance/reports}
WEEK=$(date +%G-W%V)
OUT="$OUTDIR/watermark-$WEEK.md"
mkdir -p "$OUTDIR"

KV=$(mktemp)
trap 'rm -f "$KV"' EXIT

# ---------- 1) 取数（全部经 python3 urllib，避免 curl|python 的引号转义坑）----------
python3 - "$PROM" "$KV" << 'PY'
import json, sys, urllib.parse, urllib.request

base, kvpath = sys.argv[1], sys.argv[2]

def api(path, params=None):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)

def val(expr):
    try:
        res = api("/api/v1/query", {"query": expr})["data"]["result"]
    except Exception:
        return None
    if not res:
        return None
    try:
        return float(res[0]["value"][1])
    except Exception:
        return None

def txt(expr, key="task", unit="min"):
    try:
        res = api("/api/v1/query", {"query": expr})["data"]["result"]
    except Exception:
        return "N/A"
    parts = []
    for x in res:
        try:
            parts.append("%s=%d%s" % (x["metric"].get(key, "?"), int(float(x["value"][1]) // 60), unit))
        except Exception:
            pass
    return ", ".join(parts) if parts else "N/A"

def num(x, nd=1):
    return "N/A" if x is None else ("%.*f" % (nd, x))

out = {}

def emit(k, v):
    out[k] = str(v)

# ---- 数据实际跨度（决定 7 天/14 天窗口是否成立）----
SPAN = val('(time() - min_over_time(timestamp(up{job="node"})[15d:1h])) / 86400')
if SPAN is None:
    SPAN = 0.0
ok7, ok14 = SPAN >= 7, SPAN >= 14
emit("SPAN_D", num(SPAN, 2))
emit("OK7", 1 if ok7 else 0)
emit("OK14", 1 if ok14 else 0)

# 窗口求平均统一用「5m 速率/比值 → 子查询 → avg_over_time」，
# 不用 rate(metric[7d])：长窗 rate 会按外推补足窗口，样本有缺口时数值失真。
CPU  = 'avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))'
MEMR = '(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)'
FSR  = '(node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})'

def pct_win(inner, dur, offset=None):
    off = (" offset %s" % offset) if offset else ""
    return val('100 * (1 - avg_over_time(%s[%s:5m]%s))' % (inner, dur, off))

FULL = "%ds" % int(SPAN * 86400) if SPAN > 0 else "1h"

def cell(v, ok=True):
    """数值单元格：窗口不成立时写'数据不足'，成立但无样本写'N/A'。"""
    if not ok:
        return "数据不足"
    return "N/A" if v is None else ("%.1f%%" % v)

def verdict(w1, w2, ok):
    """M-Prod 触发条件 2：两个相邻 7 天窗口都 >70%。"""
    if not ok or w1 is None or w2 is None:
        return "数据不足，无法判定（需连续 14 天）"
    return "**触发（两周均值均 >70%）**" if (w1 > 70 and w2 > 70) else "未触发"

for name, inner in (("CPU", CPU), ("MEM", MEMR), ("DISK", FSR)):
    now = val('100 * (1 - %s)' % inner)
    wf = pct_win(inner, FULL)
    w7 = pct_win(inner, "7d") if ok7 else None
    w14 = pct_win(inner, "7d", "7d") if ok14 else None
    emit(name + "_NOW", cell(now))
    emit(name + "_WF", cell(wf))
    emit(name + "_W7", cell(w7, ok7))
    emit(name + "_W14", cell(w14, ok14))
    emit("V_" + name, verdict(w7, w14, ok14))

# ---- 磁盘容量与净变化 ----
GIB = "/ 1024/1024/1024"
SIZE = val('node_filesystem_size_bytes{mountpoint="/"} %s' % GIB)
AVAIL = val('node_filesystem_avail_bytes{mountpoint="/"} %s' % GIB)
D24 = val('delta(node_filesystem_avail_bytes{mountpoint="/"}[24h]) %s' % GIB)
DFULL = val('delta(node_filesystem_avail_bytes{mountpoint="/"}[%s]) %s' % (FULL, GIB))
emit("SIZE_GIB", num(SIZE))
emit("AVAIL_GIB", num(AVAIL))
emit("D24_GIB", num(D24))
emit("DFULL_GIB", num(DFULL))
emit("THRESH_HIT", "**已命中**（< 20 GiB）" if (AVAIL is not None and AVAIL < 20) else "未命中")

# 消耗速率（GiB/天）：24h 与"全数据跨度"两条；不做事先线性外推（镜像是台阶式增长）
r24 = (-D24) if (D24 is not None and D24 < 0) else None
rfull = (-DFULL / SPAN) if (DFULL is not None and DFULL < 0 and SPAN > 0) else None
emit("RATE24", "N/A" if r24 is None else num(r24))
emit("RATEFULL", "N/A" if rfull is None else num(rfull))
rates = [r for r in (r24, rfull) if r]
if AVAIL is None:
    runway = "N/A"
elif not rates:
    runway = "无下降趋势"
else:
    head = AVAIL - 20
    if head <= 0:
        runway = "已跌破 20 GiB 阈值"
    else:
        fastd, slowd = head / max(rates), head / min(rates)
        runway = ("约 %.1f 天" % fastd) if abs(fastd - slowd) < 0.05 else ("约 %.1f ~ %.1f 天" % (fastd, slowd))
emit("RUNWAY", runway)

# ---- 平台运行面 ----
try:
    g = api("/api/v1/rules")["data"]["groups"]
    emit("RULES", sum(len(x["rules"]) for x in g))
except Exception:
    emit("RULES", "N/A")
try:
    t = api("/api/v1/targets")["data"]["activeTargets"]
    emit("TARGETS_UP", "%d/%d" % (sum(1 for x in t if x["health"] == "up"), len(t)))
except Exception:
    emit("TARGETS_UP", "N/A")
emit("JOBS", txt('time() - ai_governance_job_last_success_timestamp_seconds'))
BK = val('max(time() - ai_governance_job_last_success_timestamp_seconds{task=~"backup_.*"})')
emit("BACKUP_OLDEST", "N/A" if BK is None else "%.0f 分钟" % (BK / 60))

with open(kvpath, "w", encoding="utf-8") as f:
    for k, v in out.items():
        f.write("%s='%s'\n" % (k, v.replace("'", " ")))
PY

. "$KV"

# ---------- 2) 宿主侧运行面（docker 可用时）----------
CONTAINERS=$(docker ps -q 2>/dev/null | wc -l | tr -d ' ')
UNHEALTHY=$(docker ps --format '{{.Status}}' 2>/dev/null | grep -vc 'healthy' 2>/dev/null || true)
IMAGES=$(docker system df --format '{{.Type}}|{{.TotalCount}}|{{.Size}}|{{.Reclaimable}}' 2>/dev/null \
  | awk -F'|' '$1=="Images"{print $2" 个 / "$3"（可回收 "$4"）"}' | head -1)
[ -n "${IMAGES:-}" ] || IMAGES="N/A"

# ---------- 3) 渲染报告 ----------
{
echo "# 单机水位周报 · $(date '+%Y-%m-%d')（$WEEK）"
echo
echo "> **数据来源**：Prometheus API（node_exporter），实测数据实际跨度 **${SPAN_D} 天**；窗口值均为区间聚合，非瞬时快照。"
echo "> **对照口径**：《部署步骤_单机版》S4-4「M-Prod 触发条件 2：水位 >70% 连续两周」；0.3「磁盘达 80% 记录、停止、转人工」；加盘阈值「可用 < 20 GiB」。"
echo "> **判读**：本报告由 Agent 生成，**触发与否、是否推 M-Prod 属人工判读**；容量与趋势为工程经验估算，不作 SLA。"
echo
echo "## 1. 结论（对照 M-Prod 触发条件 2）"
echo
echo "| 指标 | 当前 | 可用窗口均值（${SPAN_D} 天） | 近 7 天均值 | 前 7 天均值（第 8–14 天） | 阈值 | 判定 |"
echo "|---|---|---|---|---|---|---|"
echo "| CPU | ${CPU_NOW} | ${CPU_WF} | ${CPU_W7} | ${CPU_W14} | >70% 连续两周 | ${V_CPU} |"
echo "| 内存 | ${MEM_NOW} | ${MEM_WF} | ${MEM_W7} | ${MEM_W14} | >70% 连续两周 | ${V_MEM} |"
echo "| 磁盘（/） | ${DISK_NOW} | ${DISK_WF} | ${DISK_W7} | ${DISK_W14} | >70% 连续两周 / 80% 转人工 | ${V_DISK} |"
echo
if [ "$OK14" != "1" ]; then
  echo "> ⚠️ **判定不可用**：判定条件要求连续 14 天数据，当前仅 ${SPAN_D} 天，故一律记「数据不足」，不做倾向性结论。"
  echo
fi
echo "## 2. 磁盘容量与趋势"
echo
echo "| 项 | 值 |"
echo "|---|---|"
echo "| 根分区容量 / 可用 | ${SIZE_GIB} GiB / **${AVAIL_GIB} GiB** |"
echo "| 加盘触发条件 | 可用 < **20 GiB** ⇒ 当前 ${THRESH_HIT} |"
echo "| 近 24 小时净变化 | ${D24_GIB} GiB |"
echo "| 全窗口（${SPAN_D} 天）净变化 | ${DFULL_GIB} GiB |"
echo "| 折算消耗速率 | 近 24h ${RATE24} GiB/天；全窗口 ${RATEFULL} GiB/天（**工程经验估算**） |"
if [ "$RUNWAY" != "N/A" ]; then
  echo "| 据此触及 20 GiB 阈值的余量 | ${RUNWAY}（**工程经验估算，需实测校准**） |"
else
  echo "| 据此触及 20 GiB 阈值的余量 | N/A（消耗速率为负或不可算） |"
fi
echo "| 镜像占用 / 可回收 | ${IMAGES} |"
echo
echo "> **口径提醒**：本环境的磁盘下降主要来自**镜像一次性拉取**（台阶式），不是稳态增长；"
echo "> 对台阶式变化做线性外推（PromQL deriv / predict_linear）会把台阶拉直、产出无物理意义的负值，故本报告不外推。"
echo
echo "## 3. 平台运行面"
echo
echo "| 项 | 值 |"
echo "|---|---|"
echo "| 容器数 / 非 healthy | ${CONTAINERS} / ${UNHEALTHY} |"
echo "| 告警规则数 | ${RULES} |"
echo "| 抓取目标 up | ${TARGETS_UP} |"
echo "| 备份/ETL 新鲜度（最近成功距今） | ${JOBS} |"
echo "| 备份类作业最旧 | ${BACKUP_OLDEST} |"
echo
echo "## 4. 建议（供人工判读）"
echo
echo "- 触发条件 2：见 §1。当前数据跨度不足以判定；**需连续两个 7 天窗口都 >70%** 才构成触发，届时按设计准备 M-Prod 评审材料（结论属人工）。"
echo "- 磁盘：若 §2 显示已命中加盘条件或余量告急，建议在接业务源前安排加盘 + 迁移窗口（步骤见《…S3前置部署记录.md》）。"
echo "- 镜像可回收量较大时（§2），可在确认无回滚需求后执行清理，属变更操作，需人工批准。"
echo "- 备份/ETL 新鲜度若出现异常值，先查 §3 对应日志与 job-freshness 告警规则。"
echo
echo "---"
echo "*本报告由 watermark_report.sh 生成（cron 每周一 08:00）；阈值与口径均取自源文档，未自造。*"
} > "$OUT"

cat "$OUT"
echo
echo "报告已写入：$OUT"
