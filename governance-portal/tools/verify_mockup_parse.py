#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真实 HTML 解析器验证 mockup 侧栏 —— 复现浏览器的"孤儿元素提升"行为。

`check_mockup_nav.py` 用正则校验源码结构；本脚本更进一步：
用 `html.parser` 真解析 HTML，检查**每一份 `<nav class="gv-nav">` 的直接
`<ul>` 子元素里恰好有 5 个 `<li>`**。

为什么必须这样验：
  本次 bug 的形态是 `<li>` 位于 `<ul>` 之外。浏览器解析器会把它"提升"到
  `<nav>` 下渲染，从而在导航列表之外独立成行。源码正则只能发现"li 在 ul 外"，
  而解析器能直接告诉你"nav 下有几个 ul、每个 ul 有几个 li、nav 底下还挂了什么"。

用法：python tools/verify_mockup_parse.py
退出码 0 = 通过。
"""
from __future__ import annotations

import io
import os
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
MOCKUP = os.path.join(os.path.dirname(HERE), "design", "mockup.html")

IA = ["首页 / 能力入口", "智能问答（场景 D）", "治理指标", "访问审计与审批（场景 E）", "系统状态（运维视角）"]


class NavProbe(HTMLParser):
    """按 DOM 层级忠实地记录每份 `.gv-nav` 的直接子结构与 ul→li 归属。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.navs: list[dict] = []          # 每份 nav 的探测结果
        self.cur: dict | None = None        # 当前 nav
        self.depth = 0                      # nav 内部深度（用于判断直接子元素）
        self.in_ul = 0
        self.in_li = 0
        self.cur_li_text: list[str] = []
        self.cur_li_attrs: dict = {}

    def handle_starttag(self, tag: str, attrs_list) -> None:
        attrs = dict(attrs_list)
        classes = (attrs.get("class") or "").split()

        if tag == "nav" and "gv-nav" in classes:
            self.cur = {"direct_uls": [], "stray_children": [], "items": [], "nav_depth": self.depth}
            self.navs.append(self.cur)
            self.depth += 1
            return

        if self.cur is None:
            return

        if tag == "ul":
            if self.depth == self.cur["nav_depth"] + 1:
                # nav 的直接子 ul
                self.cur["direct_uls"].append([])
            self.in_ul += 1
        elif tag == "li":
            self.in_li += 1
            self.cur_li_text = []
            self.cur_li_attrs = attrs
            if self.in_ul and self.cur["direct_uls"]:
                # 记录（先占位，文本与 href 在后续 endtag 时补齐）
                self.cur["direct_uls"][-1].append(None)
        elif tag == "a":
            # href 在 <a> 上，不在 <li> 上 —— 在 li 内部时挂到当前 li
            if self.in_li:
                self.cur_li_attrs = {**self.cur_li_attrs, "href": attrs.get("href")}
        else:
            if (
                self.depth == self.cur["nav_depth"] + 1
                and tag not in ("div", "svg", "path", "ellipse", "circle", "span", "code")
            ):
                self.cur["stray_children"].append(tag)

        self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self.cur is None:
            return
        self.depth = max(0, self.depth - 1)

        if tag == "li":
            txt = "".join(self.cur_li_text).strip()
            if self.in_ul and self.cur["direct_uls"]:
                lst = self.cur["direct_uls"][-1]
                for i in range(len(lst) - 1, -1, -1):
                    if lst[i] is None:
                        lst[i] = (txt, self.cur_li_attrs.get("href"))
                        break
            else:
                # 孤儿 li：没有归属的 ul
                self.cur["items"].append(("__ORPHAN__", txt, self.cur_li_attrs.get("href")))
            self.in_li = max(0, self.in_li - 1)
            self.cur_li_text = []
            self.cur_li_attrs = {}
        elif tag == "ul":
            self.in_ul = max(0, self.in_ul - 1)
        elif tag == "nav":
            self.cur = None

    def handle_data(self, data: str) -> None:
        if self.cur is not None and self.in_li:
            self.cur_li_text.append(data)


def main() -> int:
    if not os.path.exists(MOCKUP):
        print(f"FATAL: 找不到 {MOCKUP}")
        return 1

    html = io.open(MOCKUP, encoding="utf-8").read()
    probe = NavProbe()
    probe.feed(html)

    fails: list[str] = []

    def ck(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))
        if not ok:
            fails.append(name)

    ck("解析出 5 份 .gv-nav", len(probe.navs) == 5, f"found={len(probe.navs)}")

    for i, nav in enumerate(probe.navs, 1):
        uls = nav["direct_uls"]
        ck(f"nav#{i} 直接子 <ul> 恰好 1 个", len(uls) == 1, f"found={len(uls)}")
        ck(f"nav#{i} 无孤儿 <li>（落在 <ul> 之外）", not nav["items"],
           f"孤儿={[x[1] for x in nav['items']]}" if nav["items"] else "")
        if uls:
            items = [x for x in uls[0] if x]
            ck(f"nav#{i} 该 <ul> 下有 5 个 <li>", len(items) == 5, f"found={len(items)}")
            labels = [t for t, _href in items]
            ck(f"nav#{i} 项顺序符合 IA 基准", labels == IA, f"got={labels}")
            dead = [h for _t, h in items if not h or h == "#"]
            ck(f"nav#{i} 无死链", not dead, f"dead={dead}" if dead else "")

    print("-" * 62)
    total = 1 + len(probe.navs) * 5
    print(f"共约 {total} 项断言，失败 {len(fails)}")
    if fails:
        print("失败项：")
        for f in fails:
            print("  -", f)
        print("结论：未通过 ✗")
        return 1
    print("HTML 解析器视角：侧栏结构正确，无孤儿 <li> ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
