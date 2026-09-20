"""治理门户 · 主题与组件样式注入（Streamlit 落地层）。

定位
----
[设计稿](../design/mockup.html) 是视觉基准；本模块把它的**设计令牌**翻译成
Streamlit 可消费的两份产物：

1. `.streamlit/config.toml` 的 `[theme]` 段 —— 由 `emit_config_toml()` 生成，
   覆盖 Streamlit 原生控件（侧栏底色、按钮、输入框聚焦环等）。这是**结构化**渠道，
   优先级最高、最稳。
2. 一段注入式 `<style>` —— 由 `inject_css()` 写入，用于原生 theme 覆盖不到的部分
   （卡片、徽标、KPI、横幅、表格、空状态等自绘组件）。

纪律
----
* 令牌**单一事实来源**是本模块的 `TOKENS` 字典；它必须与 `design/tokens.css` 的
  `:root` 段保持同值。改一处须同步另一处（`tools/check_tokens.py` 有一致性断言）。
* 两侧前缀**故意不同**（CSS `--color-*` vs 本模块 `--gv-*`）：Streamlit/BaseWeb
  已占用大量 `--color-*` 变量，同名前缀会被内部样式污染。前缀差异由校验脚本的
  显式映射表 `_name_map` 弥合，**不产生校验盲区**。
* 颜色**不写死**在组件样式里，一律 `var(--gv-*)`。这样深色模式只需换一组变量值。
* 对比度已按 WCAG AA 校验（见 `design/design-system.md`），改色须重跑校验脚本。
* 本模块**不改任何数据口径**，只做视觉层；`db.py` / `agent.py` / `config.py` 的数据语义不变。

版本：v1.2（2026-09-20，新增站内模块卡变体 .gv-entry--internal；对齐 design/design-system.md）
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 设计令牌（与 design/tokens.css 的 :root 同值）
# ---------------------------------------------------------------------------

TOKENS: dict[str, str] = {
    # 品牌
    "--gv-primary-50": "#f2f8fc",
    "--gv-primary-100": "#e3f0f8",
    "--gv-primary-200": "#c3dff0",
    "--gv-primary-300": "#93c4e2",
    "--gv-primary-400": "#4f9dce",
    "--gv-primary-500": "#1478b4",
    "--gv-primary-600": "#0f5c8c",
    "--gv-primary-700": "#0d4d76",
    "--gv-primary-800": "#0a3d5e",
    "--gv-primary-900": "#072c44",
    # 中性
    "--gv-text-primary": "#1a2733",
    "--gv-text-secondary": "#4a5b6b",
    "--gv-text-muted": "#5f6f7e",
    "--gv-text-inverse": "#ffffff",
    "--gv-surface": "#ffffff",
    "--gv-surface-sunken": "#f7f9fb",
    "--gv-surface-raised": "#ffffff",
    "--gv-border": "#dde5ec",
    "--gv-border-strong": "#6b7c8c",
    # 语义（浅底 + 深字，均 ≥4.5:1）
    "--gv-success-bg": "#e6f4ed",
    "--gv-success-fg": "#0a5c3a",
    "--gv-success-solid": "#0a5c3a",
    "--gv-warning-bg": "#fdf3e2",
    "--gv-warning-fg": "#7a4a00",
    "--gv-warning-solid": "#7a4a00",
    "--gv-danger-bg": "#fdeaea",
    "--gv-danger-fg": "#9b1c25",
    "--gv-danger-solid": "#9b1c25",
    "--gv-info-bg": "#e8f2f9",
    "--gv-info-fg": "#0f5c8c",
    "--gv-info-solid": "#0f5c8c",
    "--gv-neutral-bg": "#eef2f5",
    "--gv-neutral-fg": "#4a5b6b",
    # 字体
    "--gv-font-sans": (
        '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", '
        '"Hiragino Sans GB", "Microsoft YaHei", "Source Han Sans SC", '
        '"Noto Sans CJK SC", sans-serif'
    ),
    "--gv-font-mono": (
        '"SF Mono", "JetBrains Mono", Menlo, Consolas, "Liberation Mono", monospace'
    ),
    # 间距（4px 基准）
    "--gv-space-1": "0.25rem",
    "--gv-space-2": "0.5rem",
    "--gv-space-3": "0.75rem",
    "--gv-space-4": "1rem",
    "--gv-space-5": "1.25rem",
    "--gv-space-6": "1.5rem",
    "--gv-space-8": "2rem",
    "--gv-space-10": "2.5rem",
    "--gv-space-12": "3rem",
    # 圆角
    "--gv-radius-sm": "4px",
    "--gv-radius-md": "6px",
    "--gv-radius-lg": "10px",
    "--gv-radius-xl": "14px",
    "--gv-radius-full": "999px",
    # 阴影
    "--gv-shadow-xs": "0 1px 2px rgb(16 36 51 / 0.05)",
    "--gv-shadow-sm": "0 1px 3px rgb(16 36 51 / 0.07), 0 1px 2px rgb(16 36 51 / 0.04)",
    "--gv-shadow-md": "0 4px 12px -2px rgb(16 36 51 / 0.09), 0 2px 4px -2px rgb(16 36 51 / 0.05)",
    "--gv-shadow-focus": "0 0 0 3px rgb(20 120 180 / 0.28)",
    # 字号
    "--gv-text-xs": "0.75rem",
    "--gv-text-sm": "0.8125rem",
    "--gv-text-base": "0.875rem",
    "--gv-text-md": "1rem",
    "--gv-text-lg": "1.125rem",
    "--gv-text-xl": "1.375rem",
    "--gv-text-2xl": "1.75rem",
    "--gv-text-3xl": "2.25rem",
    # 动效
    "--gv-dur-fast": "120ms",
    "--gv-dur-base": "200ms",
    "--gv-ease-out": "cubic-bezier(0.16, 1, 0.3, 1)",
}

# Streamlit `[theme]` 段所需的关键色（与 TOKENS 同值，单独列出便于生成 toml）
THEME_CORE = {
    "primaryColor": "#0d4d76",           # 主按钮/强调，白字 8.97:1
    "backgroundColor": "#ffffff",        # 主区底
    "secondaryBackgroundColor": "#f7f9fb",  # 侧栏/凹陷区
    "textColor": "#1a2733",              # 正文 15.20:1
    "font": "sans serif",
}


def _vars_block(dark: bool = False) -> str:
    """渲染 `--gv-*` 变量声明块；`dark=True` 时输出深色覆盖值。"""
    if not dark:
        lines = [f"  {k}: {v};" for k, v in TOKENS.items()]
        return "\n".join(lines)

    # 深色覆盖（与 design/tokens.css [data-theme="dark"] 同值）
    dark_overrides = {
        "--gv-primary-50": "#10304a",
        "--gv-primary-100": "#123a58",
        "--gv-primary-200": "#1a4a6e",
        "--gv-primary-300": "#2d6690",
        "--gv-primary-400": "#4f9dce",
        "--gv-primary-500": "#4f9dce",
        "--gv-primary-600": "#7cb8dd",
        "--gv-primary-700": "#0d4d76",
        "--gv-primary-800": "#0a3d5e",
        "--gv-text-primary": "#e8eef3",
        "--gv-text-secondary": "#a9b8c6",
        "--gv-text-muted": "#8e9dab",
        "--gv-text-inverse": "#0c1720",
        "--gv-surface": "#16202a",
        "--gv-surface-sunken": "#0e171f",
        "--gv-surface-raised": "#1c2833",
        "--gv-border": "#2b3a47",
        "--gv-border-strong": "#6f8291",
        "--gv-success-bg": "#10352a",
        "--gv-success-fg": "#6fd4a8",
        "--gv-success-solid": "#1c7d5c",
        "--gv-warning-bg": "#3a2c12",
        "--gv-warning-fg": "#f0b761",
        "--gv-warning-solid": "#a3661a",
        "--gv-danger-bg": "#3a1a1e",
        "--gv-danger-fg": "#f0979e",
        "--gv-danger-solid": "#b8323e",
        "--gv-info-bg": "#10304a",
        "--gv-info-fg": "#85c2e6",
        "--gv-info-solid": "#1a6fa3",
        "--gv-neutral-bg": "#232f3a",
        "--gv-neutral-fg": "#a9b8c6",
        "--gv-shadow-xs": "0 1px 2px rgb(0 0 0 / 0.3)",
        "--gv-shadow-sm": "0 1px 3px rgb(0 0 0 / 0.35)",
        "--gv-shadow-md": "0 4px 14px -2px rgb(0 0 0 / 0.45)",
        "--gv-shadow-focus": "0 0 0 3px rgb(79 157 206 / 0.35)",
    }
    merged = {**TOKENS, **dark_overrides}
    return "\n".join(f"  {k}: {v};" for k, v in merged.items())


# ---------------------------------------------------------------------------
# 组件样式（消费令牌，不写死色值）
# ---------------------------------------------------------------------------

_COMPONENT_CSS = """
/* ---------- 全局字体与正文色 ---------- */
html, body, [class*="css"], .stApp, button, input, textarea, select {
  font-family: var(--gv-font-sans) !important;
}
.stApp { background: var(--gv-surface-sunken); }
[data-testid="stAppViewContainer"] > .main { background: var(--gv-surface-sunken); }

/* 中文正文行高放宽，提升可读性 */
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li { line-height: 1.7; }

/* 代码/标识符等宽 */
code, kbd, pre, .gv-mono, [data-testid="stCodeBlock"] {
  font-family: var(--gv-font-mono) !important;
}

/* ---------- 页面标题（替换 Streamlit 默认 h1，收敛为治理工具尺度） ---------- */
h1 {
  font-size: var(--gv-text-xl) !important;
  font-weight: 600 !important;
  color: var(--gv-text-primary) !important;
  letter-spacing: -0.01em;
  padding-bottom: 0 !important;
  margin-bottom: var(--gv-space-1) !important;
}
h2 {
  font-size: var(--gv-text-lg) !important;
  font-weight: 600 !important;
  color: var(--gv-text-primary) !important;
  margin-top: var(--gv-space-6) !important;
  padding-bottom: var(--gv-space-2) !important;
  border-bottom: 1px solid var(--gv-border);
}
h3 { font-size: var(--gv-text-md) !important; font-weight: 600 !important; }
h4 { font-size: var(--gv-text-base) !important; font-weight: 600 !important; }

/* 标题下那行 caption（"POC 单机形态 · …"） */
[data-testid="stCaptionContainer"] p,
small {
  font-size: var(--gv-text-sm) !important;
  color: var(--gv-text-secondary) !important;
  line-height: 1.6;
}

/* ---------- 侧栏 ---------- */
[data-testid="stSidebar"] {
  background: var(--gv-surface) !important;
  border-right: 1px solid var(--gv-border);
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
  font-size: var(--gv-text-sm); color: var(--gv-text-secondary);
}

/* 侧栏导航（radio 扮成导航列表） */
[data-testid="stSidebar"] [role="radiogroup"] { gap: 2px !important; }
[data-testid="stSidebar"] [role="radiogroup"] label {
  min-height: 44px;                      /* 触摸目标 ≥44px */
  padding: var(--gv-space-2) var(--gv-space-3) !important;
  border-radius: var(--gv-radius-md);
  transition: background var(--gv-dur-fast) var(--gv-ease-out);
}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {
  background: var(--gv-primary-50);
}
/* 隐藏原生圆点，整行作为点击区（视觉上更像导航而非表单） */
[data-testid="stSidebar"] [role="radiogroup"] label > div:first-child {
  display: none;
}
[data-testid="stSidebar"] [role="radiogroup"] label p {
  font-size: var(--gv-text-base) !important;
  font-weight: 500 !important;
  color: var(--gv-text-secondary) !important;
}
/* 选中项：底色 + 左侧 3px 条（不靠颜色单一通道） */
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
  background: var(--gv-primary-100) !important;
  box-shadow: inset 3px 0 0 var(--gv-primary-600);
}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p {
  color: var(--gv-primary-800) !important;
  font-weight: 600 !important;
}

/* ---------- 按钮 ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
  min-height: 44px;                      /* 触摸目标 */
  padding: 0 var(--gv-space-5) !important;
  border-radius: var(--gv-radius-md) !important;
  font-family: var(--gv-font-sans) !important;
  font-size: var(--gv-text-base) !important;
  font-weight: 600 !important;
  transition: background var(--gv-dur-fast) var(--gv-ease-out),
              box-shadow var(--gv-dur-fast) var(--gv-ease-out);
}
/* 主按钮 */
.stButton > button[kind="primary"],
.stFormSubmitButton > button[kind="primary"] {
  background: var(--gv-primary-700) !important;
  color: var(--gv-text-inverse) !important;
  border: 1px solid var(--gv-primary-700) !important;
}
.stButton > button[kind="primary"]:hover {
  background: var(--gv-primary-800) !important;
  border-color: var(--gv-primary-800) !important;
}
/* 次按钮 */
.stButton > button[kind="secondary"] {
  background: var(--gv-surface) !important;
  color: var(--gv-primary-700) !important;
  border: 1px solid var(--gv-border-strong) !important;
}
.stButton > button[kind="secondary"]:hover {
  background: var(--gv-primary-50) !important;
  border-color: var(--gv-primary-600) !important;
}
/* 统一的键盘焦点环（无障碍必需，不可移除） */
.stButton > button:focus-visible,
[data-testid="stSidebar"] [role="radiogroup"] label:focus-within,
.stTextInput input:focus-visible,
[data-testid="stExpander"] summary:focus-visible {
  outline: none !important;
  box-shadow: var(--gv-shadow-focus) !important;
}

/* ---------- 输入框 ---------- */
.stTextInput input, .stTextArea textarea, [data-baseweb="select"] > div {
  min-height: 44px;
  border-radius: var(--gv-radius-md) !important;
  border: 1px solid var(--gv-border-strong) !important;
  background: var(--gv-surface) !important;
  color: var(--gv-text-primary) !important;
  font-size: var(--gv-text-base) !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
  border-color: var(--gv-primary-600) !important;
  box-shadow: var(--gv-shadow-focus) !important;
}
.stTextInput input::placeholder, .stTextArea textarea::placeholder {
  color: var(--gv-text-muted) !important;
}
[data-testid="stWidgetLabel"] p, .stTextInput label p {
  font-size: var(--gv-text-sm) !important;
  font-weight: 500 !important;
  color: var(--gv-text-primary) !important;
}

/* ---------- 提示条（st.success/warning/error/info 重绘为横幅） ---------- */
[data-testid="stAlert"] {
  border-radius: var(--gv-radius-lg) !important;
  border: 1px solid transparent !important;
  padding: var(--gv-space-4) var(--gv-space-5) !important;
  box-shadow: none !important;
}
[data-testid="stAlert"] p { font-size: var(--gv-text-sm) !important; line-height: 1.7; }
/* 成功 */
div[data-testid="stAlert"][data-baseweb="notification"]:has(svg[title="Success"]),
div[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) {
  background: var(--gv-success-bg) !important;
  border-color: var(--gv-success-fg) !important;
}
/* 警告 */
div[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) {
  background: var(--gv-warning-bg) !important;
  border-color: var(--gv-warning-fg) !important;
}
/* 错误 */
div[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) {
  background: var(--gv-danger-bg) !important;
  border-color: var(--gv-danger-fg) !important;
}
/* 信息 */
div[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) {
  background: var(--gv-info-bg) !important;
  border-color: var(--gv-info-fg) !important;
}

/* ---------- 指标卡（st.metric 重绘） ---------- */
[data-testid="stMetric"] {
  background: var(--gv-surface);
  border: 1px solid var(--gv-border);
  border-radius: var(--gv-radius-lg);
  box-shadow: var(--gv-shadow-xs);
  padding: var(--gv-space-5) !important;
}
[data-testid="stMetricLabel"] p {
  font-size: var(--gv-text-sm) !important;
  color: var(--gv-text-secondary) !important;
  font-weight: 500 !important;
}
[data-testid="stMetricValue"] {
  font-size: var(--gv-text-3xl) !important;
  font-weight: 700 !important;
  color: var(--gv-text-primary) !important;
  font-variant-numeric: tabular-nums;
  line-height: 1.1 !important;
}

/* ---------- 表格（st.dataframe）---------- */
[data-testid="stDataFrame"], [data-testid="stTable"] {
  border: 1px solid var(--gv-border) !important;
  border-radius: var(--gv-radius-lg) !important;
  overflow: hidden;
  box-shadow: var(--gv-shadow-xs);
}
[data-testid="stDataFrame"] [role="columnheader"] {
  background: var(--gv-surface-sunken) !important;
  font-size: var(--gv-text-sm) !important;
  font-weight: 600 !important;
  color: var(--gv-text-secondary) !important;
}
[data-testid="stDataFrame"] [role="gridcell"] {
  font-size: var(--gv-text-base) !important;
  color: var(--gv-text-primary);
  font-variant-numeric: tabular-nums;
}

/* ---------- 折叠区（st.expander 作"探测详情"） ---------- */
[data-testid="stExpander"] {
  border: 1px solid var(--gv-border) !important;
  border-radius: var(--gv-radius-md) !important;
  background: var(--gv-surface);
  box-shadow: none !important;
}
[data-testid="stExpander"] summary {
  min-height: 44px;
  font-size: var(--gv-text-sm) !important;
  font-weight: 500 !important;
  color: var(--gv-text-secondary) !important;
}

/* ---------- 代码块 ---------- */
pre, [data-testid="stCodeBlock"] code {
  background: var(--gv-surface-sunken) !important;
  border: 1px solid var(--gv-border);
  border-radius: var(--gv-radius-sm) !important;
  font-size: var(--gv-text-xs) !important;
  color: var(--gv-text-primary) !important;
}
code {
  background: var(--gv-surface-sunken);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.92em;
}

/* ---------- 分隔线 ---------- */
hr {
  border: none !important;
  border-top: 1px solid var(--gv-border) !important;
  margin: var(--gv-space-6) 0 !important;
}

/* ---------- 应用内自定义组件（供 app.py 直接调用） ---------- */

/* 卡片 */
.gv-card {
  background: var(--gv-surface);
  border: 1px solid var(--gv-border);
  border-radius: var(--gv-radius-lg);
  box-shadow: var(--gv-shadow-xs);
  padding: var(--gv-space-5);
}

/* 区块标题（带序号） */
.gv-section-title {
  display: flex; align-items: center; gap: var(--gv-space-3);
  font-size: var(--gv-text-lg); font-weight: 600;
  color: var(--gv-text-primary);
  margin: var(--gv-space-6) 0 var(--gv-space-2);
}
.gv-section-num {
  display: inline-grid; place-items: center;
  width: 24px; height: 24px; flex: 0 0 24px;
  border-radius: var(--gv-radius-sm);
  background: var(--gv-primary-100); color: var(--gv-primary-800);
  font-size: var(--gv-text-xs); font-weight: 700;
}
.gv-section-desc {
  color: var(--gv-text-secondary);
  font-size: var(--gv-text-sm); line-height: 1.7;
  margin: 0 0 var(--gv-space-4);
}

/* 能力入口卡片（链接型） */
a.gv-entry {
  display: flex; align-items: center; gap: var(--gv-space-3);
  padding: var(--gv-space-4) var(--gv-space-5);
  min-height: 76px;
  background: var(--gv-surface);
  border: 1px solid var(--gv-border);
  border-radius: var(--gv-radius-lg);
  box-shadow: var(--gv-shadow-xs);
  text-decoration: none !important;
  transition: border-color var(--gv-dur-base) var(--gv-ease-out),
              box-shadow var(--gv-dur-base) var(--gv-ease-out),
              transform var(--gv-dur-base) var(--gv-ease-out);
}
a.gv-entry:hover {
  border-color: var(--gv-primary-300);
  box-shadow: var(--gv-shadow-md);
  transform: translateY(-2px);
}
a.gv-entry:focus-visible { outline: none; box-shadow: var(--gv-shadow-focus); }

/* 站内模块卡变体（系统状态 / 智能问答）：
   与外部深链卡同族同尺寸，但用左侧强调条 + 主色底纹区分"站内跳转"与"外链打开"。
   同网格并列时，用户可凭左侧竖条一眼分辨哪些会离开本站。 */
a.gv-entry--internal {
  background: linear-gradient(90deg, var(--gv-primary-50) 0%, var(--gv-surface) 42%);
  border-color: var(--gv-primary-200);
  box-shadow: inset 3px 0 0 var(--gv-primary-600);
}
a.gv-entry--internal:hover {
  border-color: var(--gv-primary-400);
  box-shadow: inset 3px 0 0 var(--gv-primary-700), var(--gv-shadow-md);
}

.gv-entry__name {
  font-size: var(--gv-text-md); font-weight: 600;
  color: var(--gv-primary-700);
  display: flex; align-items: center; gap: var(--gv-space-2);
  flex-wrap: wrap;
}
.gv-entry__desc {
  font-size: var(--gv-text-sm); color: var(--gv-text-secondary);
  margin-top: 2px;
}
.gv-entry__hint {
  font-size: var(--gv-text-xs); color: var(--gv-text-muted);
  margin-top: var(--gv-space-1);
  font-family: var(--gv-font-mono);
}

/* 徽标 */
.gv-badge {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 2px var(--gv-space-2);
  border-radius: var(--gv-radius-full);
  font-size: var(--gv-text-xs); font-weight: 600;
  line-height: 1.6; white-space: nowrap;
}
.gv-badge--ok   { background: var(--gv-success-bg); color: var(--gv-success-fg); }
.gv-badge--warn { background: var(--gv-warning-bg); color: var(--gv-warning-fg); }
.gv-badge--err  { background: var(--gv-danger-bg);  color: var(--gv-danger-fg); }
.gv-badge--info { background: var(--gv-info-bg);    color: var(--gv-info-fg); }
.gv-badge--mute { background: var(--gv-neutral-bg); color: var(--gv-neutral-fg); }
.gv-badge__dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: 0 0 6px; }

/* KPI 卡（自绘，用于需要脚注口径的场景） */
.gv-kpi {
  background: var(--gv-surface);
  border: 1px solid var(--gv-border);
  border-radius: var(--gv-radius-lg);
  box-shadow: var(--gv-shadow-xs);
  padding: var(--gv-space-5);
  height: 100%;
}
.gv-kpi--accent { border-left: 3px solid var(--gv-primary-600); }
.gv-kpi__label {
  font-size: var(--gv-text-sm); color: var(--gv-text-secondary);
  font-weight: 500; margin: 0 0 var(--gv-space-2);
}
.gv-kpi__value {
  font-size: var(--gv-text-3xl); font-weight: 700;
  color: var(--gv-text-primary); line-height: 1.1;
  font-variant-numeric: tabular-nums;
  display: flex; align-items: baseline; gap: var(--gv-space-2);
}
.gv-kpi__unit { font-size: var(--gv-text-md); font-weight: 500; color: var(--gv-text-muted); }
.gv-kpi__foot {
  margin-top: var(--gv-space-3); padding-top: var(--gv-space-3);
  border-top: 1px solid var(--gv-border);
  font-size: var(--gv-text-xs); color: var(--gv-text-muted);
  line-height: 1.7;
}

/* 空状态 */
.gv-empty {
  text-align: center;
  padding: var(--gv-space-8) var(--gv-space-6);
  background: var(--gv-surface);
  border: 1px dashed var(--gv-border);
  border-radius: var(--gv-radius-lg);
}
.gv-empty__title {
  font-size: var(--gv-text-md); font-weight: 600;
  margin: var(--gv-space-3) 0 var(--gv-space-2);
  color: var(--gv-text-primary);
}
.gv-empty__text {
  font-size: var(--gv-text-sm); color: var(--gv-text-secondary);
  line-height: 1.7; max-width: 520px; margin: 0 auto;
}

/* 口径提示（左条 note） */
.gv-note {
  border-left: 3px solid var(--gv-primary-600);
  background: var(--gv-primary-50);
  border-radius: 0 var(--gv-radius-md) var(--gv-radius-md) 0;
  padding: var(--gv-space-3) var(--gv-space-4);
  font-size: var(--gv-text-sm); color: var(--gv-text-secondary);
  line-height: 1.7;
}

/* 问答气泡 */
.gv-chat-user {
  background: var(--gv-primary-50);
  border: 1px solid var(--gv-primary-200);
  border-radius: var(--gv-radius-lg);
  padding: var(--gv-space-4);
  font-size: var(--gv-text-base); line-height: 1.7;
  color: var(--gv-text-primary);
}
.gv-trace {
  display: inline-flex; align-items: center; gap: var(--gv-space-2);
  font-family: var(--gv-font-mono); font-size: var(--gv-text-xs);
  background: var(--gv-surface-sunken);
  border: 1px solid var(--gv-border);
  border-radius: var(--gv-radius-sm);
  padding: 2px var(--gv-space-2);
  color: var(--gv-text-secondary);
}

/* ---------- 响应式 ---------- */
@media (max-width: 768px) {
  .gv-kpi__value { font-size: var(--gv-text-2xl); }
  .gv-section-title { font-size: var(--gv-text-md); }
}

/* ---------- 尊重"减少动效"系统偏好 ---------- */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
"""


def css_variables(dark: bool = False) -> str:
    """仅返回变量声明块（供测试与 config.toml 生成复用）。"""
    return f":root {{\n{_vars_block(dark)}\n}}"


def inject_css(st_module, dark: bool = False) -> None:
    """把设计令牌 + 组件样式注入页面。

    参数
    ----
    st_module : streamlit 模块（调用方传入，便于无头测试注入替身）
    dark      : 是否输出深色令牌。默认 `False`（浅色），
                自动跟随由 Streamlit `[theme]` 决定，此处只影响自绘组件。
    """
    st_module.markdown(
        f"<style>\n{css_variables(dark)}\n{_COMPONENT_CSS}\n</style>",
        unsafe_allow_html=True,
    )


def emit_config_toml() -> str:
    """生成 `.streamlit/config.toml` 的 `[theme]` 段文本（覆盖原生控件配色）。"""
    lines = ["[theme]"]
    for key, value in THEME_CORE.items():
        lines.append(f'{key} = "{value}"')
    return "\n".join(lines) + "\n"
