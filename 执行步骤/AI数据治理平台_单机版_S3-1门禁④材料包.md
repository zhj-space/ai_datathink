# AI 数据治理平台 · 单机版 S3-1 门禁④ 材料包（草案）

> **文档性质**：S3 门禁④（S3-1）就绪材料——**草案**，供人工定稿与签字。**Agent 不代签、不背书、不判定**。
> **依据**：《AI数据治理平台设计方案.md》v3.2 ADR-A2 / ADR-A3 / 4.3；《AI数据治理平台实施执行计划_v2.0.md》P0-A 第 1~2 周、P2 第 1 周；《AI数据治理平台_部署步骤_单机版.md》S3-1 / S3-4。
> **执行日期**：2026-09-18 ｜ **执行方**：Agent（起草 + 采集 + 机制验证）
> **硬约束**：**未签不动生产库**（《部署步骤_单机版》S3-1）。本包**不含任何对生产源库的变更**，也不含任何服务器配置变更。
> **版本**：v1.0（草案）

---

## 1. 结论摘要

| # | 事项 | 状态 |
|---|---|---|
| 1 | **文件 A《合规边界确认书（草案）》** | 已成稿（§4），责任人 SEC + 合规方，**待定稿签字** |
| 2 | **文件 B《续接窗口签约定（草案）》** | 已成稿（§5），含按源库填报表与 PG 参数推导模板，责任人 DBA，**待填值签字** |
| 3 | **PG 复制槽水位机制** | 在 POC 本机 PG 上**实测通过**（建 → 取水位 → 3 次 WAL 切换 → 删，全程无残留），见 §3 |
| 4 | **S3-4 判据② 链路缺口** | 实测发现**三个环节全缺**（无 PG 指标采集、无告警规则载体、无通知通道）⇒ 判据② 当前**无法达成**，见 §6 |
| 5 | **挂账项 #3（`max_slot_wal_keep_size` / `wal_keep_size` 取值）** | 给出**推导规则 + 示例**，取值仍标 `待确认`（DBA 核定），见 §5.3 |
| 6 | **门禁④ 状态** | **人工（项目决策方）于 2026-09-18 确认通行**。两份文件的原件归档（SEC+合规方 / DBA 签字）责任人见 §8——**Agent 未代签、未填签字栏** |

**Agent 侧结论**：门禁④ 已由人工确认通行；S3-4 判据② 的执行前提（采集 → 规则 → 通知）已摸清并列出补齐草案（**未执行**）。

> **通行后的口径**：可以按 S3-1/S3-4 的流程接触源库，但**每一次源库变更仍按 S3-4 "Agent 起草 + DBA 审核后执行"**；文件 B 的取值未落定前，**不得在源库设置 `max_slot_wal_keep_size` / `wal_keep_size`**。
> **原件归档仍待补**：本包是草案，签字栏保持空白；请把两份签署原件归档结论回填到 §8 责任链。

---

## 2. 为什么现在起草（前置与位置）

1. **S2 剩余动作全部卡在人工/外部输入**：S2-2 接业务源（凭证与访问范围须人工确认）、S2-3 SSO（回调域名 / 客户端 ID `待确认`）。Agent 侧无阻塞项可推进，但**关键路径的下一段（S3）有两份书面文件是 P0-A 就该发起的挂账项**。
2. **执行计划已明确这两件事的归属**：P0-A 第 1 周「binlog 签约定预沟通（把"≥3×停机预算"的公式和监控需求带给 DBA）」；P0-A 第 2 周「合规方边界确认发起（**书面文件起草**：POC 期事后审计定位）」——**文件起草为 Agent 起草 + 人工审核**（执行计划 §8 三级授权），提交与签约定签署为人工专属。
3. 因此本包做的是**起草件**，不越界：不含签署、不含判定、不含对生产库的任何动作。

**影响面**：本包不改变任何架构与口径（设计方案的组件表、ADR、第 11 章验收标准均不受影响）；仅把 S3-1 门禁④ 的**待签材料提前备好**，使 DBA / SEC / 合规方可以直接定稿，缩短 S3 启动等待。

---

## 3. 本机 PG 复制槽机制验证（2026-09-18 实测）

**验证对象**：POC 本机 `ai-governance-poc-postgres-1`（PostgreSQL **16.15**）。
**验证方式**：**只创建/删除 Agent 自建的临时 slot**，**未修改任何 PG 参数、未触碰任何业务数据、未接触生产源库**。
**意义**：把 S3-4 判据② 的**采集口径**与判据③ 的**建删机制**先在已知环境跑通，避免到源库上现场试错（源库侧仍需 DBA 按 §5 执行）。

| # | 项目 | 实测输出 | 判读 |
|---|---|---|---|
| 1 | 本机槽相关参数 | `wal_level=replica`、`max_slot_wal_keep_size=-1`（无兜底）、`wal_keep_size=0`、`max_wal_size=1024MB`、`wal_segment_size=16MB`、`archive_mode=off` | 本机未设兜底；**兜底必须在源库设置**才有意义 |
| 2 | 现有复制槽 | `count=0` | 起始无残留，测试环境干净 |
| 3 | 建槽（默认，不 reserve） | `restart_lsn` / `wal_status` **均为 NULL** | ⚠ **监控陷阱**：naive 的水位查询会把这类槽算成"0 保留"，需显式处理（见 §5.5） |
| 4 | 建槽（`immediately_reserve=true`） | `restart_lsn=0/78B87448`、`wal_status=reserved`、`active=f`；创建瞬间 `retained=21539704 B`（21 MB） | 该态 = **"无消费者 + 已 reserve"的真实风险态** |
| 5 | 制造 WAL：`pg_switch_wal()` × 3 | `retained=38243256 B`（36 MB）→ 38,243,392 B | 水位**随 WAL 生成单调增长** ⇒ 判据② 的采集口径成立 |
| 6 | `safe_wal_size` | `NULL`（因 `max_slot_wal_keep_size=-1`） | ⚠ **兜底未配时 `safe_wal_size` 不可用**，告警阈值必须由兜底值自算（=`兜底 × 70%`） |
| 7 | 删除槽（判据③ 的动作） | `pg_drop_replication_slot()` 成功 → `count=0`；`pg_wal` 目录 2 个段 / 33 MB | 机制可跑通、**无残留 WAL 压力** |

**沿用口径提醒（来自 S3-4）**：`pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)` 量的是**LSN 距离**，不是磁盘占用（含未写满的当前段）；判读阈值时按同一口径比较即可，**不要**与 `du` 的结果混用。

---

## 4. 文件 A：合规边界确认书（草案）

> **用法**：本节内容可直接作为独立签署件打印/流转；`____` 处与签署栏由责任方填写。
> **责任人**：SEC + 合规方（执行计划 P0-A 第 2 周）｜ **性质**：**起草件**，非最终法律意见

### 4.1 确认事项

本人确认，本次 POC（试点）范围内，AI 数据治理平台的**场景 E（敏感数据访问治理）**采用以下边界：

| # | 边界项 | 口径 |
|---|---|---|
| 1 | 场景 E 定位 | **决策面（OPA）/ 执行面（查询引擎原生能力）/ 审计面（异步）** |
| 2 | **POC 期实际能力** | **只做事后审计 + 主动告警**，**不做事前拦截**（ADR-A3） |
| 3 | 动态脱敏 | **不实现在线动态脱敏**；不存在"毫秒级动态脱敏"能力（ADR-A3 明确纠正过该提法） |
| 4 | PII 识别 | Presidio **仅离线批量识别**，**已移出在线路径** |
| 5 | 失败姿态 | OPA 不可用时 **fail-closed**（默认拒绝高风险操作），不得改为放行 |
| 6 | 告警去向 | 访问审计告警经 `governance.alerts` 分发；**仅用于内部治理与运维告警** |
| 7 | 元数据 | OpenMetadata + PostgreSQL 为**元数据唯一真理源**；血缘为**作业图级**（非事件级） |
| 8 | 质量口径 | 快照基线（权威质量指标，进看板）/ 增量窗口（异常告警，**不进质量看板**，ADR-A5） |

### 4.2 数据与留存边界（安全基线）

| # | 事项 | 口径 |
|---|---|---|
| 1 | 样本数据 | **只保留脱敏特征**；不得在代码仓库留下真实业务数据样本 |
| 2 | 原文留存 | **原文 ≤ 30 天加密留存**（安全基线⑦） |
| 3 | 凭证 | 一律占位符；真实凭证入 KMS/Vault 或加密 Secret，**不写入仓库、不写入文档** |
| 4 | 暴露面 | 仅暴露 443；SSH 按源 IP 限源 + 密钥登录（残留待放行项见 §9） |

### 4.3 明确排除（POC 期不做）

- ❌ 事前拦截 / 在线策略阻断（OPA 决策不回写执行面）
- ❌ 在线逐行/毫秒级动态脱敏
- ❌ 对外多租户 SaaS 运营
- ❌ 生产级 SLA 承诺（**容量与性能数值为工程经验估算，实施前需实测压测校准**）

### 4.4 生效范围与期限

- **生效范围**：单机版 POC（1 台服务器）+ 试运行期；**不覆盖**三节点 M-Prod 生产化（触发式启动，另行确认）
- **生效期限**：自签署日起至 ____（建议与试运行期一致）
- **变更条件**：任一边界项变化（如启用事前拦截、接入生产库、扩展租户范围）**须重新确认**

### 4.5 签署

| 项 | 填写 |
|---|---|
| SEC 签字 / 日期 | 签字：________________　____ 年 __ 月 __ 日 |
| 合规方签字 / 日期 | 签字：________________　____ 年 __ 月 __ 日 |
| 备注 | ______________________________________ |

---

## 5. 文件 B：续接窗口签约定（草案）

> **责任人**：DBA（执行计划 P0-A 第 1 周 / S3-1）｜ **性质**：**起草件 + 推导模板**，取值由 DBA 实测核定
> **对应的可靠性要求**：ADR-A2 **四件套第 1 件**（续接窗口签约定）；本文件是**第 2 件**（PG 复制槽安全保护）的取值依据。

### 5.1 判据来源（不改写）

| 源库类型 | 要求 | 来源 |
|---|---|---|
| MySQL | `binlog_expire_logs_seconds` **≥ 3 × 停机预算**，且**默认 ≥ 48h**（例：停工 16h → 48h） | 部署步骤 S3-1 |
| PostgreSQL | `wal_keep_size` **≥ 3 × 停机预算内的 WAL 增量** | 部署步骤 S3-1 |

### 5.2 按源库填报表（每个源库一行，DBA 填写）

| # | 源库标识（占位） | 类型 | 停机预算 T_stop | 当前保留值 | 要求值（按公式） | 实测 WAL/binlog 生成速率 | 结论 | 签字 |
|---|---|---|---|---|---|---|---|---|
| 1 | `<source-db-1>` | MySQL / PostgreSQL `待确认` | ____ h | `binlog_expire_logs_seconds=` ____ / `wal_keep_size=` ____ | ≥ ____ | ____ MB/h（实测） | ☐ 满足　☐ 需调整 | ____ |
| 2 | `<source-db-2>` | MySQL / PostgreSQL `待确认` | ____ h | ____ | ≥ ____ | ____ MB/h（实测） | ☐ 满足　☐ 需调整 | ____ |

> **停机预算 T_stop 由业务方给定**（本文件不预设）。**实测生成速率必须实测**，不得用经验值代替。

### 5.3 PG 侧参数推导模板（覆盖挂账项 #3，取值 `待确认`）

复制槽保护的**三道防线**（部署步骤 S3-4）与参数的关系：

| # | 防线 | 参数/动作 | 推导规则（草案） |
|---|---|---|---|
| ① | 兜底上限 | 源库 `max_slot_wal_keep_size` | `= ceil(3 × T_stop × R_WAL)` **且** ≤ 源库磁盘可承受的占用上限（§5.4）；超限时 slot 自动失效 ⇒ **宁可断流重快照，不撑爆磁盘** |
| ② | 水位告警 | Prometheus 规则，阈值 = 兜底值 **70%** | `retained_bytes > 0.7 × max_slot_wal_keep_size` → **最高优先级告警** |
| ③ | 运维纪律 | 作业下线**必须先删 slot** | 见 §5.6 checklist |

**符号**：`T_stop` = 停机预算（h）；`R_WAL` = 源库 WAL 生成速率（MB/h，**须实测**）。

**示例计算（仅示范公式用法，非承诺值、非建议取值）**：

```
假设 T_stop = 16 h，R_WAL = 3000 MB/h（实测）
  ① 兜底 = ceil(3 × 16 × 3000) = 144,000 MB ≈ 141 GiB   ← 须与源库磁盘余量对照（§5.4）
  ② 告警阈值 = 0.7 × 兜底 ≈ 100,800 MB ≈ 98 GiB
  ③ 兜底值未配置时：safe_wal_size 返回 NULL，无法用 safe_wal_size 触发告警 ⇒ 必须自算阈值
```

> ⚠ 上例若超出源库磁盘承受范围，**DBA 有权下调兜底值**并同步缩小停机预算——这是**业务续接窗口与源库磁盘安全的取舍**，属人工决策，Agent 不预设。

### 5.4 磁盘余量约束（须 DBA 核定）

| # | 约束 | 说明 |
|---|---|---|
| 1 | 兜底值 × 1 < 源库可用空间 | 避免"兜底值本身就能撑爆磁盘" |
| 2 | 建议同时评估 `archive_mode` / 备份链路是否也会占用 WAL | 本机 POC 实例 `archive_mode=off`；**源库取值以实测为准** |
| 3 | 兜底失效后的后果须接受 | slot 失效 ⇒ **必须重快照**（ADR-A2 第 4 件：重快照预案） |

### 5.5 监控需求（交给 INFRA/DE 落地，本包只写需求）

| # | 采集项 | 频率 | 级别 | 说明 |
|---|---|---|---|---|
| 1 | `pg_replication_slots` 水位：`pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)` | ≤ 1 min | **最高优先级** | 阈值 = 兜底 × 70% |
| 2 | slot 数与 `active` 状态 | ≤ 1 min | 最高优先级 | **`active=f` 且持有 WAL** = 无消费者却在占 WAL |
| 3 | `wal_status` / 失效检测 | ≤ 1 min | 最高优先级 | `wal_status IN ('lost','unreserved')` ⇒ slot 已失效，须告警并触发重快照评估 |
| 4 | `restart_lsn IS NULL` 的槽 | ≤ 1 min | 记录 | **监控陷阱**：不 reserve 的槽水位为 NULL，不能被静默算作 0（§3 第 3 项实测） |
| 5 | Checkpoint 成功率 / 作业状态 / 断流检测 | ≤ 1 min | 最高优先级 | S3-6 范围，一并纳入 |

### 5.6 作业下线 checklist（S3-4 第③道防线）

```text
[ ] 1. 停止 CDC 作业（并确认不再有消费者）
[ ] 2. 记录 slot_name 与最终 LSN（留档）
[ ] 3. SELECT pg_drop_replication_slot('<slot_name>');   -- 必删，否则 WAL 持续累积
[ ] 4. 复核：SELECT count(*) FROM pg_replication_slots;   -- 确认无残留
[ ] 5. 复核源库 WAL 占用回落（pg_wal 目录 / 磁盘水位）
[ ] 6. 记录到变更单，附上第 2/4 步输出
```

> **同一条纪律适用于源库**：删除复制槽必须走 checklist（ADR-A2 第 2 件第③道防线），**不得作为临时清理手段随手执行**。

### 5.7 签署

| 项 | 填写 |
|---|---|
| DBA 签字 / 日期 | 签字：________________　____ 年 __ 月 __ 日 |
| 源库负责人签字（如涉及跨组织） | 签字：________________　____ 年 __ 月 __ 日 |
| 结论 | ☐ 全部源库满足　☐ 有条件满足（条件：____________）　☐ 不满足（退回收敛） |

### 5.8 取值作业单（交给 DBA / 业务方的一页）

> **用途**：把文件 B 的取值从"待确认"变成"已核定"。**Agent 不代填、不代签**；以下三个输入一旦齐备，本包 §5.2 即可定稿，平台侧接线约 30 分钟。
> **前置说明**：门禁④ 的"通行"是对**边界原则**的确认；文件 B 的取值是**按源库逐个填**的——所以"通行"之后仍需要每个源库的这一次填值。

#### 5.8.1 需要三个输入

| # | 输入 | 谁给 | 说明 |
|---|---|---|---|
| 1 | **停机预算 `T_stop`（小时）** | 业务方 + PM | "最长可接受的 CDC 停机时长"的上界（含故障修复 + 变更窗口）；文首示例 16h **只是示例，不是建议值** |
| 2 | **WAL / binlog 生成速率 `R_WAL`（MB/h）** | DBA（实测） | **必须实测**，不得用经验值代替；采样窗口建议 ≥1h 或覆盖业务高峰 |
| 3 | **源库可用空间与安全占用比例** | DBA | 用于校验兜底值不会撑爆磁盘（§5.4） |

#### 5.8.2 采样命令（占位符，直接可跑）

**PostgreSQL 源库**：

```sql
-- ① 累计 WAL 生成量（PG 14+ 推荐）：采样两次，Δt 间隔
SELECT wal_bytes, stats_reset FROM pg_stat_wal;

-- ② 若无 pg_stat_wal（或需精确窗口），按 LSN 差值：采样两次
SELECT pg_current_wal_lsn();

-- ③ 现有保留参数（当前值）
SELECT name, setting, unit FROM pg_settings
 WHERE name IN ('wal_level','wal_keep_size','max_slot_wal_keep_size','max_wal_size');

-- ④ 是否已有其它复制槽消费者（避免误删/冲突）
SELECT slot_name, slot_type, active, database FROM pg_replication_slots;
```

```bash
# ⑤ 源库数据目录所在挂载点的可用空间（在源库宿主上执行）
df -h <PG数据目录挂载点>
```

**计算**：`R_WAL = Δwal_bytes / Δt`（换算为 MB/h）。

**MySQL 源库**：

```sql
-- ① 保留期当前值
SHOW VARIABLES LIKE 'binlog_expire_logs_seconds';
SHOW VARIABLES LIKE 'log_bin';

-- ② binlog 生成速率：采样两次求 SUM(File_size) 差值 / Δt
SHOW BINARY LOGS;
```

```bash
# ③ 源库磁盘可用空间（在源库宿主上执行）
df -h <MySQL数据目录挂载点>
```

> **采样注意**：`SHOW BINARY LOGS` 的合计值会因**过期清理**下降 ⇒ 采样窗口应短于保留期，或改用"最新文件大小增量 + 新增文件数"估算；PG 侧同理注意 `stats_reset` 时间。

#### 5.8.3 计算与校验（三步，套公式即可）

```text
① 续接窗口要求 A = 3 × T_stop × R_WAL                      （单位 MB）
② 复制槽兜底    B = ceil(A)，且 B ≤ 源库可用空间 × 安全比例   （比例由 DBA 定，§5.4）
③ 告警阈值      = 0.7 × B                                    ← 无需手填，规则已按源库实际 B 现场计算
   MySQL：binlog_expire_logs_seconds ≥ max(3 × T_stop × 3600, 172800)   （即默认 ≥48h）
```

**示例（仅示范算法，不是建议取值）**：`T_stop = 16h`、实测 `R_WAL = 3000 MB/h` ⇒ `A = 144,000 MB ≈ 141 GiB`；若源库可用空间不足，DBA 应**下调兜底值并同步缩小停机预算**——这是"续接窗口 vs 源库磁盘安全"的人工取舍。

#### 5.8.4 回填表格（把这一段填好发回即可）

| 源库标识（占位） | 类型 | 版本 | `T_stop`(h) | `R_WAL`(MB/h) | 采样窗口 | 可用空间(GB) | 当前保留参数 | DBA 核定结论 |
|---|---|---|---|---|---|---|---|---|
| `<source-db-1>` | | | | | | | | ☐ 满足 ☐ 需调整 |

#### 5.8.5 拿到取值后 Agent 会做的四件事（不需要你额外安排）

| # | 动作 | 性质 |
|---|---|---|
| 1 | 把取值回填本包 §5.2/§5.3 并追加版本记录（**按 DBA 核定结果落值，Agent 不自拟数值**） | 文档 |
| 2 | 新增**源库侧 `postgres_exporter` 实例**（新 systemd 单元 + Prometheus 抓取目标）；**告警规则零改动**（水位阈值由 SQL 按该实例实际兜底值计算） | `[A]` |
| 3 | 起草 **CDC 接入配置**与 `ALTER SYSTEM SET max_slot_wal_keep_size / wal_keep_size` 脚本 | `[A+H]`，**DBA 审核后执行**（文件 B 取值落定前不动源库参数） |
| 4 | 用临时 slot 做**判据②/③ 复验**（水位告警触发 + 建删演练），源库侧动作仍按 DBA 流程办 | `[A]` + DBA |

#### 5.8.6 现在就能并行做的（不等源库选定）

| # | 事项 | 谁 |
|---|---|---|
| 1 | 定 `T_stop`（业务方口径，与具体源库无关） | 业务方 + PM |
| 2 | 在源库侧**预置只读 + 复制权限账号**（`pg_monitor`/`REPLICATION`；MySQL `REPLICATION SLAVE`/`REPLICATION CLIENT`），并把连接信息放入仓库外敏感文件 | DBA |
| 3 | 通知通道地址（IM / Webhook） | INFRA |

> 源库选定后把 §5.8.4 一表回填发回即可；**源库连接信息与口令请走仓库外敏感文件，不要贴进对话或仓库**。

---

## 6. S3-4 判据② 链路缺口（实测）与补齐草案

**实测结论（2026-09-18 补录前）**：从「PG 指标采集」到「告警规则」到「通知通道」，**三个环节曾经全部缺失** ⇒ S3-4 判据②「水位告警触发」当时**不可能达成**。以下为当时的**实测证据**与补齐动作。

> **补齐进展（2026-09-18，人工授权后执行）**：
> | 环节 | 现状 | 证据 |
> |---|---|---|
> | 采集 | ✅ **已补**：`postgres_exporter` 0.20.1（宿主二进制 + systemd，仅 `pg_monitor` 只读角色），绑定 `172.17.0.1:9187`，**经 PgBouncer 6432 回环**（不直连 5432） | Prometheus job `postgres` → `pg_up=1`（667 条 `pg_` 指标） |
> | 规则 | ✅ **已补**：`alerting/poc-alerts.yml` 入 Git → 服务器规则目录，Prometheus 加 `rule_files` + `--web.enable-lifecycle`；**8 条规则**（SLO 3 + 复制槽 5），`promtool check rules` SUCCESS | `/api/v1/rules` → `poc-slo(3) + postgres-replication-slot(5)`，`health=ok` |
> | 通知 | ⏳ **链路已证实可用，地址待定**：临时 sink 端到端自检通过（Prometheus firing → Alertmanager → sink 收到 1045 字节真实通知体），临时物已清理；**真实 IM/Webhook 地址属 S3-6** | 《AI数据治理平台_单机版_S3前置部署记录.md》§4.5 |
> **判据② 仍差一步**：等通知地址确定后用同一自检方法复验，即可判"水位告警触发"通过。
> **规则设计要点已按本包口径实现**：水位阈值 = `retained_bytes / max_slot_wal_keep_size`，**由 SQL 现场计算、规则里不硬编码兜底值** ⇒ §5.3 取值未落定也能先用；"未配兜底"另有单独规则（`NoSafetyCap`）fail-visible，**且仅在该实例确有复制槽时告警**（避免平台自身 PG 刷屏）。
> 详细落地证据见《AI数据治理平台_单机版_S3前置部署记录.md》§4。

| 环节 | 现状实测 | 缺口 | 补齐动作（草案，待放行后执行） |
|---|---|---|---|
| **采集** | `systemctl is-active postgres_exporter` → **inactive**；`/usr/local/bin`、`/usr/bin`、`/opt` 均**无二进制** | **没有任何 PG 指标采集**（node_exporter 为宿主二进制、cadvisor 已跑，但都不含复制槽指标） | 部署 `postgres_exporter` 宿主二进制（systemd），采集 DSN 用**最小权限只读账号**；在 `prometheus.yml` 增 `job_name: postgres` |
| **规则** | `prometheus.yml` **无 `rule_files`**；`/api/v1/rules` → `groups: []` | **没有任何告警规则被加载** | 新增规则文件并挂载（`/etc/prometheus/rules/*.yml`）：① slot 水位 > 兜底×70%（最高优先级）；② slot 失效；③ **S1-7 约定的 SLO：CPU<80%、内存<85%**（该阈值至今无规则载体，见 §10 第 1 项） |
| **通知** | `alertmanager.yml` 的 `receivers` 仅 `default` 且**无任何通道配置**；`/api/v2/alerts` → `[]` | 告警**无处可送** | 接入 IM/Webhook 通道（S3-6 范围：Alertmanager/IM 打通），并做一次真实触发验证 |

**判据② 的可达成条件（补齐后按序验证）**：能采（指标出现）→ 能判（规则 FIRING）→ 能送（通知到达接收方）→ **三者皆有证据**才可判"告警触发"通过。

---

## 7. 复现命令清单

> 全部为**只读**命令，除第 4/5 条会创建并按行删除 Agent 自建临时 slot（**无残留**）。主机名/口令一律占位。

```bash
# 1) 本机 PG 复制槽参数与现状（只读）
docker exec ai-governance-poc-postgres-1 psql -U postgres -At -F'|' -c \
  "select name, setting from pg_settings where name in ('wal_level','wal_keep_size','max_slot_wal_keep_size','max_wal_size','wal_segment_size','archive_mode');"
docker exec ai-governance-poc-postgres-1 psql -U postgres -At -c "select count(*) from pg_replication_slots;"

# 2) 告警链路三环节（只读）
systemctl is-active node_exporter postgres_exporter
grep -nE 'rule_files|scrape_configs' -A3 /data/ai-governance/config/prometheus/prometheus.yml
curl -s localhost:9090/api/v1/rules | python3 -m json.tool | head -20
curl -s localhost:9093/api/v2/alerts

# 3) Prometheus 抓取目标
curl -s localhost:9090/api/v1/targets | python3 -c 'import json,sys;d=json.load(sys.stdin);[print(t["labels"].get("job"),t["labels"].get("instance"),t["health"]) for t in d["data"]["activeTargets"]]'

# 4) 【会建临时槽】已 reserve 的真实风险态 + 水位增长
docker exec ai-governance-poc-postgres-1 psql -U postgres -At -F'|' -c \
  "select slot_name, lsn from pg_create_physical_replication_slot('s34_probe_reserved', true);"
docker exec ai-governance-poc-postgres-1 psql -U postgres -At -F'|' -c \
  "select slot_name,active,wal_status,pg_wal_lsn_diff(pg_current_wal_lsn(),restart_lsn) from pg_replication_slots where slot_name='s34_probe_reserved';"
for i in 1 2 3; do docker exec ai-governance-poc-postgres-1 psql -U postgres -At -c "select pg_switch_wal();"; done
docker exec ai-governance-poc-postgres-1 psql -U postgres -At -F'|' -c \
  "select slot_name,active,wal_status,pg_wal_lsn_diff(pg_current_wal_lsn(),restart_lsn) from pg_replication_slots where slot_name='s34_probe_reserved';"

# 5) 【必做】删除临时槽并复核无残留
docker exec ai-governance-poc-postgres-1 psql -U postgres -At -c "select pg_drop_replication_slot('s34_probe_reserved');"
docker exec ai-governance-poc-postgres-1 psql -U postgres -At -c "select count(*) from pg_replication_slots;"
```

**本包执行脚本**（落操作机仓库外 `~/.ai-datathink/remote/`）：`157_slot_probe.sh`（参数/槽/导出器/抓取目标）、`158_slot_mechanism.sh`（建删机制）、`159_slot_reserve.sh`（reserve 态与水位增长）、`160_alert_chain.sh`（告警链路三环节）。

---

## 8. 接源前置清单（谁提供什么）

| 面 | 事项 | 责任方 | 阻塞谁 |
|---|---|---|---|
| 合规 | 文件 A 定稿签字 | SEC + 合规方 | S3-1（门禁④） |
| 合规 | 文件 B 填值与签字（按源库分列） | DBA + 源系统负责人 | S3-1（门禁④）、S3-4 |
| 数据 | 源库连接信息（主机/端口/库/schema）与**最小权限只读账号** | DBA + 源系统负责人 | S2-2、S3-3 |
| 权限 | CDC 权限：MySQL `REPLICATION SLAVE`/`REPLICATION CLIENT`；PG `REPLICATION` + 目标表 `SELECT` | DBA | S3-3 |
| 参数 | 源库 `max_slot_wal_keep_size` / `wal_keep_size`（PG）、`binlog_expire_logs_seconds`（MySQL）落值 | DBA（按 §5.3 推导） | S3-4 |
| 监控 | 补齐 §6 三环节（采集/规则/通知） | INFRA + DE | S3-4 判据②、S3-6 |
| 业务 | 停机预算 `T_stop`（业务方给定） | 业务方 + PM | 文件 B 全部填值 |

> 连接信息、账号、口令**一律不写入仓库**，按占位符流转（AGENTS §9）。

---

## 9. 待人工决策与签字清单（汇总）

### 9.1 本包内

| # | 事项 | 责任方 | 性质 |
|---|---|---|---|
| 1 | 文件 A《合规边界确认书》定稿签字 | SEC + 合规方 | 签字 |
| 2 | 文件 B《续接窗口签约定》填值签字（含 `T_stop`） | DBA（+ 业务方） | 签字 |
| 3 | §6 三环节补齐是否放行（放行后 Agent 可执行） | PM / INFRA | 放行 |

> **状态更新（2026-09-18）**：上表第 1、2 项已由**人工（项目决策方）确认通行**——门禁④ 判为通过。签字栏仍保持空白（Agent 不代签），两份文件的原件归档由 SEC + 合规方 / DBA 按 §4.5、§5.7 完成。
> 第 3 项（§6 三环节补齐）**已放行并完成**：采集与规则已补（8 条规则 `health=ok`），通知链路已证实可用、真实地址待 S3-6；**文件 B 的按源库取值仍待填**（见 §5.8 取值作业单）。

### 9.2 仍在他人手上的挂账项（不在本包范围，相互引用）

| # | 事项 | 指向 |
|---|---|---|
| 1 | 门禁② DBA 签字（出机介质未闭环） | 《…S1准出材料包.md》《…S1-8备份恢复脚本.md》 |
| 2 | 门禁③ DBA + DE 判定签字 | 《…S2-4门禁③证据包.md》§8 |
| 3 | OPA 策略与阈值审核栏 | 《…S2-5OPA策略与部署.md》§6 |
| 4 | Presidio 规则与评分审核栏 | 《…S2-5Presidio部署与规则.md》§6 |
| 5 | S2 准出（覆盖率/PII/血缘需接源后才有数） | 《单机版部署计划.md》S2 段 |
| 6 | 443 暴露面收敛方式（OM 当前对公网开放） | 《…S2接入方案.md》§8.5 |
| 7 | FERNET_KEY 重评（接含真实凭证的业务源之前） | 《…S2接入方案.md》§9.3 |
| 8 | SSH 残留待放行（装公钥/关口令/轮换口令/fail2ban） | 《…S1部署记录.md》§3.7.5 |
| 9 | 出机备份介质 | 《单机版部署计划.md》Open Questions |
| 10 | SSO 回调域名 / 客户端 ID | 同上 |

---

## 10. 未做 / 待确认

| # | 项 | 说明 |
|---|---|---|
| 1 | **S1 记录应回填的偏差**：S1-7 约定的 SLO 阈值（CPU<80%/内存<85%）**至今无规则载体**（实测 `groups: []`），S1 准出只验了 `/targets` 全 UP | **本次未改**《…S1部署记录.md》——属 S1 主题，按"一次一个主题"另起一处修订；**建议**在 S1 记录的偏差表补一行（同 §6 证据） |
| 2 | 本机 PG 未设 `max_slot_wal_keep_size`（`-1`） | 本机非源库、无槽消费方，**故意不设**；兜底属**源库**配置，取值 `待确认`（§5.3） |
| 3 | 兜底失效演练未做 | 属 S3-7 三项演练之一，且必须在**源库或源库副本**上做；本机不做（改本机参数无证明力） |
| 4 | §6 三环节**未执行** | 采集/规则/通知的补齐动作属 S3-4/S3-6，须在门禁④ 签字与放行后执行 |
| 5 | MySQL 侧未实测 | 无 MySQL 源库可测；`binlog_expire_logs_seconds` 判据按源文档写，实测由 DBA 在源库执行 |
| 6 | 文件 A/B 未经法务与合规审阅 | 本包为**技术与治理口径草案**，法律表述须由责任方定稿 |

---

## 11. 影响面与需同步的下层文档

| 文档 | 是否需要同步 | 理由 |
|---|---|---|
| 设计方案 v3.2 / 实施执行计划 v2.0 | **否** | 本包不含新增架构决策，不改 ADR 与验收标准 |
| 《部署步骤_单机版》S3-1 / S3-4 | **是（轻量）**：加"材料包已起草"指引行 | 部署步骤是该主题的执行依据，须能指向草案 |
| 《单机版部署计划》S3-1 段 | **是**：加执行状态注 + 版本记录 | 计划是对外进度视图 |
| 《…S1部署记录.md》 | **建议**（第 10 节第 1 项） | S1-7 的规则载体缺口属 S1 主题，本次未改 |

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-18 | 首次产出（草案）：① 本机 PG 复制槽水位机制实测（7 项，含 `restart_lsn`/`safe_wal_size` 两个监控陷阱与建删无残留）；② 文件 A《合规边界确认书（草案）》；③ 文件 B《续接窗口签约定（草案）》含按源库填报表、PG 参数推导模板与示例、磁盘约束、监控需求、作业下线 checklist；④ **实测发现 S3-4 判据② 三环节全缺**（无 postgres_exporter、`rule_files` 空、Alertmanager 仅 default 无通道）；⑤ 接源前置清单与待签字/待决策汇总；⑥ 明确 Agent 不代签、未做任何服务器配置变更。**未改动源文档、未接生产库** |
| v1.1 | 2026-09-18 | **门禁④ 通行记录（人工决定）**：§1 增状态行与通行后口径（★ 每次源库变更仍按 S3-4 "Agent 起草 + DBA 审核后执行"；文件 B 取值落定前不得在源库设置 `max_slot_wal_keep_size` / `wal_keep_size`）；§9.1 增状态更新（第 1/2 项已人工确认通行，**签字栏保持空白**，原件归档由 SEC+合规方 / DBA 完成；第 3 项 §6 三环节补齐仍待放行）。**Agent 未代签、未背书** |
| v1.2 | 2026-09-18 | **§6 三环状态更新**：采集（postgres_exporter 0.20.1，经 PgBouncer 6432 回环，`pg_up=1`）与规则（8 条，promtool SUCCESS，`health=ok`）**已补齐**；通知链路**已用临时 sink 端到端证实可用、真实地址待定（S3-6）** ⇒ 判据② 只差通知地址一步。补记规则实现口径（水位阈值由 SQL 现场计算、无硬编码兜底值；"未配兜底"单独 fail-visible 且仅在有槽时告警）。指向《AI数据治理平台_单机版_S3前置部署记录.md》§4 |
| v1.3 | 2026-09-18 | **新增 §5.8 取值作业单**（交给 DBA/业务方的一页）：三个输入（`T_stop` / 实测 `R_WAL` / 源库可用空间与安全比例）、**可直接执行的 PG/MySQL 采样命令**（含 `pg_stat_wal`、`SHOW BINARY LOGS` 的采样陷阱）、三步计算与校验公式、回填表格、拿到取值后 Agent 的四项动作（回填 / 源库 exporter 实例 / CDC 配置与 `ALTER SYSTEM` 脚本起草 / 判据②③ 复验）与"不等源库选定即可并行"的三件事。同时明确：**门禁④"通行"是对边界原则的确认，文件 B 取值仍须按源库逐个填**；Agent 不代填、不代签 |

---

*本文件为 S3-1（门禁④）的就绪材料草案，由《部署步骤_单机版》S3-1 / S3-4 展开，不含新增架构决策；与源文档冲突时以源文档为准并回报修订。*
*门禁判定与签字为人工专属，Agent 不代签、不背书。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；文件 A/B 的法律与合规表述、以及所有参数取值，须由 SEC/合规方/DBA 定稿与实测核定。
