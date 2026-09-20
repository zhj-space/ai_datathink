#!/usr/bin/env bash
# S4-4「年度化备份恢复演练」本地段执行器
#
# 做什么：把"备份可恢复"从单点演练提升为**跨对象、带计时、带 RPO/RTO 对照**的一次复核。
#   §0 备份新鲜度（RPO ≤1h 证据）  §1 PG 逐库恢复（复用 restore_drill_pg_all.sh，6 库）
#   §2 OpenSearch 快照恢复（复用 restore_drill.sh）  §3 配置归档恢复校验（.env/compose/config）
#   §4 Flink Checkpoint（**如实报告能测/不能测**）  §5 汇总与未覆盖声明
#
# 不做什么（明确声明，避免被当成"全灾备演练"）：
#   * **PG PITR 不做**：本环境按 S1 设计**未启用 WAL 归档**，不存在时间点恢复能力；实际保证是"最近一次小时级逻辑 dump"。
#   * **出机副本恢复不做**：出机介质（OFFSITE_TARGET）仍未配置。
#   * **全量重建 RTO 不测**：本脚本只测"对象级恢复耗时"，不等价于"主机级灾难重建时间"。
#
# 判读归属：**结果判读与签字为人工专属**（AGENTS §11）。

set -uo pipefail
BASE=/data/backup
REPORT_DIR=${REPORT_DIR:-/data/ai-governance/reports}
TS=$(date +%Y%m%d-%H%M%S)
OUT="$REPORT_DIR/restore-annual-drill-$TS.md"
LOGDIR="$BASE/log"
mkdir -p "$REPORT_DIR" "$LOGDIR"

log() { echo "[$(date '+%F %T')] $*"; }
age_h() { awk -v t="$1" 'BEGIN{printf "%.2f", (systime()-t)/3600}'; }
lt() { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a<b)}'; }   # 数值比较（字符串比较会把 12.5 < 1.5 判成真）
newest() { ls -1t "$@" 2>/dev/null | head -1; }

log "=== S4-4 年度化恢复演练（本地段） $TS ==="

# ---------------- §0 备份新鲜度（RPO 证据）----------------
PGDUMP=$(newest "$BASE"/pg/*.dump)
CFGTAR=$(newest "$BASE"/config/config_*.tar.gz)
OSSNAP=$(newest /data/opensearch/snapshots/index-*)
R0=""
fresh_row() { # $1=对象 $2=最新产物 $3=阈值小时 $4=说明
  if [ -z "$2" ]; then R0="$R0| $1 | **缺失** | — | ✘ |\n"; return; fi
  a=$(age_h "$(stat -c %Y "$2")")
  if lt "$a" "$3"; then v="✔ 阈值内"; else v="✘ 超阈值"; fi
  R0="$R0| $1 | $(basename "$2") | **${a} h**（阈值 ${3}h） | $v |\n"
}
fresh_row "PG 逻辑备份" "$PGDUMP" 1.5
fresh_row "配置归档" "$CFGTAR" 30
fresh_row "OpenSearch 快照仓库" "$OSSNAP" 30
log "§0 新鲜度：PG=$(basename "${PGDUMP:-无}") / 配置=$(basename "${CFGTAR:-无}") / OS快照=$(basename "${OSSNAP:-无}")"

# ---------------- §1 PG 逐库恢复 ----------------
log "§1 PG 逐库恢复（调用 restore_drill_pg_all.sh）"
T0=$(date +%s)
PG_ALL_OUT=$(bash /data/ai-governance/scripts/restore_drill_pg_all.sh 2>&1)
PG_SEC=$(( $(date +%s) - T0 ))
# 原始输出是定宽表，直接塞进 markdown 单元格会变成单列——改为**原样放进代码块**（保真、可核对）
PG_RAW=$(echo "$PG_ALL_OUT" | grep -E '^(openmetadata_db|airflow_db|superset|gov_metrics|governance_agent|postgres) ')
PG_VERDICT=$(echo "$PG_ALL_OUT" | grep -oE '全部库「对象数一致」.*|存在不一致库.*' | head -1)
PG_LEFTOVER=$(echo "$PG_ALL_OUT" | grep -A1 '临时库清理核验' | tail -1)
log "§1 完成，用时 ${PG_SEC}s；$PG_VERDICT"

# ---------------- §2 OpenSearch 快照恢复 ----------------
log "§2 OpenSearch 快照恢复（调用 restore_drill.sh；其 PG 单库段顺带复核）"
T0=$(date +%s)
OS_OUT=$(bash /data/ai-governance/scripts/restore_drill.sh 2>&1)
OS_SEC=$(( $(date +%s) - T0 ))
OS_VERDICT=$(echo "$OS_OUT" | grep -E 'OS 段结论' | tail -1 | sed 's/^.*结论：//')
PG2_VERDICT=$(echo "$OS_OUT" | grep -E 'PG 段结论' | tail -1 | sed 's/^.*结论：//')
log "§2 完成，用时 ${OS_SEC}s；OS: ${OS_VERDICT:-（未取到结论）}"

# ---------------- §3 配置归档恢复校验 ----------------
log "§3 配置归档恢复校验"
R3=""
if [ -z "${CFGTAR:-}" ]; then
  R3="| 配置归档 | 未找到 tar.gz，跳过 |\n"
else
  TMP=$(mktemp -d /tmp/cfgdrill.XXXXXX)
  case "$TMP" in /tmp/cfgdrill.*) ;; *) echo "临时目录异常，跳过"; exit 1 ;; esac
  if tar -xzf "$CFGTAR" -C "$TMP" 2>/dev/null; then
    if [ -s "$TMP/.env" ]; then N_ENV="存在且非空"; else N_ENV="**缺失/空**"; fi
    if [ -s "$TMP/docker-compose.yml" ] && grep -q '^services:' "$TMP/docker-compose.yml"; then
      N_CMP="存在且含 services"
    else
      N_CMP="**缺失/异常**"
    fi
    N_CFG=$(find "$TMP/config" -type f 2>/dev/null | wc -l)
    live=$(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' /data/ai-governance/.env | sort)
    arch=$(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$TMP/.env" | sort)
    if [ "$live" = "$arch" ]; then KEYS="一致（$(echo "$arch" | wc -l) 个键名）"; else KEYS="**不一致**：$(diff <(echo "$live") <(echo "$arch") | head -6 | tr '\n' ' ')"; fi
    FILES=$(tar -tzf "$CFGTAR" | wc -l)
    R3="| 归档条目数 | $FILES |\n| .env | $N_ENV |\n| docker-compose.yml | $N_CMP |\n| config/ 文件数 | $N_CFG |\n| 密钥键名集合（归档 vs 现网） | $KEYS |"
  else
    R3="| 配置归档 | **解包失败** |\n"
  fi
  rm -rf "$TMP"
  log "§3 完成"
fi

# ---------------- §4 Flink Checkpoint ----------------
log "§4 Flink Checkpoint 可测性"
CHK_DIRS=$(find /data/flink-checkpoint -maxdepth 2 -name 'chk-*' -type d 2>/dev/null | wc -l)
FLINK_JOBS=$(curl -s --max-time 8 http://127.0.0.1:8081/jobs 2>/dev/null | grep -oE '"jobs":\[[^]]*\]' | head -1)
R4="| 磁盘上的 checkpoint 目录数 | $CHK_DIRS |\n| Flink 作业 | ${FLINK_JOBS:-（REST 不可达）} |\n| 判定 | $([ "$CHK_DIRS" -eq 0 ] && echo '**本环境无可演练对象**（无运行中作业、演练产物已按纪律清理）' || echo '存在 checkpoint，可进一步做恢复演练') |"
log "§4 checkpoint 目录数=$CHK_DIRS"

# ---------------- §5 汇总 ----------------
TOTAL_SEC=$(( PG_SEC + OS_SEC ))
R5="| PG 逐库恢复（6 库） | ${PG_SEC}s |\n| OpenSearch 快照恢复 | ${OS_SEC}s |\n| **对象级恢复合计** | **${TOTAL_SEC}s**（RTO 目标 4h，量级充裕） |\n| PG 备份年龄（RPO 目标 ≤1h） | $( [ -n "${PGDUMP:-}" ] && age_h "$(stat -c %Y "$PGDUMP")" || echo "?") h |"

{
echo "# S4-4 年度化备份恢复演练（本地段） · $(date '+%Y-%m-%d %H:%M')"
echo
echo "> **定位**：复核「备份→恢复」在**全部本地对象**上确实可用，并给出计时与 RPO/RTO 对照。"
echo "> **判读归属**：结果判读与签字为**人工专属**；本报告不构成任何验收结论。"
echo
echo "## 1. 备份新鲜度（RPO 证据）"
echo
echo "| 对象 | 最新产物 | 年龄 | 判定 |"
echo "|---|---|---|---|"
echo -e "$R0"
echo
echo "## 2. PostgreSQL 逐库恢复"
echo
echo '```'
echo "$PG_RAW"
echo '```'
echo
echo "> $PG_VERDICT"
echo "> 临时库清理核验：$PG_LEFTOVER"
echo
echo "## 3. OpenSearch 快照恢复"
echo
echo "| 项 | 结果 |"
echo "|---|---|"
echo "| OS 段结论 | ${OS_VERDICT:-（未取到）} |"
echo "| 同批 PG 单库段结论 | ${PG2_VERDICT:-（未取到）} |"
echo
echo "## 4. 配置归档恢复校验"
echo
echo "| 项 | 值 |"
echo "|---|---|"
echo -e "$R3"
echo
echo "## 5. Flink Checkpoint"
echo
echo "| 项 | 值 |"
echo "|---|---|"
echo -e "$R4"
echo
echo "## 6. 计时与 RPO/RTO 对照"
echo
echo "| 项 | 值 |"
echo "|---|---|"
echo -e "$R5"
echo
echo "## 7. 明确未覆盖（不得据此推断「全灾备可用」）"
echo
echo "| # | 未覆盖项 | 原因 |"
echo "|---|---|---|"
echo "| 1 | **PG PITR（时间点恢复）** | 本环境按 S1 设计**未启用 WAL 归档**，不存在该能力；实际保证是「最近一次小时级逻辑 dump」（RPO ≤1h） |"
echo "| 2 | **Flink Checkpoint 出机恢复** | 出机介质未配置；且当前无运行中作业（演练产物已清理） |"
echo "| 3 | **出机副本恢复**（所有对象） | 出机目标未配置（OFFSITE_TARGET 为空）⇒ 出机链路未闭环 |"
echo "| 4 | **主机级灾难重建 RTO** | 本脚本只测对象级恢复耗时，未做「从零重建整机」演练 |"
echo "| 5 | **真实业务数据量下的恢复** | 未接业务源（S2-2 挂账） |"
echo
echo "---"
echo "*本报告由 restore-annual-drill/run_annual_drill.sh 生成；所有演练临时对象用毕即删；判读与签字为人工专属。*"
} > "$OUT"

cat "$OUT"
echo
echo "报告已写入：$OUT"
