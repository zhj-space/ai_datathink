"""治理门户（自研 Streamlit 前端）。

定位（设计方案 2.1 组件表 / 第 4 章 / 场景 D、E）：
  * 场景 D：元数据智能问答聊天界面（调用 Governance Agent API）
  * 场景 E：访问审批台（POC 期只做事后审计 + 主动告警，ADR-A3）
  * 统一入口：登录后首页/导航中心，深链到各能力界面
纪律：
  * 只读 `gov_metrics`（ADR-A6），**不直连 OpenMetadata 内表**
  * **Agent 未就绪时降级显示**（不报错、不阻塞其余功能）
视觉：
  * 设计令牌与组件样式由 `theme.py` 提供（单一事实来源），本文件**不写死任何色值**
  * 视觉基准见 `design/mockup.html`；规范见 `design/design-system.md`
  * 无障碍：WCAG AA、触摸目标 ≥44px、状态用"语义色 + 文案"双通道表达

版本：v1.5（2026-09-20 视觉重构，接入设计系统）
"""

from __future__ import annotations

import html
import json

import pandas as pd
import streamlit as st

import agent
import config
import db
import theme

st.set_page_config(page_title="AI 数据治理门户（POC）", page_icon="🧭", layout="wide")

# 设计令牌 + 组件样式（必须在任何渲染之前注入）
theme.inject_css(st)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _safe(fn, *args, **kwargs):
    """调用可能失败的数据读取；异常时**降级为未就绪**，不中断页面。"""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        st.error(f"读取指标层失败：{exc.__class__.__name__}: {exc}")
        return None


def _esc(value) -> str:
    """HTML 转义（所有拼进 markdown 的外部文本都必须过这一层）。"""
    return html.escape("" if value is None else str(value))


def _badge(text: str, kind: str = "mute") -> str:
    """状态徽标（内联 HTML）。`kind` ∈ ok / warn / err / info / mute。

    状态**同时**用颜色和小圆点 + 文案表达（色觉障碍用户仍可辨读）。
    """
    return f'<span class="gv-badge gv-badge--{kind}"><span class="gv-badge__dot"></span>{_esc(text)}</span>'


def _section(num: str, title: str, desc: str = "") -> None:
    """区块标题（序号 + 标题 + 说明），替代裸 `st.subheader`。

    同时输出一个对屏幕阅读器友好的 `st.subheader`，保证结构语义不丢失。
    """
    st.subheader(title)
    badge = f'<span class="gv-section-num" aria-hidden="true">{_esc(num)}</span>'
    desc_html = f'<p class="gv-section-desc">{desc}</p>' if desc else ""
    st.markdown(
        f'<div class="gv-section-title">{badge}<span>{_esc(title)}</span></div>{desc_html}',
        unsafe_allow_html=True,
    )


def _note(text: str) -> None:
    """口径/边界说明（左蓝色竖条，非告警语义）。"""
    st.markdown(f'<div class="gv-note">{text}</div>', unsafe_allow_html=True)


def _empty(title: str, text: str) -> None:
    """空状态：说清"数据从哪来 / 为什么还没有 / 现在去哪看"。"""
    st.markdown(
        f'<div class="gv-empty">'
        f'<div class="gv-empty__title">{_esc(title)}</div>'
        f'<div class="gv-empty__text">{text}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _kpi(label: str, value: str, unit: str = "", foot: str = "", accent: bool = False) -> str:
    """KPI 卡 HTML（脚注用于承载口径说明，避免口径丢失）。"""
    unit_html = f'<span class="gv-kpi__unit">{_esc(unit)}</span>' if unit else ""
    foot_html = f'<div class="gv-kpi__foot">{foot}</div>' if foot else ""
    cls = "gv-kpi gv-kpi--accent" if accent else "gv-kpi"
    return (
        f'<div class="{cls}">'
        f'<p class="gv-kpi__label">{_esc(label)}</p>'
        f'<div class="gv-kpi__value">{_esc(value)}{unit_html}</div>'
        f'{foot_html}'
        f'</div>'
    )


def _header() -> None:
    st.title("🧭 AI 数据治理门户")
    st.caption("POC 单机形态 · 治理指标只读 gov_metrics · Agent 未就绪时自动降级")
    as_of = _safe(db.as_of)
    if as_of:
        st.info(f"**数据截至：{as_of}**（指标层每小时 ETL，最长延迟 1h）")
    else:
        st.warning("指标层暂无数据（`gov_metrics` 尚无快照）")


def _agent_health() -> tuple:
    """就绪探测：返回 (是否就绪, 原始详情, version, scope)。

    **探测与渲染分开**（2026-09-19 人工实测反馈）：服务健康状态属"运维视角"，
    不能塞进业务区块（问答）里，否则用户分不清"这是功能入口还是系统告警"。
    """
    ready, detail = agent.health()
    ver = scope = ""
    if ready:
        try:
            info = json.loads(detail)
            ver = str(info.get("version") or "")
            scope = str(info.get("scope") or "")
        except Exception:  # noqa: BLE001  非 JSON 也照常展示
            pass
    return ready, detail, ver, scope


def _render_agent_status() -> bool:
    """渲染 **Governance Agent 服务状态**（人话 + 折叠原始返回）；仅供"系统状态"区块调用。"""
    ready, detail, ver, scope = _agent_health()
    if not ready:
        st.warning(
            "**Governance Agent 未就绪 —— 门户已降级（fail-safe）。**\n\n"
            f"探测地址 `{config.GOVERNANCE_API_URL}`，结果：`{detail}`\n\n"
            "**降级影响**：智能问答不可用；**资产目录 / 血缘 / 质量检索请走 OpenMetadata**（见上方入口），"
            "治理指标请走「治理指标」页或 Superset 看板。"
        )
    else:
        text = "Governance Agent **已就绪**"
        if ver and scope:
            text += f"（version `{ver}` · scope `{scope}`）"
        elif ver:
            text += f"（version `{ver}`）"
        st.success(text)
    with st.expander("探测详情（GA `/health` 原始返回）"):
        st.code(detail or "(空)")
    return ready


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

def page_home() -> None:
    # 首页 = **统一入口总览**：把"能力界面"和"站内模块"放在同一层网格里。
    # IA 变更（2026-09-20 用户要求）：系统状态、智能问答不再作为首页下方区块，
    # 而是各自提为独立模块，既在入口网格占位，也在左侧导航各占一项。
    _section("1", "能力入口", "统一入口总览：外部能力界面（深链）+ 站内模块。门户**只做入口与深链**，不重复建设 UI。")

    _ENTRY_META = {
        "OpenMetadata": ("📚", "资产目录 · 血缘 · 质量 · 标签（含 PII 识别）"),
        "Superset": ("📊", "治理指标看板（自定义图表与分享）"),
        "Airflow": ("🔁", "ETL 调度与运行记录"),
        "Prometheus": ("📈", "指标采集与告警规则评估"),
        "Alertmanager": ("🔔", "告警路由与通知渠道"),
    }

    def _entry_meta(name: str) -> tuple:
        for key, meta in _ENTRY_META.items():
            if key.lower() in name.lower():
                return meta
        return ("🔗", "外部能力界面")

    def _entry_card(icon, name, desc, hint, href, external, badge=""):
        tail = 'target="_blank" rel="noopener noreferrer"' if external else ""
        arrow = "↗" if external else "›"
        badge_html = f'<span class="gv-badge gv-badge--info">{_esc(badge)}</span>' if badge else ""
        st.markdown(
            f'<a class="gv-entry{" gv-entry--internal" if not external else ""}" '
            f'href="{_esc(href)}" {tail}>'
            f'<span style="font-size:1.35rem" aria-hidden="true">{icon}</span>'
            f'<span style="flex:1">'
            f'<span class="gv-entry__name">{_esc(name)} {badge_html}</span>'
            f'<span class="gv-entry__desc">{_esc(desc)}</span>'
            f'<span class="gv-entry__hint">{_esc(hint)}</span>'
            f'</span>'
            f'<span style="font-size:1.1rem;color:var(--gv-primary-600)" aria-hidden="true">{arrow}</span>'
            f'</a>',
            unsafe_allow_html=True,
        )

    # --- 站内模块（与外部深链同网格，但用页面链接，站内跳转）---
    # 顺序与左侧导航一致（IA 基准）：业务模块在前、运维页在后。
    # 入口卡顺序 ≠ 导航顺序会让用户产生"两套 IA"的错觉，故此处刻意对齐。
    _MODULES = [
        ("chat", "智能问答（场景 D）", "回答包含 LangGraph 节点轨迹、检索到的资产、上下文预算口径。"),
        ("status", "系统状态（运维视角）", "门户依赖的下游服务健康度；异常时门户**自动降级**（fail-safe），不阻塞其它入口。"),
    ]

    cols = st.columns(2)
    slot = 0

    # 先放站内模块（更靠前，因为是本门户自身能力）
    for slug, name, desc in _MODULES:
        ready, _detail, ver, _scope = _agent_health()
        if slug == "chat":
            badge = "可用" if ready else "降级中"
            ok = ready
        else:
            badge = "全部正常" if ready else "有降级项"
            ok = True  # 系统状态页本身始终可进（它就是用来展示降级信息的）
        with cols[slot % 2]:
            _entry_card(
                "🧭" if slug == "status" else "💬",
                name,
                desc,
                "站内模块",
                f"./?page={slug}",
                external=False,
                badge=badge,
            )
        slot += 1

    # 再放外部能力界面
    for name, url in config.ENTRY_LINKS.items():
        icon, desc = _entry_meta(name)
        is_tunnel = "localhost" in url or "127.0.0.1" in url
        with cols[slot % 2]:
            # ⚠️ 卡片上**不显示具体 URL**（人工要求，2026-09-20）：入口卡只给"是什么 + 怎么访问"，
            # 地址细节见「系统状态」页的依赖服务清单（那才是需要看地址的地方）。
            # 「需隧道」按设计稿（mockup）做成**徽标**，不放 hint 文案（人工要求，2026-09-20）。
            _entry_card(icon, name, desc, "外部界面", url, external=True,
                        badge="需隧道" if is_tunnel else "")
        slot += 1

    st.divider()

    # --- 口径与边界说明（首页保留一段，避免入口页无上下文）---
    _note(
        "**门户只读**指标层 `gov_metrics`（ADR-A6）；数据每小时 ETL，最长延迟 1h。"
        "资产目录 / 血缘 / 质量检索请走 **OpenMetadata**，自定义看板请走 **Superset**。"
        "带「需隧道」的外部界面需在本机先开 SSH 隧道（见《…单机版_测试入口与用例.md》）。"
    )


def page_status() -> None:
    """系统状态（运维视角）—— 独立页面。

    2026-09-20 从首页区块抽出为独立模块（用户要求）：服务健康属运维关注点，
    独立成页后才能被左侧导航直达、被深链 `?page=status` 引用。
    """
    _section("1", "系统状态（运维视角）", "门户依赖的下游服务；异常时门户**自动降级**（fail-safe），不阻塞其它入口。")
    _render_agent_status()

    st.divider()
    st.markdown("**依赖服务清单**")
    _note(
        "下表为门户运行所依赖的服务与端口。`localhost` 地址需在本机先开 SSH 隧道（详见运行手册）。"
    )
    rows = []
    for name, url in config.ENTRY_LINKS.items():
        rows.append({"服务": name, "访问地址": url, "类型": "隧道（本机）" if ("localhost" in url or "127.0.0.1" in url) else "内网直连"})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def page_metrics() -> None:
    _section("1", "治理指标（只读 gov_metrics）", "ADR-A6：门户只读指标层，不直连 OpenMetadata 内表。")

    coverage = _safe(db.latest_asset_coverage) or []
    if coverage:
        st.markdown("**资产覆盖 / 治理完备度（最新快照）**")
        df = pd.DataFrame(coverage)
        st.dataframe(df, width="stretch", hide_index=True)
        total = int(df["asset_total_count"].sum())
        missing_owner = int(df["missing_owner"].sum())
        # KPI 用自绘卡片：脚注承载口径，避免"数字漂移但口径丢失"
        kpi_cols = st.columns(2)
        with kpi_cols[0]:
            st.markdown(
                _kpi(
                    "已登记资产总数",
                    f"{total:,}",
                    unit="个",
                    foot="口径：`asset_total_count` 求和（当前快照）",
                    accent=True,
                ),
                unsafe_allow_html=True,
            )
        with kpi_cols[1]:
            if total:
                st.markdown(
                    _kpi(
                        "治理完备度（已有负责人占比）",
                        f"{(total - missing_owner) / total * 100:.1f}",
                        unit="%",
                        foot=f"分子 = 已登记 − 缺负责人（当前 {missing_owner} 个）",
                    ),
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    _kpi(
                        "治理完备度（已有负责人占比）",
                        "—",
                        foot="无已登记资产，**无法计算**（`—` 表示不适用，不等于 0）",
                    ),
                    unsafe_allow_html=True,
                )
        _note(
            "口径：`asset_coverage_pct`（已登记 ÷ 源侧应采集数）在接入业务源前为 NULL，"
            "**不得用完备度替代**。"
        )
    else:
        _empty(
            "暂无资产快照",
            "数据来自指标层 <code>gov_metrics</code> 的每小时 ETL。"
            "当前尚未产生快照，或采集尚未覆盖到资产维度。"
            "可到 <b>OpenMetadata</b> 查看实时资产目录。",
        )

    st.markdown("**质量趋势（最新快照）**")
    quality = _safe(db.latest_quality_trend) or []
    if quality:
        st.dataframe(pd.DataFrame(quality), width="stretch", hide_index=True)
    else:
        _empty(
            "暂无质量数据",
            "质量趋势在**接入业务源并跑通质量测试**后产生；"
            "当前 POC 仅接入平台自身指标，尚无业务源质量样本。",
        )

    st.markdown("**指标 ETL 运行记录**")
    runs = _safe(db.recent_etl_runs) or []
    if runs:
        st.dataframe(pd.DataFrame(runs), width="stretch", hide_index=True)
    else:
        _empty("暂无 ETL 运行记录", "ETL 每小时 :25 触发；首次运行完成后此处会出现记录。")


def page_chat() -> None:
    _section("1", "元数据智能问答（场景 D）", "基于元数据与血缘检索回答，**只读**、不产生写操作。")
    ready, detail, _, _ = _agent_health()
    if not ready:
        st.warning(
            "**Governance Agent 未就绪 —— 已降级显示**（设计明确要求）。\n\n"
            f"`{config.GOVERNANCE_API_URL}` → `{detail}`\n\n"
            "本页在 Agent 就绪后即可用；当前请使用 OpenMetadata 的搜索/血缘界面。"
        )
        return
    question = st.text_input("请输入问题（例如：哪些表包含 PII 标签？）")
    if st.button("提问") and question:
        ok, answer = agent.ask(question)
        (st.success if ok else st.error)(answer)


def page_audit() -> None:
    _section("1", "访问审计与审批台（场景 E）", "POC 期只做事后审计 + 主动告警（ADR-A3），不做事前拦截。")
    st.info(config.POC_NOTES["scenario_e"])
    _note(_esc(config.POC_NOTES["pii"]))
    st.markdown(
        "- 事后审计数据来源：OPA 决策（`governance.access_audit`）与审计日志\n"
        "- 主动告警：命中策略后进入 `governance.alerts`（S3 打通通知渠道）\n"
        "- 在线审批放行：**POC 不提供**（属后续阶段）"
    )


# 页面标识 → (标题, 渲染函数) ；标识同时用于 URL 深链 `?page=<slug>`
# 顺序即左侧导航顺序。
# IA 调整（2026-09-20）：
#   ① 系统状态、智能问答从首页区块提升为独立模块（各自成导航项 + 首页入口卡）；
#   ② 「系统状态」排到导航**最后一项** —— 它是运维视角的诊断页，
#      业务用户日常主路径是 首页 → 问答 → 指标 → 审计，运维页不该插在业务路径中间。
#      放在末尾既符合「先业务后运维」的阅读顺序，也让导航的 1~N 位置稳定可预期。
PAGES = {
    "home": ("首页 / 能力入口", page_home),
    "chat": ("智能问答（场景 D）", page_chat),
    "metrics": ("治理指标", page_metrics),
    "audit": ("访问审计与审批（场景 E）", page_audit),
    "status": ("系统状态（运维视角）", page_status),
}

_header()

# 深链支持：?page=metrics 直接进入对应页面（便于统一入口跳转与自动化验证）
default_slug = st.query_params.get("page", "home")
if default_slug not in PAGES:
    default_slug = "home"
labels = [f"{v[0]}" for v in PAGES.values()]
slugs = list(PAGES.keys())
index = slugs.index(default_slug)
choice_label = st.sidebar.radio("导航", labels, index=index)
chosen_slug = slugs[labels.index(choice_label)]
if st.query_params.get("page") != chosen_slug:
    st.query_params["page"] = chosen_slug
st.sidebar.divider()
st.sidebar.caption("POC · 自研前端（Streamlit, Apache-2.0）")
st.sidebar.caption("提示：本页可通过 ?page=home|chat|metrics|audit|status 深链")
PAGES[chosen_slug][1]()
