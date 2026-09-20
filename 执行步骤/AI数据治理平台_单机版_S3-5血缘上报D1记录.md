# AI 数据治理平台 · 单机版 S3-5 作业图级血缘上报（D1）记录

> **文档版本**：v1.0（2026-09-19）
> **定位**：ADR-A7「作业图级血缘（作业启动/变更时上报）」的**实现机制变更与落地记录**。
> **决策链**：A（升 Flink 2.x）前提已证伪 → A′（血缘边界落 Kafka）与 ADR-A1 冲突 → **人工选定 D1**（自研侧上报·简版）。
> **结论先行**：**D1 已落地并端到端验证通过**——GA 解析 Flink SQL → 按 FQN 在 OM 建/删血缘边 → OM 可读回（含作业 SQL 原文）；幂等、变更删边、清理零残留、镜像重扫无新增漏洞。**但真实业务边仍要等 S2-2 接源**（现在 OM 里 `tables=0`，没有边两端）。

---

## 1. 为什么是"自研上报"（不是 OpenLineage）

| 路径 | 结论 |
|---|---|
| OpenLineage · Flink 1.x | 走 `JobListener`：**须改作业代码、不支持 Flink SQL**、须 Application Mode + `execution.attached:true` ⇒ **本环境不符** |
| OpenLineage · Flink 2.x | 走原生 SPI（FLIP-314）：支持 SQL、不改代码，但**该 SPI 在 1.20.1 不存在**（实测 0 命中） |
| 升级 Flink 2.x 后能否拿到 CDC 源血缘 | **不能**：`flink-cdc` 源码树**含 lineage 路径 = 0**（未实现 `LineageVertexProvider`）；Flink 官方文档亦写明连接器覆盖是"gradually"、自定义连接器须自行实现 |
| **D1（本方案）** | **GA 在作业上报时把边写进 OM**——ADR-A7 原文只说"作业启动/变更时上报"，**未限定必须由 OpenLineage 上报** ⇒ 机制变更经人工批准（2026-09-19 选 D1） |

---

## 2. 交付物

| # | 交付物 | 仓库位置 | 服务器落点 | sha256（现行） |
|---|---|---|---|---|
| 1 | 血缘解析/映射/OM 客户端（含**对账判据**） | `governance-agent/lineage.py` | 构建上下文 `/data/ai-governance/governance-agent/lineage.py` | `2d59a4e45b0afa67…` |
| 2 | 上报端点 + **对账端点** | `governance-agent/agent.py` | 同上 `agent.py` | `3ac8e16e85f7bfc5…` |
| 3 | 幂等表 `ga.lineage_edge` | `governance-agent/schema.sql` | 同上 `schema.sql`（已在库中应用） | `dc283fd93c2c3f70…` |
| 4 | 镜像构建（新增 COPY） | `governance-agent/Dockerfile` | 同上 `Dockerfile` | `4f4a29bccef5a695…` |
| 5 | **解析准确性测试**（ADR-A7 验收用） | `governance-agent/test_lineage.py` | 按需上传运行（**不入镜像**） | `955d13976d0127c7…` |
| 6 | **对账执行器** | `governance-agent/lineage_reconcile.sh` | `/data/ai-governance/scripts/lineage_reconcile.sh`（cron `/etc/cron.d/ai-governance-lineage` 每日 04:10） | `be9803f883000fe0…` |
| 7 | 新镜像 | —— | `ai-governance/governance-agent:0.1.0@sha256:b4d3f29bc3f188cf…` | —— |
| 8 | compose 校验和 | —— | 由 `0d07688e…` 变为 **`21168b4b6a2f5f710a92048b8b923ccfce23611bbe1fc1ba0bb263efd003e067`** | —— |

---

## 3. 口径与纪律（D1 的"边界声明"）

| 项 | 口径 |
|---|---|
| 覆盖粒度 | **表/主题级边**（table→table、table→topic 等）；**不建作业实体**（那是 D2） |
| 来源标记 | OM `LineageDetails.source = **PipelineLineage**`，并把**作业 SQL 原文**写入 `sqlQuery`（可追溯） |
| 幂等键 | GA 侧 `(job_key, from_type, from_fqn, to_type, to_fqn)`；OM 侧同一对 from/to 是 upsert ⇒ **重复上报不产生重复边** |
| **宁缺勿错** | 解析/映射**不确定即拒绝上报**（HTTP 400 + errors 列表），**不落任何行**——这是 ADR-A7"血缘定准确率验收"的落地 |
| 映射显式 | 表名→OM FQN 由 `mapping`（`table_service` / `topic_service` / `default_schema`）给出，缺项即报错；**不猜** |
| 变更/下线 | 上报新集合时，**不在期望集里的旧边会被 `DELETE` 并标 `removed`** |
| 鉴权 | 复用 `GA_INGEST_TOKEN`（Bearer）；`GA_INGEST_TOKEN` 非空即强制校验 |
| 写入权限 | 用 OM admin token（POC 简化）⇒ **记待收口**：正式期应换专用 service account |

---

## 4. 验收证据（命令 + 实际输出 + 判读）

### 4.1 解析准确性（4/4，含两个负例）

```
$ python3 test_lineage.py
[PASS] 负例：datagen 源应报错且不产边          errors: ["表 datagen_orders：不认识的 connector='datagen'…"] edges: 0
[PASS] CDC → Kafka（表→主题）                  edge: table probe_pg.app_db.public.orders -> topic probe_kafka.governance.alerts
[PASS] CDC → JDBC（表→表）                     edge: table probe_pg.app_db.public.orders -> table probe_pg.app_db.public.orders_agg
[PASS] 负例：子查询应报错                      errors: ['INSERT INTO sink_b 的 FROM 含子查询，D1 不解析']
结果：4/4 通过
```

### 4.2 端点行为（dry-run / 幂等 / 实体缺失）

```
# 1) dry-run（apply=false，不写 OM，只留痕）
HTTP 200 {"job_key":"d1-verify","apply":false,"parsed_edges":1,
          "added":[{"from":"…orders","to":"…probe_orders_alerts","status":"planned"}],"kept":0,"removed":[]}
# 2) 同一 payload 再报 → 幂等
HTTP 200 {"added":[],"kept":1,"removed":[]}
# 3) apply=true 但实体不存在 → 记 entity_missing，不崩
HTTP 200 {"added":[{"…","status":"entity_missing"}],"kept":0}
```

### 4.3 **端到端**（建临时实体 → 上报 → OM 读回 → 变更 → 清理）

```
本次 job_key = d1-e2e-1789757744
--- 1) 建临时实体（临时数据库服务 probe_pg + 3 张表）---   全部 201
--- 2) 上报（apply=true）---
HTTP 200 {"parsed_edges":1,"added":[{"…orders -> …orders_agg","status":"applied"}],"kept":0,"removed":[]}
读 OM 血缘 HTTP 200 downstream = ['a5e7cf99-edcb-4093-8fd3-47757dc14d00']      ← 边在 OM 里真实存在
downstreamEdges[0] 原样: {"fromEntity":"6dd095ef-…","toEntity":"a5e7cf99-…",
                          "lineageDetails":{"sqlQuery":"\nCREATE TABLE src_orders (…"}}  ← source/sqlQuery 已落
--- 3) 幂等：同 payload 再报 ---
HTTP 200 {"added":[],"kept":1} ；downstream 仍为同一条 ⇒ **OM 未重复建边**
--- 4) 变更 sink 后上报 ---
HTTP 200 {"added":[{"… orders_agg_v2","status":"applied"}],"removed":[{"… orders_agg","status":"removed"}]}
变更后 downstream = ['104831be-0c3e-4b26-b54b-3969c6bd7118']  ⇒ **旧边已删、新边生效**
--- 5) 清理（子→父）--- 6 项删除全部 200
--- 6) 残留检查 --- tables total=0  schemas total=0  databases total=0  services total=0   ⇒ **零残留**
```

> **验证留痕的清理（同样按"零残留"口径做）**：`ga.lineage_edge` 中 8 条 `d1-*` 探针行已删除
> （`DELETE 8`，之后查询返回 `(none)`）；**`ga.audit_log` 保留 13 条 `lineage.report` 审计轨迹**——
> 状态表要干净，审计表按设计只追加（这正是"留痕"与"状态"该有的分工）。

### 4.4 镜像漏洞重扫

```
SUMMARY total=0 HIGH=0 CRITICAL=0            ← 现行镜像（00774ed36a13…），JSON 逐目标校验
  target=ai-governance/governance-agent:0.1.0 (debian 13.7)  type=debian      vulns=0
  target=Python                                             type=python-pkg  vulns=0

# 对照实验（同一命令、同一漏洞库、同一时点）
  ai-governance/governance-portal:0.1.0 (debian 13.6) = 13 (HIGH 10 / CRIT 3)   ← 未重建
  ai-governance/superset:6.1.0-pg      (debian 12.15) = 100 (HIGH 100 / CRIT 0) ← 未重建
```

> ⚠️ **重要更正（2026-09-19，同一轮内发现）**：**不是"与改动前一致"，而是从 10/3 降到了 0/0。**
> 根因：GA 的 Dockerfile 是 `FROM python:3.12-slim`（**浮动 tag**），本轮为修复缺陷多次重建时，
> **11:17 那次重建取用了更新后的基础**（`python:3.12-slim@sha256:23b5dc88c7dd…`，**debian 13.6 → 13.7**）。
> 三门证据：① 门户（未重建、debian 13.6）仍 13/10/3；② superset（未重建）仍 100/100/0 ⇒ **trivy 与漏洞库语义没变**；
> ③ GA（已重建、debian 13.7）为 0/0。这正是《供应链漏洞处置清单》§4.3「**触发式重建**」被触发后的预期收益
> （该清单 §表第 76 行早已记录"`python:3.12-slim` 有新 digest `78387bc3881b…`，重建时取用"）。
>
> **连带暴露一个真偏差**：**基础镜像未按 digest 固定** ⇒ 每次重建都可能**静默换基础版本**（本次即是），
> 与 AGENTS §7 对自建镜像的纪律（固定基础 tag/digest）不一致。
>
> ✅ **已按人工批准执行（同日）**：GA 与门户的 `FROM` 均固定为
> **`python:3.12-slim@sha256:23b5dc88c7dd47fec3f960b51dc30d19df9875cfbfc60f3b62d3e5b88cbccf62`**（debian 13.7），
> 两个镜像**重建后重扫均为 0/0**（门户 13 → 0、GA 保持 0），容器重建后 healthy、门户功能复核通过。
> **踩坑留痕（值得记）**：本地 tag 与"新 digest"**要分别核实**——本轮先误把 **`78387bc3881b…` 当成新基础**去钉，
> 结果两个镜像又回到 13（该 digest 其实是**旧**的、debian 13.6）；`docker pull python:3.12-slim` 才暴露出真正的新 digest
> `23b5dc88c7dd…`。**钉完必须重扫验证**，否则"固定了但固定到旧基础"不会有任何报错。

### 4.5 仓库 ↔ 服务器一致性

`lineage.py` / `agent.py` / `schema.sql` / `Dockerfile` 四个文件的本地与服务器 sha256 **逐一相等**（见 §2）。

---

## 5. 实测踩坑（5 条，均已修或已声明）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 解析器把**合法的窗口聚合作业**判成"含子查询" | 早期用"SELECT 段含 `(`"做子查询判据，而 `COUNT(*)` 也含括号 | 判据改为"**含嵌套 SELECT**"；并对 `FROM TABLE(TUMBLE(TABLE t, …))` 做**窗口 TVF 特判**（否则会误杀我们自己的窗口作业） |
| 2 | JDBC sink 映射不出库名 | 该表只给 `url`，没给 `database-name` | 允许**从 JDBC url 反解库名**（`//host:port/db`），仍取不到才报错 |
| 3 | 临时实体**清理失败留下残留**（`databases total=1`） | 脚本清理排序**写反**（先删父后删子，OM 拒绝删除非空父） | 排序改为**子→父**（table → schema → database → service）；重跑后**零残留**；并在验证脚本里加了"残留自愈"步骤 |
| 4 | **OM 删实体会连带删血缘边**，而 GA 库里那条边仍是 `applied` | D1 **只做上报、没有对账** | **如实记录为已知漂移**：这是"对账任务"的需求实证（见 §6），不是缺陷掩盖 |
| 5 | 读血缘时解析崩了 | OM 2.0.1 的 `downstreamEdges[].fromEntity/toEntity` 是**实体 id 字符串**，**不是对象** | 回读改为防御式解析（同时兼容两种形态） |

---

## 5.5 对账任务（同日追加，关闭 §6 原"未做 #2"）

**为什么要做**：§5 第 4 条实测到**真实漂移**——OM 侧实体被删除会**连带删掉血缘边**，而 GA 库里那条边仍是 `applied`。
对账的价值就是"发现漂移"；**对账自己静默失败等于没做**，所以它必须有告警兜底。

**做法**：

| 项 | 口径 |
|---|---|
| 端点 | `POST /api/v1/lineage/reconcile`，body `{job_key?, repair:bool}`（`job_key` 省略=全部作业） |
| 判据 | 用 OM 的 `GET /lineage/getLineageEdge/{fromType}/name/{fromFQN}/{toType}/name/{toFQN}` 逐边核对 |
| repair=false | 仅检出：缺失边状态置 **`drift`**（新增的第 6 种状态） |
| repair=true | **自愈**：缺失边**重新 PUT**；成功→`applied`、实体不存在→`entity_missing`、其他→`failed` |
| **绝不删除** | 只新增/修复，**不删 OM 上任何边**——OM 还承载 sqllineage 等离线血缘，删错即"第二套真理源"问题（ADR-A7） |
| 定时 | cron `/etc/cron.d/ai-governance-lineage` **每日 04:10** → `lineage_reconcile.sh`（repair=true） |
| 告警兜底 | 脚本成功时写**成功戳**；`job_freshness.sh` 采集为 `task="lineage_reconcile"`；新规则 **`LineageReconcileStale`**（>30h 未成功）——**规则总数 21 → 22** |

**验收（含"制造漂移"的完整闭环）**：

```
job_key = d1rec-1789758798
1) 上报 apply=true                          → added: applied
2) 对账 repair=false                        → present=1 drift=0          ← 无漂移时不误报
3) 人为删除 OM 中那条边                     → delete_edge ok / edge_exists=False
4) 对账 repair=false                        → drift=1，状态 applied→drift   ← **检出漂移**
5) 对账 repair=true                         → repaired=1，状态 drift→applied；edge_exists=True  ← **自愈**
6) 再对账 repair=true                       → present=1 repaired=0       ← 不会反复"假修复"
7) 清理临时实体（子→父）6 项全 200           → tables/databases/services 全 0   ← 零残留
```

**同轮发现并修复的两个真问题**（都属"会静默或误导"的类别）：

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 对账在**刚上报成功**后就报 `drift=1`，每次都"修复"一遍 | `getLineageEdge` **存在时返回 `{"edge":{…}}`**（边信息在 `edge` 键内），早期按顶层 `toEntity` 判 ⇒ **永远 False** | 判据改为读 `edge` 键；并加"目标实体同时出现在 `downstreamEdges` 与 `nodes` 且 FQN 吻合"的精确兜底 |

> 修复验证：`promtool check metrics` **rc=0**；修复后 45 秒内 node_exporter 错误数 **0**（此前每 30s 一条）；Prometheus `count(ai_governance_job_last_success_timestamp_seconds)` 恢复为 **6**。

---

## 5.6 自主触发（"提交即上报"，2026-09-19 同日追加）

**为什么要做**：靠"提交完再手动 POST"上报血缘，**迟早会漏**——而漏了不会有人知道。把它**串进提交动作**才可靠。

**两个交付**：

| 文件 | 作用 |
|---|---|
| `submit_flink_job.sh <job_key> <sql文件> [--no-submit]` | **干跑门禁 → 登记 → 提交 → 上报**。提交路径沿用 S3-5 演练验证过的做法（`docker cp` 进 JM 容器 + `sql-client.sh -f`）；`SUBMIT_CMD` 可覆盖提交命令 |
| `lineage_autoreport.sh [--dry-run]` | 遍历**登记目录** `/data/ai-governance/jobs/*.sql` 逐个上报（幂等 upsert）；**全部成功才**调用 `lineage_reconcile.sh`（对账 + 写成功戳）。cron 每日 04:10 跑它 |

**关键设计**：

* **登记目录是唯一真相**：登记了就会每日重新上报一次 ⇒ 提交时漏报也会被补上（兜底）；
* **干跑不通过就不提交**：解析/映射不确定时**拦住作业**，而不是让它跑出一个没有血缘的作业（宁缺勿错）；
* **失败不静默**：任一作业上报失败 ⇒ **跳过对账、不更新成功戳** ⇒ `LineageReconcileStale` 会在 30h 内报出来。

**验收（含负例）**：

```
1) 好作业经 submit_flink_job.sh：
   干跑通过 → 提交被调用（SUBMIT_CMD=touch <标记>，标记存在=True）→ apply=true 上报 → OM 侧 edge_exists=True
2) 坏作业（datagen 源，无法映射）：
   rc=3 且 **提交未被调用**（标记文件不存在）→ 作业不会被提交出来
3) lineage_autoreport.sh：
   --dry-run → OM 无变化（edge_exists=False）；全量 → 写入 OM（True）且成功戳更新
   登记目录为空 → 直接对账并写成功戳（rc=0）
4) 清理：登记文件与临时实体全部清除，残留 0
```

**同轮修复第三个真缺陷**：**dry-run 会把行写成 `planned`，随后的 `apply=true` 把它当"已存在"跳过 ⇒ 边永远进不了 OM**——
而"先干跑再提交"正是主路径，所以这个缺陷会**让所有走提交包装的作业都没有血缘**。已改为：
**只有 `status='applied'` 才算"OM 里已有"，`planned`/`drift`/`entity_missing`/`failed` 一律重推**。

---

## 6. 明确未做（不得据此推断"血缘已可用"）

| # | 未做项 | 说明 |
|---|---|---|
| 1 | **真实业务边** | 现在 OM `tables=0`（未接源）⇒ 没有任何真实边可连；本次用**临时实体**证明链路，**清理后边也消失**。**接源 + OM 采集建表后才能真正产出业务血缘** |
| 2 | ~~周期对账任务~~ **已完成（见 §5.5）** | 端点 + 每日 cron + `LineageReconcileStale` 告警兜底；制造漂移的完整闭环已验收。**仍未做**：对"OM 侧多出来的边"的识别（可能来自 sqllineage，属他源，本服务只报告不处理） |
| 3 | **作业节点（D2）** | 建 Custom Pipeline service 把 Flink 作业注册成 pipeline 实体，让血缘图上出现"作业节点"——**未做** |
| 4 | ~~运行侧自动触发~~ **已完成（见 §5.6）** | 提交包装 + 登记目录 + 每日自动上报已落地。**仍未做**：与真实作业编排（有人接源后写第一个真作业时）的对接实测——**现在没有真实作业可跑** |
| 5 | 门户展示 | 门户只有 4 个页面（首页/指标/问答/审批台），**没有血缘页**；本次交付是"**落库 + API 可查 + OM 可见**" |
| 6 | OM 写权限收口 | 仍用 admin token；应换专用 service account（安全基线①） |

---

## 7. 影响面与需人工办理

| 项 | 说明 |
|---|---|
| **ADR-A7 实现方式回写** | ADR-A7 现文写"通过官方 OpenLineage Flink 集成生成"——**D1 已改为自研上报**，须在《设计方案》第 9 章对应位置回写（**属架构文档变更，人工专属**；本记录只提供依据） |
| 源文档偏差 | 《设计方案》3.3 / 场景 C 仍写"Flink（OpenLineage 集成）产生的作业图级血缘边"——**同类偏差，一并在 ADR 回写时处理** |
| 验收方式 | ADR-A7 要求"血缘定准确率验收" ⇒ 本记录的 `test_lineage.py`（解析准确率）+ 后续"抽样比对 OM 边 vs 作业定义"构成验收手段 |

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-19 | 首次产出：D1（作业图级血缘自研上报·简版）落地与端到端验证。交付 `lineage.py` / `test_lineage.py` / `POST·GET /api/v1/lineage/report` / `ga.lineage_edge` / Dockerfile COPY；新镜像 `fc10a6b1…`、compose `f2d02896…`。验收：解析 4/4、dry-run、幂等、`entity_missing` 容错、**端到端 applied 且 OM 可读回（含 sqlQuery）**、变更删边、**清理零残留**、**镜像重扫 Total 13 未变**。记录 5 条踩坑（窗口 TVF 误判、JDBC url 反解、清理顺序写反留残留、**OM 删实体连带删边导致的已知漂移**、OM lineage 响应 `toEntity` 为 id 字符串）。明确 6 条未做（真实业务边待接源、对账任务、D2 作业节点、自动触发、门户页、OM 写权限收口）与 ADR-A7 回写需求 |
| v1.1 | 2026-09-19 | **同日追加 §5.5 对账任务（关闭未做 #2）**：新增 `POST /api/v1/lineage/reconcile`（`repair` 自愈；**只新增/修复、绝不删 OM 上的边**）、新增状态 `drift`（schema 约束升级为 `lineage_edge_status_check_v2`）、执行器 `lineage_reconcile.sh` + cron 每日 04:10、**新规则 `LineageReconcileStale`（>30h）⇒ 规则总数 21 → 22**。**验收含完整漂移闭环**：无漂移不误报 → 人为删边后 `drift=1` → `repair` 自愈 `repaired=1` 且 OM 侧 `edge_exists=True` → 再跑不重复修复 → 清理零残留。**同轮修复两个真问题**：① `getLineageEdge` 存在时返回 `{"edge":{…}}`，早期判据按顶层 `toEntity` ⇒ 永远 False ⇒ 对账误报漂移（已改为读 `edge` 键 + 精确兜底）；② **成功戳缺失导致指标行空值 ⇒ node_exporter 拒收整个 textfile，所有新鲜度指标被打哑**（日志实证，每 30s 报错）⇒ 改为 `num_or0()` 全量兜底 + 落盘自检（非法行则保留旧文件）+ 补 `# HELP`。修复后 `promtool check metrics` rc=0、Prometheus 指标数恢复 6、node_exporter 零错误。镜像漏洞扫描见 §4.4 的**重要更正**（重建时基础被换成 debian 13.7 ⇒ HIGH/CRIT 由 10/3 **降为 0/0**；并暴露"基础镜像未固定 digest"的偏差） |
| v1.2 | 2026-09-19 | **同日追加 §5.6 自主触发（提交即上报）**：新增 `submit_flink_job.sh`（**干跑门禁 → 登记 → 提交 → 上报**；`SUBMIT_CMD` 可覆盖提交命令）与 `lineage_autoreport.sh`（遍历登记目录 `/data/ai-governance/jobs/*.sql` 逐个上报，**全部成功才**对账并写成功戳；cron 改为每日跑它）。**验收**：① 好作业 → 门禁通过 → **调用提交**（用 `SUBMIT_CMD=touch <标记>` 证明）→ `apply=true` **真正写入 OM**（`edge_exists=True`）；② 坏作业（datagen）→ **rc=3 且未调用提交**（标记文件不存在）；③ 自动上报 dry-run 不写 OM、全量写入 OM、登记为空时直接对账写成功戳；④ 全程零残留。**同轮修复第三个真缺陷**：dry-run 会把行写成 `planned`，随后的 `apply=true` 把它当"已存在"跳过 ⇒ **边永远进不了 OM**（而"先干跑再提交"正是主路径）——已改为"**只有 `status='applied'` 才算已在 OM**，其余一律重推" |

---

*本成果由 AI 生成，仅供决策参考；命令与版本号执行前请按各组件官方文档核对。血缘结果判读与 ADR 回写属人工专属。*
