#!/usr/bin/env bash
# 作业新鲜度采集（S1-8 备份 / ETL / RAG）→ node_exporter textfile 收集器
#
# 为什么需要：备份与 ETL 的失败是**静默的**（cron 会把错误写日志但没人看）。本脚本把"最近一次成功时间"
# 暴露成指标，由 Prometheus 规则判定"是否过期"，从而把静默失败变成可告警。
#
# 纪律（2026-09-18 实测教训）：
#   * **不要用 `job` 作为标签名**——它与 Prometheus 自身的 `job` 标签冲突，会被重命名为 `exported_job`，
#     导致规则里的 `{job="..."}` 永远匹配不到 ⇒ 告警静默失效。本脚本统一用 **`task`** 标签。
#   * 原子写入（先写 .tmp 再 mv），避免 Prometheus 读到半个文件。
#   * node_exporter 需以 `--collector.textfile.directory=/var/lib/node_exporter/textfile` 启动（见 README）。
#   * **配置文件路径必须与 `backup_offsite.sh` 完全一致**（`/data/ai-governance/backup-offsite.conf`）——
#     曾出现两脚本用了不同文件名（`backup-offsite.conf` vs `offsite.conf`），后果是"人工按文档配好介质后，
#     `ai_governance_job_configured` 仍为 0 ⇒ 出机过期告警**永不触发**"（静默失效）。改路径前先看 backup_offsite.sh。
#   * 出机新鲜度取**本地成功戳**（`backup_offsite.sh` 仅在 rsync 全部成功后才写），不扫描远端介质——
#     每次采集都去 stat 网络挂载点可能挂死或被限流。

set -u
TEXTDIR=${TEXTDIR:-/var/lib/node_exporter/textfile}
OUT="$TEXTDIR/ai_governance_jobs.prom"
TMP="$OUT.$$"
PG_CONTAINER=${PG_CONTAINER:-ai-governance-poc-postgres-1}
CONF=${CONF:-/data/ai-governance/backup-offsite.conf}   # 必须与 backup_offsite.sh 的 CONF 一致
OFFSITE_MARK=${OFFSITE_MARK:-/data/backup/log/offsite_last_success}

newest_epoch() { ls -1t "$@" 2>/dev/null | head -1 | xargs -r stat -c %Y; }
dir_newest_epoch() { find "$1" -maxdepth 1 -mindepth 1 -printf '%T@\n' 2>/dev/null | sort -rn | head -1 | cut -d. -f1; }
psql_epoch() { docker exec "$PG_CONTAINER" psql -U postgres -d "$1" -At -c "$2" 2>/dev/null | head -1; }

# **必须保证每一行的值都是数字**：Prometheus 的 textfile 格式只要有一行值缺失/非数字，
# node_exporter 会**拒收整个文件** ⇒ 所有新鲜度指标一起消失 ⇒ 备份/ETL 的静默失败全都不可观测。
# 取不到就写 0（"从未成功"），让对应规则**明确报出来**，而不是把整张表打哑（2026-09-19 实测踩到）。
num_or0() { case "${1:-}" in ''|*[!0-9]*) echo 0 ;; *) echo "$1" ;; esac; }
metric_epoch() { num_or0 "$(newest_epoch "$@")"; }
metric_dir_epoch() { num_or0 "$(dir_newest_epoch "$1")"; }
metric_psql_epoch() { num_or0 "$(psql_epoch "$1" "$2")"; }

{
  echo "# HELP ai_governance_job_last_success_timestamp_seconds 最近一次成功时间（Unix 秒）"
  echo "# TYPE ai_governance_job_last_success_timestamp_seconds gauge"
  printf 'ai_governance_job_last_success_timestamp_seconds{task="backup_pg"} %s\n'         "$(metric_epoch /data/backup/pg/*.dump)"
  printf 'ai_governance_job_last_success_timestamp_seconds{task="backup_opensearch"} %s\n' "$(metric_dir_epoch /data/opensearch/snapshots)"
  printf 'ai_governance_job_last_success_timestamp_seconds{task="backup_config"} %s\n'     "$(metric_epoch /data/backup/config/*.tar.gz)"
  printf 'ai_governance_job_last_success_timestamp_seconds{task="etl_gov_metrics"} %s\n'   "$(metric_psql_epoch gov_metrics "select extract(epoch from max(run_ts))::bigint from gov_metrics.etl_run where status='success'")"
  printf 'ai_governance_job_last_success_timestamp_seconds{task="etl_rag"} %s\n'           "$(metric_psql_epoch governance_agent 'select extract(epoch from max(updated_at))::bigint from rag.doc')"
  printf 'ai_governance_job_last_success_timestamp_seconds{task="lineage_reconcile"} %s\n' "$(metric_epoch /data/ai-governance/state/lineage_reconcile_last_success)"
  echo "# HELP ai_governance_job_configured 目标介质是否已配置（1/0）；0 = 出机未闭环（已知状态）"
  echo "# TYPE ai_governance_job_configured gauge"
  if [ -f "$CONF" ] && grep -q '^OFFSITE_TARGET=..*' "$CONF"; then OFF=1; else OFF=0; fi
  printf 'ai_governance_job_configured{task="backup_offsite"} %s\n' "$OFF"
  if [ "$OFF" = "1" ]; then
    # 成功戳仅由 backup_offsite.sh 在 rsync 全部成功后写入；缺失即 0（"从未成功"）——宁可误报，不静默
    ts=$(stat -c %Y "$OFFSITE_MARK" 2>/dev/null || true)
    printf 'ai_governance_job_last_success_timestamp_seconds{task="backup_offsite"} %s\n' "${ts:-0}"
  fi
  echo "# HELP ai_governance_job_freshness_collector_last_run_timestamp_seconds 采集器自身最近一次运行时间（Unix 秒）"
  echo "# TYPE ai_governance_job_freshness_collector_last_run_timestamp_seconds gauge"
  printf 'ai_governance_job_freshness_collector_last_run_timestamp_seconds %s\n' "$(date +%s)"
  # 已登记血缘的作业数（D1 登记目录）——供 Flink 侧规则 `FlinkRegisteredJobNotRunning` 用：
  # "登记了作业但 Flink 上没有 RUNNING 作业" = 作业掉了。**必须始终输出数字**（0 也算），否则整个文件会被拒收。
  echo "# HELP ai_governance_lineage_registered_jobs 已登记血缘的作业数（/data/ai-governance/jobs/*.sql）"
  echo "# TYPE ai_governance_lineage_registered_jobs gauge"
  printf 'ai_governance_lineage_registered_jobs %s\n' "$(ls -1 /data/ai-governance/jobs/*.sql 2>/dev/null | wc -l | tr -d ' ')"
} > "$TMP" 2>/dev/null

# 落盘自检：**只要有一行值非数字，node_exporter 会拒收整个文件**（所有新鲜度指标一起消失）。
# 校验不通过时保留上一版文件（它会被判定为"过期"从而告警）并如实报错，绝不覆盖成坏文件。
if grep -qvE '^(#.*|[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? [0-9.eE+-]+)$' "$TMP"; then
  echo "[job_freshness] FATAL: 生成的指标文件含非法行，已保留旧文件不覆盖（避免整表被拒收）" >&2
  grep -nvE '^(#.*|[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? [0-9.eE+-]+)$' "$TMP" >&2
  rm -f "$TMP"
  exit 1
fi
mv -f "$TMP" "$OUT"
chmod 0644 "$OUT"
