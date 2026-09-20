#!/usr/bin/env bash
# 单机容量基线（S4-4「真实负载压测，对照 4.1.2 容量表校准」的**前置自压骨架**）
#
# 定位与声明（务必连读）：
#   * 本脚本产出的是**单机自压基线**，**不是真实业务负载**，不得用于 S3/S4 准出判读；
#   * 全部数值为**工程经验估算的实测锚点**，不是 SLA（设计方案 4.1.3：本环境不满足压测档）；
#   * 本环境门禁①（fio/容量）已由人工豁免，故磁盘子项**不测**；
#   * 不接触任何业务源，不修改任何组件配置；临时对象（库/Topic/索引）一律用完即删。
#
# 覆盖（每条都走与生产一致的路径）：
#   A 宿主资源与运行面   B PG 读写/只读（**经 PgBouncer 6432**，硬约束 2）   C Kafka 生产吞吐
#   D OpenSearch 索引吞吐   E Ollama 推理（冷启/热跑）   F 服务 API 往返时延（含 OM 认证读取）
#
# 用法：bash run_baseline.sh [--quick]

set -u
umask 022

QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

ENVF=/data/ai-governance/.env
REPORT_DIR=${REPORT_DIR:-/data/ai-governance/reports}
PG_CT=ai-governance-poc-postgres-1
KAFKA_CT=ai-governance-poc-kafka-1
OS_CT=ai-governance-poc-opensearch-1
OLLAMA=http://127.0.0.1:11434
OM=http://127.0.0.1:8585

PG_SCALE=${PG_SCALE:-2}
PG_TIME=$(( QUICK ? 15 : 30 ))
PG_CLIENTS=${PG_CLIENTS:-8}
KAFKA_RECORDS=${KAFKA_RECORDS:-300000}
KAFKA_SIZE=${KAFKA_SIZE:-1024}
OS_DOCS=${OS_DOCS:-50000}
TMPDB=bench_baseline
TMPTOPIC=bench.baseline
TMPIDX=bench-baseline

TS=$(date +%Y%m%d-%H%M%S)
OUT="$REPORT_DIR/capacity-baseline-$TS.md"
mkdir -p "$REPORT_DIR"
SELF_DIR=$(cd "$(dirname "$0")" && pwd)

log() { echo "[$(date '+%F %T')] $*"; }
die() { log "ERROR: $*"; exit 1; }
num() { echo "$1" | grep -oE '[0-9]+([.][0-9]+)?' | head -1; }

# ---- 凭据：仅在服务器侧读取，不回显、不入仓库 ----
[ -f "$ENVF" ] || die "缺少 $ENVF"
set -a; . "$ENVF"; set +a
: "${POSTGRES_PASSWORD:?}"; : "${OPENSEARCH_INITIAL_ADMIN_PASSWORD:?}"

# ---- 临时对象清理（只认本脚本创建的固定名字）----
# 坑：经 PgBouncer 建库后，池里会保留到该库的服务端连接 ⇒ DROP DATABASE 报"database is being accessed
# by other users"。必须先 RECONNECT 回收池连接（与 AGENTS §7「权限变更后必须回收池连接」同一条纪律）。
# 注意：RECONNECT 会短暂断开所有客户端，POC 期可接受（客户端会自动重连）。
pg_reconnect() {
  docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$PG_CT" \
    psql -h pgbouncer -p 6432 -U postgres -d pgbouncer -Atc "RECONNECT" >/dev/null 2>&1
  sleep 1
}
drop_bench_db() {
  docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$PG_CT" \
    psql -h pgbouncer -p 6432 -U postgres -d postgres -Atc "drop database if exists $TMPDB" >/dev/null 2>&1
}
db_exists() {
  docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$PG_CT" psql -h pgbouncer -p 6432 -U postgres -d postgres \
    -Atc "select 1 from pg_database where datname='$TMPDB'" 2>/dev/null | grep -q 1
}
ensure_db_gone() {   # 建库前 / 收尾都要用：单纯 DROP 会被池内连接挡住
  drop_bench_db
  if db_exists; then
    pg_reconnect
    drop_bench_db
  fi
  if db_exists; then
    return 1
  fi
  return 0
}
cleanup() {
  ensure_db_gone || true
  docker exec "$KAFKA_CT" /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 \
    --delete --topic "$TMPTOPIC" >/dev/null 2>&1
  docker exec -e OS_PWD="$OPENSEARCH_INITIAL_ADMIN_PASSWORD" "$OS_CT" sh -c \
    'curl -sk -u "admin:$OS_PWD" -X DELETE "https://localhost:9200/'"$TMPIDX"'-*" >/dev/null' 2>/dev/null
}
trap cleanup EXIT

# ---------- A. 宿主资源与运行面 ----------
log "A 宿主资源与运行面"
A_CPU=$(nproc)
A_MEM=$(free -m | awk '/^Mem:/{print $2}')
A_AVAIL=$(df -Pm / | awk 'NR==2{print $4}')
A_LOAD=$(awk '{print $1}' /proc/loadavg)
A_CONT=$(docker ps -q | wc -l)
A_UNHEALTHY=$(docker ps --format '{{.Status}}' | grep -vc healthy || true)
A_IMAGES=$(docker system df --format '{{.Type}}|{{.Size}}|{{.Reclaimable}}' | awk -F'|' '$1=="Images"{print $2"（可回收 "$3"）"}')
R_HOST="| CPU 核数 | $A_CPU |\n| 内存总量 / 根分区可用 | ${A_MEM} MiB / ${A_AVAIL} MiB |\n| load1（压测前） | $A_LOAD |\n| 容器数 / 非 healthy | $A_CONT / $A_UNHEALTHY |\n| 镜像占用 | $A_IMAGES |"

# ---------- B. PG（经 PgBouncer 6432）----------
log "B PostgreSQL（经 PgBouncer 6432；scale=$PG_SCALE 时长=${PG_TIME}s 客户端=$PG_CLIENTS）"
pgc() { docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$PG_CT" "$@"; }
ensure_db_gone || die "无法清理既有临时库 $TMPDB（PgBouncer 池连接未释放）"
pgc psql -h pgbouncer -p 6432 -U postgres -d postgres -Atc "create database $TMPDB" >/dev/null 2>&1 \
  || die "建库失败（经 PgBouncer 6432）"
B_INIT=$(pgc pgbench -h pgbouncer -p 6432 -U postgres -i -s "$PG_SCALE" "$TMPDB" 2>&1 | tail -1)
B_RW=$(pgc pgbench -h pgbouncer -p 6432 -U postgres -c "$PG_CLIENTS" -j 4 -M simple -T "$PG_TIME" "$TMPDB" 2>&1)
B_RO=$(pgc pgbench -h pgbouncer -p 6432 -U postgres -S -c $((PG_CLIENTS*2)) -j 8 -M simple -T "$PG_TIME" "$TMPDB" 2>&1)
RW_TPS=$(num "$(echo "$B_RW" | grep -oE 'tps = [0-9.]+' | head -1)")
RW_LAT=$(num "$(echo "$B_RW" | grep -oE 'latency average = [0-9.]+' | head -1)")
RO_TPS=$(num "$(echo "$B_RO" | grep -oE 'tps = [0-9.]+' | head -1)")
RO_LAT=$(num "$(echo "$B_RO" | grep -oE 'latency average = [0-9.]+' | head -1)")
PG_INIT_MSG="${B_INIT:-N/A}"
R_PG="| 初始化 pgbench -i -s $PG_SCALE | $PG_INIT_MSG |\n| 读写混合（$PG_CLIENTS 客户端 / ${PG_TIME}s） | **${RW_TPS:-N/A} tps**，平均延迟 ${RW_LAT:-N/A} ms |\n| 只读（$((PG_CLIENTS*2)) 客户端 / ${PG_TIME}s） | **${RO_TPS:-N/A} tps**，平均延迟 ${RO_LAT:-N/A} ms |"
R_PG_NOTE="经 PgBouncer transaction 模式，刻意用 -M simple（避免扩展协议）；与门禁③ 的 prepared statement 口径无关。"

# ---------- C. Kafka 生产吞吐 ----------
log "C Kafka 生产吞吐（${KAFKA_RECORDS} 条 x ${KAFKA_SIZE}B）"
K_CREATE=$(docker exec "$KAFKA_CT" /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 \
  --create --if-not-exists --topic "$TMPTOPIC" --partitions 1 --replication-factor 1 2>&1 | tail -1)
K_OUT=$(docker exec "$KAFKA_CT" /opt/kafka/bin/kafka-producer-perf-test.sh \
  --topic "$TMPTOPIC" --num-records "$KAFKA_RECORDS" --record-size "$KAFKA_SIZE" --throughput -1 \
  --producer-props bootstrap.servers=localhost:9092 acks=1 2>&1 | tail -2)
K_RPS=$(num "$(echo "$K_OUT" | grep -oE '[0-9.]+ records/sec' | head -1)")
K_MBS=$(num "$(echo "$K_OUT" | grep -oE '[0-9.]+ MB/sec' | head -1)")
K_P95=$(num "$(echo "$K_OUT" | grep -oE '[0-9.]+ ms 95th' | head -1)")
R_KAFKA="| 建 Topic（1 分区 / RF1） | ${K_CREATE:-N/A} |\n| 生产 ${KAFKA_RECORDS} 条 x ${KAFKA_SIZE}B（acks=1） | **${K_RPS:-N/A} records/s**，**${K_MBS:-N/A} MB/s** |\n| 生产延迟 p95 | ${K_P95:-N/A} ms |"

# ---------- D. OpenSearch 索引吞吐 ----------
log "D OpenSearch 索引吞吐（${OS_DOCS} 文档）"
OS_PY="$SELF_DIR/os_bulk_bench.py"
if [ -f "$OS_PY" ]; then
  docker cp "$OS_PY" "$OS_CT":/tmp/os_bulk_bench.py >/dev/null 2>&1
  OS_JSON=$(docker exec -e OS_PWD="$OPENSEARCH_INITIAL_ADMIN_PASSWORD" -e OS_DOCS="$OS_DOCS" -e OS_IDX="$TMPIDX" \
    "$OS_CT" python3 /tmp/os_bulk_bench.py 2>&1 | tail -1)
  osf() { echo "$OS_JSON" | python3 -c "import json,sys;d=json.loads(sys.stdin.read());print(d.get('$1',''))" 2>/dev/null; }
  OS_EL=$(osf elapsed); OS_BYTES=$(osf bytes); OS_ERRS=$(osf errors); OS_TOOK=$(osf took)
  OS_CNT=$(osf count); OS_OKITEMS=$(osf ok_items); OS_FATAL=$(osf fatal); OS_ERR1=$(osf first_error); OS_REFRESH=$(osf refresh_s)
  OS_RATE=""
  [ -n "${OS_EL:-}" ] && OS_RATE=$(awk -v n="$OS_DOCS" -v t="$OS_EL" 'BEGIN{if(t+0>0) printf "%.0f", n/t}')
  R_OS="| 写入 ${OS_DOCS} 文档（单分片 0 副本，单次 bulk，refresh=false） | **${OS_RATE:-N/A} docs/s**（耗时 ${OS_EL:-N/A}s，报文 ${OS_BYTES:-N/A}B） |\n| 响应 | 集群 took ${OS_TOOK:-N/A} ms；errors=${OS_ERRS:-N/A}；成功条目 ${OS_OKITEMS:-N/A} |\n| 落库校验（_refresh ${OS_REFRESH:-N/A}s 后 _count） | ${OS_CNT:-N/A} / ${OS_DOCS} |"
  [ -n "${OS_FATAL:-}" ] && R_OS="$R_OS\n| 异常 | ${OS_FATAL} |"
  case "${OS_ERR1:-}" in ""|None) ;; *) R_OS="$R_OS\n| 首个错误 | ${OS_ERR1} |" ;; esac
  docker exec -u root "$OS_CT" rm -f /tmp/os_bulk_bench.py >/dev/null 2>&1
else
  R_OS="| （缺少 $OS_PY，已跳过） | |"
fi

# ---------- E. Ollama 推理 ----------
log "E Ollama 推理（冷启 + 热跑，两轮用不同 prompt 规避 cache）"
oll() {
  curl -s --max-time 300 "$OLLAMA/api/generate" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$1\",\"prompt\":\"$2\",\"stream\":false,\"keep_alive\":\"0s\",\"options\":{\"num_predict\":128}}" \
    | python3 -c 'import json,sys;d=json.load(sys.stdin);ld=d.get("load_duration",0) or 0;pd=d.get("prompt_eval_duration",0) or 0;ec=d.get("eval_count",0) or 0;ed=d.get("eval_duration",0) or 0;print("%.2f %.1f %d"%((ld+pd)/1e9, (ec/(ed/1e9) if ed else 0), ec))' 2>/dev/null \
    || echo "N/A N/A 0"
}
MODELS=$(curl -s "$OLLAMA/api/tags" | python3 -c 'import json,sys;print(" ".join(m["name"] for m in json.load(sys.stdin).get("models",[])))' 2>/dev/null || echo "")
R_OLLAMA=""
for m in ${MODELS:-}; do
  case "$m" in qwen2.5:1.5b|qwen2.5:3b) ;; *) continue ;; esac
  r1=$(oll "$m" "基线冷启 $(date +%s%N)：用一句话说明数据血缘的用途。")
  r2=$(oll "$m" "基线热跑 $(date +%s%N)：用一句话说明快照基线与增量窗口的区别。")
  R_OLLAMA="${R_OLLAMA}| $m | 冷启：近似首 token $(echo "$r1" | cut -d' ' -f1)s / 生成 $(echo "$r1" | cut -d' ' -f2) tok/s；热跑：近似首 token $(echo "$r2" | cut -d' ' -f1)s / 生成 $(echo "$r2" | cut -d' ' -f2) tok/s（$(echo "$r2" | cut -d' ' -f3) tokens） |\n"
done
[ -n "$R_OLLAMA" ] || R_OLLAMA="| （未找到 qwen2.5:1.5b / 3b，已跳过） | |\n"

# ---------- F. 服务 API 往返 ----------
log "F 服务 API 往返时延"
OK_HDR=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$OM/health" 2>/dev/null || echo 000)
[ "$OK_HDR" = "200" ] || die "OM /health 不可达（$OK_HDR）"
R_API=""
while IFS='|' read -r nm url; do
  [ -z "$nm" ] && continue
  res=$(curl -s -o /dev/null -w '%{http_code}  %{time_total}s' --max-time 15 "$url" 2>/dev/null || echo "000  0s")
  R_API="${R_API}| $nm | $res |\n"
done <<'EOT'
OM /health|http://127.0.0.1:8585/health
OM /api/v1/system/version|http://127.0.0.1:8585/api/v1/system/version
GA /health|http://127.0.0.1:8085/health
GA /ready|http://127.0.0.1:8085/ready
Superset /health|http://127.0.0.1:8088/health
门户 /healthz|http://127.0.0.1:8501/healthz
Prometheus /-/healthy|http://127.0.0.1:9090/-/healthy
EOT
OM_NOTE="未测 OM 认证读取（缺 OPENMETADATA_ADMIN_PASSWORD）"
if [ -n "${OPENMETADATA_ADMIN_PASSWORD:-}" ]; then
  B64=$(printf '%s' "$OPENMETADATA_ADMIN_PASSWORD" | base64 -w0)
  TOK=$(curl -s --max-time 15 -X POST "$OM/api/v1/users/login" -H 'Content-Type: application/json' \
    -d "{\"email\":\"admin@open-metadata.org\",\"password\":\"$B64\"}" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin).get("accessToken",""))' 2>/dev/null || echo "")
  if [ -n "${TOK:-}" ]; then
    t=$(curl -s -o /dev/null -w '%{time_total}' --max-time 20 -H "Authorization: Bearer $TOK" "$OM/api/v1/tables?limit=10")
    R_API="${R_API}| OM 认证读取 /tables?limit=10 | 200  ${t}s |\n"
    OM_NOTE="已含 OM 认证读取（基础 token，非 SSO）"
  else
    OM_NOTE="OM 登录失败，认证路径未测"
  fi
fi

# ---------- 清理与残留核查 ----------
log "清理临时对象"
cleanup; trap - EXIT
LEFTOVER=""
pgc psql -h pgbouncer -p 6432 -U postgres -d postgres -Atc "select 1 from pg_database where datname='$TMPDB'" 2>/dev/null \
  | grep -q 1 && LEFTOVER="$LEFTOVER DB"
docker exec "$KAFKA_CT" /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list 2>/dev/null \
  | grep -qx "$TMPTOPIC" && LEFTOVER="$LEFTOVER TOPIC"
[ -n "$LEFTOVER" ] || LEFTOVER="无"
AFTER_HEALTH=$(docker ps --format '{{.Status}}' | grep -vc healthy || true)

R_RUN="| 临时对象残留 | ${LEFTOVER}（目标名：$TMPDB / $TMPTOPIC / $TMPIDX-*） |\n| 压测后非 healthy 容器 | $AFTER_HEALTH |\n| 报告文件 | $OUT |"

{
echo "# 单机容量基线（自压） · $(date '+%Y-%m-%d %H:%M')"
echo
echo "> **口径声明**：本报告是**单机自压基线**，**不是真实业务负载**；数值为**工程经验估算的实测锚点**，**不是 SLA**。"
echo "> 本环境门禁①（fio/容量）已由人工豁免（设计方案 4.1.3：本环境不满足压测档），故**不得**用本报告做准出判读。"
echo "> 参数：PG scale=$PG_SCALE / 时长 ${PG_TIME}s / 客户端 $PG_CLIENTS；Kafka ${KAFKA_RECORDS}x${KAFKA_SIZE}B；OS ${OS_DOCS} 文档；$([ "$QUICK" = 1 ] && echo '模式 quick' || echo '模式 标准')。"
echo
echo "## 1. 宿主与运行面"
echo
echo "| 项 | 值 |"
echo "|---|---|"
echo -e "$R_HOST"
echo
echo "## 2. PostgreSQL（经 PgBouncer 6432）"
echo
echo "| 项 | 值 |"
echo "|---|---|"
echo -e "$R_PG"
echo
echo "> $R_PG_NOTE"
echo
echo "## 3. Kafka 生产吞吐"
echo
echo "| 项 | 值 |"
echo "|---|---|"
echo -e "$R_KAFKA"
echo
echo "## 4. OpenSearch 索引吞吐"
echo
echo "| 项 | 值 |"
echo "|---|---|"
echo -e "$R_OS"
echo
echo "## 5. Ollama 推理"
echo
echo "| 模型 | 结果 |"
echo "|---|---|"
echo -e "$R_OLLAMA"
echo
echo "> 非流式接口无法直接给首 token：表中「近似首 token」 = load_duration + prompt_eval_duration；"
echo "> 冷启/热跑使用**不同 prompt**（含纳秒后缀）以规避 prompt cache 失真（M0 第 6 项的实测教训）。"
echo
echo "## 6. 服务 API 往返时延"
echo
echo "| 端点 | HTTP / 耗时 |"
echo "|---|---|"
echo -e "$R_API"
echo
echo "> $OM_NOTE；单次采样，仅代表连通性与本地链路量级，**不代表业务查询时延**。"
echo
echo "## 7. 现场与残留"
echo
echo "| 项 | 值 |"
echo "|---|---|"
echo -e "$R_RUN"
echo
echo "---"
echo "*本报告由 capacity-baseline/run_baseline.sh 生成；临时对象已清理；不接触业务源、不改组件配置。*"
} > "$OUT"

cat "$OUT"
echo
echo "报告已写入：$OUT"
