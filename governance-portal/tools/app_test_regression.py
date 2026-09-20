"""门户 UI 回归（Streamlit AppTest + mockup 结构校验）。

目的
----
视觉重构（v1.5 接入 `theme.py`）**不得改变**任何被自动化断言的语义文案与页面行为。
本脚本把 `执行步骤/AI数据治理平台_单机版_测试入口与用例.md` 中约定的断言固化为可重跑测试。

覆盖
----
1. 五个页面（home / status / chat / metrics / audit）均可渲染，`at.exception` 为空
2. 左侧导航 5 项 + **IA 基准顺序**（首页→问答→指标→审计→系统状态）
3. 首页统一入口网格：站内模块卡 + 外部深链卡，且入口卡顺序与导航同构
4. Agent 未就绪路径：稳定走降级分支（fail-safe）
5. 指标页 / 审计页
6. `?page=<slug>` 深链可用 + 非法 slug 回落
7. 设计系统接入：页面 HTML 中确实注入了 `--gv-` 令牌
8. mockup.html 结构校验（防"侧栏重复/缺项/死链"回归 + 确认无冗余顶部导航）

用法
----
    python tools/app_test_regression.py
退出码 0 表示全绿。
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
sys.path.insert(0, APP_DIR)

# Agent 探测指向一个必然不可达的地址 → 稳定走「未就绪降级」分支（确定性）。
# ⚠️ 必须**强制覆盖**而不是 setdefault：本脚本常在容器内运行，而容器里
# `GOVERNANCE_API_URL` 本来就指向真实 Agent —— setdefault 不会生效，第 [4] 组
# 降级断言就会在"Agent 其实是好的"环境里失败（2026-09-20 实测踩到：58 通过 / 4 失败）。
# 需要跑"就绪路径"时显式传 PORTAL_TEST_AGENT_URL=http://governance-agent:8080。
os.environ["GOVERNANCE_API_URL"] = os.environ.get(
    "PORTAL_TEST_AGENT_URL", "http://127.0.0.1:1/health"
)

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = os.path.join(APP_DIR, "app.py")

PASS = 0
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def _qp(at: AppTest, key: str):
    """`query_params` 在 AppTest 里可能返回标量或列表，统一取标量。"""
    value = at.query_params.get(key)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def run(slug: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["page"] = slug
    at.run()
    return at


print("=" * 68)
print("门户 UI 回归（AppTest · streamlit 1.64.0）")
print("=" * 68)

# --- 1. 五页渲染无异常 -------------------------------------------------
print("\n[1] 五页渲染（exception 应为空）")
for slug in ("home", "status", "chat", "metrics", "audit"):
    at = run(slug)
    exc = [str(e.value) for e in at.exception]
    check(f"/?page={slug} 无异常", len(exc) == 0, str(exc[:2]))

# --- 2. 左侧导航（IA：系统状态与智能问答各占一项；系统状态排最后）----------
print("\n[2] 左侧导航项")
h = run("home")
nav = [r.label for r in h.sidebar.radio] or []
nav_labels = [x for x in ([h.sidebar.radio[0].options] if h.sidebar.radio else []) for x in x]
check("导航含「首页 / 能力入口」", any("首页" in s for s in nav_labels), f"实际={nav_labels}")
check("导航含「系统状态（运维视角）」独立项", any("系统状态" in s for s in nav_labels), f"实际={nav_labels}")
check("导航含「智能问答（场景 D）」独立项", any("智能问答" in s for s in nav_labels), f"实际={nav_labels}")
check("导航含「治理指标」", any("治理指标" in s for s in nav_labels), f"实际={nav_labels}")
check("导航含「访问审计与审批」", any("访问审计" in s for s in nav_labels), f"实际={nav_labels}")
check("导航共 5 项", len(nav_labels) == 5, f"实际 {len(nav_labels)} 项 {nav_labels}")

# IA 基准顺序（2026-09-20 第二轮调整）：
#   首页 → 智能问答 → 治理指标 → 访问审计 → 系统状态
# 顺序即 IA，回归必须锁死；否则"系统状态移到最左/中间"这类改动会静默通过。
IA_ORDER = ["首页 / 能力入口", "智能问答（场景 D）", "治理指标", "访问审计与审批（场景 E）", "系统状态（运维视角）"]
check(
    "导航顺序符合 IA 基准（首页→问答→指标→审计→系统状态）",
    nav_labels == IA_ORDER,
    f"实际={nav_labels}",
)
check(
    "「系统状态」位于导航最后一项",
    bool(nav_labels) and "系统状态" in nav_labels[-1],
    f"末项={nav_labels[-1] if nav_labels else None}",
)
check("侧栏仅一个导航控件（无重复导航）", len(h.sidebar.radio) == 1, f"实际={len(h.sidebar.radio)}")

# --- 3. 首页 = 统一入口网格 --------------------------------------------
print("\n[3] 首页统一入口网格")
subs = [s.value for s in h.subheader]
check("首页含「能力入口」区块", "能力入口" in subs, f"实际={subs}")
check("首页不再有独立「系统状态」区块（已提为模块）", "系统状态（运维视角）" not in subs, f"实际={subs}")
check("首页不再有独立「智能问答」区块（已提为模块）", "智能问答（场景 D）" not in subs, f"实际={subs}")

blob = "".join(b.value for b in h.markdown)
check("网格含站内模块卡 page=status", "page=status" in blob, "未发现 status 站内链接")
check("网格含站内模块卡 page=chat", "page=chat" in blob, "未发现 chat 站内链接")
check("网格含站内模块卡变体类", "gv-entry--internal" in blob, "未发现 gv-entry--internal")
# 入口卡顺序须与导航一致（避免"两套 IA"）：问答卡在前，系统状态卡在后
if "page=chat" in blob and "page=status" in blob:
    check(
        "首页问答卡在系统状态卡之前（与导航顺序同构）",
        blob.index("page=chat") < blob.index("page=status"),
        f"chat@{blob.find('page=chat')} status@{blob.find('page=status')}",
    )
for prod in ("OpenMetadata", "Superset", "Airflow", "Prometheus", "Alertmanager"):
    check(f"网格含外部深链 {prod}", prod in blob, "未发现")
check("外部深链用新窗口打开", 'target="_blank"' in blob, "未发现 target=_blank")

# 入口卡**可见文案**不得出现具体 URL（人工要求，2026-09-20）：
# 卡片只给"是什么 + 怎么访问"，地址细节去「系统状态」页的依赖服务清单看。
# 注意：href 里当然有 URL ⇒ 先剥掉 href="…" 再检查可见文本。
_visible = re.sub(r'href="[^"]*"', "", blob)
_leaks = [t for t in ("localhost", "127.0.0.1", "http://", "https://") if t in _visible]
check(
    "首页入口卡可见文案不含 URL",
    not _leaks,
    f"命中={_leaks}；可见片段={_visible[:160]}",
)
# 「需隧道」必须做成**徽标**（与设计稿 mockup 一致），不得退回成 hint 文案（人工要求，2026-09-20）
check(
    "「需隧道」以徽标（gv-badge）呈现",
    bool(re.search(r'gv-badge[^>]*>\s*需隧道', _visible)),
    "未在徽标内发现「需隧道」",
)
check(
    "「需隧道」不再出现在 hint 文案里",
    not re.search(r'gv-entry__hint[^>]*>[^<]*需隧道', _visible),
    "hint 文案仍含「需隧道」",
)

# --- 4. 降级路径 -------------------------------------------------------
# 本组是**降级态**断言：仅当探测地址不可达时成立。
# 若用 PORTAL_TEST_AGENT_URL 指向真实 Agent（用于"就绪态"人工核对），本组整体跳过——
# 否则会在"Agent 其实是好的"环境里报 4 条必然失败，看起来像回归（2026-09-20 实测）。
if os.environ.get("PORTAL_TEST_AGENT_URL"):
    print("\n[4] Agent 未就绪 → 降级路径（fail-safe）—— 已指向真实 Agent，本组跳过 ⏭")
else:
    print("\n[4] Agent 未就绪 → 降级路径（fail-safe）")
    st_page = run("status")
    check("系统状态页 success 元素数 == 0（未就绪不显示已就绪）", len(st_page.success) == 0, f"实际={len(st_page.success)}")
    check("系统状态页出现 warning（降级提示）", len(st_page.warning) >= 1, f"实际={len(st_page.warning)}")
    warn_text = " ".join(w.value for w in st_page.warning)
    check("降级提示含「未就绪」", "未就绪" in warn_text, warn_text[:80])

    c = run("chat")
    check("问答页 success 元素数 == 0", len(c.success) == 0, f"实际={len(c.success)}")
    cw = " ".join(w.value for w in c.warning)
    check("问答页降级提示含「未就绪」", "未就绪" in cw, cw[:80])

# --- 5. 指标页与审计页 ------------------------------------------------
print("\n[5] 指标页 / 审计页")
m = run("metrics")
msubs = [s.value for s in m.subheader]
check("指标页标题含「治理指标」", any("治理指标" in s for s in msubs), f"实际={msubs}")
check("指标页无异常", len(m.exception) == 0)

a = run("audit")
asubs = [s.value for s in a.subheader]
check("审计页标题含「访问审计」", any("访问审计" in s for s in asubs), f"实际={asubs}")
check("审计页无异常", len(a.exception) == 0)

# --- 6. 深链 ----------------------------------------------------------
print("\n[6] 深链 ?page=<slug>")
for slug in ("home", "status", "chat", "metrics", "audit"):
    at = run(slug)
    check(f"?page={slug} 生效", _qp(at, "page") == slug, f"实际={at.query_params.get('page')}")
at = run("nonexistent")
check("非法 slug 回落 home", _qp(at, "page") == "home", f"实际={at.query_params.get('page')}")

# --- 7. 设计系统接入 ---------------------------------------------------
print("\n[7] 设计系统接入（令牌注入）")
h2 = run("home")
blob2 = "".join(b.value for b in h2.markdown)
check("注入样式含 --gv- 令牌", "--gv-primary-700" in blob2, "未发现令牌声明")
check("注入样式含组件类 gv-entry", "gv-entry" in blob2, "未发现组件类")
check("注入样式含站内卡变体样式", "gv-entry--internal" in blob2, "未发现变体样式")
check("注入样式含 reduced-motion 兜底", "prefers-reduced-motion" in blob2, "缺少动效偏好兜底")
check("存在 st.subheader 语义标题（结构未因视觉层丢失）", len(h2.subheader) >= 1, f"实际={len(h2.subheader)}")

# --- 8. mockup.html 侧栏导航结构 ---------------------------------------
# 背景：2026-09-20 用户发现 mockup 侧栏底部多出一项「访问审计与审批（场景 E）」。
# 根因是 `panel-home` 的 `<ul class="gv-nav__list">` 提前闭合，其后的 <li> 成为
# 孤儿元素被浏览器提升渲染。此处内联做**确定性**结构校验，防止同类回归。
print("\n[8] mockup.html 侧栏导航结构")
import re as _re  # noqa: E402

_mock = os.path.join(APP_DIR, "design", "mockup.html")
if not os.path.exists(_mock):
    check("mockup.html 存在", False, _mock)
else:
    _src = open(_mock, encoding="utf-8").read()
    _navs = _re.findall(r'<nav class="gv-nav".*?</nav>', _src, _re.S)
    check("mockup 含 5 份侧栏 <nav>", len(_navs) == 5, f"实际={len(_navs)}")

    _ia = ["首页 / 能力入口", "智能问答（场景 D）", "治理指标", "访问审计与审批（场景 E）", "系统状态（运维视角）"]
    _ok_orphan, _ok_count, _ok_dead, _ok_order, _ok_cur = [], [], [], [], []
    _panels = [(m.start(), m.group(1)) for m in _re.finditer(r'id="panel-([a-z]+)"', _src)]

    for _i, _r in enumerate(_navs, 1):
        _owner = next((n for p, n in reversed(_panels) if p < _src.find(_r)), "?")
        # 孤儿 li：nav 区域内、<ul> 之外
        _stripped = _re.sub(r'<ul class="gv-nav__list">.*?</ul>', "", _r, flags=_re.S)
        if "<li" in _stripped:
            _ok_orphan.append(f"nav#{_i}({_owner})")
        # 项数
        _items = _re.findall(r'<li class="gv-nav__item">.*?</li>', _r, _re.S)
        if len(_items) != 5:
            _ok_count.append(f"nav#{_i}({_owner})={len(_items)}")
        # 死链
        _hrefs = _re.findall(r'href="([^"]*)"', _r)
        if any(h in ("#", "") for h in _hrefs):
            _ok_dead.append(f"nav#{_i}({_owner})")
        # 顺序
        _labs = [x.strip() for x in _re.findall(r"</svg>\s*([^<]+?)</a>", _r)]
        if _labs != _ia:
            _ok_order.append(f"nav#{_i}({_owner})={_labs}")
        # aria-current
        _cur = _re.findall(r'href="#(panel-[a-z]+)"[^>]*aria-current="page"', _r)
        if _cur != [f"panel-{_owner}"]:
            _ok_cur.append(f"nav#{_i}({_owner})={_cur}")

    check("mockup nav 无孤儿 <li>（侧栏重复内容根因）", not _ok_orphan, f"命中={_ok_orphan}")
    check("mockup 每份 nav 恰好 5 项", not _ok_count, f"异常={_ok_count}")
    check("mockup nav 无 href=\"#\" 死链", not _ok_dead, f"命中={_ok_dead}")
    check("mockup 每份 nav 项顺序符合 IA 基准", not _ok_order, f"异常={_ok_order}")
    check("mockup 每份 nav 的 aria-current 指向自身", not _ok_cur, f"异常={_ok_cur}")

    # tabs / panels 配对（tabbar 已删，改为校验"页面切换仅由侧栏驱动"）
    check(
        "mockup 已删除顶部 tabbar（切页只走侧栏）",
        'class="ds-tabs"' not in _src and 'role="tablist"' not in _src,
        "仍存在 .ds-tabs / role=tablist",
    )
    check(
        "mockup 5 个页面面板齐全",
        len(set(_re.findall(r'id="panel-([a-z]+)"', _src))) == 5,
        f"panels={sorted(set(_re.findall(r'id=.panel-([a-z]+).', _src)))}",
    )
    check(
        "mockup 侧栏链接驱动切页（JS 绑定 .gv-nav__link）",
        "gv-nav__link[href^=\"#panel-\"]" in _src and "showPanel" in _src,
        "未发现侧栏驱动的切页逻辑",
    )
    # ARIA 引用完整性：删 tabbar 后 panel 的 aria-labelledby 不能悬空
    _ids = set(_re.findall(r'\bid="([^"]+)"', _src))
    _refs = _re.findall(r'aria-(?:labelledby|controls|describedby|owns)="([^"]+)"', _src)
    _dangling = [t for r in _refs for t in r.split() if t not in _ids]
    check(
        "mockup ARIA 引用无悬空 id",
        not _dangling,
        f"悬空={sorted(set(_dangling))}" if _dangling else f"校验 {len(_refs)} 处",
    )
    # 零硬编码色值
    _body = _src[_src.find("</style>"):]
    _hexes = _re.findall(r"#[0-9a-fA-F]{3,8}\b", _body)
    check("mockup 正文零硬编码 hex 色值", len(_hexes) == 0, f"命中={sorted(set(_hexes))[:5]}")

    # 类名完整性：mockup 引用的每个 CSS 类都应有定义
    # （2026-09-20 校对发现 `<caption class="sr-only">` 的 .sr-only 从未定义 ——
    #   屏幕阅读器专用文本被可见渲染。这类"用了但没定义"正则查不出、浏览器不报错。）
    _css_all = ""
    _tk = os.path.join(os.path.dirname(HERE), "design", "tokens.css")
    if os.path.exists(_tk):
        _css_all += open(_tk, encoding="utf-8").read()
    _st = _re.search(r"<style>(.*?)</style>", _src, _re.S)
    if _st:
        _css_all += _st.group(1)
    _used_cls = set()
    for _m in _re.finditer(r'class="([^"]+)"', _src):
        _used_cls.update(_m.group(1).split())
    _def_cls = set(_re.findall(r"\.([A-Za-z][A-Za-z0-9_-]*)", _css_all))
    _undef = sorted(c for c in _used_cls if c not in _def_cls)
    check("mockup 引用的 CSS 类均有定义", not _undef, f"未定义={_undef}")
    check("mockup 无障碍工具类 .sr-only 已定义", "sr-only" in _def_cls, "缺少 .sr-only 定义")

    # 正文无评审批注口吻残留（设计稿是产品视觉基准，不是带批注的评审稿）
    _txt = _src
    for _tag in ("style", "script"):
        _txt = _re.sub(rf"<{_tag}>.*?</{_tag}>", "", _txt, flags=_re.S)
    _txt = _re.sub(r"<!--.*?-->", "", _txt, flags=_re.S)
    _voice = ["原稿", "原截图", "本次改造", "替代原", "读者无法", "评审时"]
    _hits = [w for w in _voice if w in _txt]
    check("mockup 正文无评审批注口吻残留", not _hits, f"命中={_hits}")

    # 用真实 HTML 解析器复现浏览器行为，确认 nav 下没有孤儿 <li>
    # （正则只能看源码；解析器能直接回答"nav 底下挂了什么"）
    import subprocess as _sp  # noqa: E402
    _vp = os.path.join(HERE, "verify_mockup_parse.py")
    if os.path.exists(_vp):
        _r = _sp.run([sys.executable, _vp], capture_output=True, text=True, encoding="utf-8")
        check(
            "mockup 经 HTML 解析器验证（无孤儿 <li>）",
            _r.returncode == 0,
            (_r.stdout or "").strip().splitlines()[-1] if _r.stdout else "",
        )
    else:
        check("mockup 经 HTML 解析器验证（无孤儿 <li>）", False, f"缺少 {_vp}")

# --- 汇总 -------------------------------------------------------------
print("\n" + "=" * 68)
print(f"通过 {PASS} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  -", f)
    print("结论：未通过 ✗")
    sys.exit(1)
print("结论：通过 ✓")
sys.exit(0)
