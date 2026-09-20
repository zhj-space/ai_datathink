"""设计令牌一致性校验（确定性，无假阳性）。

职责
----
`design/tokens.css` 与 `theme.py::TOKENS` 是同一套令牌的两份表达。本脚本
**解析 CSS 声明**并与 Python 字典逐项比对，任何差异都以非零退出码报出。

用法
----
    python tools/check_tokens.py            # 校验令牌一致性 + 对比度

设计取舍
--------
* **声明级解析**（按 `;` 切分），不做行级匹配 —— 因为 tokens.css 里
  存在一行两个声明（如 `--color-success-bg: …; --color-success-fg: …;`），
  行级正则会吞掉第二条，产生假阴性。
* 只校验浅色 `:root` 段；深色段单列（`DARK_TOKENS`）后同样比对。
* 命名前缀不同（css `--color-*` / py `--gv-*`）是**有意**的：`--gv-` 前缀
  避免与 Streamlit 自带 CSS 变量冲突。故比对前先做显式名称映射，
  映射表在 `_NAME_MAP`，不靠猜测。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import theme  # noqa: E402

# CSS 专用令牌：纯布局/排版，Streamlit 侧不需要（组件样式直接用字面量或 rem）
CSS_ONLY_PREFIXES = (
    "--leading-", "--weight-", "--sidebar-w", "--content-max", "--header-h",
)


def parse_css_block(text: str, start_index: int) -> dict[str, str]:
    """从 `start_index` 处的 `{` 开始，取出配对花括号内的声明。

    返回 {token_name: value}。先剥离块注释，再按 `;` 切分（声明级）。
    """
    brace_open = text.index("{", start_index)
    depth = 0
    for i in range(brace_open, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                body = text[brace_open + 1 : i]
                break
    else:
        raise ValueError("花括号未闭合")

    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    out: dict[str, str] = {}
    for decl in body.split(";"):
        m = re.match(r"^\s*(--[A-Za-z0-9-]+)\s*:\s*(.+?)\s*$", decl, flags=re.S)
        if m:
            out[m.group(1)] = re.sub(r"\s+", " ", m.group(2)).strip()
    return out


def _name_map(css_name: str) -> str:
    """css 令牌名 → python 令牌名。显式映射，不靠前缀猜测。"""
    # 长前缀优先匹配，避免 --color-surface-sunken 被 --color-surface 抢走。
    # 注意：`--color-primary` 需能匹配 `--color-primary-100`（子级带连字符），
    # 故匹配规则为「等于」或「以 <prefix> 开头」，不等号后要求连字符。
    table = [
        ("--color-surface-sunken", "--gv-surface-sunken"),
        ("--color-surface-raised", "--gv-surface-raised"),
        ("--color-surface", "--gv-surface"),
        ("--color-border-strong", "--gv-border-strong"),
        ("--color-border", "--gv-border"),
        ("--color-primary", "--gv-primary"),
        ("--color-text", "--gv-text"),
        ("--color-success", "--gv-success"),
        ("--color-warning", "--gv-warning"),
        ("--color-danger", "--gv-danger"),
        ("--color-info", "--gv-info"),
        ("--color-neutral", "--gv-neutral"),
        ("--font-sans", "--gv-font-sans"),
        ("--font-mono", "--gv-font-mono"),
        ("--text", "--gv-text"),
        ("--space", "--gv-space"),
        ("--radius", "--gv-radius"),
        ("--shadow", "--gv-shadow"),
        ("--dur", "--gv-dur"),
        ("--ease", "--gv-ease"),
    ]
    for css_prefix, py_prefix in table:
        if css_name == css_prefix:
            return py_prefix
        # 子级匹配：前缀为 `--color-primary`，则 `--color-primary-100` 命中
        if css_name.startswith(css_prefix + "-"):
            return py_prefix + css_name[len(css_prefix):]
    return ""


def _norm(value: str) -> str:
    """规范化：折叠空白、去引号、去末尾分号。"""
    v = re.sub(r"\s+", " ", str(value)).replace(";", "").strip()
    return v.replace('"', "").replace("'", "")


def check_token_parity() -> int:
    css_text = (ROOT / "design" / "tokens.css").read_text(encoding="utf-8")

    light = parse_css_block(css_text, css_text.index(":root"))
    dark = parse_css_block(css_text, css_text.index('[data-theme="dark"]'))

    failures: list[str] = []
    compared = 0

    for css_name, css_val in sorted(light.items()):
        if css_name.startswith(CSS_ONLY_PREFIXES):
            continue
        py_name = _name_map(css_name)
        if not py_name:
            failures.append(f"  未映射的 CSS 令牌：{css_name}")
            continue
        py_val = theme.TOKENS.get(py_name)
        if py_val is None:
            failures.append(f"  py 缺失：{css_name}  →  {py_name}")
            continue
        compared += 1
        if _norm(css_val) != _norm(py_val):
            failures.append(
                f"  值不一致：{css_name}\n"
                f"      css = {_norm(css_val)}\n"
                f"      py  = {_norm(py_val)}"
            )

    print(f"[1] 浅色令牌一致性：比对 {compared} 项")
    if failures:
        print("\n".join(failures))
        print("  ✗ 存在不一致")
    else:
        print("  ✓ 全部一致")

    # 深色：token 名必须都在 py 的 dark 覆盖里
    py_dark_src = theme._vars_block(dark=True)  # noqa: SLF001  （有意直连，校验用）
    dark_missing = [
        n for n in dark
        if _name_map(n) and f"{_name_map(n)}:" not in py_dark_src
    ]
    print(f"[2] 深色令牌覆盖：css 定义 {len(dark)} 项")
    if dark_missing:
        for n in dark_missing:
            print(f"  py 深色未覆盖：{n}")
        print("  ✗ 覆盖不全")
    else:
        print("  ✓ 全部已覆盖")

    return 1 if (failures or dark_missing) else 0


# ---------------------------------------------------------------------------
# 对比度校验（WCAG 2.1）
# ---------------------------------------------------------------------------

def _srgb(channel: float) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 对比度（确定性纯函数）。"""
    l1, l2 = _luminance(fg), _luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# (用途, 前景, 背景, 最低要求)
CONTRAST_CASES: list[tuple[str, str, str, float]] = [
    # 浅色 · 品牌
    ("浅/主按钮白字", "#ffffff", "#0d4d76", 4.5),
    ("浅/主按钮 hover 白字", "#ffffff", "#0a3d5e", 4.5),
    ("浅/链接文字", "#0f5c8c", "#ffffff", 4.5),
    ("浅/焦点环（非文字）", "#1478b4", "#ffffff", 3.0),
    # 浅色 · 中性
    ("浅/正文", "#1a2733", "#ffffff", 4.5),
    ("浅/正文 on 凹陷底", "#1a2733", "#f7f9fb", 4.5),
    ("浅/次要文字", "#4a5b6b", "#ffffff", 4.5),
    ("浅/次要 on 凹陷底", "#4a5b6b", "#f7f9fb", 4.5),
    ("浅/弱化文字", "#5f6f7e", "#ffffff", 4.5),
    ("浅/弱化 on 凹陷底", "#5f6f7e", "#f7f9fb", 4.5),
    ("浅/边界（非文字）", "#6b7c8c", "#ffffff", 3.0),
    # 浅色 · 语义
    ("浅/成功 深字浅底", "#0a5c3a", "#e6f4ed", 4.5),
    ("浅/警告 深字浅底", "#7a4a00", "#fdf3e2", 4.5),
    ("浅/危险 深字浅底", "#9b1c25", "#fdeaea", 4.5),
    ("浅/信息 深字浅底", "#0f5c8c", "#e8f2f9", 4.5),
    ("浅/中性 深字浅底", "#4a5b6b", "#eef2f5", 4.5),
    # 深色
    ("深/正文", "#e8eef3", "#16202a", 4.5),
    ("深/正文 on 凹陷底", "#e8eef3", "#0e171f", 4.5),
    ("深/次要文字", "#a9b8c6", "#16202a", 4.5),
    ("深/弱化文字", "#8e9dab", "#16202a", 4.5),
    ("深/链接文字", "#7cb8dd", "#16202a", 4.5),
    ("深/焦点环（非文字）", "#4f9dce", "#16202a", 3.0),
    ("深/边界（非文字）", "#6f8291", "#16202a", 3.0),
    ("深/成功 浅字深底", "#6fd4a8", "#10352a", 4.5),
    ("深/警告 浅字深底", "#f0b761", "#3a2c12", 4.5),
    ("深/危险 浅字深底", "#f0979e", "#3a1a1e", 4.5),
    ("深/信息 浅字深底", "#85c2e6", "#10304a", 4.5),
    ("深/中性 浅字深底", "#a9b8c6", "#232f3a", 4.5),
]


def check_contrast() -> int:
    print(f"\n[3] WCAG AA 对比度：校验 {len(CONTRAST_CASES)} 组")
    bad = 0
    for name, fg, bg, need in CONTRAST_CASES:
        r = contrast_ratio(fg, bg)
        ok = r >= need
        if not ok:
            bad += 1
            print(f"  ✗ {name}：{r:.2f}:1 < {need}:1  （{fg} on {bg}）")
    if bad:
        print(f"  ✗ {bad} 组未达标")
    else:
        print("  ✓ 全部达标")
    return 1 if bad else 0


def main() -> int:
    rc = check_token_parity()
    rc |= check_contrast()
    print()
    print("结论：" + ("通过 ✓" if rc == 0 else "未通过 ✗"))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
