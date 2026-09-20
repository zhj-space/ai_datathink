#!/usr/bin/env bash
# D1 作业提交包装：**提交即登记血缘**（干跑门禁 → 登记 → 提交 → 上报）
#
# 为什么要有它：血缘上报如果靠"提交完再手动 POST"，迟早会漏；把它**串进提交动作**才是可靠的。
# 提交路径沿用 S3-5 演练已验证的做法（避开多层 shell 引号）：
#   SQL 写文件 → docker cp 进 JobManager 容器 → `sql-client.sh -f`
#
# 用法：
#   submit_flink_job.sh <job_key> <sql文件>              # 先干跑校验，通过才提交，提交后写血缘
#   submit_flink_job.sh <job_key> <sql文件> --no-submit  # 只登记+校验+上报（演练/预检用，不提交作业）
#   SUBMIT_CMD="自定义提交命令" submit_flink_job.sh ...   # 覆盖默认提交命令（容器名/路径变化时用）
#
# 纪律：
#   * **干跑不通过就不提交**（宁缺勿错：血缘解析/映射不确定时，宁可拦住作业，也不要跑出一个没有血缘的作业）；
#   * 作业 SQL 会被**登记**到 `/data/ai-governance/jobs/<job_key>.sql`（自动上报的唯一真相源）；
#   * mapping 放在同目录 `mapping.json`（共享）或 `<job_key>.mapping.json`（按作业覆盖）。
set -uo pipefail
GA=${GA_URL:-http://127.0.0.1:8085}
ENVF=/data/ai-governance/.env
JOBDIR=/data/ai-governance/jobs
LOG=/data/backup/log/lineage_submit.log
JM_CT=${JM_CT:-ai-governance-poc-flink-jobmanager-1}
mkdir -p "$JOBDIR" "$(dirname "$LOG")"

JOB=${1:-}; SRC=${2:-}; MODE=${3:-}
if [ -z "$JOB" ] || [ -z "$SRC" ] || [ ! -f "$SRC" ]; then
  echo "用法：$0 <job_key> <sql文件> [--no-submit]" >&2
  exit 2
fi
TOKEN=$(grep -m1 '^GA_INGEST_TOKEN=' "$ENVF" | cut -d= -f2-)
TS=$(date '+%F %T')

# ---------- 1) 登记（先登记：即使后面失败，自动上报也能发现这个作业）----------
install -m 644 "$SRC" "$JOBDIR/$JOB.sql"
echo "[$TS] 已登记 $JOBDIR/$JOB.sql" | tee -a "$LOG"

if [ -f "$JOBDIR/$JOB.mapping.json" ]; then MAP="$JOBDIR/$JOB.mapping.json"
elif [ -f "$JOBDIR/mapping.json" ]; then MAP="$JOBDIR/mapping.json"
else MAP=""; fi

report() { # $1 = true|false
  python3 - "$GA" "$TOKEN" "$JOB" "$JOBDIR/$JOB.sql" "$MAP" "$1" <<'PY'
import json, sys, urllib.error, urllib.request
ga, token, job, path, mappath, apply_ = sys.argv[1:7]
mapping = json.load(open(mappath, encoding="utf-8")) if mappath else {}
body = {"job_key": job, "sql": open(path, encoding="utf-8").read(),
        "mapping": mapping, "apply": apply_ == "true"}
req = urllib.request.Request(ga + "/api/v1/lineage/report", data=json.dumps(body).encode(),
                             method="POST", headers={"Content-Type": "application/json",
                                                     "Authorization": "Bearer " + token})
try:
    with urllib.request.urlopen(req, timeout=180) as r:
        print(r.read().decode("utf-8", "replace"))
except urllib.error.HTTPError as e:
    print(e.read().decode("utf-8", "replace")); sys.exit(1)
PY
}

# ---------- 2) 干跑门禁 ----------
echo "[$TS] 干跑校验（不写 OM）..." | tee -a "$LOG"
if ! RESP=$(report false); then
  echo "[$TS] 干跑被拒 ⇒ **不提交作业**。响应：${RESP:0:400}" | tee -a "$LOG"
  exit 3
fi
echo "[$TS] 干跑通过：${RESP:0:200}" | tee -a "$LOG"

# ---------- 3) 提交（可跳过）----------
if [ "$MODE" = "--no-submit" ]; then
  echo "[$TS] --no-submit：跳过提交动作" | tee -a "$LOG"
else
  CMD=${SUBMIT_CMD:-}
  if [ -z "$CMD" ]; then
    docker cp "$JOBDIR/$JOB.sql" "$JM_CT:/tmp/$JOB.sql" >/dev/null || { echo "[$TS] docker cp 失败" | tee -a "$LOG"; exit 4; }
    CMD="docker exec $JM_CT /opt/flink/bin/sql-client.sh -f /tmp/$JOB.sql"
  fi
  echo "[$TS] 提交：$CMD" | tee -a "$LOG"
  if ! eval "$CMD" >>"$LOG" 2>&1; then
    echo "[$TS] 提交命令失败（血缘登记已存在，可由自动上报兜底）" | tee -a "$LOG"
    exit 5
  fi
fi

# ---------- 4) 提交后写血缘 ----------
if ! RESP=$(report true); then
  echo "[$TS] 作业已提交但血缘上报失败 —— **由自动上报兜底**（每日 04:10）。响应：${RESP:0:400}" | tee -a "$LOG"
  exit 6
fi
echo "[$TS] 血缘已上报：${RESP:0:300}" | tee -a "$LOG"
exit 0
