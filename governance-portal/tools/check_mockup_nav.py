#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mockup.html 结构静态校验 —— 防「侧栏导航重复/缺失/死链」回归。

背景：2026-09-20 用户发现 mockup 侧栏底部多出一项「访问审计与审批（场景 E）」。
根因：`panel-home` 的 `<ul class="gv-nav__list">` 提前闭合，其后的 `<li>` 成为
孤儿元素，被 HTML 解析器提升渲染到导航列表之外（并伴随一个多余的 `</ul>`）。
同时 `panel-metrics / panel-chat / panel-audit` 三份 nav 各只有 4 项且链接全是
`href="#"` 死链。

本脚本做**确定性**结构校验（纯正则 + 标签配对，不依赖浏览器）：
  N1  `<ul class="gv-nav__list">` 与 `</ul>` 数量相等，且每份 nav 恰好一个 ul
  N2  每份 nav 恰好 5 个 `gv-nav__item`
  N3  每份 nav 的链接集合恰为 5 个合法锚点，无 `href="#"` 死链
  N4  每份 nav 恰好一个 `aria-current="page"`，且与所在 panel 的 id 一致
  N5  5 份 nav 的「label 序列」完全一致（同构），且顺序为 IA 基准顺序
  N6  不存在 `<ul>` 之外的裸 `<li>`（孤儿 li —— 本次 bug 的直接形态）
  N7  顶部无导航控件（tabbar 已删）；页面切换仅由侧栏驱动
  N8  ARIA 引用无悬空 id（labelledby/controls/describedby/owns）
  N9  引用的 CSS 类均有定义（防「用了但没定义」，如 `.sr-only` 漏定义）
  N10 正文无「评审批注」口吻残留（设计稿是产品视觉基准，不是带批注的评审稿）

用法：python tools/check_mockup_nav.py
退出码：0 = 全通过；1 = 有失败项
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOCKUP = os.path.join(os.path.dirname(HERE), "design", "mockup.html")

# IA 基准顺序（2026-09-20 调整：系统状态移到导航最后）
EXPECTED_SLUGS = ["panel-home", "panel-chat", "panel-metrics", "panel-audit", "panel-status"]
EXPECTED_LABELS = [
    "首页 / 能力入口",
    "智能问答（场景 D）",
    "治理指标",
    "访问审计与审批（场景 E）",
    "系统状态（运维视角）",
]

results = []


def check(name: str, ok: bool, detail: str = "", ok_detail: str = "") -> None:
    """`detail` 仅在**失败时**打印（它描述失败原因）；`ok_detail` 仅在成功时打印。

    分开两个参数是有意的：早期版本把失败原因模板无条件打印，导致 PASS 行后面
    跟着"仍存在 .ds-tabs"这类误导性文字。
    """
    results.append((name, ok, detail))
    flag = "PASS" if ok else "FAIL"
    note = (ok_detail if ok else detail) or ""
    print(f"[{flag}] {name}" + (f" —— {note}" if note else ""))


def main() -> int:
    if not os.path.exists(MOCKUP):
        print(f"FATAL: 找不到 {MOCKUP}")
        return 1

    src = io.open(MOCKUP, encoding="utf-8").read()

    # ---------- N7 tabs / panels 配对 ----------
    tab_ids = re.findall(r'id="tab-([a-z]+)"', src)
    panel_ids = re.findall(r'id="panel-([a-z]+)"', src)
    # ---------- N7 顶部无导航控件（2026-09-20：删除设计稿专用 tabbar） ----------
    # 决策依据：产品的页面切换**只由左侧导航承担**（与 app.py 的 st.sidebar.radio 一致）。
    # 原 `.ds-tabs` 是一排仅供设计稿评审用的按钮，顺序还与 IA 基准不一致 —— 属冗余 UI。
    check(
        "N7a 已删除顶部 tabbar（.ds-tabs）",
        'class="ds-tabs"' not in src and 'class="ds-tab"' not in src,
        "仍存在 .ds-tabs / .ds-tab",
        "未发现 .ds-tabs / .ds-tab",
    )
    check(
        "N7b 无 role=\"tablist\" 顶部导航",
        'role="tablist"' not in src,
        "仍存在 role=tablist",
        "未发现 role=tablist",
    )
    check(
        "N7c 无残留 tab 按钮 id",
        not re.search(r'id="tab-[a-z]+"', src),
        f"命中={re.findall(r'id=.tab-[a-z]+.', src)}",
        "无 id=\"tab-*\" 残留",
    )
    # panel 改为无 aria-labelledby 依赖（原 tabpanel 语义已随 tabbar 移除）
    check(
        "N7d panels 数量仍为 5（页面未被误删）",
        len(set(panel_ids)) == 5,
        f"panels={len(set(panel_ids))}",
        f"panels={len(set(panel_ids))}",
    )
    check(
        "N7e 页面切换由左侧导航驱动（JS 监听 .gv-nav__link）",
        "gv-nav__link[href^=\"#panel-\"]" in src and "showPanel" in src,
        "未发现侧栏驱动的切页逻辑",
        "showPanel() + .gv-nav__link 监听已就位",
    )
    # 每份 nav 静态 aria-current 只应有一个，且默认落在各自 panel 对应的项上
    # 注意：只统计 HTML 属性形态（`aria-current="page">`），排除 CSS 选择器与 JS 字符串
    static_cur = re.findall(r'aria-current="page">', src)
    check(
        "N7f 静态 aria-current 共 5 处（每份 nav 一处）",
        len(static_cur) == 5,
        f"found={len(static_cur)}",
        f"found={len(static_cur)}",
    )

    # ---------- N8 ARIA 引用完整性（无悬空 id 引用） ----------
    # 背景：删除 tabbar 后，panel 上的 aria-labelledby="tab-*" 变成悬空引用
    # （指向已不存在的 id）—— 屏幕阅读器会读到空标签。已改用 h-panel-* 指向页面标题。
    all_ids = set(re.findall(r'\bid="([^"]+)"', src))
    refs = re.findall(r'aria-(?:labelledby|controls|describedby|owns)="([^"]+)"', src)
    dangling = []
    for ref in refs:
        for token in ref.split():
            if token not in all_ids:
                dangling.append(token)
    check(
        "N8 ARIA 引用无悬空 id（labelledby/controls/describedby/owns）",
        not dangling,
        f"悬空引用={sorted(set(dangling))}",
        f"校验 {len(refs)} 处引用，全部有效",
    )
    check(
        "N8b 已无 role=\"tabpanel\"（随 tabbar 一并移除）",
        'role="tabpanel"' not in src,
        '仍存在 role="tabpanel"',
        "未发现 role=tabpanel",
    )

    # ---------- N1 ul 配对（仅统计 nav 区域内的 ul，页面别处还有合法的 <ul>） ----------
    nav_regions = [m.group(0) for m in re.finditer(r'<nav class="gv-nav".*?</nav>', src, re.S)]
    check("N1d 可解析出 5 份 <nav> 区域", len(nav_regions) == 5, f"found={len(nav_regions)}")
    n_ul_open = sum(r.count('<ul class="gv-nav__list">') for r in nav_regions)
    n_ul_close = sum(r.count("</ul>") for r in nav_regions)
    check(
        "N1a nav 区域内 <ul> 开合数量相等",
        n_ul_open == n_ul_close,
        f"open={n_ul_open} close={n_ul_close}",
    )
    check("N1b nav 的 <ul> 恰好 5 份（每 panel 一份）", n_ul_open == 5, f"found={n_ul_open}")

    # ---------- N6 孤儿 <li>（仅检查 nav 区域） ----------
    orphan_sources = []
    for ri, region in enumerate(nav_regions, 1):
        stripped = re.sub(r'<ul class="gv-nav__list">.*?</ul>', "", region, flags=re.S)
        if "<li" in stripped:
            for m in re.finditer(r".{0,80}<li.{0,120}", stripped, re.S):
                orphan_sources.append(f"nav#{ri}: " + m.group(0).replace("\n", " ")[:160])
    check(
        "N6 nav 区域内不存在 <ul> 之外的孤儿 <li>",
        not orphan_sources,
        f"发现 {len(orphan_sources)} 处：{orphan_sources[:2]}" if orphan_sources else "",
    )

    # ---------- 逐份 nav 校验 ----------
    blocks = list(re.finditer(r'<ul class="gv-nav__list">(.*?)</ul>', src, re.S))
    check("N1c 可解析出 5 份 nav 块", len(blocks) == 5, f"found={len(blocks)}")

    # 找到每份 nav 块所属的 panel（取其前最近的 id="panel-*"）
    panels = [m.start() for m in re.finditer(r'id="panel-([a-z]+)"', src)]
    panel_names = re.findall(r'id="panel-([a-z]+)"', src)

    def owner_panel(pos: int) -> str:
        idx = -1
        for i, p in enumerate(panels):
            if p < pos:
                idx = i
            else:
                break
        return panel_names[idx] if idx >= 0 else "?"

    label_seqs = []
    for bi, b in enumerate(blocks, 1):
        body = b.group(1)
        owner = owner_panel(b.start())

        # N2 项数
        items = re.findall(r'<li class="gv-nav__item">.*?</li>', body, re.S)
        check(f"N2 nav#{bi}({owner}) 恰好 5 项", len(items) == 5, f"found={len(items)}")

        # N3 链接
        hrefs = re.findall(r'href="([^"]*)"', body)
        dead = [h for h in hrefs if h in ("#", "", None)]
        check(
            f"N3a nav#{bi}({owner}) 无 href=\"#\" 死链",
            not dead,
            f"dead={dead}" if dead else "",
        )
        anchors = [h[1:] for h in hrefs if h.startswith("#panel-")]
        check(
            f"N3b nav#{bi}({owner}) 锚点集合完整",
            sorted(set(anchors)) == sorted(set(EXPECTED_SLUGS)) and len(anchors) == 5,
            f"anchors={anchors}",
        )

        # N4 aria-current
        cur = re.findall(r'href="#(panel-[a-z]+)"[^>]*aria-current="page"', body)
        expect_cur = f"panel-{owner}"
        check(
            f"N4 nav#{bi}({owner}) aria-current 指向自身且唯一",
            cur == [expect_cur],
            f"found={cur} expect=[{expect_cur}]",
        )

        # N5 顺序
        labels = [re.sub(r"\s+", " ", t).strip() for t in re.findall(r"</svg>\s*([^<]+?)</a>", body)]
        labels = [re.sub(r"\s+", " ", x).strip() for x in labels]
        check(
            f"N5 nav#{bi}({owner}) 项顺序符合 IA 基准",
            labels == EXPECTED_LABELS,
            f"got={labels}",
        )
        label_seqs.append(tuple(labels))

    if len(label_seqs) == 5:
        check("N5e 5 份 nav 完全同构（label 序列一致）", len(set(label_seqs)) == 1,
              f"distinct={len(set(label_seqs))}")

    # ---------- N9 类名完整性（防「用了但没定义」） ----------
    # 背景：2026-09-20 校对时发现 `<caption class="sr-only">` 引用了**从未定义**的
    # `.sr-only` —— 该表格 caption 本应仅对屏幕阅读器可见，却因类无定义而被**可见渲染**，
    # 与视觉标题重复。这类缺陷正则查不出、浏览器也不报错，只能靠类名交叉比对捕捉。
    tokens_css_path = os.path.join(os.path.dirname(HERE), "design", "tokens.css")
    css_all = ""
    for p in (tokens_css_path,):
        if os.path.exists(p):
            css_all += io.open(p, encoding="utf-8").read()
    m_style = re.search(r"<style>(.*?)</style>", src, re.S)
    if m_style:
        css_all += m_style.group(1)

    used_cls = set()
    for m in re.finditer(r'class="([^"]+)"', src):
        used_cls.update(m.group(1).split())
    defined_cls = set(re.findall(r"\.([A-Za-z][A-Za-z0-9_-]*)", css_all))
    undefined = sorted(c for c in used_cls if c not in defined_cls)
    check(
        "N9a mockup 引用的 CSS 类均有定义",
        not undefined,
        f"未定义={undefined}",
        f"校验 {len(used_cls)} 个类，全部有定义",
    )
    check(
        "N9b .sr-only 工具类已定义（视觉隐藏但保留无障碍树）",
        "sr-only" in defined_cls,
        "缺少 .sr-only 定义 —— 屏幕阅读器专用文本会被可见渲染",
        ".sr-only 已定义",
    )

    # ---------- N10 无「评审批注」残留 ----------
    # 设计稿是**产品视觉基准**，正文里不应出现写给评审者看的话（"原稿的问题是…"、
    # "图形替代原…"）。这类文字会随稿子被当成产品文案。项目说明统一放 design-system.md。
    reviewer_voice = [
        "原稿", "原截图", "本次改造", "替代原", "读者无法", "不写\"无数据\"",
        "评审时", "设计稿说明",
    ]
    body_only = src
    for tag in ("style", "script"):
        body_only = re.sub(rf"<{tag}>.*?</{tag}>", "", body_only, flags=re.S)
    # 剔除 HTML 注释（注释里保留设计意图说明是允许的）
    body_text = re.sub(r"<!--.*?-->", "", body_only, flags=re.S)
    hits = [w for w in reviewer_voice if w in body_text]
    check(
        "N10 mockup 正文无评审批注口吻残留",
        not hits,
        f"命中={hits}",
        "正文文案均为产品口吻",
    )

    # ---------- 汇总 ----------
    failed = [n for n, ok, _ in results if not ok]
    print("-" * 62)
    print(f"共 {len(results)} 项断言，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    if failed:
        print("失败项：")
        for f in failed:
            print("  -", f)
        return 1
    print("mockup 侧栏导航结构校验：全通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
