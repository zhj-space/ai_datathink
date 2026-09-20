#!/usr/bin/env bash
# 通用容器运行面采集（全部运行中容器的 CPU / 内存 / OOM）→ node_exporter textfile 收集器
#
# 为什么需要（2026-09-19 实测）：
#   本环境的 **cAdvisor 拿不到逐容器指标**——Docker 29.8.1 使用 **containerd 镜像存储**
#   （`driver-type: io.containerd.snapshotter.v1`），`/var/lib/docker/image/overlayfs/layerdb/mounts/<id>/mount-id` 不存在，
#   而现役 cAdvisor 是 **v0.45.0（2022-09-23 构建）**，日志持续报
#   `Failed to create existing container … failed to identify the read-write layer ID …` ⇒ **只导出根 cgroup**；
#   实测 `/api/v1/label/id/values` 仅 `["/","1","ubuntu"]`、逐容器序列 0 条（对比：`docker stats` 能看到容器 ⇒ 不是容器的问题）。
#   升级 cAdvisor 需要可用镜像源，而本环境**镜像源被策略拦截/不可达**（DaoCloud 拦截非白名单仓库、gcr/quay 不可达、
#   可用镜像站上 zcube/cadvisor 最高只到 v0.45.0）⇒ **改为自采**（与 `job_freshness.sh`/`ollama_probe.sh` 同一套机制，不引入新组件）。
#
# 数据来源：宿主 **cgroup v2**（systemd driver）
#   /sys/fs/cgroup/system.slice/docker-<容器ID>.scope/{cpu.stat,cpu.max,memory.current,memory.max,memory.stat,memory.events}
# 标签口径：`container` = compose 服务名（无则容器名）、`name` = 容器名；**不得用 `job`**（与 Prometheus 冲突会被改名 ⇒ 规则静默失效）。
# 纪律：原子写入 + 逐行格式自检（任一非法行会让 node_exporter **拒收整个文件**）；独立文件，坏行不牵连其它采集器。

set -u
TEXTDIR=${TEXTDIR:-/var/lib/node_exporter/textfile}
OUT="$TEXTDIR/ai_governance_containers.prom"
TMP="$OUT.$$"
CGROUP_ROOT=${CGROUP_ROOT:-/sys/fs/cgroup/system.slice}
LIST=$(mktemp /tmp/container_probe.XXXXXX)
trap 'rm -f "$LIST" "$TMP"' EXIT

num() { case "${1:-}" in ''|*[!0-9.eE+-]*) echo 0 ;; *) echo "$1" ;; esac; }
esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

# 容器清单：ID | 名称 | compose 服务名
docker ps --no-trunc --format '{{.ID}}|{{.Names}}|{{.Label "com.docker.compose.service"}}' > "$LIST" 2>/dev/null || true
total=$(grep -c . "$LIST" || true)
now=$(date +%s)

# 逐容器取值（只保留 cgroup 目录存在的容器）
cg_of() { printf '%s/docker-%s.scope' "$CGROUP_ROOT" "$1"; }

{
  echo "# HELP ai_governance_container_cpu_usage_seconds_total 容器累计 CPU 时间（cgroup v2 cpu.stat usage_usec）"
  echo "# TYPE ai_governance_container_cpu_usage_seconds_total counter"
  while IFS='|' read -r cid name svc; do
    [ -d "$(cg_of "$cid")" ] || continue
    u=$(awk '$1=="usage_usec"{print $2; exit}' "$(cg_of "$cid")/cpu.stat" 2>/dev/null || echo '')
    awk -v u="$(num "$u")" -v c="$(esc "${svc:-$name}")" -v n="$(esc "$name")" \
      'BEGIN{ printf "ai_governance_container_cpu_usage_seconds_total{container=\"%s\",name=\"%s\"} %.6f\n", c, n, u/1000000 }'
  done < "$LIST"

  echo "# HELP ai_governance_container_cpu_limit_cores 容器 CPU 限额（cgroup v2 cpu.max quota/period；0=未设限）"
  echo "# TYPE ai_governance_container_cpu_limit_cores gauge"
  while IFS='|' read -r cid name svc; do
    [ -d "$(cg_of "$cid")" ] || continue
    q=$(awk '{print $1; exit}' "$(cg_of "$cid")/cpu.max" 2>/dev/null || echo '')
    p=$(awk '{print $2; exit}' "$(cg_of "$cid")/cpu.max" 2>/dev/null || echo '')
    if [ -n "$q" ] && [ "$q" != "max" ] && [ -n "$p" ]; then
      v=$(awk -v q="$q" -v p="$p" 'BEGIN{ if (p+0>0) printf "%.4f", q/p; else print "0" }')
    else
      v=0
    fi
    printf 'ai_governance_container_cpu_limit_cores{container="%s",name="%s"} %s\n' "$(esc "${svc:-$name}")" "$(esc "$name")" "$v"
  done < "$LIST"

  echo "# HELP ai_governance_container_memory_current_bytes 容器当前内存占用（cgroup v2 memory.current，含页缓存）"
  echo "# TYPE ai_governance_container_memory_current_bytes gauge"
  while IFS='|' read -r cid name svc; do
    [ -d "$(cg_of "$cid")" ] || continue
    printf 'ai_governance_container_memory_current_bytes{container="%s",name="%s"} %s\n' \
      "$(esc "${svc:-$name}")" "$(esc "$name")" "$(num "$(cat "$(cg_of "$cid")/memory.current" 2>/dev/null || echo '')")"
  done < "$LIST"

  echo "# HELP ai_governance_container_memory_max_bytes 容器内存上限（cgroup v2 memory.max；0=未设限）"
  echo "# TYPE ai_governance_container_memory_max_bytes gauge"
  while IFS='|' read -r cid name svc; do
    [ -d "$(cg_of "$cid")" ] || continue
    m=$(cat "$(cg_of "$cid")/memory.max" 2>/dev/null || echo '')
    case "$m" in ''|max) v=0 ;; *) v=$(num "$m") ;; esac
    printf 'ai_governance_container_memory_max_bytes{container="%s",name="%s"} %s\n' "$(esc "${svc:-$name}")" "$(esc "$name")" "$v"
  done < "$LIST"

  echo "# HELP ai_governance_container_memory_anon_bytes 容器匿名内存（cgroup v2 memory.stat anon；不可回收，用于区分「真占用」与页缓存）"
  echo "# TYPE ai_governance_container_memory_anon_bytes gauge"
  while IFS='|' read -r cid name svc; do
    [ -d "$(cg_of "$cid")" ] || continue
    printf 'ai_governance_container_memory_anon_bytes{container="%s",name="%s"} %s\n' \
      "$(esc "${svc:-$name}")" "$(esc "$name")" "$(num "$(awk '$1=="anon"{print $2; exit}' "$(cg_of "$cid")/memory.stat" 2>/dev/null || echo '')")"
  done < "$LIST"

  echo "# HELP ai_governance_container_memory_file_bytes 容器页缓存（cgroup v2 memory.stat file；**可回收**）"
  echo "# TYPE ai_governance_container_memory_file_bytes gauge"
  while IFS='|' read -r cid name svc; do
    [ -d "$(cg_of "$cid")" ] || continue
    printf 'ai_governance_container_memory_file_bytes{container="%s",name="%s"} %s\n' \
      "$(esc "${svc:-$name}")" "$(esc "$name")" "$(num "$(awk '$1=="file"{print $2; exit}' "$(cg_of "$cid")/memory.stat" 2>/dev/null || echo '')")"
  done < "$LIST"

  echo "# HELP ai_governance_container_oom_kill_total 容器 cgroup 累计 OOM kill 次数（cgroup v2 memory.events oom_kill）"
  echo "# TYPE ai_governance_container_oom_kill_total counter"
  while IFS='|' read -r cid name svc; do
    [ -d "$(cg_of "$cid")" ] || continue
    printf 'ai_governance_container_oom_kill_total{container="%s",name="%s"} %s\n' \
      "$(esc "${svc:-$name}")" "$(esc "$name")" "$(num "$(awk '$1=="oom_kill"{print $2; exit}' "$(cg_of "$cid")/memory.events" 2>/dev/null || echo '')")"
  done < "$LIST"

  echo "# HELP ai_governance_container_probe_containers 本次采集到的容器数（用于与指标序列数比对，发现「采了一半」）"
  echo "# TYPE ai_governance_container_probe_containers gauge"
  printf 'ai_governance_container_probe_containers %s\n' "$(num "$total")"
  echo "# HELP ai_governance_container_probe_last_run_timestamp_seconds 本采集器最近一次运行时间（Unix 秒）"
  echo "# TYPE ai_governance_container_probe_last_run_timestamp_seconds gauge"
  printf 'ai_governance_container_probe_last_run_timestamp_seconds %s\n' "$now"
} > "$TMP" 2>/dev/null

# 落盘自检：任一非法行会让 node_exporter 拒收整个文件 ⇒ 保留旧文件并报错，绝不覆盖成坏文件
if grep -qvE '^(#.*|[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? [0-9.eE+-]+)$' "$TMP"; then
  echo "[container_probe] FATAL: 生成的指标文件含非法行，已保留旧文件不覆盖" >&2
  grep -nvE '^(#.*|[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? [0-9.eE+-]+)$' "$TMP" >&2
  exit 1
fi
# 序列数自检：采到的容器数应等于 memory_current 的序列数（否则说明有容器没读到 cgroup）
series=$(grep -c '^ai_governance_container_memory_current_bytes{' "$TMP" || true)
if [ "$series" -lt "$total" ]; then
  echo "[container_probe] WARN: 容器数 $total 但内存序列只有 $series（有容器未读到 cgroup，规则 ContainerSeriesIncomplete 会报出）" >&2
fi
mv -f "$TMP" "$OUT"
chmod 0644 "$OUT"
