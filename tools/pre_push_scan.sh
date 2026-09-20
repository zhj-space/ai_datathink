#!/usr/bin/env bash
# 提交前敏感信息自检（仓库入口：AGENTS §9 / §7）
#
# 为什么要有它：2026-09-21 首次把仓库推到 GitHub 时发现 6 份文档含**服务器公网 IP 与主机名**
# （共 12 处），不得不重写历史 + force-push 才清掉。把人肉检查固化成可执行断言，成本最低。
#
# 用法：
#   bash tools/pre_push_scan.sh              # 默认：扫「已 staged」的文件（pre-commit 钩子用）
#   bash tools/pre_push_scan.sh --all        # 扫全部受控文件（推送前全量复查）
#   bash tools/pre_push_scan.sh --file a.md  # 扫指定文件（自测/排查用）
#
# 退出码：0 = 干净；1 = 命中（提交应被拦下）。
# 纪律：**命中时只报告 文件:行号 + 规则名，不打印命中的具体值**（避免把密文再写进日志/终端历史）。

set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

MODE="${1:---staged}"
shift || true

case "$MODE" in
  --staged) FILES=$(git diff --cached --name-only --diff-filter=ACM) ;;
  --all)    FILES=$(git ls-files) ;;
  --file)
    FILES=""
    for f in "$@"; do
      # 兼容 Windows 路径（C:\a\b -> /c/a/b），并**校验可读**——读不到就退出，绝不静默通过
      case "$f" in
        [A-Za-z]:\\*|[A-Za-z]:/*)
          drive=$(printf '%s' "$f" | cut -c1 | tr 'A-Z' 'a-z')
          rest=$(printf '%s' "$f" | cut -c3- | tr '\\' '/')
          f="/$drive$rest" ;;
      esac
      if [ ! -r "$f" ]; then
        echo "[scan] 错误：无法读取文件 $f（路径写错？）" >&2
        exit 2
      fi
      FILES="$FILES $f"
    done ;;
  *) echo "用法：$0 [--staged|--all|--file <路径>...]" >&2; exit 2 ;;
esac

if [ -z "${FILES:-}" ]; then
  echo "[scan] 没有待扫描文件（staged 为空）"
  exit 0
fi

# 只看文本类文件；跳过明显二进制/大文件
SCAN=$(printf '%s\n' $FILES | grep -vE '\.(png|jpg|jpeg|gif|pdf|zip|gz|tar|tgz|pyc|ico|woff2?|jar)$' || true)
[ -n "$SCAN" ] || { echo "[scan] 无需扫描的文本文件"; exit 0; }

FAIL=0
# 只报「文件:行号 + 规则名」，**绝不打印命中内容**（否则等于把密文又写进终端/日志）
report() {
  local rule="$1" line="$2" where
  case "$line" in
    *:*:*) where="$(printf '%s' "$line" | cut -d: -f1-2)" ;;    # 多文件模式：file:line
    *:*)   where="line $(printf '%s' "$line" | cut -d: -f1)" ;; # 单文件模式：只有 line:content
    *)     where="(?位置未知)" ;;
  esac
  echo "  ✗ [$rule] $where" >&2
  FAIL=1
}
scan_rule() {  # $1=规则名 $2=正则（ERE） $3=可选 grep 额外参数（如 -i）
  local rule="$1" re="$2" extra="${3:-}" hits
  hits=$(printf '%s\n' $SCAN | xargs -r grep -InE $extra -- "$re" 2>/dev/null || true)
  if [ -n "$hits" ]; then
    while IFS= read -r l; do [ -n "$l" ] && report "$rule" "$l"; done <<< "$hits"
  fi
}

echo "[scan] 模式=$MODE  文本文件数=$(printf '%s\n' $SCAN | grep -c . )"

# ---------- 结构性规则 ----------
scan_rule 'private-key'    'BEGIN (OPENSSH|RSA|EC|PGP) PRIVATE KEY'
scan_rule 'aws-key'        'AKIA[0-9A-Z]{16}'
scan_rule 'github-token'   'gh[pousr]_[A-Za-z0-9]{20,}'
scan_rule 'slack-token'    'xox[baprs]-[A-Za-z0-9-]{10,}'
scan_rule 'bearer-literal' 'Bearer [A-Za-z0-9._-]{24,}'
# 两条 2026-09-21 自测校准：
#   ① 口令部分 ≥8 位 —— 否则把"文档里描述规则的示例串（user:pw@）"误报；
#   ② 值的**首字符不得是 $** —— 否则把 `PGPASSWORD="$PG_PASSWORD"` 这类**变量引用**误报（实测踩到）。
scan_rule 'conn-string'    '(postgres|postgresql|mysql|redis)://[^/[:space:]:]+:[^@$[:space:]][^@[:space:]]{7,}@'
# 注意：正则里要匹配引号，但**单引号在 shell 单引号串里会提前闭合**——故用变量 SQ 传单引号（2026-09-21 踩到）
SQ="'"
scan_rule 'password-assign' "(password|passwd|secret|token)[[:space:]]*[:=][[:space:]]*[${SQ}\"]?[A-Za-z0-9!@#%^&*_-][A-Za-z0-9!@#\$%^&*_-]{11,}" -i

# ---------- 公网 IP（排除 loopback / RFC1918 / 链路本地 / 文档示例段）----------
IP_RE='([0-9]{1,3}\.){3}[0-9]{1,3}'
ip_hits=$(printf '%s\n' $SCAN | xargs -r grep -InE -- "$IP_RE" 2>/dev/null || true)
if [ -n "$ip_hits" ]; then
  while IFS= read -r l; do
    [ -n "$l" ] || continue
    ips=$(printf '%s' "$l" | grep -oE "$IP_RE" || true)
    for ip in $ips; do
      case "$ip" in
        127.*|10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|169.254.*|0.0.0.0|255.*|\
        192.0.2.*|198.51.100.*|203.0.113.*|1.2.3.4|8.8.8.8|114.114.114.114|223.5.5.5) continue ;;
      esac
      report 'public-ip' "$l"
      break
    done
  done <<< "$ip_hits"
fi

# ---------- 主机名/实例名（云主机常见前缀）----------
scan_rule 'cloud-hostname' '\biZ[a-z0-9]{12,}\b'
scan_rule 'cloud-hostname2' '\bi-[0-9a-f]{8,}\b'

# ---------- 本机已知口令字面量（放在仓库外的清单里，绝不入库）----------
PATTERN_FILE="${HOME}/.ai-datathink/secret-patterns.local"
if [ -f "$PATTERN_FILE" ]; then
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    case "$p" in \#*) continue ;; esac
    hits=$(printf '%s\n' $SCAN | xargs -r grep -InF -- "$p" 2>/dev/null || true)
    if [ -n "$hits" ]; then
      while IFS= read -r l; do [ -n "$l" ] && report 'known-secret' "$l"; done <<< "$hits"
    fi
  done < "$PATTERN_FILE"
else
  echo "  · 提示：未配置 ${PATTERN_FILE}（把本环境已知口令逐行放进去可启用「字面量」扫描）"
fi

if [ "$FAIL" -ne 0 ]; then
  echo "[scan] 未通过：命中以上敏感信息 —— 请改用占位符（AGENTS §9），确认为误报时用 'git commit --no-verify' 并说明原因" >&2
  exit 1
fi
echo "[scan] 通过 ✓"
