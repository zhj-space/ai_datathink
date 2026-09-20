# AI 数据治理平台 · 单机版 S2 接入方案（准备稿，未执行）

> **定位**：`执行步骤/` 下的部署准备产物，由《部署步骤_单机版》S2 段展开，**不含新增架构决策**。
> **前置门禁**：**S1 准出（门禁②）未签字放行前，本方案只做准备，不启动任何容器**（《单机版部署计划》S2-1 前置）。
> **状态**：**S2-1 已于 2026-09-17 执行完成**（OpenMetadata 服务已上线，见 §8 执行记录）；S2 其余步骤（ingestion、Presidio/OPA/Superset/门户、SSO）仍未执行。
> **起草日期**：2026-09-17 ｜ **依据**：《设计方案 v3.2》6.3 /《实施执行计划 v2.0-r2》Phase 1 /《部署步骤_单机版》S2。

---

## 1. 材料来源与版本固定（可复现）

官方发布：**OpenMetadata `2.0.1-release`**（published 2026-09-02），取自 `api.github.com` 发布资产（`github.com` 在服务器侧不可达，API 主机可达，故用 `Accept: application/octet-stream` 取资产）。

| 资产 | GitHub asset id | 大小 | sha256（本机存档） |
|---|---|---|---|
| `docker-compose-openmetadata.yml`（**采用**：仅 server + migrate） | 540648615 | 27582 B | `84eae775aa5f68da7f96d4a6086fa2f973ebdee1bf84f1322d8c0e2eeef90612` |
| `docker-compose-ingestion.yml`（**采用**：ingestion 服务定义） | 540648636 | 2353 B | `54f5d155763107dac0cb1689c25c0a0abd2c82ee555339dbc5d36287d4aa64a8` |
| `docker-compose-postgres.yml`（**不使用**，见 §2） | 540648595 | 30753 B | `3084e6a61604a3006f2db735d634f4f95a39eb4484bc79ea0ee5d8797fca1803` |
| `docker-compose.yml`（不采用） | 540648608 | 30801 B | 未取 |

**服务器存档位置**：`/data/ai-governance/s2/`（`quickstart-variant.yml`、`quickstart-ingestion.yml`、`quickstart-postgres.yml`）
**开发用文件（不要用）**：`docker/development/docker-compose-postgres.yml`（取自 main）——它用 `build:` **从源码构建**，需要仓库源码上下文，不是发布镜像。

> **版本固定纪律**：上表 sha256 为执行 S2 时的比对基准；**执行前须重新核对是否有新发布**（本方案按 2026-09-17 的 2.0.1 记录）。

## 2. 骨架选择：为什么不用 postgres 版 quickstart

| 方案 | 服务 | 判断 |
|---|---|---|
| 官方 `docker-compose-postgres.yml` | postgresql、**elasticsearch 9.3.0**、execute-migrate-all、openmetadata-server、ingestion | ❌ 会引入**第二套 PG 与检索引擎**，与既有组件重复，且使容器数远超既有清单；平台口径是「OpenMetadata + PostgreSQL（唯一）」「**OpenSearch** 作检索」 |
| 官方 `docker-compose-openmetadata.yml`（变体） | **execute-migrate-all、openmetadata-server** | ✅ 采用：正是"自带基础设施"骨架，把 DB/检索指向我们既有组件 |
| 官方 `docker-compose-ingestion.yml` | ingestion（Airflow） | ✅ 采用：单独叠加 |

## 3. 必改点（相对官方文件）

| # | 官方默认 | 本平台取值 | 依据 |
|---|---|---|---|
| 1 | `DB_HOST=${DB_HOST:-postgresql}`、`DB_PORT=${DB_PORT:-5432}` | **`DB_HOST=pgbouncer`、`DB_PORT=6432`** | 硬约束 2「所有服务经 PgBouncer(6432) 访问 PG，不允许直连 5432」；部署步骤 S2-1「唯一必改：连接串指向 pgbouncer:6432」 |
| 2 | `DB_SCHEME=${DB_SCHEME:-postgresql}`、`OM_DATABASE=openmetadata_db`、`DB_USER/DB_USER_PASSWORD` | 取本环境实际值（`overwrite with existing .env`） | 复用 S1 既有 PG 与凭证管理方式（`.env`，chmod 600） |
| 3 | `SEARCH_TYPE=${SEARCH_TYPE:- "elasticsearch"}` | **`SEARCH_TYPE=opensearch`** | 平台检索引擎为 OpenSearch |
| 4 | `ELASTICSEARCH_HOST=${ELASTICSEARCH_HOST:- elasticsearch}` | **指向既有 OpenSearch 容器**（注意官方默认值带前导空格，须去掉） | 复用 S1 的 OpenSearch，不新装 |
| 5 | `ELASTICSEARCH_SCHEME=http`、`USER=""`、`PASSWORD=""` | **`https` + `admin` + 既有口令** | 本环境 OpenSearch **security plugin 未关闭**（S1 记录 §3.3）；官方默认是"无认证 http"，直接照抄必失败 |
| 6 | `image: docker.getcollate.io/openmetadata/server:2.0.1` | **`openmetadata/server:2.0.1`（Docker Hub 名）** | 见 §4：`docker.getcollate.io` 在本环境**不可达**（它实际走 registry-1.docker.io） |
| 7 | `ports: ["8585:8585","8586:8586"]`（server）、`"8080:8080"`（ingestion） | **一律不发布端口**，改由 Traefik 走 443 路由 | 安全基线④「仅暴露 443」 |
| 8 | `airflow` 元数据库 `DB_SCHEME=mysql+mysqldb`、`DB_HOST=mysql` | **Postgres Airflow 库 + 指向 PgBouncer**（⚠️ 见 §5 风险 1） | 本平台无 MySQL |
| 9 | 官方 `app_net`（自建网段 172.16.240.0/24） | 挂既有三网（`frontend-net` / `backend-net` / `data-net`）与既有 compose 合并 | 硬约束：三网隔离，Compose 为唯一真相源 |
| 10 | 官方未收紧资源 | 按 0.3.1 本环境基调收紧（磁盘受限） | 《部署步骤_单机版》0.3.1 |

## 4. 镜像可达性实测（2026-09-17，服务器侧）

| 镜像引用 | 结果 | 说明 |
|---|---|---|
| `docker.getcollate.io/openmetadata/server:2.0.1` | ❌ **失败** | `docker manifest inspect` 报 `error pinging v2 registry: Get "https://registry-1.docker.io/v2/"` —— 该域名实际落到 Docker Hub，而 Docker Hub 在本环境不可达 |
| `openmetadata/server:2.0.1`（Docker Hub 名，经 registry-mirrors） | ✅ 可见（manifest 1310B） | 加速器 `docker.m.daocloud.io` 命中 |
| `openmetadata/ingestion:2.0.1` | ✅ 可见 | 同上 |
| `openpolicyagent/opa` | ✅ **真实拉取成功（9 秒）**，测试后已删除镜像 | 证明加速器对新仓库同样有效 |
| `apache/superset`、`ollama/ollama` | ✅ 可见 | 同上 |
| `mcr.microsoft.com/presidio-analyzer` / `presidio-anonymizer` | ✅ 可见（**直连 mcr**，非加速器） | Presidio 官方来源 |

**结论**：S2 所需镜像**全部可获得**，但**必须把 `docker.getcollate.io/*` 改写为 Docker Hub 名**（写成 getcollate 会直接拉失败）。
**注**：`docker manifest inspect` **不经过 registry-mirrors**，故它对 `docker.io` 名的检查会因直连 Docker Hub 而失败——上面的 ✅ 是用显式加速器路径（`docker.m.daocloud.io/...`）等价验证的；实际 `docker pull openmetadata/server:2.0.1` 由 daemon 走 mirror，无需改写 compose 的 registry 前缀。

## 5. 风险与待人工确认（**执行前必须拍板**）

| # | 风险 | 说明 | 建议 |
|---|---|---|---|
| 1 | **Airflow（ingestion）元数据库 × PgBouncer transaction 模式** | Airflow 官方对 PgBouncer transaction pooling 支持有限（Airflow 自己会在 DB 上做 schema 操作与会话级行为）。硬约束 2 要求全部经 PgBouncer，但此处可能被迫走 session 或直连 | **须按门禁③ 的同一套纪律处理：不自动降级**；建议 S2-4 实测时把 Airflow 元数据库一并纳入观测，DBA+DE 一并决策 |
| 2 | **OM 2.0.1 × OpenSearch 3.8.0 兼容性** | 官方 quickstart 的检索后端是 **Elasticsearch 9.3.0**；变体默认 `SEARCH_TYPE=elasticsearch`。OM 对 OpenSearch 3.x 的支持范围**需按官方文档核对**（AGENTS §3 版本纪律） | 执行前查官方兼容矩阵；若 OM 2.0.1 不支持 OS 3.8，需评估降 OM 版本或调整检索引擎——**属架构决策，须人工** |
| 3 | **OpenSearch 安全插件 × OM 接入方式** | 本环境 OS 开着 security plugin（HTTPS + admin 凭证）。OM 需能建索引、写审计 | 实测值：`ELASTICSEARCH_SCHEME=https`、`USER=admin`、`PASSWORD=<占位>`；若 OM 依赖的证书校验失败，需评估导入 CA（**不得**关闭 security plugin，S1-5 已声明） |
| 4 | 容器数口径 | 变体骨架（server+ingestion）不新增 PG/ES，符合既有 S2 七个容器口径；若改为照搬 postgres 版 quickstart 会破坏口径 | 按 §2 采用变体 |
| 5 | 磁盘 | OM server/ingestion 镜像各约 1GB+，PG 元数据与 Airflow 库持续增长；本机 99G（可用 86G） | 启动前核对余量；磁盘 80% 触发「记录、停止、转人工」 |
| 6 | OM 版本与既有文档 | 设计方案 6.2.3 声明镜像版本「需按官方文档核对」；本方案记录的是 **2.0.1**（2026-09-02 发布） | 执行前复核是否有更新版本与安全修复 |

## 6. 与门禁的对应关系

| 门禁 | 触发点 | 本方案中的位置 |
|---|---|---|
| **门禁③ T-M0-1**：OM × PgBouncer transaction 兼容性（日志零命中 `prepared statement .* does not exist` / `transaction aborted`） | S2-4 | `execute-migrate-all` 与 `openmetadata-server` 首次连库即触发；**失败不自动降级**，由 DBA+DE 决策 session/直连 |
| S2 准出门禁 | S2 段末 | 资产覆盖率 100%、PII 抽检 >90%、血缘 Top50 ≥90%、Superset 零查 OM 内表、合规文件归档 |

## 7. 本轮"未做"声明

> 本节为 §1~§6 准备阶段（2026-09-17 更早时点）的边界声明，**现已被 §8 的执行结果取代**：
> 准备阶段未启动任何 S2 容器；随后门禁①② 经人工确认放行，S2-1 已实际执行（见 §8）。

---

## 8. S2-1 执行记录（2026-09-17 实际落地）

### 8.1 结果

| 项 | 结果 |
|---|---|
| 新增容器 | `execute-migrate-all`（一次性作业）+ `openmetadata-server`；全栈由 7 → 9 个运行单元 |
| 数据库迁移 | 退出码 **0**；`openmetadata_db` 建成 **195** 张表（Flyway 迁移至 2.0.1） |
| OM 服务健康 | `healthy`，约 30s 起；`/healthcheck` 返回 `{"healthy":true,"database":{"healthy":true}...}` |
| **UI 可达性** | `https://<host>/` → **HTTP 200（27228B）**，经 Traefik **443**；容器端口 8585/8586 **未对外发布** |
| API 探活 | `/api/v1/system/config/jwks` → HTTP 200 |
| OM × OpenSearch | OM 已在既有 OpenSearch 建索引（`tag_search_index`、`column_search_index`、`dashboard_data_model_search_index` 等） |
| **门禁③ 预判** | 迁移阶段与 server 阶段日志中 `prepared statement .* does not exist`、`transaction aborted` **命中 0 次** |
| 镜像 | `openmetadata/server:2.0.1`（1.15GB，47s）、`openmetadata/ingestion:2.0.1`（7.91GB，198s）经加速器拉取成功；磁盘 7.9GB → 17GB（余 78GB） |

> **门禁③ 的正式判定在 S2-4**：需在 ingestion 就绪后跑「登录→浏览→搜索→ingestion→血缘/质量页」全链路并复采日志。本节数据是**预判**，不能替代 S2-4 结论。

### 8.2 执行中遇到的五个坑（根因 + 处置）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 迁移 `FATAL: SASL authentication failed`（新建角色经 6432 全部失败，`postgres` 却正常） | PgBouncer 用 `auth_file`，其中只有 `postgres`；**`auth_user` 为空**导致已配置的 `auth_query` 无法执行 → 新角色无法认证 | 增加 **`PGBOUNCER_AUTH_USER=postgres`**（bitnami 镜像 `libpgbouncer.sh:231` 支持该 env），使 `auth_query` 生效 ⇒ 任意 PG 登录角色可经 6432 认证。**这是 S1-4 基线的必要补强**（S1 阶段只有单角色，未暴露） |
| 2 | OM 连 OpenSearch（HTTPS + security plugin）的主机名/证书信任 | 节点证书 SAN 仅 `node-0.example.com` / `localhost`，**不含服务名 `opensearch`** | ① 给 opensearch 容器加网络别名 **`node-0.example.com`**（与 SAN 对齐）；② 由 `root-ca.pem` 生成 **JKS 信任库**并挂给 OM（`ELASTICSEARCH_TRUST_STORE_PATH`）。**全程未关闭 security plugin**（S1-5 声明） |
| 3 | 登录 API 返回 400 `Password needs to be encoded in Base-64` | OM 2.0.x **口令需 base64 编码**传输 | 记录口径：浏览器 UI 自动处理；命令行调 API 需自行 base64 |
| 4 | 改密后 admin 一度无法登录（默认口令也失效） | `PUT /v1/users/changePassword` 的编码语义与登录不一致（`oldPassword` 传明文、`newPassword` 原样存储），且 `requestType` 仅接受 `SELF\|USER`（传 `INTERNAL` 报 400 Invalid request format） | 改用 **bcrypt 哈希精准写库**定版（见 8.3）；**不重置整库** |
| 5 | **自助注册对公网开放**（探针真的建号，HTTP 201） | 官方默认 `AUTHENTICATION_ENABLE_SELF_SIGNUP=true`；更关键的是 **DB 中 `openmetadata_settings` 实体的 `enableSelfSignup` 覆盖了 env** | 关闭 `openmetadata_settings.enableSelfSignup` → 实测 `POST /api/v1/users/signup` 返回 **HTTP 501**（Self Signup is not enabled），用户数不变 |

### 8.3 口令定版方式（如实记录，含一次失误）

1. 先按 API 路径改密，其中一次返回 200，但登录语义不可预期 → **admin 一度完全无法登录**（默认口令也已失效）。
2. 处置：用 `openmetadata/ingestion` 镜像内自带的 `python3 + bcrypt` 生成 `$2b$12$…` 哈希，`UPDATE user_entity` 精准写入 `authenticationMechanism.config.password` → 以 `base64(明文)` 提交登录返回 **HTTP 200**。
3. **期间一次失误**：首次生成哈希的 `docker run` 参数写错（多传了 `python3`），导致把**空字符串**短暂写入口令字段（约 1 分钟），随后修正并复验通过。
4. 现状：`admin@open-metadata.org` + 新口令（记录在仓库外的本地敏感文件 `C:\Users\Yuanhui\.ai-datathink\server-info.local.md` 与服务器 `.env`）；**默认 `admin/admin` 已失效（401）**。
5. 遗留建议：请人工在 UI 里自查改一次口令（Settings → Users → admin），使口令口径完全由 UI 管理。

### 8.4 compose 变更与校验和链路

| 变更点 | 说明 |
|---|---|
| 追加 `execute-migrate-all` / `openmetadata-server` | 取自官方 2.0.1 发布资产的"自带基础设施"变体；环境变量以 YAML 锚点 `&om-env` 复用 |
| opensearch 增加网络别名 | `node-0.example.com`（见坑 2） |
| pgbouncer 增加 `PGBOUNCER_AUTH_USER` | 见坑 1 |
| OM 增加 `AUTHENTICATION_ENABLE_SELF_SIGNUP=false` | 见坑 5（最终生效靠 DB settings） |
| Traefik 动态配置 | 新增 `config/traefik/dynamic/openmetadata.yml`：`PathPrefix('/')` → `openmetadata-server:8585`，`websecure` 入口，**热加载不重启 Traefik** |

**校验和链路**：`3cec4d63…`（S1 初始）→ `895cb471…`（path.repo）→ `07654a18…`（运维接入回环端口）→ `17e26cd5…`（追加 OM 服务）→ **`aee7c2c4e645dcaa03feaa3c49b042eb7ef31ff09acdeb68fc10e775cbacbd80`（现行）**；各阶段备份 `docker-compose.yml.bak-20260917-*`。

### 8.5 未做 / 待办（下一步）

| # | 项 | 说明 |
|---|---|---|
| 1 | **ingestion 容器未启动** | 需要 Airflow 元数据库；且 **Airflow × PgBouncer transaction 模式**的处理方式仍未决策（§5 风险 1）——**不自动降级**，须 DBA+DE 定 |
| 2 | 门禁③ 正式测试 | S2-4 全链路（依赖 1） |
| 3 | 443 暴露面 | **已闭环（2026-09-18，人工决策"改走隧道"）**：本环境**无固定企业 VPN/出口 IP** ⇒ 不做限源，改为**撤回暴露** —— 443 不再发布（**宿主无 443 监听**）、OM 改 `127.0.0.1:8585` 回环 + SSH 隧道（与 Superset/Airflow/Prometheus/门户同口径）；ETL 基址与门户链接同步改。详见《AI数据治理平台_单机版_S3前置部署记录.md》§10 |
| 4 | OM 版本兼容性 | 实测 OM 2.0.1 与本机 OpenSearch 3.8.0 **可用**（索引已建、服务健康）；正式结论仍建议按官方兼容矩阵留档 |
| 5 | 未接业务源 | S2-2，依赖 1 |

---

## 9. S2-2 前置：ingestion（Airflow）落地（2026-09-17，方案 A 已通过）

### 9.1 结论

**方案 A（Airflow 元数据库经 PgBouncer transaction 模式）实测通过**，无需降级、未碰直连 501 红线：

| 判据 | 结果 |
|---|---|
| `airflow db migrate` 经 `pgbouncer:6432` | **成功**：`airflow_db` 建成 **71** 张表，`alembic_version = d2f4e1b3c5a7` |
| 门禁③ 同类判据（Airflow 侧） | `transaction aborted` **0**、`prepared statement .* does not exist` **0**、`Deadlock detected` **0** |
| 容器健康 | `healthy`（约 30s），`restarts=0`；`/api/v2/monitor/health` 显示 metadatabase / scheduler / dag_processor **全部 healthy** |
| OM ↔ ingestion 网络 | OM 容器内 `wget` 探测 `http://openmetadata-ingestion:8080/api/v2/monitor/health` **可达** |
| OM 侧管道 API | `GET /api/v1/services/ingestionPipelines` → HTTP 200 |

**Airflow 版本**：镜像内 **3.3.1**（SimpleAuthManager 模式）。**Airflow 元数据库与 OpenMetadata 元数据库分库**（`airflow_db` / `openmetadata_db`），同实例不同角色。

### 9.2 本轮新增的三个坑

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 6 | ingestion 容器反复重启，日志只打印 `airflow` 用法 | 我方服务定义**漏了官方 `entrypoint: /bin/bash` + `command: ["/opt/airflow/ingestion_dependency.sh"]`**，容器按镜像默认入口运行 `airflow`（无参数）→ exit 2 | 补回这两行；**注意：这不是 transaction 模式的问题**，修正后方案 A 才真正被测 |
| 7 | Airflow API 带 basic auth 恒返回 401 `{"detail":"Not authenticated"}` | Airflow 3.x + **SimpleAuthManager** 下认证走 **JWT**：需 `POST /auth/token`（表单）取 token，再 `Authorization: Bearer` | 实测：`/auth/token` 取到 token（365 字节）→ `/api/v2/dags` **HTTP 200**。OM 侧 `AIRFLOW_USERNAME/PASSWORD` 已与容器内 `simple_auth_manager_passwords.json` 对齐 |
| 8 | OM 启动报 16 次 `Failed to decrypt OpenMetadataConnection instance. Encryption key not found.` | **FERNET_KEY 事后变更**：既有 bot/secrets 是用"未设置 env 时的官方默认 key"加密的，我方后加的随机 key 解不开 | 将 OM 与 ingestion 的 FERNET_KEY **统一为同一值**后解密错误归零。**教训：FERNET_KEY 必须在首次迁移前定版，事后变更会导致已加密数据不可读** |

### 9.3 遗留决策（**须人工定**）

| # | 决策 | 说明 | 建议 |
|---|---|---|---|
| 1 | **FERNET_KEY 采用哪把** | **已闭环（2026-09-18）：已重做为自持 key**（`base64url(32 随机字节)`，值在 `/data/ai-governance/.env` 的 `OPENMETADATA_FERNET_KEY`，0600；compose 以 `${...}` 引用）。原决策（2026-09-17 保持官方默认值）按触发条件"接入含真实凭证的业务源之前"执行完毕：重置 `openmetadata_db` → 自持 key → 重新迁移（195 表，退出码 0）→ 复建 ingestion 链路（DagRun success）→ **门禁③ 判据复跑 0 命中** | 不再需要"接源前重评"；后续新增连接器可直接录入凭证。**务必注意**：`.env` 必须随备份出机（已新增 `backup_config.sh`），否则恢复后 secrets 不可解密。详见《AI数据治理平台_单机版_S3前置部署记录.md》§7 |
| 2 | OM → Airflow **端到端触发**未验证 | Airflow 侧 JWT 认证已通；OM 的 AirflowRESTClient 是否同样走 JWT，要等创建第一条 ingestion pipeline 才能确认 | 建议作为 S2-2 第一步：建一个 pipeline service + pipeline，观察 OM 是否成功向 Airflow 部署 DAG |
| 3 | 443 暴露面 | 同 §8.5 第 3 项 | **已闭环**（2026-09-18 改走隧道；安全组 443 规则可选删除） |

### 9.4 资源占用

| 项 | 值 |
|---|---|
| 磁盘 | `/` 已用 17G / 可用 78G（ingestion 镜像 7.91GB 为最大单项） |
| 容器 | 共 **9** 个运行中（S1 七个 + `openmetadata-server` + `openmetadata-ingestion`） |
| 新增宿主目录 | `/data/ingestion/{dag_generated_configs,dags,tmp}` |
| 校验和 | 现行 `dc13e89605104ffa4cff921003ca1314990a954466ec04ec9622b014e46e62e0`（历史：`3cec4d63…` S1 初始 → `895cb471…` path.repo → `07654a18…` 回环端口 → `17e26cd5…` 追加 OM → `aee7c2c4…` self-signup → `c7ab30ac…` ingestion） |

### 9.5 OM → Airflow 端到端触发验证（2026-09-17，进行中）

**结论**：**OM → Airflow 的认证与调用链已打通**；**连接器配置尚需人工在 UI 完成**（属 `[A+H]` 人工确认项）。

**已证实的部分（实测）**：

| # | 事实 | 证据 |
|---|---|---|
| 1 | OM 能成功调用 Airflow API | `POST /services/ingestionPipelines/deploy/{id}` 的失败信息来自 **Airflow 自己**（`airflow API returned Internal Server Error ...`），说明请求已到达并被处理 |
| 2 | Airflow 侧确实写入了 DAG 配置 | 宿主 `/data/ingestion/dag_generated_configs/airflow_metadata_poc.json` 生成（3503B） |
| 3 | Airflow 的 API 认证走 **JWT** | `POST /auth/token` 取 token → `Bearer` 调 `/api/v2/dags` 返回 200（basic auth 恒 401） |
| 4 | 修复了一个真实缺陷：**DAG 生成目录属主** | 目录由 root 创建时 Airflow（uid **50000**）写入报 `Permission denied`；`chown -R 50000:0 /data/ingestion` 后可写。**须写入部署基线** |

**（已解决）嵌套 `connection` 子对象**：通过内省 ingestion 镜像内 pydantic 模型取得精确结构，**已用 API 一次建对，无需人工在 UI 创建**：

```json
"connection": { "config": {
  "type": "Airflow",
  "hostPort": "http://openmetadata-ingestion:8080",
  "numberOfStatus": 10,
  "supportsMetadataExtraction": true,
  "connection": {
    "type": "RestAPI",          // 关键：不是 AirflowRestApi
    "apiVersion": "v2",          // 允许 v1/v2/auto（Airflow 3 → v2）
    "verifySSL": false,
    "authConfig": { "username": "<AIRFLOW_ADMIN_USER>", "password": "<AIRFLOW_ADMIN_PASSWORD>" }
  }
}}
```

> 定位方法（可复用）：在 ingestion 镜像内 `python3` 内省 `AirflowConnection.model_fields["connection"].annotation` 得到允许的联合类型，再逐个打印其 `model_fields` 与枚举成员。

**给人工的 UI 填写要点**：

> **前置：浏览器必须先信任自签证书**（否则 UI 登录会进 401 循环）。原因与处置见《部署步骤_单机版》S1-6 的本环境适配注：
> 证书未受信 ⇒ Chrome/Edge 禁用 Service Worker ⇒ OM 2.0.x 新 UI 登录态切换失败（实测：登录 200 后立刻 `loggedInUser` 401，循环）。
> 服务端侧已排除：用 curl 走 `/auth/login` 或 `/users/login` 取到的 Bearer token 调 `loggedInUser` 均返回 **200**；且**仅带 cookie 无效**（服务端只认 `Authorization: Bearer`）。

| 字段 | 取值 |
|---|---|
| 服务类型 | Airflow |
| Host / Port（REST API） | `http://openmetadata-ingestion:8080` |
| 认证 | 基本认证；用户名/口令见本地敏感文件 `server-info.local.md`（`AIRFLOW_ADMIN_USER/PASSWORD`） |
| 管道类型 | `metadata`（PipelineMetadata），勾选 includeLineage / includeUnDeployedPipelines |
| 调度 | 先设为暂停（`pausePipeline=true`），验证通过后再启用 |

**API 使用要点（供后续用脚本建实体时参考）**：`sourceConfig` 需 `{"config":{...}}` 包裹，而 **`airflowConfig` 是扁平对象**（再套一层 `config` 会报 `Invalid request format`）。

### 9.6 运维接入：Airflow UI（SSH 隧道，2026-09-17）

**做法**（与 Prometheus/Alertmanager 同一套，见 S1 记录 §8）：ingestion 服务增加 `ports: ["127.0.0.1:8080:8080"]`，
并加入 `frontend-net`（**仅挂 internal 网络的容器回环发布不生效**——S1 记录偏差 4 已两次复现）。

**实测验证**：

```
宿主监听          LISTEN 127.0.0.1:8080        （仅回环）
公网可达性        <POC公网IP>:8080 → 不可达 ✔   （当时 443/22 不受影响）
隧道（= ssh -L）  127.0.0.1:8080/                      → HTTP 200，title=Airflow
                  127.0.0.1:8080/api/v2/monitor/health  → metadatabase/scheduler healthy
                  127.0.0.1:8080/api/v2/version         → 3.3.1
反向对照          127.0.0.1:9200（未发布端口）→ Connection refused ✔
```

**人工使用方式**：

```bash
# 可与运维界面隧道合并成一条命令
ssh -N -L 8080:127.0.0.1:8080 -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 root@<POC服务器IP>
# 浏览器：http://localhost:8080
```

**登录凭据**：Airflow 用户名/口令见仓库外本地敏感文件 `server-info.local.md` 的 `AIRFLOW_ADMIN_USER/PASSWORD`（同一组值已配置在 OM 侧 `AIRFLOW_USERNAME/PASSWORD`，供 OM 触发管道用）。

**注意**：Airflow 3.x 的 API 认证走 **JWT**（`POST /auth/token`），浏览器 UI 会自动处理；用 curl 直连 API 时需先取 token。

### 9.6.1 UI 渲染复核（2026-09-17 23:53，无头浏览器）

| # | 项 | 实测 | 判定 |
|---|---|---|---|
| 1 | 登录页渲染 | `/auth/login` 渲染出 `Sign into Airflow` + 用户名/口令/`Sign in`（Chakra/React 表单） | ✅ |
| 2 | **登录接口** | **`POST /auth/token`**（`{"username","password"}`）→ **201 + `access_token`**；`POST /auth/login` → **405**（不是表单 POST，已踩） | ✅ |
| 3 | Bearer 直连 API | `Authorization: Bearer <token>` → `GET /api/v2/dags` **200**（`role: admin`）；不带 token → **401** | ✅ |
| 4 | UI 登录后页面 | 浏览器内真敲口令登录 → 首页；`/dags` 渲染出 **1 Dags**：`airflow_metadata_poc`（调度 `0 0 * * *`、上次 Dag 执行 `2026-09-17 13:18:37`、下次 `2026-09-18 08:00:00`） | ✅ 界面可用 |
| 5 | 截图留档 | `airflow-dags.png` / `airflow-home.png`（1600×1200，操作机 `~/.ai-datathink/shots/`） | ✅ |

> **方法要点**：Airflow 3 UI 是 React 受控表单，脚本直接赋值 `input.value` 不会触发提交（本轮实测点击后无任何网络请求）；必须用 CDP `Input.dispatchKeyEvent` **逐字真敲**，或直接调 `POST /auth/token` 取 JWT。

### 9.7 端到端闭环实测结果（2026-09-17，**通过**）

| 步骤 | 实测结果 |
|---|---|
| 建 pipelineService（Airflow / RestAPI 嵌套） | HTTP **201** |
| 建 ingestionPipeline（PipelineMetadata，`airflowConfig` 扁平） | HTTP **201** |
| **部署 DAG**（OM → Airflow） | HTTP **200**，`Workflow [airflow_metadata_poc] has been created`；`deployed=True` |
| Airflow 侧出现 DAG | `dag_id=airflow_metadata_poc`，`relative_fileloc=airflow_metadata_poc.py`；宿主 `/data/ingestion/dags/` 生成 .py（318B），配置在 `dag_generated_configs/`（3503B） |
| **触发**（OM → Airflow） | HTTP **200**，`Workflow [airflow_metadata_poc] has been triggered <DagRun ... state:queued>` |
| Airflow DagRun 执行 | 两次运行均 **success**；任务 `ingestion_task` 1 次尝试成功（04:36:16→04:36:22、04:36:22→04:36:24） |
| **OM 侧管道状态** | `pipelineState: **success**`；ingestion 统计 `{"name":"Airflow","records":8,"updated_records":0,"warnings":0,"errors":0,"filtered":0,"failures":[]}` |
| **OM 中落地的资产** | pipeline 服务下出现 **1 个 pipeline 资产**（`airflow_poc.airflow_metadata_poc`）——证明数据真的经 ingestion 写回 OM |
| **门禁③ 判据** | OM server 日志 `prepared statement .* does not exist` **0**、`transaction aborted` **0**；PgBouncer 侧同样 **0**，且池统计 `wait 0 us` |

> **意义**：这是**完整闭环**——OM server（经 PgBouncer transaction）→ Airflow API（JWT）→ DAG → OpenMetadata ingestion → 写回 OM（PgBouncer + OpenSearch）。
> 门禁③ 的**正式判定仍在 S2-4**（需按《部署步骤》S2-4 的完整动作与日志采集口径执行），本轮数据是其**前置证据**。

**过程中新踩的一个坑（已解决，值得记录）**：我们创建管道时设了 `pausePipeline: true`，**DagRun 会一直停在 `queued`**；而 **OM 的 `toggleIngestion` 只改 OM 自身的开关，不会取消 Airflow DAG 的暂停**。
处置：直接在 Airflow 侧 `PATCH /api/v2/dags/<dag_id>` 传 `{"is_paused": false}`（或在创建管道时就不暂停），任务随即被调度。

### 9.8 S2-2 下一步（接真实业务源）

前置已全部就绪。接业务源时按《部署步骤_单机版》S2-2 的纪律执行：连接器配置为 **`[A+H]`（凭证与访问范围必须人工确认）**，且遵守 ADR-A7（sqllineage 解析失败的 SQL 进"待人工确认"队列，禁止静默丢弃）。

**接源时请同时圈定"纳入范围"**（哪些库/schema、是否含视图与分区子表、临时表排除清单）——它是 S2 准出**覆盖率分母**的定义依据，须写入本方案的接入记录且事后不得擅自调整；量测与抽检模板见《AI数据治理平台_单机版_S2准出量测口径与抽检模板.md》。

---

### 9.9 页面口径复跑与「执行记录」回填机制（2026-09-17，为门禁③ 补齐）

本节记录的是**运维口径的两项实测发现**，为门禁③（S2-4）证据包 v1.1 的支撑材料；**不涉及任何架构或配置变更**。

#### 9.9.1 OM 页面口径走查（无头 Chrome，真实键鼠事件）

门禁原文的动作为"页面"口径。Agent 用 CDP 驱动无头 Chrome 逐条执行，判据取自**渲染后的 DOM 文本/状态**：

| 动作 | 结果 |
|---|---|
| OM 登录 | `/signin` → `#email`/`#password` 真键盘输入 → 登录成功（`nav-user-name=admin`） |
| 浏览元数据 | `/explore` 出资产卡（含 `airflow_poc / airflow_metadata_poc`） |
| 搜索 | `#searchBox` 输入 `airflow_metadata_poc` 回车 → `/explore/?search=...`，**1 result** |
| 跑完整 ingestion | **见 9.9.2 的入口限制**；最终由 Airflow UI 触发 `airflow_metadata_poc` → DagRun `manual__2026-09-17T17:50:04.811601+00:00` = success |
| 血缘页 | 实体页「血缘关系」tab → `/pipeline/airflow_poc.airflow_metadata_poc/lineage`；全局 `/lineage` 出 Service 血缘树 |
| 质量页 | `/data-quality` 出「数据质控 / 数据健康 / 覆盖范围 0 / 测试用例结果 0」（数值为 0 属预期，尚无质量测试定义） |

#### 9.9.2 方法坑：OM 实体页的"在 Airflow 中查看"指向容器内主机名

OM 管道实体页提供的跳转链接是 `http://openmetadata-ingestion:8080/dags/airflow_metadata_poc`——**容器内主机名**，从办公机浏览器**不可达**。这是"回环 + SSH 隧道"发布方式的**必然结果**，不是缺陷；运维侧应改走隧道地址（`127.0.0.1:18080`）。

另有两个实测方法坑（供后续 UI 自动化复用）：

1. **登录后不要立刻整页跳转**：`Page.navigate` 到应用路由会停在 `full-screen-loader`（登录态尚未落盘）。应先等待 `[data-testid=nav-user-name]` 出现，再走**应用内点击/整页跳转**，后者在 SPA 预热后可正常工作。
2. **触发 ingestion 的按钮在 Airflow UI**：OM 侧的触发入口在"管道服务"页（实测该路由停在 loader），实体页没有"运行"按钮；Airflow UI 上为 `[data-testid=trigger-dag-button]` → 弹窗内 `[data-testid=trigger-dag-submit]`。

#### 9.9.3 「执行」记录 `Pending → Success` 的回填机制（非缺陷）

**现象**：刚在 Airflow 触发完一次 ingestion，OM 管道实体「执行」记录里该次运行显示 `Pending`（无结束时间），而 Airflow 侧为 `success`、OM 的 ingestion 记录为 `pipelineState=success / records=10~11 / errors=0`。

**定位**（三层证据，逐步排除）：

| 步骤 | 结论 |
|---|---|
| 换触发入口（改用 OM 侧 `POST /services/ingestionPipelines/trigger/{id}`） | **同样停在 Pending** ⇒ 与触发入口无关 |
| 查"是否 OM 周期性从 Airflow 拉 DagRun 回填" | 近 6h 内 Airflow 侧**零** `/dags/.../dagRuns` 外部请求 ⇒ 不是轮询回填 |
| 查库内 `entity_extension_time_series`（`extension='pipeline.pipelineStatus'`） | 每次 ingestion 会把**最近 N 次 DagRun**（连接配置 `numberOfStatus: 10`）**整体重推**；当次运行上报的是**自身启动态 Pending**，终态由**下一次运行**写入。实测 `17:49:55Z` 那条在 `17:55` 的运行后被改写为 `Successful`（`endTime=17:50:10Z`），页面同步显示 `Success / 4.23 s` |

**运维含义**：门禁/巡检时，"刚跑完看到 Pending"属正常，**下一次运行（或下一次状态推送）后自动收敛**；若要求当场看到终态，可在页面上再触发一次。

**口径提醒**：OM 的 `GET /pipelines/name/{fqn}?fields=pipelineStatus` 返回的是**单条**状态（实测为启动态），浏览器的「执行」列表才是时间序列视图；判读状态请以「执行」列表为准。

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-17 | 首次产出（准备稿）：官方发布材料取用与 sha256 固定（OM 2.0.1-release）、骨架选择（变体而非 postgres 版）、10 项必改点、镜像可达性实测（getcollate 不可达 / Docker Hub 名经加速器可取 / OPA 实拉成功）、6 项风险（Airflow×PgBouncer、OM×OpenSearch 3.8、安全插件接入等）。**未执行任何部署动作** |
| v1.1 | 2026-09-17 | **S2-1 执行记录（§8）**：新增结果表、五个坑的根因与处置（PgBouncer auth_user、OpenSearch SAN 别名+JKS 信任库、base64 口令口径、改密语义与 bcrypt 定版、self-signup 被 DB settings 覆盖）、口令定版如实记录（含一次空哈希失误）、compose 校验和链路（现行 `aee7c2c4…`）、未做清单。状态由"未执行"改为"S2-1 已执行" |
| v1.2 | 2026-09-17 | **S2-2 前置（§9）**：ingestion 落地，**方案 A（Airflow 元数据库经 PgBouncer transaction）实测通过**（71 表 / 0 命中 / healthy / JWT 200）；新增三个坑（漏 entrypoint、Airflow 3 走 JWT 而非 basic、FERNET_KEY 事后变更导致解密失败）与三项遗留决策（FERNET_KEY 定版建议、OM→Airflow 端到端触发待验、443 暴露面）。校验和现行 `c7ab30ac…` |
| v1.3 | 2026-09-17 | **FERNET_KEY 决策落定**（人工：保持官方默认值，记风险与重评触发条件）；新增 **§9.5 OM→Airflow 端到端验证**：认证与调用链已打通（deploy 请求到达 Airflow 并写出 DAG 文件）、修复 DAG 目录属主缺陷（uid 50000）、连接器嵌套 `connection` 未定 ⇒ **转人工在 UI 创建**（[A+H]）+ UI 填写要点 + API 使用要点（airflowConfig 扁平）；测试残留已清理 |
| v1.4 | 2026-09-17 | §9.5 补 **UI 登录前置条件**：自签证书未受信 ⇒ 浏览器禁用 Service Worker ⇒ OM 新 UI 登录 401 循环（已定位并处置，详见《部署步骤_单机版》S1-6）；记录服务端侧排除结论（Bearer 有效、cookie 无效） |
| v1.5 | 2026-09-17 | 新增 **§9.6 运维接入：Airflow UI（SSH 隧道）**：回环发布 127.0.0.1:8080 + 加入 frontend-net、公网不可达与隧道端到端实测（UI 200 / health healthy / 3.3.1）、合并隧道命令、凭据指向本地敏感文件；§9.4 校验和更新为 `dc13e896…` |
| v1.6 | 2026-09-17 | **端到端闭环打通（§9.7）**：§9.5 的"未打通"改为已解决并给出精确连接结构（`connection.type=RestAPI`、`apiVersion=v2`、`verifySSL`、`authConfig`，含内省定位方法）；新增 §9.7 全链路实测结果（建服务/建管道/部署 DAG/触发/DagRun success/OM `pipelineState=success`/8 条记录/落 1 个 pipeline 资产/**门禁③ 两侧 0 命中**）+ 新坑（暂停的 DAG 会一直 queued，`toggleIngestion` 不取消 Airflow 暂停）+ §9.8 下一步。**无需人工在 UI 建服务** |
| v1.7 | 2026-09-17 | 新增 **§9.6.1 UI 渲染复核**（无头浏览器）：登录页渲染、`POST /auth/token` → 201 JWT、Bearer `GET /api/v2/dags` → 200/无 token 401、UI 登录后 `/dags` 渲染出 `airflow_metadata_poc`（上次 13:18:37／下次 08:00:00）、截图留档；补记方法要点（React 受控表单须真实键盘事件；`POST /auth/login` 是 405） |
| v1.8 | 2026-09-18 | 新增 **§9.9 页面口径复跑与「执行记录」回填机制**：① OM 页面口径走查（登录/探索/搜索/血缘/质量页）结果表；② 方法坑——实体页"在 Airflow 中查看"指向容器内主机名 `openmetadata-ingestion:8080`（回环+隧道的必然结果）、登录后不可立刻整页跳转、触发按钮在 Airflow UI；③ 定位 `Pending → Success` 回填机制（换触发入口同样 Pending ⇒ 非入口问题；6h 内无 Airflow DagRun 轮询 ⇒ 非轮询；库内 `entity_extension_time_series` 显示每次运行整体重推最近 N 次状态 ⇒ **终态由下一次运行写入**），并给出运维口径提醒。**未做任何配置/架构变更** |
| v1.9 | 2026-09-18 | **§9.3 遗留决策 #1 闭环（FERNET_KEY 重做）**：由"保持官方默认值"改为"**已重做为自持 key**"（重置 `openmetadata_db` → 自持 key → 195 表迁移退出码 0 → 链路复建 DagRun success → **门禁③ 判据复跑 0 命中**）；补记两项运维要求（`.env` 必须随备份出机；重置元数据库须同时清理 OpenSearch 幽灵文档）。证据见《AI数据治理平台_单机版_S3前置部署记录.md》§7 |
| v2.0 | 2026-09-18 | **443 暴露面闭环（改走隧道）**：§8.5 第 3 项与 §9.3 第 3 项由"待人工决策"改为**已闭环**——无固定企业 VPN/出口 IP ⇒ 撤回暴露（443 不发布、OM 走回环 8585 + SSH 隧道）；同步说明 ETL 基址与门户链接的连带修改。证据见《…S3前置部署记录.md》§10 |

---

*本文件为 S2 部署准备方案，由《部署步骤_单机版》S2 段展开，不含新增架构决策；与源文档冲突时以源文档为准并回报修订。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；镜像版本、兼容矩阵与命令执行前请按各组件官方文档核对。
