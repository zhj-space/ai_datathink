# AI 数据治理平台 · 单机版 S2-6｜Superset 与指标看板

> **定位**：`执行步骤/` 下的部署产物，由《部署步骤_单机版》S2-6 展开，**不含新增架构决策**。
> **依据**：设计方案 2.1 组件表（Apache Superset｜BI 报表与治理可视化大盘｜Apache-2.0｜**数据源直接对接 PostgreSQL**）、**ADR-A6**（指标层解耦）、S2-6 硬约束。
> **硬约束（ADR-A6）**：数据源**只读 `gov_metrics`**，**禁止直连 OM 内表**；看板须标注**「数据截至」**时间戳（每小时 ETL，最长延迟 1h）。
> **暴露方式（阶段选择，已获人工确认）**：**仅回环 `127.0.0.1:8088` + SSH 隧道**；对外暴露面零新增。443 的最终分配（路径前缀 vs 子域）与 SSO 一并规划（S2-3）。
> **状态**：**已部署并验收通过**（含"Superset 查询落在 gov_metrics"的正反验证）。

---

## 1. 结论摘要

| 项 | 结果 |
|---|---|
| 镜像 | **自建派生镜像** `ai-governance/superset:6.1.0-pg`，digest `sha256:c8e65ec48f77f8c7cc5953be79e16560d0168adb7d4c08b7baa22ca4cbdf7c3b`（基础 `apache/superset:6.1.0` 固定 tag） |
| 版本 | Apache Superset **6.1.0**（Python 3.10、SQLAlchemy 1.4.54、psycopg2-binary 2.9.13） |
| 容器 | `superset` — **healthy**，`restart: unless-stopped` + healthcheck（`/health` → 200 `OK`） |
| 监听 | **仅回环 `127.0.0.1:8088`**；未接 Traefik |
| 元数据库 | 独立库 **`superset`**，角色 `superset_user`；**经 PgBouncer 6432**（alembic 迁移实测 53 张表） |
| 数据源 | **唯一一个**：`gov_metrics (read-only, ADR-A6)`，使用 **`superset_ro`**；`allow_dml=false`、`allow_ctas=false`、`allow_file_upload=false` |
| 看板产物 | dataset 1（`gov_metrics.asset_coverage_snapshot`）、chart 1（资产总览，含 `run_ts`＝数据截至）、dashboard 1（`AI 数据治理 · 指标看板（gov_metrics 只读）`，已挂图） |
| 资源 | 1 vCPU / 2GB（与设计方案 4.2 一致） |
| compose 校验和 | `a4adb6ec3633d4bfad6a714ec2e2cec2b388cae8d4c123e1c0fb30c9921e0105` |

---

## 2. 验收结果（S2-6 验收标准：**Superset 全部查询落在 gov_metrics，无 OM 内表直查**）

| # | 验证 | 结果 |
|---|---|---|
| 1 | 数据源清单 | ✅ **只有一个**，URI 指向 `pgbouncer:6432/gov_metrics`，账号 `superset_ro` |
| 2 | 权限姿态 | ✅ `allow_dml=false`、`allow_ctas=false`（Superset 侧**只读**）＋ 数据库侧 `superset_ro` 仅有 gov_metrics 的 SELECT |
| 3 | **由 Superset 执行查询**（`select_star` / SQL Lab） | ✅ HTTP 200；SQL Lab 返回 `{"as_of": "2026-09-17T11:25:01+00:00", "rows": 24}` —— **"数据截至"来自 `max(run_ts)`** |
| 4 | **反向：查 OM 内表** | ✅ `select_star pipeline_entity` → **404 `Table not found on the database`** |
| 5 | PG 侧连接证据 | ✅ `superset_ro → gov_metrics`(1)｜`superset_user → superset`(2)：**数据查询只连 gov_metrics** |
| 6 | 附带确认：每小时 ETL 在跑 | ✅ 行数从首跑 8 → 24（8 类资产 × 3 个整点），与 `:25` 调度一致 |

> 其中第 4 项的更强一层保证在《S2-5gov_metrics与ETL》已验证：`superset_ro` **连 `openmetadata_db` 都会被库级拒绝**（`permission denied for database`）。

---

## 3. 部署记录

### 3.1 为什么要自建镜像（**关键**）

官方 `apache/superset` 镜像是 **lean** 变体，**不含 PostgreSQL 驱动**（实测 6.1.0：`psycopg2` 与 `psycopg` 均缺失）。Superset 既要连自己的元数据库，又要连只读数据源 `gov_metrics`，因此按官方做法派生镜像：

```dockerfile
FROM apache/superset:6.1.0          # 固定 tag，不用 latest
USER root
# 必须装进镜像自带 venv：CLI 与 gunicorn 都跑在 /app/.venv；
# 且该 venv 不含 pip，故用镜像自带的 uv 安装
RUN uv pip install --python /app/.venv/bin/python --no-cache psycopg2-binary
USER superset
```

构建：`docker build -t ai-governance/superset:6.1.0-pg /data/ai-governance/superset-image/`

### 3.2 compose 服务（要点）

```yaml
  superset:
    image: ai-governance/superset:6.1.0-pg
    restart: unless-stopped
    environment:
      SUPERSET_SECRET_KEY: ${SUPERSET_SECRET_KEY}                       # 从 .env 注入，不入仓库
      SUPERSET__SQLALCHEMY_DATABASE_URI: postgresql+psycopg2://${SUPERSET_DB_USER}:${SUPERSET_DB_PASSWORD}@pgbouncer:6432/superset
      SUPERSET_PORT: "8088"
      SUPERSET_ADMIN_USERNAME: ${SUPERSET_ADMIN_USER}
      SUPERSET_ADMIN_PASSWORD: ${SUPERSET_ADMIN_PASSWORD}
      SUPERSET_ADMIN_EMAIL: superset-admin@example.local
    command:                            # 自初始化：迁移 → 建管理员（幂等）→ init → 起服务
      - /bin/bash
      - -c
      - |
        set -e
        superset db upgrade
        superset fab create-admin --username "$$SUPERSET_ADMIN_USERNAME" --firstname Admin \
          --lastname Superset --email "$$SUPERSET_ADMIN_EMAIL" --password "$$SUPERSET_ADMIN_PASSWORD" || true
        superset init
        exec /app/docker/entrypoints/run-server.sh
    ports: ["127.0.0.1:8088:8088"]      # 仅回环
    networks: [backend-net, frontend-net]
    healthcheck: { test: ["CMD-SHELL", "curl -sf http://localhost:8088/health >/dev/null"], start_period: 180s }
    deploy: { resources: { limits: { cpus: "1", memory: "2G" } } }
```

> 元数据库连接**经 PgBouncer 6432**（硬约束 2）。实测 **alembic 迁移在 transaction 模式下正常**（建成 53 张表），与执行计划 M0 第 1 项（OM × PgBouncer）互为旁证。

### 3.3 访问方式（回环 + SSH 隧道）

```bash
ssh -N -L 8088:127.0.0.1:8088 root@<POC服务器公网IP>
# 浏览器打开 http://localhost:8088
```

**登录凭据**：见仓库外本地敏感文件 `server-info.local.md`（`SUPERSET_ADMIN_USER/PASSWORD`）；口令亦存于服务器 `.env`（chmod 600）。

---

## 4. 执行中踩到的坑（如实记录）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 容器反复重启，日志停在 `[init] superset db upgrade`，最终 `ModuleNotFoundError: No module named 'psycopg2'` | 官方 **lean** 镜像不带 PG 驱动 | **派生镜像**安装 `psycopg2-binary` |
| 2 | 派生镜像构建失败：`/app/.venv/bin/python: No module named pip` | 镜像 venv **不含 pip** | 用镜像自带的 **`uv pip install --python /app/.venv/bin/python`**；注意第一次误用系统 `pip`，驱动装到了系统 Python，运行期仍报缺驱动 |
| 3 | Superset API 写操作全部 400 `The CSRF token is missing`（仅带 Bearer 不够） | Superset 对写操作强制 CSRF | 保持会话 Cookie + `GET /api/v1/security/csrf_token/` 取 token，写请求带 `X-CSRFToken` |
| 4 | SQL Lab 执行 400 `{"json": ["Unknown field."]}` | 6.x 的 `/api/v1/sqllab/execute/` **不接受 `json` 字段** | 用 `{"database_id","schema","sql","runAsync"}` |
| 5 | 把图挂到看板报 405 | 关联是**更新**语义 | 用 **`PUT /api/v1/chart/{id}`** 带 `{"dashboards":[id]}` |

---

## 5. 与设计边界的对齐

| 设计口径 | 本环境落实 |
|---|---|
| ADR-A6：Superset 只读 `gov_metrics`，禁止直连 OM 内表 | Superset 侧 `allow_dml=false` ＋ 一个数据源只指 gov_metrics；DB 侧 `superset_ro` 仅 SELECT 且**连 OM 库被库级拒绝**（正向/反向均实测） |
| 看板标注"数据截至"，最长延迟 1h | 图 1 的列包含 `run_ts`；SQL 侧以 `max(run_ts)` 取"数据截至"（实测返回 11:25:01） |
| 组件与版本纪律 | 未引入新组件；自建镜像**固定基础 tag**（6.1.0）并记录 digest |
| 硬约束 2（经 PgBouncer） | Superset 元数据库与数据源**都经 pgbouncer:6432**；未使用 5432 |
| 最小暴露 | Superset **仅绑回环**、未接 Traefik；对外仍只有 443/22 |

---

## 6. 待人工 / 后续

| # | 项 | 说明 |
|---|---|---|
| 1 | 看板完善 | 现只有 1 张"资产总览"表图；建议后续补：**质量趋势图**（`quality_trend_snapshot`）、**"数据截至"单独展示**、资产覆盖率（待 S2-2 回填分母后才有意义） |
| 2 | 443 分配（**待决策**） | Superset 现在只能经隧道访问；若要给业务用户用，需与 S2-3 SSO 一并决定路径前缀或子域（属"怎么暴露"的选择） |
| 3 | 告警趋势 | Phase 2（依赖 Kafka `governance.alerts`，S3） |
| 4 | ~~Superset 元数据库备份~~ | **已闭环（2026-09-18）**：新增逐库恢复演练脚本 `restore_drill_pg_all.sh`，**Superset 库实测恢复 103/103 对象一致（0.63 s）**，与其余 5 个平台库一并通过；证据见《…S1-8备份恢复脚本.md》§5.1 |

---

## 7. 复核记录（2026-09-17 13:59 UTC 起）

对 S2-6 做了一轮**完整复核**（容器 → 登录 → 数据源 → 产物 → 查询落点 → 反向 → 新鲜度 → 暴露面），结果全部符合预期：

| # | 复核项 | 实测 | 判定 |
|---|---|---|---|
| 1 | 容器 | `status=running health=healthy restarts=0` | ✅ |
| 2 | `/health` 原文 | HTTP 200，body=`OK` | ✅ |
| 3 | 版本 | `apache-superset 6.1.0` | ✅ |
| 4 | 监听 | `LISTEN 127.0.0.1:8088`（docker-proxy） | ✅ 仅回环 |
| 5 | 登录 / CSRF | 登录 200（token 324 字符）／CSRF 91 字符 | ✅ |
| 6 | 数据源数量 | **1 个**（`gov_metrics (read-only, ADR-A6)`，`allow_dml=False`、`allow_ctas=False`） | ✅ |
| 7 | 产物 | dataset 1／chart 1（table，列含 `run_ts`）／dashboard 1（published） | ✅ |
| 8 | **Superset 执行查询** | `数据截至 = 2026-09-17T13:25:01+00:00`，`行数 = 40`，`ETL 轮次 = 5`；**新鲜度 34 分钟（≤1h）** | ✅ |
| 9 | **图级验证** | `PUT /api/v1/chart/warm_up_cache` → `{"chart_id":1,"viz_error":null,"viz_status":"success"}` | ✅ 图自身查询通路成立 |
| 10 | 看板页面 | `GET /superset/dashboard/governance-metrics/` → HTTP **200**（15014B） | ✅ |
| 11 | **反向：查 OM 内表** | `select_star pipeline_entity` → **404 `Table not found`** | ✅ |
| 12 | **反向：DB 级** | `superset_ro` 连 `openmetadata_db` / `airflow_db` → 均 **`permission denied for database`** | ✅ |
| 13 | 角色连接分布 | `superset_ro → gov_metrics`(1)｜`superset_user → superset`(1) | ✅ 数据查询只落 gov_metrics |
| 14 | 对外暴露面 | 公网仅 443/22；8088/3000/8080/8181/9090/9093 全部只绑回环 | ✅ |

**本轮新学到的两个 API 要点（已回写本文件与《部署步骤》S2-6）**：

1. **API 建的图必须补 `query_context`**：否则图在 UI 里会提示 `Chart has no query context saved. Please save the chart again.`。做法：`PUT /api/v1/chart/{id}` 带 `{"query_context": "<json>"}`（本轮已补写，686 字符）。
2. **图级验证用 `PUT /api/v1/chart/warm_up_cache`**（注意是 **PUT**，POST 会 405；路径**无尾斜杠**），返回 `viz_status: success` 即代表 Superset 成功执行了图自身的查询——这是无浏览器环境下最接近"图能出数"的服务端证据。

**当时未能自动验证的部分**：图与看板在浏览器中的**视觉渲染**（当时无浏览器环境）。服务端侧查询通路已由第 9 项证明 —— 该遗留项已由 **§8 视觉验证**补齐。

---

## 8. 视觉验证：看板在浏览器中的实际渲染（2026-09-17 23:49 UTC+8）

补上 §7 的遗留项（"图与看板在浏览器中的视觉渲染未自动验证"）。

### 8.1 方法（可复现）

```bash
# 1) 本机建隧道（回环 + 隧道，Superset 仍只绑 127.0.0.1:8088）
ssh -N -L 18088:127.0.0.1:8088 root@<server>
# 2) 无头 Chrome（--headless=new）经 CDP 驱动：
#    a. 打开 http://localhost:18088/login/，用**真实键盘事件**逐字键入账号口令并提交
#       （React/Chakra 受控表单不吃脚本赋值，必须 Input.dispatchKeyEvent 真敲）
#    b. 登录跳转成功后再导航到 /superset/dashboard/1/，按**真实时间**等待渲染
#    c. 抓 document.body.innerText + 整页截图，按"标题/图表名/列名/run_ts 数值"判定
```

### 8.2 实测结果

| # | 项 | 实测 | 判定 |
|---|---|---|---|
| 1 | 登录（浏览器内） | 提交后跳转 `/superset/welcome/` | ✅ |
| 2 | 看板标题 | `AI 数据治理 · 指标看板（gov_metrics 只读）`（Published，Admin Superset） | ✅ |
| 3 | 图表 | `治理资产总览（数据截至 run_ts）` **成功出数**，渲染 **56 行**（8 类资产 × 7 轮快照） | ✅ 界面能看到数 |
| 4 | 列 | `run_ts` / `entity_type` / `asset_total_count` / `missing_owner` / `governance_completeness_pct` | ✅ |
| 5 | 数据抽样（最新轮） | `2026-09-17 15:25:02  pipeline  1  1  0`；各实体 `table/database/... = 0`（未接业务源自 0，见 §6 待办） | ✅ 与库内一致 |
| 6 | 截图留档 | `superset-dashboard.png`（1600×1200） | ✅ |

> 截图与 DOM 文本落在操作机（**仓库外**）：`C:\Users\Yuanhui\.ai-datathink\shots\`（`superset-dashboard.png` / `.txt` / `.json`）。

### 8.3 判读与遗留

1. **S2-6 验收标准里的"看板可用"现已闭环**：此前证据只到"服务端图查询成功（`warm_up_cache`）"，本轮补到"浏览器里图真的出数"。
2. 待人工确认（不属缺陷）：图表当前展示**全部快照轮次**（56 行），不是"仅最新一轮"。若期望看板默认只显示最新快照，需给图加 `run_ts = (select max(run_ts) ...)` 过滤 —— 属看板内容调整，需人工拍板后执行。

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-17 | 首次产出：Superset 6.1.0 部署（自建派生镜像含 PG 驱动、元数据库经 PgBouncer、自初始化命令、仅回环 8088）、唯一只读数据源 `gov_metrics`（`superset_ro`，`allow_dml=false`）、dataset/chart/dashboard 1 套、**正反验收通过**（Superset 查询落在 gov_metrics、查 OM 内表 404）、五个坑与待办 |
| v1.1 | 2026-09-17 | 新增 **§7 复核记录**（14 项复核含图级 `warm_up_cache` 验证、ETL 新鲜度 34 分钟、库级与 Superset 级双重只读、暴露面）；补记两个 API 要点（API 建图需补 `query_context`；图级验证用 `PUT warm_up_cache`）；如实说明"浏览器视觉渲染"未自动验证 |
| v1.2 | 2026-09-17 | 新增 **§8 视觉验证**：经隧道 + 无头 Chrome（CDP）真实登录后渲染看板，标题/图表/列名/数值四项判据通过（图表 56 行、最新轮 `2026-09-17 15:25:02 pipeline 1 1 0`），补齐 §7 遗留的"浏览器视觉渲染"；记录 React 受控表单须真实键盘事件；提出"看板是否只显示最新快照"待人工确认 |
| v1.3 | 2026-09-18 | **§6 待办 #4 闭环**：Superset 元数据库的**恢复演练已覆盖**（新增逐库演练脚本，Superset 103/103 对象一致、0.63 s；证据见《…S1-8备份恢复脚本.md》§5.1）。另：**访问方式变更**——443 已收敛，Superset 经隧道 `http://localhost:8088` 访问（见《…S3前置部署记录.md》§10） |
| v1.4 | 2026-09-19 | **派生镜像重建 + 基础/依赖钉版（人工批准）**：① `FROM apache/superset:6.1.0` 改为**按 digest 固定**（`@sha256:16b50bbef664…`；`docker pull` 实测该 tag **已是最新** digest ⇒ 属"把现状写死"而非版本升级）；② 原先 `uv pip install psycopg2-binary` **未钉版本** ⇒ 改为 **`psycopg2-binary==2.9.13`**（＝镜像内实际安装版本），堵住"重建静默换依赖"；③ **重建方式更正**：superset 是**手工 `docker build`**、compose **无 `build` 段**（实测 `docker compose build superset` 返回 "No services to build"，是 no-op——本轮先踩到）。**结果**：新镜像 `0b42de36db7d…`；**漏洞数不变：100（HIGH 100 / CRIT 0）**，逐目标与重建前一致（debian 67 + python-pkg 33）⇒ **重建无收益**（基础已最新）。**验收复跑通过**：容器 healthy、`/health` **200**、`superset_ro → gov_metrics` **可读（400 行）**、**反向 `openmetadata_db` 被拒**、API 登录成功、看板 **1** 个 / 数据集 1 个（`asset_coverage_snapshot`）。compose 校验和现行 **`b154e94b6f212459…`**。**暴露面与只读边界未变**（仍仅回环 8088，只读 gov_metrics） |
| v1.5 | 2026-09-21 | **脱敏整改（同步 GitHub 前）**：把服务器**公网 IP** 与**主机名**替换为占位符（`<POC服务器公网IP>` / `<POC服务器主机名>`），以满足 AGENTS §9「域名/敏感信息一律占位符」；**未改任何口径、结论与判据**（仓库已推送 GitHub，历史提交中的原值另行处理） |

---

*本文件为 S2-6 的 Superset 部署产物，由《部署步骤_单机版》S2-6 展开，不含新增架构决策；暴露方式与看板内容的最终确认权归人工。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；版本号与参数执行前请按官方文档核对。
