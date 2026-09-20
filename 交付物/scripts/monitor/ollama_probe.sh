#!/usr/bin/env bash
# Ollama 运行面采集（**Ollama API / 模型驻留面**）→ node_exporter textfile 收集器
#
# 为什么需要：
#   * S4-1 纪律要求 Ollama「并发限流，**不得拖慢实时告警链路**」，但 Ollama 0.34.2 **没有 /metrics 端点**
#     （实测 `GET /metrics` = 404），Prometheus 侧 **ollama 抓取目标 = 0** ⇒ 运行面完全不可观测。
#   * **2026-09-19 拆分**：容器级 CPU/内存/OOM 指标移交 **`container_probe.sh`（全容器通用）**，
#     本脚本只负责 **Ollama API 面**（`/api/ps`：模型是否驻留、卸载期限、占用大小、API 可达性）。
#     原因：cAdvisor 在本环境拿不到逐容器指标，需自采；自采做成**通用**的比只做 Ollama 更有价值，
#     且**同一指标只能有一个生产者**（否则同一 series 重复采样会让 Prometheus 抓取直接失败）。
#
# 数据来源：Ollama REST `GET /api/ps`（仅回环 11434）
#
# 纪律（沿用 job_freshness.sh 的实测教训）：
#   * **自定义标签不得用 `job`**（与 Prometheus 冲突会被改名 exported_job ⇒ 规则静默失效）。
#   * **每一行必须是数字**：任一非法行会让 node_exporter 拒收**整个文件**；取值失败写 0 并靠规则暴露。
#   * **独立文件**：与 job_freshness.sh / container_probe.sh 分开写（`ai_governance_ollama_runtime.prom`），
#     避免一个采集器的坏行把其它指标一起打哑。
#   * 原子写入（先写 .tmp 再 mv）+ 落盘自检；自检不过**保留旧文件**并报错退出。
#   * 容器不存在时**不覆盖旧文件**，由 `OllamaProbeStale` 报「运行面不可观测」。

set -u
TEXTDIR=${TEXTDIR:-/var/lib/node_exporter/textfile}
OUT="$TEXTDIR/ai_governance_ollama_runtime.prom"
TMP="$OUT.$$"
CONTAINER=${CONTAINER:-ai-governance-poc-ollama-1}
OLLAMA_URL=${OLLAMA_URL:-http://127.0.0.1:11434}

num() { case "${1:-}" in ''|*[!0-9.eE+-]*) echo 0 ;; *) echo "$1" ;; esac; }

# ---------- 前置：Ollama 容器必须存在 ----------
CID=$(docker inspect -f '{{.Id}}' "$CONTAINER" 2>/dev/null || true)
if [ -z "$CID" ]; then
  echo "[ollama_probe] FATAL: 容器 $CONTAINER 不存在或不可查（保留旧文件，让 OllamaProbeStale 报出来）" >&2
  exit 1
fi

# ---------- Ollama API /api/ps ----------
ps_json=$(curl -s --max-time 5 "$OLLAMA_URL/api/ps" 2>/dev/null || echo '')
if printf '%s' "$ps_json" | jq -e '.models' >/dev/null 2>&1; then
  ollama_up=1
else
  ollama_up=0
  ps_json='{"models":[]}'
fi

esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
now=$(date +%s)

# ---------- 落盘 ----------
{
  # 容器级 CPU/内存/OOM 指标自 2026-09-19 起由 `container_probe.sh`（全容器通用）统一产出，
  # 本文件不再输出 —— **同一 series 只能有一个生产者**，否则重复采样会让 Prometheus 抓取失败。
  echo "# HELP ai_governance_ollama_up Ollama /api/ps 是否可达（1/0）"
  echo "# TYPE ai_governance_ollama_up gauge"
  printf 'ai_governance_ollama_up %s\n' "$ollama_up"
  echo "# HELP ai_governance_ollama_loaded_models 当前驻留内存的模型数（/api/ps）"
  echo "# TYPE ai_governance_ollama_loaded_models gauge"
  printf 'ai_governance_ollama_loaded_models %s\n' "$(printf '%s' "$ps_json" | jq -r '.models | length' 2>/dev/null || echo 0)"
  echo "# HELP ai_governance_ollama_model_loaded 该模型当前是否驻留内存（1=驻留；无请求时不输出该序列）"
  echo "# TYPE ai_governance_ollama_model_loaded gauge"
  echo "# HELP ai_governance_ollama_model_size_bytes 该模型加载占用字节（/api/ps size）"
  echo "# TYPE ai_governance_ollama_model_size_bytes gauge"
  echo "# HELP ai_governance_ollama_model_vram_bytes 该模型占用显存字节（/api/ps size_vram；纯 CPU 环境为 0）"
  echo "# TYPE ai_governance_ollama_model_vram_bytes gauge"
  echo "# HELP ai_governance_ollama_model_expires_in_seconds 距该模型被自动卸载的剩余秒数（/api/ps expires_at - now）；容器配 OLLAMA_KEEP_ALIVE=10m，空闲时应 ≤600"
  echo "# TYPE ai_governance_ollama_model_expires_in_seconds gauge"
  printf '%s' "$ps_json" | jq -r '.models[]? | [.name, (.size // 0), (.size_vram // 0), (.expires_at // "")] | @tsv' 2>/dev/null |
  while IFS=$'\t' read -r m_name m_size m_vram m_exp; do
    [ -n "$m_name" ] || continue
    label=$(esc "$m_name")
    if [ -n "$m_exp" ]; then
      m_epoch=$(date -d "$m_exp" +%s 2>/dev/null || echo '')
      m_left=$(num "$m_epoch")
      if [ "$m_left" -gt 0 ] 2>/dev/null; then
        m_left=$((m_left - now))
      fi
      m_left=$(num "$m_left")
    else
      m_left=0
    fi
    printf 'ai_governance_ollama_model_loaded{model="%s"} 1\n' "$label"
    printf 'ai_governance_ollama_model_size_bytes{model="%s"} %s\n' "$label" "$(num "$m_size")"
    printf 'ai_governance_ollama_model_vram_bytes{model="%s"} %s\n' "$label" "$(num "$m_vram")"
    printf 'ai_governance_ollama_model_expires_in_seconds{model="%s"} %s\n' "$label" "$m_left"
  done
  echo "# HELP ai_governance_ollama_probe_last_run_timestamp_seconds 本采集器最近一次运行时间（Unix 秒）"
  echo "# TYPE ai_governance_ollama_probe_last_run_timestamp_seconds gauge"
  printf 'ai_governance_ollama_probe_last_run_timestamp_seconds %s\n' "$now"
} > "$TMP" 2>/dev/null

# 落盘自检：任一非法行会让 node_exporter 拒收整个文件 ⇒ 保留旧文件并报错，绝不覆盖成坏文件
if grep -qvE '^(#.*|[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? [0-9.eE+-]+)$' "$TMP"; then
  echo "[ollama_probe] FATAL: 生成的指标文件含非法行，已保留旧文件不覆盖" >&2
  grep -nvE '^(#.*|[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? [0-9.eE+-]+)$' "$TMP" >&2
  rm -f "$TMP"
  exit 1
fi
mv -f "$TMP" "$OUT"
chmod 0644 "$OUT"
