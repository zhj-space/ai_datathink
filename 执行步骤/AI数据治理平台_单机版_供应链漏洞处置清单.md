# AI 数据治理平台 · 单机版 供应链漏洞处置清单（决策件）

> **文档性质**：把 2026-09-18 的镜像扫描结果整理成**可直接勾选的处置清单**。**升级动作属组件版本变更，需人工批准**；本文件只提方案、步骤与回滚，不代批。
> **扫描口径**：`trivy image --scanners vuln --ignore-unfixed --severity HIGH,CRITICAL`（**只统计"上游已有修复"的高/严重项**）；完整方法、四层卡点与原始结论见《AI数据治理平台_单机版_S3前置部署记录.md》§9。
> **必读纪律**：**计数 ≠ 可利用**。这些是"可修复项的数量"，不等于"可被利用的漏洞数量"；处置方式（升级 / 等上游 / 记录风险接受）**属人工决策**。**不得**在任何准出材料里写成"已扫清"。
> **版本**：v1.0（2026-09-18）

---

## 1. 结论摘要

| # | 结论 | 依据 |
|---|---|---|
| 1 | **多数镜像已是最新 digest** ⇒ 漏洞**只能等上游重建**，我方无法单方面消除 | 逐 tag 拉取比对 digest（§3.2） |
| 2 | ~~重建自建派生镜像即可降漏洞~~ → **实测更正（同日）**：**当前重建无收益**。四个自建镜像**已包含其基础镜像的全部 layer**（门户/GA 4/4、superset 29/29、flink 11/11），且三个基础 tag 的**本地 digest 与上游完全一致** ⇒ 基础层未变，重建只会产出相同层。**正确做法：把"重建"改为触发式**——先 `docker pull <基础镜像>` 比对 digest，**变了才重建** | 见 §3.4 层比对实测 |
| 3 | **2 个镜像有明确可升版本**：`prom/alertmanager`（有新 digest）、`apache/kafka`（4.0.1 补丁 / 4.1.0 大版本） | §3.2 / §3.3 |
| 4 | **2 个镜像零高危**：`openmetadata/server`、`openpolicyagent/opa` | 扫描结果 |
| 5 | **`zcube/cadvisor` 属已知供应链替代品**（gcr.io 不可达），99 HIGH/6 CRIT 需与"换可信源/自建"一并决策 | S1 记录 §4 偏差 3 |

---

## 2. 逐镜像清单（按 CRITICAL 降序，其次 HIGH）

| # | 镜像（当前固定 digest 前 12 位） | HIGH | CRIT | 上游是否有更新 | **建议动作** |
|---|---|---|---|---|---|
| 1 | `openmetadata/ingestion:2.0.1`（`00bfe3febfd4`） | 149 | **5** | 版本固定为 2.0.1（未查 2.0.x 更新） | **等上游 2.0.x 补丁并核对**；本版本无官方补丁前**记录风险接受** |
| 2 | `bitnami/pgbouncer:latest`（`823c47c0615e`） | 117 | **8** | ✔ 已是最新 | **记录风险接受**（tag 已最新）；关注 Bitnami 后续重建 |
| 3 | `mcr.microsoft.com/presidio-anonymizer:latest`（`a10a12a2a613`） | 88 | **8** | ✔ 已是最新 | **记录风险接受**；或改用 Presidio 官方源码自建（成本高，需评估） |
| 4 | `mcr.microsoft.com/presidio-analyzer:latest`（`286e3fa7f3a7`） | 86 | **8** | ✔ 已是最新 | 同上 |
| 5 | `apache/kafka:4.0.0`（`3f7b939115cd`） | 70 | **7** | ★ **4.0.1 / 4.1.0 可拉取** | **建议升 4.0.1（补丁级）**；4.1.0 属大版本，需按官方升级矩阵核对（§4） |
| 6 | `zcube/cadvisor:latest`（`f5d9acd3d30d`） | 99 | **6** | ✔ 已是最新（社区镜像） | **与"换可信源/自建"一并决策**（供应链风险已在 S1 记录登记）；短期**记录风险接受** |
| 7 | `opensearchproject/opensearch:latest`（`fafe3fc35870`） | 35 | **6** | ✔ 已是最新 | **记录风险接受** |
| 8 | `traefik:v3.0`（`a208c74fd80a`） | 77 | **5** | 未查（`v3.0` 为浮动 minor） | 可评估升 `v3.x` 最新补丁；**当前 443 未对外发布，暴露面已大幅降低** |
| 9 | `ai-governance/governance-portal:0.1.0`（`a8ac7d049c7d…`，**自建**；2026-09-20 最近一次因**「需隧道」改徽标**重建，见文末 v1.11） | **0** | **0** | 基础 `python:3.12-slim@sha256:23b5dc88c7dd…`（**debian 13.6 → 13.7**） | ✅ **已重建并实测：HIGH/CRIT 由 13（10/3）降为 0/0**（JSON 逐目标校验）；重建后**功能复核**：容器 healthy、`/_stcore/health` 200、5 个深链 200、`portal_ro` 经 PgBouncer 读到 `asset_coverage` 行、日志无异常 |
| 9b | `ai-governance/governance-agent:0.1.0`（`44ca36b1e519…`，**自建**；2026-09-19 最近一次因 **RAG 上下文预算定档 1800** 重建，见文末 v1.6） | **0** | **0** | 基础 `python:3.12-slim@sha256:23b5dc88c7dd…`（**debian 13.6 → 13.7**） | ✅ **实测：HIGH/CRIT 由 10/3 降为 0/0**（`--ignore-unfixed --severity HIGH,CRITICAL`，`--format json` 逐目标：debian 0、python-pkg 0）。**这正是本清单 §4.3「触发式重建」的预期收益**——上游重建基础镜像后重建自建镜像确实降漏洞（此前"重建无收益"仅对**基础未变**时成立）。**教训**：本地 tag 与"新 digest"要分别核实——本轮先误钉了旧的 `78387bc3881b…`（重扫又回到 13），改用 `23b5dc88c7dd…` 后才 0/0。**后续多次重建（基础未变）重扫均为 0/0** |
| 10 | `pgvector/pgvector:pg16`（`ccc6e83d6e35`） | 24 | **1** | 未查（浮动 tag `pg16`） | 重建时按新 digest 固定（注意：**升级 PG 镜像需考虑数据目录兼容**，须在维护窗口做） |
| 11 | `prom/alertmanager:latest`（`690c7b525f43`） | 8 | 0 | ★ **有新 digest（`e9733bafb1bd`）** | ★ **建议升级**（无状态、配置已就位、回归成本低，§4） |
| 12 | `ai-governance/flink:1.20.1-cdc3.6.0`（`5681153a3742`，**自建**） | 31 | 0 | 基础 `flink:1.20.1-…` 可重拉 | ★ **重建镜像**（§5） |
| 13 | `ai-governance/superset:6.1.0-pg`（`0b42de36db7d…`，**自建**；2026-09-19 已按批准重建） | **100** | 0 | 基础 `apache/superset:6.1.0@sha256:16b50bbef664…`（**已是最新**：`docker pull` 返回 "Image is up to date"） | ❌ **实测：重建不降漏洞**（重建前 100 → 重建后 **100**，逐目标一致：debian 67 + python-pkg 33）。**构成**：**64 条来自 `linux-libc-dev`**（内核**头文件**包，容器不加载内核 ⇒ 可利用性需人工评估）、33 条 Python 依赖（pillow 13 / pyasn1 4 / cryptography 3 / Mako 2 / PyJWT 2 …）、3 条 `libpcre2-8-0`。**真正能降的两条路（均需人工批准）**：① **升级 Superset 版本**（组件版本变更）；② **在派生镜像里显式升级个别 Python 依赖**（如 Pillow，属改依赖，须重跑验收）。③ 若人工判读 `linux-libc-dev` 那 64 条不可利用，可走"风险接受勾选" |
| 14 | `prom/prometheus:latest`（`5ce7540c3c00`） | 6 | 0 | ✔ 已是最新 | 无需动作 |
| 15 | `openmetadata/server:2.0.1`（`55d6df724747`） | **0** | **0** | — | **无需动作** |
| 16 | `openpolicyagent/opa:latest`（`1b9ca9be95e6`） | **0** | **0** | ✔ 已是最新 | **无需动作** |

> 表中"上游是否有更新"来自 2026-09-18 逐 tag 实拉比对（§3.2）；**digest 会随上游重建变化，升级前须重新比对**。

---

## 3. 核查方法与原始结果

### 3.1 方法

```bash
# 1) 从 compose 取当前固定 digest
grep -oE '^\s+image:\s+\S+' /data/ai-governance/docker-compose.yml | awk '{print $2}' | sort -u

# 2) 逐个 tag 实拉，比较"当前固定 digest" vs "上游当前 digest"
docker pull <image:tag>            # 走 registry 加速器
docker image inspect <image:tag> --format '{{index .RepoDigests 0}}'
```

> **为什么用"实拉"而不是 `docker manifest inspect`**：本环境 `docker manifest inspect` **直连 registry API 不可达**（S2 期间已实测），只有经加速器的 `docker pull` 才走得通。

### 3.2 digest 比对结果（2026-09-18）

| 镜像 | 结果 |
|---|---|
| `bitnami/pgbouncer:latest` | ✔ 已是最新 |
| `opensearchproject/opensearch:latest` | ✔ 已是最新 |
| `prom/prometheus:latest` | ✔ 已是最新 |
| **`prom/alertmanager:latest`** | ★ **有更新版本**：当前 `690c7b525f43…` → 上游 `e9733bafb1bd…` |
| `openpolicyagent/opa:latest` | ✔ 已是最新 |
| `zcube/cadvisor:latest` | ✔ 已是最新 |
| `apache/kafka:4.0.0` | ✔ 该 tag 已是最新（**但存在 4.0.1 / 4.1.0 新版本**，见 §3.3） |
| `mcr.microsoft.com/presidio-analyzer:latest` | ✔ 已是最新 |
| `mcr.microsoft.com/presidio-anonymizer:latest` | ✔ 已是最新 |
| `python:3.12-slim`（自建镜像基础） | ⚠️ **更正（2026-09-19）**：**`78387bc3881b…` 是旧的**（debian 13.6）；**新的是 `sha256:23b5dc88c7dd47fec3f960b51dc30d19df9875cfbfc60f3b62d3e5b88cbccf62`**（debian 13.7）。**已按此 digest 固定**在 `governance-agent/Dockerfile` 与 `governance-portal/Dockerfile`（此前两者用的是浮动 tag `python:3.12-slim`，会在重建时静默换基础——本轮实测踩到） |

### 3.3 Kafka 版本可用性

```text
apache/kafka:4.1.0  可拉取     ← 大版本，需按官方升级矩阵核对（KRaft 元数据版本、客户端兼容）
apache/kafka:4.0.1  可拉取     ← 补丁级，风险最低
apache/kafka:4.0.0  可拉取     ← 当前使用
```

### 3.4 自建镜像"重建是否有收益"的实测判定（2026-09-18）

**方法**：比较自建镜像的 `RootFS.Layers` 与当前基础镜像的 `RootFS.Layers`——**若基础镜像的全部 layer 都在自建镜像里，说明构建用的就是当前基础层**。

```bash
docker image inspect <自建镜像> --format '{{join .RootFS.Layers "\n"}}'   # 与基础镜像逐层比对
```

| 自建镜像 | 基础镜像 | 结果 |
|---|---|---|
| `ai-governance/governance-portal:0.1.0`（9 层） | `python:3.12-slim`（4 层） | **4/4 全部包含** ⇒ 基础已是最新 |
| `ai-governance/governance-agent:0.1.0`（10 层） | `python:3.12-slim`（4 层） | **4/4 全部包含** |
| `ai-governance/superset:6.1.0-pg`（30 层） | `apache/superset:6.1.0`（29 层） | **29/29 全部包含** |
| `ai-governance/flink:1.20.1-cdc3.6.0`（13 层） | `flink:1.20.1-scala_2.12-java17`（11 层） | **11/11 全部包含** |

**结论**：**当前重建不降低漏洞数** ⇒ 把 §4.3 的"重建"从"建议优先做"降级为**触发式动作**：当基础镜像 digest 变化（上游安全重建）后再重建，并重新扫描。**同理**：`apache/superset:6.1.0`、`flink:1.20.1-…`、`python:3.12-slim` 三个基础 tag 的本地与上游 digest **完全一致**（实测），因此"我们自己的镜像"并不比其他镜像更有"可立刻改善"的空间。

---

## 4. 三件"建议优先决策"的升级（含步骤与回滚）

### 4.1 `prom/alertmanager` 升到新 digest

| 项 | 内容 |
|---|---|
| 为什么值得 | 有明确更新版本；**无状态服务**；配置与规则已就位且刚做过投递自检，回归成本低 |
| 步骤 | ① 备份 compose ② 把 `prom/alertmanager:latest@sha256:690c…` 改为 `…@sha256:e973…` ③ `docker compose up -d alertmanager` ④ 等 healthy ⑤ `amtool check-config` + `/api/v2/alerts` + 复跑一次投递自检（用既有自检方法）|
| 回滚 | 改回原 digest 并 `up -d`（配置未变） |
| 影响 | **compose 校验和变化**；需同步《…S3前置部署记录.md》§4 与本文版本记录 |

### 4.2 `apache/kafka` 升 4.0.1（补丁级）

| 项 | 内容 |
|---|---|
| 为什么值得 | 补丁级、风险低；单节点 KRaft 形态升级路径短 |
| 前置 | ① 按官方说明核对 **KRaft 元数据版本**与客户端兼容（`kafka-clients 3.4.0` 与本项目 Flink 连接器）② 确认 `governance.alerts` 无积压（`kafka_consumergroup_lag_sum = 0`）③ 记录当前 `CLUSTER_ID` |
| 步骤 | 改 digest → `docker compose up -d kafka` → 校验 healthy + `--list` + 收发往返 + GA `/ready` + lag 指标 + 触发一次 Flink 冒烟（可复用 §12 演练脚本）|
| 回滚 | 改回 4.0.0 的 digest 并 `up -d`（KRaft 元数据向上兼容，向下需按官方说明核对 ⇒ **升级前先确认回滚可行性**）|
| 升级到 4.1.0 | 属**大版本**，须按官方升级矩阵单独评估，**不建议与本次一并做** |

### 4.3 重建自建派生镜像（**触发式：仅当基础镜像 digest 变化时才做**）

| 镜像 | 重建命令 | 备注 |
|---|---|---|
| 治理门户 | `cd /data/ai-governance/governance-portal && docker compose build --no-cache governance-portal`（compose 内 `build`） | 取新 `python:3.12-slim` |
| Governance Agent | 同上（`governance-agent/`） | 取新基础 + 依赖版本固定不变 |
| Superset 派生镜像 | `cd /data/ai-governance/superset-image && docker build -t ai-governance/superset:6.1.0-pg .` | **风险最高的一张自建镜像（100 HIGH）**；驱动装在 `/app/.venv` 的既有约束不变 |
| Flink 派生镜像 | `cd /data/ai-governance/flink-image && docker build -t ai-governance/flink:1.20.1-cdc3.6.0 .` | 基础 digest + JAR 的 sha256 校验不变（构建期强制） |

> **⚠ 本动作已实测降级**：2026-09-18 层比对证明**当前重建无收益**（§3.4）。**先判定再动手**：`docker pull <基础镜像>` 后比对 digest，**未变化就不要重建**。

**基础镜像更新后，重建的必做项**：① 记录**新 image id** 并更新 compose 中的 digest ② 逐服务回归（门户 4 页面 / GA `/ready` + 告警 / Superset 正反验收 / Flink 冒烟）③ 重取 compose 校验和并写入部署记录 ④ **重新扫描新镜像**（用 §2 的同一命令）。

---

## 5. 风险接受（需人工勾选）

对"已是最新 digest、只能等上游"的镜像，建议按下表**逐条勾选接受**并说明理由（本表可直接作为准出材料附件）：

> 📋 **可直接勾选的一页版**：见《AI数据治理平台_单机版_漏洞风险接受单.md》（把本节 8 条 + 两条可升版本 + superset 待拍板项整理成单页，含签署栏；并已剔除已闭环的 GA/门户两行）。

| # | 镜像 | HIGH | CRIT | 接受理由 | 复核时点 | 勾选 |
|---|---|---|---|---|---|---|
| 1 | `openmetadata/ingestion:2.0.1` | 149 | 5 | 上游未发补丁；不对外暴露 | 上游 2.0.x 更新时 | ☐ 接受 |
| 2 | `bitnami/pgbouncer:latest` | 117 | 8 | tag 已最新；仅容器网内可达（宿主侧只发布回环 6432） | 上游重建时 | ☐ 接受 |
| 3 | `mcr.microsoft.com/presidio-analyzer:latest` | 86 | 8 | 官方镜像已最新；仅回环访问（未接 Traefik） | 上游重建时 | ☐ 接受 |
| 4 | `mcr.microsoft.com/presidio-anonymizer:latest` | 88 | 8 | 同上 | 上游重建时 | ☐ 接受 |
| 5 | `zcube/cadvisor:latest` | 99 | 6 | 社区镜像已最新；**供应链替代品**（gcr.io 不可达），换源/自建需单独排期 | 换源决策时 | ☐ 接受 |
| 6 | `opensearchproject/opensearch:latest` | 35 | 6 | 已最新；仅容器网内（宿主无 9200 监听） | 上游重建时 | ☐ 接受 |
| 7 | `traefik:v3.0` | 77 | 5 | **443 已停止对外发布**（S3 前置记录 §10），暴露面为零 | 恢复 443 之前必须重评 | ☐ 接受 |
| 8 | `pgvector/pgvector:pg16` | 24 | 1 | 升 PG 镜像涉及数据目录兼容，须维护窗口 | 维护窗口 | ☐ 接受 |

> **复核触发条件（统一）**：① 上游发布新 digest；② 恢复公网暴露（如 443 重新发布）；③ POC 转试运行/M-Prod 评审前。

---

## 6. 纪律

1. **不得**把扫描结果写成"已扫清/无风险"——本清单只做**事实与建议**的整理。
2. **升级自建/上游镜像 = 组件版本变更**：须同步《部署步骤_单机版》对应段落、重取 compose 校验和、重跑该组件验收，并**重新扫描**。
3. **digest 会漂移**：每次升级前重新比对上游 digest（§3.1 命令），不要复用本文件的历史值。
4. 修复**不属于本清单**的安全问题（如源库接入、凭证管理）仍按各自主题处理。

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-18 | 首次产出：把 16 个镜像的扫描结果整理为决策件——逐镜像清单（含"上游是否有更新"的实拉比对结论）、三类建议动作（**重建自建镜像 / 两件可升版本：alertmanager 与 kafka 4.0.1 / 其余记录风险接受**）、升级步骤与回滚、8 条风险接受勾选表（含复核触发条件）与 4 条纪律（计数≠可利用、升级属组件版本变更、digest 会漂移、不得写成已扫清） |
| v1.1 | 2026-09-18 | **实测更正"重建自建镜像"这一条**：新增 §3.4 层比对（四个自建镜像**全部包含其基础镜像的所有 layer**：门户/GA 4/4、superset 29/29、flink 11/11；三个基础 tag 本地=上游 digest）⇒ **当前重建无收益**；§1 第 2 条与 §4.3 相应降级为**触发式动作**（先比对 digest，变了才重建）。**诚实更正：A3 初稿把"重建"当成"可立刻降风险"，实测不成立** |
| v1.2 | 2026-09-19 | **三处实测回写（人工批准后执行）**：① **GA 镜像**：重建时取用了更新后的基础（debian 13.6→13.7）⇒ **HIGH/CRIT 由 10/3 降为 0/0**，并**按 digest 固定基础**（`python:3.12-slim@sha256:23b5dc88c7dd…`）；② **门户镜像**：同法固定基础并重建 ⇒ **13（10/3）→ 0/0**，功能复核通过（healthy、`/_stcore/health` 200、`portal_ro` 经 PgBouncer 读到 `asset_coverage_snapshot` 392 行）；③ **superset 派生镜像**：固定基础 digest（`apache/superset:6.1.0@sha256:16b50bbef664…`）与依赖版本（`psycopg2-binary==2.9.13`）后重建 ⇒ **漏洞数不变：100（HIGH 100 / CRIT 0）**，**实测重建无收益**（`docker pull` 返回 "Image is up to date"，基础已最新）；构成中 **64 条来自 `linux-libc-dev`（内核头文件包）**、33 条 Python 依赖、3 条 libpcre2 ⇒ 真正降漏洞只有"升 Superset 版本"或"显式升个别 Python 依赖"两条路，**均需人工批准**。④ **更正一处记录错误**：原记"`python:3.12-slim` 新 digest `78387bc3881b…`"**是错的**（那是旧基础，debian 13.6）；真正的新 digest 是 `23b5dc88c7dd…`（debian 13.7）。**教训：本地 tag 与"新 digest"须分别核实，钉完必须重扫验证** |
| v1.3 | 2026-09-19 | **新增一页可勾选单页**：把 §5 的 8 条"建议风险接受"整理为《AI数据治理平台_单机版_漏洞风险接受单.md》，并**按最新实测重新分类**——A 类 8 条（建议接受，逐条三选项勾选）、B 类 2 条（alertmanager / kafka，**有 §4.1/§4.2 的具体升级方案**，勾"执行升级/暂接受"）、C 类 superset（**无接受选项**，在"升版本 / 升依赖 / 接受 64 条头文件包"三路择一）；**已闭环的 GA/门户（0/0）单列为"仅备查"**，避免与待决项混淆。含统一复核触发条件 4 条与签署栏。**判读与签字仍属人工专属** |
| v1.4 | 2026-09-19 | **GA 二次重建后重扫复核**：因 **RAG 上下文预算 + Ollama 告警处置建议模板**（见《…S4-2…》v1.3 /《…S3-6…》v1.3）重建 `governance-agent:0.1.0` ⇒ 新 digest **`2c8b39e97dcd00aed490754d43ebb4080aa12dca1e6e3a0cd4a44bf068fca926`**、体积 **296.9 MB**；按同一口径（`--ignore-unfixed --severity HIGH,CRITICAL`，双 DB 源）重扫 **HIGH+CRITICAL = 0**，**与重建前一致**（基础 digest 未变，属预期）。§1 表中 GA 的 digest 由 `86cab9c6b590d…` 更新为本值 |
| v1.5 | 2026-09-19 | **GA 三次重建后重扫复核 + 镜像回收留痕**：因补 **通用容器告警 6 条 `ADVICE` 模板**（见《…S3-6…》v1.4）再次重建 `governance-agent:0.1.0` ⇒ 新 digest **`394700abe6ed1ece66c6692230b4a77e0999b6ca7aaa63aa0450e253f8fff69b`**；按同一口径重扫 **HIGH+CRITICAL = 0**（基础未变，属预期）。**同轮回收未使用镜像/缓存**：`ollama/ollama:0.11.10`、`apache/kafka:4.0.1`、`apache/kafka:4.1.0`、`postgres:16-alpine`、`openpolicyagent/opa:latest-debug`、未使用的 `prom/alertmanager:latest`（容器实际用 `690c7b52…`）、`edoburu/pgbouncer:latest`、旧 `python:3.12-slim` 悬空层 + 构建缓存 ⇒ 镜像 34.26→**29.01 GB**、缓存 2.93→**1.554 GB**（合计**释放≈6.63 GB**）。**保留项及理由**：自建镜像的三个基础镜像（`apache/superset:6.1.0@16b50bbef664`、`flink:1.20.1-scala_2.12-java17`、`python:3.12-slim@23b5dc88c7dd`，Dockerfile 按 digest 引用）与 `danielqsj/kafka-exporter`（宿主二进制来源，重拉依赖镜像源） |
| v1.6 | 2026-09-19 | **GA 四次重建后重扫复核**：因 **RAG 上下文预算定档 1800**（人工决定"1.5b + 压预算"，`rag.py` 默认值与 compose 同步）重建 `governance-agent:0.1.0` ⇒ 新 digest **`44ca36b1e519046418232c10865e85c976b936c3065788f89e281aac625faa0d`**；同口径重扫 **HIGH+CRITICAL = 0**（基础未变，属预期）。§1 表 GA 行 digest 同步更新 |
| v1.7 | 2026-09-19 | **门户重建后重扫复核（首页布局修复）**：因**首页"智能问答"区块就地可提问 + 就绪状态改人话**重建 `governance-portal:0.1.0` ⇒ 新 digest **`308e50f8ff4c2271e16f66b2ceafe27d449ddc5f6ee88aa8dc51f96e6fdbd678`**；同口径重扫 **HIGH+CRITICAL = 0**（基础未变，属预期）。§1 表门户行 digest 同步更新。详见《…S2-7治理门户.md》v1.3 |
| v1.8 | 2026-09-19 | **门户重建后重扫复核（首页区块拆分）**：因**把"系统状态（运维视角）"与"智能问答（场景 D）"拆成两个独立区块**（人工第二轮反馈）重建 `governance-portal:0.1.0` ⇒ 新 digest **`e1fe07781a234d449b3229172100960f3baeee4b466cd074c60e3f252a536d7f`**；同口径重扫 **HIGH+CRITICAL = 0**（基础未变，属预期）。§1 表门户行 digest 同步更新。详见《…S2-7治理门户.md》v1.4 |
| v1.9 | 2026-09-20 | **门户重建后重扫复核（视觉重构 v1.5）**：按 `design/mockup.html` + `design/design-system.md` 重做门户（新增 `theme.py` 落地层、`.streamlit/config.toml`、`design/`、`tools/` 4 个校验脚本）⇒ 新 digest **`650a57b42570e8c158c3acb726029965e8fe1cd379c0830c0b1d375f9baed0da`**（747 MB）；同口径重扫 **HIGH+CRITICAL = 0**（基础未变，属预期）。§1 表门户行 digest 同步更新。详见《…S2-7治理门户.md》v1.5 |
| v1.10 | 2026-09-20 | **门户重建后重扫复核（入口卡隐藏 URL，v1.6）**：首页入口卡不再显示 `localhost:…`/`?page=…` 等地址（人工要求），`design/mockup.html` 同步 ⇒ 新 digest **`ba381aa10dad84b67067b38ef797aa841f703f420817f235aef1f18c23a55d41`**（747 MB）；重扫 **HIGH+CRITICAL = 0**（基础未变，属预期）。§1 表门户行 digest 同步更新。**扫描方法提示**：trivy 默认会先更新漏洞库，本环境该步骤常达数分钟甚至超时；本地库已是当日时用 **`--skip-db-update`**，实测 **2 秒**完成。详见《…S2-7治理门户.md》v1.6 |
| v1.11 | 2026-09-20 | **门户重建后重扫复核（「需隧道」改徽标，v1.7）**：按设计稿把"需隧道"由文案改为 `gv-badge`（并在 mockup 里去掉重复的 hint 文案）⇒ 新 digest **`a8ac7d049c7d63bd72158c23996b8bd24c0bb0afe40d2213796459d4a1fa99ec`**；重扫 **HIGH+CRITICAL = 0**（`--skip-db-update`，秒级）。§1 表门户行 digest 同步更新。详见《…S2-7治理门户.md》v1.7 |

---

*本文件为供应链扫描结果的处置清单，由《…S3前置部署记录.md》§9 的扫描结论展开；**升级动作需人工批准**，Agent 不代批、不代签。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；升级前请按各组件官方文档核对兼容性与升级路径。
