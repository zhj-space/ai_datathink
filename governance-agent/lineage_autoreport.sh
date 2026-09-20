#!/usr/bin/env bash
# D1 作业图级血缘 · **自动上报**（cron 每日 04:10；也是提交脚本的兜底）
#
# 为什么需要它："提交即上报"依赖提交方自觉——**漏报会让血缘静默缺失**。
# 本脚本以**登记目录**为唯一真相：`/data/ai-governance/jobs/<job_key>.sql`；
# 登记了就会每日重新上报一次（幂等 upsert），提交时漏报也会在这里被补上。
#
# 流程：遍历登记目录 → 逐个 report(apply=true) → **全部成功才**调用 `lineage_reconcile.sh`
#        （对账 + 写成功戳）；任一失败 ⇒ 退出码非 0 且**不更新成功戳**
#        ⇒ `LineageReconcileStale` 会在 30h 内把它报出来（对账链静默失败＝没做）。
#
# 用法：
#   lineage_autoreport.sh              # 全量：登记作业逐个上报 + 对账
#   lineage_autoreport.sh --dry-run    # 只解析校验，不写 OM、不写库
set -uo pipefail
GA=${GA_URL:-http://127.0.0.1:8085}
ENVF=/data/ai-governance/.env
JOBDIR=/data/ai-governance/jobs
LOG=/data/backup/log/lineage_autoreport.log
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
mkdir -p "$JOBDIR" "$(dirname "$LOG")"
TOKEN=$(grep -m1 '^GA_INGEST_TOKEN=' "$ENVF" | cut -d= -f2-)
TS=$(date '+%F %T')
APPLY=true
[ "$DRY" = "1" ] && APPLY=false

shopt -s nullglob
JOBS=("$JOBDIR"/*.sql)
if [ ${#JOBS[@]} -eq 0 ]; then
  echo "[$TS] 登记目录为空（尚无作业登记）——无事可做" | tee -a "$LOG"
  if [ "$DRY" = "1" ]; then exit 0; fi
  /data/ai-governance/scripts/lineage_reconcile.sh
  exit $?
fi

FAIL=0
for f in "${JOBS[@]}"; do
  job=$(basename "$f" .sql)
  # mapping：优先 <job_key>.mapping.json，其次共享 mapping.json；都没有则空对象（会因缺 service 被拒）
  if [ -f "$JOBDIR/$job.mapping.json" ]; then MAP="$JOBDIR/$job.mapping.json"
  elif [ -f "$JOBDIR/mapping.json" ]; then MAP="$JOBDIR/mapping.json"
  else MAP=""; fi

  RESP=$(python3 - "$GA" "$TOKEN" "$job" "$f" "$MAP" "$APPLY" <<'PY'
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
)
  RC=$?
  echo "[$TS] job=$job apply=$APPLY rc=$RC resp=${RESP:0:400}" >> "$LOG"
  if [ "$RC" != "0" ]; then
    echo "[$TS] 未成功：$job 上报被拒/失败（见日志响应）" | tee -a "$LOG"
    FAIL=1
    continue
  fi
  VERDICT=$(printf '%s' "$RESP" | python3 -c 'import json,sys;d=json.load(sys.stdin);a=d.get("added",[]);print("failed" if any(x.get("status")=="failed" for x in a) else ("entity_missing" if any(x.get("status")=="entity_missing" for x in a) else "ok"))' 2>/dev/null || echo badjson)
  echo "[$TS] $job verdict=$VERDICT" >> "$LOG"
  [ "$VERDICT" = "ok" ] || FAIL=1
done

if [ "$DRY" = "1" ]; then
  echo "[$TS] dry-run 完成；FAIL=$FAIL" | tee -a "$LOG"
  exit "$FAIL"
fi

if [ "$FAIL" != "0" ]; then
  echo "[$TS] 存在未成功的作业 ⇒ **跳过对账、不更新成功戳**（由 LineageReconcileStale 报出）" | tee -a "$LOG"
  exit 1
fi
echo "[$TS] 全部登记作业上报完成 ⇒ 进入对账" | tee -a "$LOG"
/data/ai-governance/scripts/lineage_reconcile.sh
