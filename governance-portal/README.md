# 治理门户（自研 Streamlit 前端）

> **基础镜像按 digest 固定（2026-09-19）**：`FROM python:3.12-slim@sha256:23b5dc88c7dd…`（debian 13.7）。
> 动因：浮动 tag 会在重建时**静默换基础**（GA 镜像实测踩到：debian 13.6 → 13.7，HIGH/CRIT 由 10/3 变 0/0）。
> 本次按此重建后**门户镜像漏洞由 13（10/3）降为 0/0**，并已复核功能（healthy、`/_stcore/health` 200、`portal_ro` 经 PgBouncer 读到 `gov_metrics`）。
> 换基础属组件版本变更：须同步《…S2-7治理门户.md》并重跑验收 + 重扫。

> **定位**：设计方案 2.1 组件表「治理门户前端（自研，基于 Streamlit）」——承载**场景 D 智能问答**、**场景 E 访问审批台**，并作为登录后的**统一入口（导航中心）**深链各能力界面。
> **依据**：设计方案第 4 章（统一入口）、场景 D/E、**ADR-A3**（POC 只做事后审计 + 主动告警）、**ADR-A6**（只读 gov_metrics）、**S2-7 口径"Agent 未就绪时降级显示"**。
> **权威文档**：《AI数据治理平台_单机版_S2-7治理门户.md》（含部署、验证与构建加速记录）。

| 文件 | 用途 |
|---|---|
| `app.py` | Streamlit 应用：首页/能力入口、治理指标、智能问答（场景 D）、访问审计与审批（场景 E） |
| `db.py` | 只读 `gov_metrics`（连接即 `readonly=True`；经 PgBouncer 6432） |
| `agent.py` | Governance Agent 客户端（`/health`、`/chat`）；**任何异常都按"未就绪"处理**（fail-safe） |
| `config.py` | 全部配置来自环境变量：DB DSN、Agent URL、各界面深链、POC 口径声明 |
| `requirements.txt` | **固定版本**：streamlit 1.64.0 / pandas 3.0.5 / psycopg2-binary 2.9.13 |
| `theme.py` | **Streamlit 落地层（2026-09-20 视觉重构新增）**：`--gv-*` 令牌块（与 `design/tokens.css` 同值，**双前缀是有意设计**：避免与 Streamlit/BaseWeb 的 `--color-*` 撞名）+ 组件 CSS（`.gv-card`/`.gv-badge`/`.gv-kpi`/`.gv-entry`/`.gv-note`/`.gv-empty`/`.gv-section-*`/`.gv-trace`/`.gv-mono`）+ `emit_config_toml()` |
| `design/` | **设计资产（视觉基准）**：`mockup.html`（5 页面板高保真稿）、`tokens.css`（令牌单一事实来源：74 令牌 / 深色覆盖 31 项）、`design-system.md`（色彩·字体·布局·组件·无障碍·**Streamlit 落地映射**·验收口径） |
| `tools/` | **可执行验收（容器内可跑）**：`check_tokens.py`（令牌一致性 + 深色覆盖 + WCAG AA 对比度）、`check_mockup_nav.py`（mockup 结构 42 项）、`verify_mockup_parse.py`（`html.parser` 真解析）、`app_test_regression.py`（AppTest + mockup 结构；**默认走"Agent 未就绪"降级路径**，`PORTAL_TEST_AGENT_URL` 指向真实 Agent 时跳过降级组） |
| `.streamlit/config.toml` | 由 `theme.py::emit_config_toml()` 生成（Streamlit 官方换肤入口，优先级高于注入 CSS） |
| `overview.md` | 本次视觉重构的**交付说明**（设计目标、落地映射、验收清单；面向评审） |

**纪律**：

1. **只读指标层**：门户只读 `gov_metrics`，**不直连 OpenMetadata 内表**（ADR-A6）；数据库侧由 `portal_ro` 最小权限兜底。
2. **Agent 未就绪必须降级**：不得把异常抛给用户；降级时给出替代入口（OpenMetadata 检索）。
3. **不重复建设 UI**：OM / Superset / Airflow 的能力一律深链其自带界面。
4. **依赖版本固定**；构建镜像时用**阿里云内网 PyPI 镜像 + `--trusted-host`**（公网源在构建容器内实测仅 ~100 kB/s，会导致构建长期挂住）。
5. 变更本目录后须同步《…S2-7治理门户.md》版本记录并重跑验证（健康、读指标层、**降级**、**反向权限**）。
6. **改 UI/视觉必须跑设计系统自带的校验**（容器内即可，无需本地装 streamlit）：
   `check_tokens.py`（改任何颜色都要跑）→ `check_mockup_nav.py` → `verify_mockup_parse.py` → `app_test_regression.py`；
   **令牌只有两个出处**：`design/tokens.css` 与 `theme.py::TOKENS`，改一处必须同步另一处（`check_tokens.py` 会报 DIFF）；
   **不要再往本目录放第三份 CSS 副本**（2026-09-20 曾误加 `static/*.css`，已删除）。
7. **`app.py` 不得写死任何色值**（由令牌承担）；`app_test_regression.py` 默认按"Agent 未就绪"跑（确定性），
   要验"就绪路径"须显式 `PORTAL_TEST_AGENT_URL=http://governance-agent:8080`（此时降级组自动跳过）。
