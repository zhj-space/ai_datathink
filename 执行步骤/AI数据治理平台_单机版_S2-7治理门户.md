# AI 数据治理平台 · 单机版 S2-7｜治理门户（自研 Streamlit）

> **定位**：`执行步骤/` 下的部署与代码产物，由《部署步骤_单机版》S2-7 展开，**不含新增架构决策**。
> **依据**：设计方案 2.1 组件表（**治理门户前端（自研，基于 Streamlit）**｜场景 D 智能问答聊天界面、场景 E 访问审批台，**调用 Governance Agent API**｜Streamlit 为 Apache-2.0；曾评估 Open WebUI 因品牌附加条款被排除）、第 4 章「统一入口（导航 launcher）」、场景 D/E。
> **S2-7 口径**：**"Agent 未就绪时降级显示"**（Governance Agent 属 S3 交付）。
> **状态**：**已部署并验证通过**（含"降级显示"与"只读 gov_metrics、不触其他库"的正反验证）。

---

## 1. 结论摘要

| 项 | 结果 |
|---|---|
| 容器 | `governance-portal` — **healthy**（约 15s 起），`restart: unless-stopped` + healthcheck（`/_stcore/health` → 200 `ok`） |
| 镜像 | 自建 `ai-governance/governance-portal:0.1.0`，imageID `sha256:d73803bbf78f624c62eb22e5d2255d806714e81550245d9903fcbb9745e4f968`（基础镜像 `python:3.12-slim` 固定 tag） |
| 依赖版本（已固定） | streamlit **1.64.0**｜pandas **3.0.5**｜psycopg2-binary **2.9.13** |
| 监听 | **仅回环 `127.0.0.1:8501`**（阶段选择，SSH 隧道访问；与 Superset/Airflow 同款） |
| 资源 | 0.5 vCPU / 512MB（与设计方案 4.2 一致） |
| 数据访问 | **只读 `gov_metrics`**（角色 `portal_ro`），经 **PgBouncer 6432** |
| 降级 | ✅ Governance Agent 未就绪时 `health()` 返回 `ready=False`，门户显示降级提示（不报错、不阻塞其余页面） |
| 反向权限 | ✅ `portal_ro` 连 `openmetadata_db` / `airflow_db` / `superset` **均被库级拒绝** |
| 容器口径 | **S2 段 7 个容器全部就位**（om-server、ingestion、presidio×2、opa、superset、governance-portal），与设计方案 S2 清单一致 |
| compose 校验和 | `6dba31da7b0339566c42994c355ada553b2125a41d4a8de0240cbab14ef677e2`（本轮变更后） |

---

## 2. 门户功能（POC 范围）

| 页面 | 内容 | 依据 |
|---|---|---|
| **首页 / 能力入口** | 卡片深链：OpenMetadata（资产目录/血缘/质量）、Superset（BI 看板）、Airflow（批处理）、Prometheus/Alertmanager；隧道类界面标注"需先开隧道" | 设计方案第 4 章「统一入口（导航 launcher）」 |
| **治理指标** | 资产覆盖/完备度（最新快照）、质量趋势、ETL 运行记录、"数据截至" | ADR-A6：门户只读指标层 |
| **智能问答（场景 D）** | Agent 就绪后为聊天界面；**未就绪时降级显示**并指向 OpenMetadata 检索 | 场景 D + S2-7 口径 |
| **访问审计与审批（场景 E）** | 说明**POC 只做事后审计 + 主动告警、不做事前拦截**；标注 PII 由 Presidio 离线批量识别 | 场景 E / **ADR-A3** |

**关键实现约束**：门户**不直连 OpenMetadata 内表**（只读 gov_metrics）；Agent 调用全部走 `GOVERNANCE_API_URL`，任何异常按"未就绪"处理（fail-safe，不抛错到用户）。

---

## 3. 部署记录

### 3.1 代码目录（入 Git）

仓库 `governance-portal/`：

| 文件 | 用途 |
|---|---|
| `app.py` | Streamlit 应用（4 个页面；降级逻辑） |
| `db.py` | gov_metrics 只读访问（连接即设 `readonly=True`） |
| `agent.py` | Governance Agent 客户端（health / chat；异常即视为未就绪） |
| `config.py` | 全部配置来自环境变量（DB DSN、Agent URL、入口链接、POC 口径声明） |
| `requirements.txt` | **固定版本**：streamlit 1.64.0 / pandas 3.0.5 / psycopg2-binary 2.9.13 |
| `Dockerfile` | `python:3.12-slim` + pip 源与环境变量参数化；非 root 用户运行 |

### 3.2 compose 服务（要点）

```yaml
  governance-portal:
    build:
      context: ./governance-portal
      args:
        PIP_INDEX_URL: http://mirrors.cloud.aliyuncs.com/pypi/simple/   # 见 §5 坑 1
        PIP_TRUSTED_HOST: mirrors.cloud.aliyuncs.com
    image: ai-governance/governance-portal:0.1.0
    restart: unless-stopped
    environment:
      GOVERNANCE_API_URL: http://governance-agent:8080     # S3 提供；当前未就绪 -> 门户降级
      GOV_METRICS_DB_HOST: pgbouncer
      GOV_METRICS_DB_PORT: "6432"
      GOV_METRICS_DB_NAME: gov_metrics
      GOV_METRICS_DB_USER: portal_ro
      GOV_METRICS_DB_PASSWORD: ${PORTAL_RO_PASSWORD}        # 从 .env 注入，不入仓库
      PORTAL_LINK_OPENMETADATA: https://localhost/
    ports: ["127.0.0.1:8501:8501"]                          # 仅回环
    networks: [backend-net, frontend-net]
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=5)\""]
    deploy: { resources: { limits: { cpus: "0.5", memory: "512M" } } }
```

### 3.3 数据权限（最小权限）

```sql
CREATE ROLE portal_ro LOGIN PASSWORD '<见 .env>';
GRANT USAGE ON SCHEMA gov_metrics TO portal_ro;               -- 仅 gov_metrics
GRANT SELECT ON ALL TABLES IN SCHEMA gov_metrics TO portal_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA gov_metrics GRANT SELECT ON TABLES TO portal_ro;
GRANT CONNECT ON DATABASE gov_metrics TO portal_ro;
-- 其余库的 CONNECT 已在 S2-5 收回 PUBLIC 并只放行各自角色
```

### 3.4 访问方式

```bash
ssh -N -L 8501:127.0.0.1:8501 root@<POC服务器公网IP>
# 浏览器打开 http://localhost:8501
```

---

## 4. 验证结果（实测）

| # | 验证项 | 实测 | 判定 |
|---|---|---|---|
| 1 | 容器健康 | `healthy`（约 15s），`restarts=0` | ✅ |
| 2 | 健康端点 | `/_stcore/health` → HTTP 200，body `ok` | ✅ |
| 3 | 监听 | `127.0.0.1:8501`（仅回环） | ✅ |
| 4 | **读指标层** | `数据截至 = 2026-09-17T14:25:01+00:00`；资产行数 8；ETL 记录 6 | ✅ |
| 5 | **降级显示** | `agent.health()` → `ready=False`（`governance-agent` 尚不存在，DNS 解析失败即视为未就绪） | ✅ 符合 S2-7 口径 |
| 6 | **反向权限** | `portal_ro` 连 `openmetadata_db`/`airflow_db`/`superset` → 均 `permission denied for database` | ✅ |
| 7 | 正向权限 | `portal_ro` 读 `gov_metrics.asset_coverage_snapshot` → 可读（48 行含历史轮次） | ✅ |
| 8 | 依赖版本 | 容器内实测 streamlit 1.64.0 / pandas 3.0.5 / psycopg2 2.9.13（与 requirements.txt 一致） | ✅ |
| 9 | 全栈 | 14 个容器全部 healthy | ✅ |

---

## 5. 执行中踩到的坑（如实记录，**对后续自建镜像同样适用**）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | **镜像构建长期挂住（15 分钟以上未完成）**：pip 进程存在但宿主负载 0.11、dockerd CPU 0% | 用**公网 PyPI 镜像 `mirrors.aliyun.com`** 在**构建容器内**下载极慢（实测 ~100 kB/s，10MB 要 109s）；而同机**宿主**访问同一镜像却有 16 MB/s、容器访问**阿里云内网镜像**有 74+ MB/s ⇒ 瓶颈在"构建容器 → 公网镜像"这条路径 | 改用**阿里云内网 PyPI 镜像** `http://mirrors.cloud.aliyuncs.com/pypi/simple/`；因其为 **HTTP**，pip 需 `--trusted-host`（已在 Dockerfile 用 `PIP_TRUSTED_HOST` 参数化）⇒ 构建**从 15 分钟挂住变为 17 秒完成** |
| 2 | 换内网源后构建立即报 `Could not find a version that satisfies the requirement streamlit` | 内网源是 HTTP，pip 默认拒绝不受信任主机 | Dockerfile 增加 `ARG PIP_TRUSTED_HOST`，构建时显式 `--trusted-host` |
| 3 | `docker compose up -d --build` 期间 SSH 会话超时（输出丢失） | 构建耗时超过会话超时 | 改用**前台 `--progress=plain` + 落日志文件**再读取的方式定位（后续大镜像构建建议照此） |

> **给后续自建镜像的纪律（已写入 AGENTS §7）**：本项目服务器在构建镜像时**优先使用阿里云内网 PyPI 镜像并显式 `--trusted-host`**；大镜像构建应前台 plain 输出并落日志，避免 SSH 超时丢证据。

---

## 6. 与设计边界的对齐

| 设计口径 | 本环境落实 |
|---|---|
| 门户为**自研 Streamlit**，仅承载场景 D 问答 + 场景 E 审批台 + 统一入口 | 4 个页面按此实现；**未重复建设** OM/Superset/Airflow 已有 UI，仅深链 |
| **Agent 未就绪时降级显示** | 已实现并实测（`ready=False` → 页面显示降级提示 + 替代入口） |
| 场景 E：POC 只做事后审计 + 主动告警（ADR-A3） | 审批台页面**明示不提供在线放行**，并列出审计/告警来源 |
| PII：Presidio **仅离线批量**（已移出在线路径） | 门户不调用 Presidio；页面标注该口径 |
| ADR-A6：只读 gov_metrics、不直连 OM 内表 | 只读角色 + 库级隔离；反向验证通过 |
| 最小暴露 | 仅回环 8501；对外仍只有 443/22 |

---

## 7. 待办 / 后续

| # | 项 | 说明 |
|---|---|---|
| 1 | 接 Agent 问答 | S3 部署 Governance Agent 后，`GOVERNANCE_API_URL` 生效即自动脱离降级（无需改代码） |
| 2 | SSO | 门户需自行集成 OIDC（设计方案 4.x）；与 S2-3 一并规划 |
| 3 | 443 分配 | 门户现仅隧道可达；给业务用户用需与 SSO 一并决定路径/子域 |
| 4 | 审批台实体化 | 场景 E 的"决策面/执行面/审计面"在 S3 落地后，本页可接审计查询与告警列表 |

---

## 8. 视觉验证：门户在浏览器中的实际渲染（2026-09-17 23:46 UTC+8）

对 4 个页面做**无头浏览器渲染 + DOM 文本判据**，并留截图供人工目视。

### 8.1 方法（可复现）

```bash
ssh -N -L 18501:127.0.0.1:8501 root@<server>      # 门户仍只绑 127.0.0.1:8501
# 无头 Chrome（--headless=new）+ CDP：打开页面 → 按真实时间等待渲染 → 抓 innerText + 整页截图
```

> **关键方法要点（本轮踩到的坑）**：Streamlit 的界面是经 **WebSocket** 增量下发的，用 `chrome --dump-dom`（配合 `--virtual-time-budget`）只能拿到**空壳 HTML**（实测 DOM 9 KB、页面只有 `You need to enable JavaScript to run this app.`、截图近空白）。必须改用 **CDP 驱动 + 真实时间等待**（`Page.navigate` → 等待 → `Runtime.evaluate` 取 `innerText` → `Page.captureScreenshot`）才能取到渲染结果。同类 Streamlit/SPA 界面后续验证一律用此法。

### 8.2 实测结果（深链 `?page=home|metrics|chat|audit`）

| # | 页面 | URL | 判据（DOM 实测文本） | 截图 |
|---|---|---|---|---|
| 1 | 首页 / 能力入口 | `/?page=home` | 标题 `🧭 AI 数据治理门户`；`数据截至：2026-09-17T15:25:02+00:00（指标层每小时 ETL，最长延迟 1h）`；5 个入口卡片（OM / Airflow / Alertmanager / Superset / Prometheus）均标注"需在本机先开 SSH 隧道" | `portal-home.png` |
| 2 | 治理指标 | `/?page=metrics` | `治理指标（只读 gov_metrics）`、`ADR-A6：门户只读指标层，不直连 OpenMetadata 内表。`、`已登记资产总数 1`、`治理完备度（已有负责人占比）0.0%`、口径说明（`asset_coverage_pct` 接源前为 NULL） | `portal-metrics.png` |
| 3 | 智能问答（场景 D） | `/?page=chat` | `元数据智能问答（场景 D）`、`Governance Agent 未就绪 —— 已降级显示（设计明确要求）。`、探测地址 `http://governance-agent:8080 → URLError (Temporary failure in name resolution)`、替代入口提示 | `portal-chat.png` |
| 4 | 访问审计与审批（场景 E） | `/?page=audit` | `访问审计与审批台（场景 E）`、`POC 期口径（ADR-A3）：只做事后审计 + 主动告警，不做事前拦截…`、PII 由 Presidio 离线批量（不在线调用）、审计来源/告警/审批三条边界 | `portal-audit.png` |

> 截图与 DOM 文本落在操作机（**仓库外**）：`C:\Users\Yuanhui\.ai-datathink\shots\`（各自 `.png` / `.txt` / `.json`）。另经 HTTP 直连校验：4 个深链 URL 全部 **200**。

### 8.3 判读与说明

1. **降级显示**在界面层成立：第 3 页与首页都以"未就绪 + 替代入口"呈现，无报错、无阻塞其余页面（对应 §4 第 5 项的服务端判据，本轮补上界面判据）。
2. **指标页的"1"是可解释的**：与库内 SQL 对照，最新快照（`2026-09-17 15:25:02`）8 类实体中只有 `pipeline = 1`（OpenMetadata 自身的元数据采集 pipeline），`table/database/databaseSchema/dashboard/topic/mlmodel/container = 0` —— **因为尚未接入业务源（S2-2 待办）**，这也正是 `asset_coverage_pct` 为 NULL 的原因。接源后该数才会 >0。
3. 指标页的表格是 Streamlit **canvas 渲染**，`innerText` 取不到表格行内容；因此页面上表格内的数值经**库内 SQL 对照**验证（同上），不靠截图判读。
4. 对外暴露面不变：门户仍只绑 `127.0.0.1:8501`，公网仅 443/22。

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-17 | 首次产出：自研 Streamlit 门户部署（4 页面、仅回环 8501、0.5vCPU/512MB、依赖版本固定、`portal_ro` 最小权限）、**降级显示与权限正反验证通过**、**构建容器内 PyPI 速度问题与解法**（内网源 + trusted-host，构建 15 分钟 → 17 秒），S2 段 7 容器全部就位 |
| v1.1 | 2026-09-17 | 新增 **§8 视觉验证**：4 个页面深链（`?page=home|metrics|chat|audit`）无头浏览器渲染 + DOM 判据全部通过（首页含"数据截至 2026-09-17T15:25:02+00:00"与 5 个入口；问答页与首页均呈现"Agent 未就绪"降级；审计页呈现 ADR-A3 口径）；记录**方法坑**（Streamlit 经 WebSocket 渲染，`--dump-dom` 只能取空壳，须 CDP + 真实等待）；说明指标页"已登记资产总数 1 = OM 自举 pipeline"的原因（未接业务源，S2-2 待办） |
| v1.2 | 2026-09-19 | **基础镜像按 digest 固定 + 镜像重建（人工批准）**：`FROM python:3.12-slim` 由浮动 tag 改为 **`python:3.12-slim@sha256:23b5dc88c7dd…`**（**debian 13.6 → 13.7**）。动因：GA 镜像重建时发现浮动 tag 会**静默换基础**（同轮实测 HIGH/CRIT 由 10/3 变 0/0，且钉错 digest 不会有任何报错）。**结果**：门户镜像漏洞 **13（HIGH 10 / CRIT 3）→ 0/0**（`--ignore-unfixed --severity HIGH,CRITICAL`，JSON 逐目标校验）；**验收复跑**：容器 healthy、`/_stcore/health` **200**、`/` **200**、`portal_ro` 经 PgBouncer 读到 `gov_metrics.asset_coverage_snapshot` **392 行**、最近 ETL 成功 `2026-09-19 03:25`、容器日志 5 分钟内 **0 条 error/traceback**。新镜像 `d86b159fc93f4…`；compose 校验和现行 **`05da87bd5e9b3f2f…`**。**功能范围与暴露方式未变**（仍只读 gov_metrics、仅回环 8501） |
| v1.3 | 2026-09-19 | **首页布局修复（人工实测反馈）**：此前首页「智能问答（场景 D）」区块**只有一行就绪提示、没有提问框**（真正的输入框在侧栏那一页），标题下面空着、用户体验差；且那行提示直接把 GA `/health` 的**原始 JSON** 贴出来，看起来像报错（人工实测原话："下面就是 Governance Agent 已就绪，这里布局有问题"）。**改动**：① 新增 `_agent_status()` —— 状态行改为**人话**（`Governance Agent 已就绪（version \`0.1.0\` · scope \`S3-6 首版\`）`），原始 `/health` 返回移入 **"就绪探测详情"折叠区**（仍可核验）；② **首页就地可提问**（`text_input` + "提问"按钮，直接调 GA `/chat` 并在本页显示回答），并提示"完整页面见左侧导航"；③ 问答页复用同一状态行（紧凑样式）；④ 修正 `config.py` 里 OpenMetadata 入口的**兜底默认值**（`https://localhost/` → `http://localhost:8585/`，与隧道口径一致；compose 早已用 env 覆盖，此处只是消除过期兜底）。**验收**：镜像重建 **`308e50f8ff4c…`**、**重扫 HIGH+CRITICAL = 0**、compose 校验和 **`03d0ebf8014e4ee0…`**；容器 healthy、`/_stcore/health` 200、`/` 200、深链 `?page=chat` 200；**Streamlit 官方 `AppTest` 无头跑四个页面（home/chat/metrics/audit）异常数全为 0**，并断言首页出现 `success=[Governance Agent 已就绪（version 0.1.0 · scope S3-6 首版）]` 与 `text_input=[直接提问…]`；`portal_ro` 仍读到 `asset_coverage` 8 行、`as_of=2026-09-19T14:25:01+00:00`。**只改前端展示与交互，未改数据口径、未改暴露方式（仍仅回环 8501）** |
| v1.4 | 2026-09-19 | **首页区块拆分（人工第二轮反馈："不应该把智能问答和它放在一起，分开说明"）**：v1.3 把服务状态行留在了「智能问答」区块内，仍把**运维信息**与**业务功能**混在一起。本次重构为三个独立区块：**① 能力入口**（链接）→ **② 系统状态（运维视角）**（Governance Agent 就绪与否 + 降级影响说明 + "探测详情"折叠原始 `/health`）→ **③ 智能问答（场景 D）**（只有输入框与回答；Agent 未就绪时该区块显示一行"依赖未就绪，见上方系统状态"，**不再重复报状态**）。代码上把"探测"与"渲染"拆开：`_agent_health()`（返回四元组）与 `_render_agent_status()`（仅系统状态区块调用）；**问答页不再显示就绪状态**（只在未就绪时给降级警告），避免同一信息在业务页重复出现。**验收**：镜像重建 **`e1fe07781a23…`**、**重扫 HIGH+CRITICAL = 0**、compose 校验和 **`3d09d579b0628d28…`**；容器 healthy、`/_stcore/health` 200、首页 200；**`AppTest` 四页异常数全 0**，断言首页 `subheader=['能力入口','系统状态（运维视角）','智能问答（场景 D）']`、`success=[已就绪…]`、`text_input=[直接提问…]`，而**问答页 `success=[]`**（状态不再混入）且 `text_input` 正常。**只改前端布局** |
| **v1.5** | 2026-09-20 | **视觉重构：按高保真设计稿重做门户（人工要求"按 `mockup.html` 重新设计"）**。① **设计资产入库**：`design/mockup.html`（5 页面板 + 主题切换，零硬编码色值）、`design/tokens.css`（74 个令牌 / 深色覆盖 31 项）、`design/design-system.md`（色彩·字体·布局·组件·无障碍·**Streamlit 落地映射**·验收口径）。② **落地层**：新增 `theme.py`（`--gv-*` 令牌块 + 组件 CSS + `emit_config_toml()`，避免与 Streamlit/BaseWeb 变量撞名）、`.streamlit/config.toml`（官方换肤入口）、`app.py` v1.5（改用原生控件 + `.gv-*` 自绘组件，**零硬编码色值**）。③ **IA 调整**：导航 5 页 = 首页/能力入口 → 智能问答 → 治理指标 → 访问审计与审批 → **系统状态（末位）**；首页改为**统一入口网格**（2 站内模块 + 5 外部深链，`target=_blank`），状态与问答各自成页（**替代 v1.3/v1.4 的"首页三区块"**）。④ **可执行验收（容器内实跑）**：`tools/check_tokens.py` ✅（浅色令牌 64 项两侧同值 / 深色 31 项覆盖 / **WCAG AA 对比度 28 组，最低 4.14:1**）；`tools/check_mockup_nav.py` ✅ **42/42**（含孤儿 `<li>`、死链、5 份 nav 同构、"评审批注口吻残留"黑名单）；`tools/verify_mockup_parse.py` ✅ 26 项（`html.parser` 真解析）；`tools/app_test_regression.py` ✅ **降级路径 62/62**。⑤ **本次修掉一个测试缺陷**：该回归脚本用 `os.environ.setdefault("GOVERNANCE_API_URL", <不可达>)` 模拟降级，但**容器内该变量本就存在**（指向真实 GA）⇒ setdefault 不生效 ⇒ 4 条降级断言**必然失败**（实测 58 通过/4 失败）。已改为**强制覆盖**并新增 `PORTAL_TEST_AGENT_URL` 开关（指向真实 Agent 时自动跳过降级组）；**两种模式现均以 0 退出**：降级 **62/62**、就绪 **57/57**。⑥ **就绪态实证**：系统状态页 `success=Governance Agent **已就绪**（version 0.1.0 · scope S3-6 首版）`、`warning=[]`，问答页 0 异常 0 警告。⑦ **部署态**：门户镜像重建 **`650a57b42570…`**（747 MB）、**重扫 HIGH+CRITICAL = 0**、compose 校验和 **`d030657ae80c5bd1…`**；容器 healthy、`/_stcore/health` 200、**5 个深链（home/chat/metrics/audit/status）全 200**；镜像内含 `design/` 与 `tools/`（校验脚本可在生产容器内直接跑）。**边界未变**：只读 `gov_metrics`（ADR-A6）、无在线审批表单（ADR-A3）、Agent 未就绪时降级（fail-safe）；`db.py`/`agent.py`/`config.py` 数据口径未改动 |
| **v1.6** | 2026-09-20 | **首页入口卡不再显示具体 URL（人工要求）**：原卡片 hint 显示 `localhost:8585` 这类地址（含站内卡的 `?page=…`），人工要求去掉——**卡片只给"是什么 + 怎么访问"，地址细节留给「系统状态」页的依赖服务清单**。改动：站内卡 hint → `站内模块`；外部卡 hint → `外部界面 · 需隧道`（非隧道时为 `外部界面`）；移除每张外部卡下重复的"需隧道"小字，改由首页底部口径条统一说明；**`design/mockup.html` 同步**（7 处 hint 文案，保持"设计稿 = 视觉基准"不漂移）。**新增回归断言**（`tools/app_test_regression.py`）：把 href 剥离后检查**可见文案**不含 `localhost/127.0.0.1/http(s)://` ⇒ 这类"顺手把地址露出来"的改动以后会被拦住。**验收**：镜像 **`ba381aa10dad…`**、compose **`ca2d8c791bce6391…`**、**重扫 HIGH+CRITICAL = 0**；`check_tokens` ✅、`check_mockup_nav` **42/42**、`verify_mockup_parse` 26 项 ✅、`AppTest` **降级 63/63（新增 1 项断言）/ 就绪 58/58**；首页可见文案实证 `URL 命中 = (无)`，hint 实际为 `['站内模块', '外部界面 · 需隧道', …]`；5 深链全 200、容器 19/19 healthy |
| **v1.7** | 2026-09-20 | **「需隧道」改成徽标而不是文案（人工要求；同时纠正 v1.6 的一处偏差）**：v1.6 把"需隧道"写进了 hint 文案，但**设计稿 `mockup.html` 原本就是徽标**（`<span class="gv-badge gv-badge--info">需隧道</span>` 挂在 `gv-entry__name` 里）⇒ 落地层与设计稿不一致。本次按设计稿改正：外部入口卡 `badge="需隧道"`（站内卡保持 `可用/降级中/全部正常` 徽标），hint 只留类别 **`外部界面`**；去掉原先冗余的 `打开 ↗` 徽标（卡片右侧已有 ↗ 箭头），并把 `design/mockup.html` 的 5 处 hint 由 `外部界面 · 需隧道` 收敛为 `外部界面`（避免徽标与文案重复）。**新增 2 条回归断言**：「需隧道」必须出现在 `gv-badge` 内、且**不得**出现在 `gv-entry__hint` 文案里。**验收**：首页实测徽标 5 个 `需隧道`（共 21 个 badge）、hint 为 `['站内模块','外部界面'…]`；`AppTest` **降级 65/65 / 就绪 60/60**；`check_tokens` ✅、`check_mockup_nav` 42/42、`verify_mockup_parse` 26 项；镜像 **`a8ac7d049c7d63bd…`**、compose **`bc24dd9171aff05e…`**、**重扫 0/0**、5 深链 200、容器 19/19 healthy |
| v1.8 | 2026-09-21 | **脱敏整改（同步 GitHub 前）**：把服务器**公网 IP** 与**主机名**替换为占位符（`<POC服务器公网IP>` / `<POC服务器主机名>`），以满足 AGENTS §9「域名/敏感信息一律占位符」；**未改任何口径、结论与判据**（仓库已推送 GitHub，历史提交中的原值另行处理） |

---

*本文件为 S2-7 治理门户部署产物，由《部署步骤_单机版》S2-7 展开，不含新增架构决策；门户功能范围与暴露方式的最终确认权归人工。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；版本号与参数执行前请按官方文档核对。
