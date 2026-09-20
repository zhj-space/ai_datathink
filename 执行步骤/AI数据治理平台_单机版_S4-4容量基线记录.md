# AI 数据治理平台 · 单机版 S4-4 容量基线记录（自压）

> **文档版本**：v1.0（2026-09-19）
> **定位**：《部署步骤_单机版》**S4-4**「真实负载压测，对照 4.1.2 容量表校准」中**压测骨架**部分的落地记录。
> **执行方**：脚本与采集 `[A]`；**容量判读与是否据此调参 `[H]`**。
> **结论先行**：**自压骨架已建立并跑出首版基线**（六条链路：PG / Kafka / OpenSearch / Ollama / 服务 API / 宿主面），全程**零临时对象残留、零容器异常**。
> 但这是**单机自压基线，不是真实业务负载**：本环境门禁① 已由人工豁免（设计方案 4.1.3 明文本环境不满足压测档），故**本记录不得用于准出判读**，只作"链路可用性与量级锚点"。

---

## 1. 交付物

| # | 交付物 | 仓库位置 | 服务器落点 |
|---|---|---|---|
| 1 | 基线主脚本 | `capacity-baseline/run_baseline.sh` | `/data/ai-governance/scripts/capacity_baseline.sh`（0755） |
| 2 | OpenSearch 写入子项 | `capacity-baseline/os_bulk_bench.py` | `/data/ai-governance/scripts/os_bulk_bench.py`（运行时 `docker cp` 进容器执行后删除） |
| 3 | 目录说明 | `capacity-baseline/README.md` | —— |
| 4 | 基线报告（首份） | —— | `/data/ai-governance/reports/capacity-baseline-20260919-000128.md`（`reports/` 已纳入 S1-8 备份与出机） |

---

## 2. 口径与边界

| 项 | 说明 |
|---|---|
| 依据 | 《部署步骤_单机版》S4-4「真实负载压测，对照 4.1.2 容量表校准（**容量数值为经验估算，作压测校准依据，不作 SLA 承诺**）」 |
| 定位 | **前置自压骨架**：证明链路可跑 + 给出量级锚点。**真实负载压测**仍需业务源与压测窗口（未做） |
| 不做什么 | 不接触业务源；不改任何组件配置；不测磁盘（门禁① 已豁免）；不给吞吐上限结论 |
| 硬约束遵守 | PG 读写**全部经 PgBouncer 6432**（硬约束 2）；临时对象用完即删并**在报告里自查残留** |
| 判读归属 | 数值与是否据此调参属**人工判读**；Agent 只产出与核对 |

---

## 3. 首版基线（2026-09-19 00:01，标准模式；报告 sha 见 §5）

### 3.1 宿主与运行面

| 项 | 值 |
|---|---|
| CPU / 内存 | 24 vCPU / 94,874 MiB |
| 根分区可用 | 53,093 MiB |
| load1（压测前） | 1.28 |
| 容器数 / 非 healthy | 19 / 0 |
| 镜像占用 | 34.08 GB（可回收 4.826 GB） |

### 3.2 四条数据/推理链路

| 链路 | 参数 | 结果 |
|---|---|---|
| **PostgreSQL 读写混合** | pgbench TPC-B，8 客户端 × 30s，`-M simple`，**经 PgBouncer 6432** | **1342.5 tps**，平均延迟 **5.96 ms** |
| **PostgreSQL 只读** | pgbench `-S`，16 客户端 × 30s | **12,101.6 tps**，平均延迟 **1.32 ms** |
| **Kafka 生产** | 300,000 条 × 1024 B，acks=1，单分区 | **60,423 records/s**，**59.01 MB/s**，p95 700 ms |
| **OpenSearch 写入** | 单次 bulk，50,000 文档，单分片 0 副本，refresh=false | **100,806 docs/s**（0.496s），`errors=false`，**落库 50000/50000**（显式 refresh 后计数） |
| **Ollama qwen2.5:1.5b** | 冷启 / 热跑（不同 prompt） | 近似首 token **1.52s / 1.58s**；生成 **33.7 / 32.9 tok/s** |
| **Ollama qwen2.5:3b** | 同上 | 近似首 token **2.82s / 2.88s**；生成 **17.7 / 17.3 tok/s** |

### 3.3 服务 API 往返（单次采样，仅表量级）

| 端点 | HTTP / 耗时 |
|---|---|
| OM `/health` | 200 / 1.1 ms |
| OM `/api/v1/system/version` | 200 / 1.0 ms |
| **OM 认证读取 `/api/v1/tables?limit=10`** | 200 / **3.2 ms** |
| GA `/health`、`/ready` | 200 / 0.7 ms、32.5 ms |
| Superset `/health` | 200 / 1.9 ms |
| 门户 `/healthz` | 200 / 1.6 ms |
| Prometheus `/-/healthy` | 200 / 0.5 ms |

### 3.4 现场自查

| 项 | 值 |
|---|---|
| 临时对象残留 | **无**（目标名：`bench_baseline` / `bench.baseline` / `bench-baseline-*`） |
| 压测后非 healthy 容器 | **0** |

---

## 4. 实测踩坑（四条，全部已复现并修复）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 首次跑完**残留 `bench_baseline` 库**，二次运行直接建库失败 | 经 PgBouncer 建库后池内保留服务端连接 ⇒ `DROP DATABASE` 报 `database is being accessed by other users` | 新增 `ensure_db_gone()`：DROP → 仍在则 **`RECONNECT` 回收池连接** → 再 DROP；建库前与收尾各执行一次（与 AGENTS §7 既有纪律同源） |
| 2 | OpenSearch 报 `Validation Failed: 1: index is missing; 2: index is missing; …`（每行一条） | action 元数据用 `{"index":{}}` 但请求打到 `/_bulk`，URL 里没有索引 | 改打 `/<index>/_bulk` |
| 3 | **首个版本误得 18 万 docs/s**（虚高） | bulk 全被拒仍返回很快，脚本当时只看耗时 | 除耗时外**必须校验 `errors` 与成功条目数**；本轮实得 10.1 万 docs/s 且 `errors=false`、落库计数一致 |
| 4 | bulk 成功后 `_count` 为 0 | `refresh=false` 时文档在 translog、尚不可搜 | 显式 `_refresh` 后再 `_count`，且 refresh 耗时不计入写入吞吐 |

> 另一条与本环境相关的通用坑（已在 `capacity-baseline/README.md` 固化）：**远程命令内嵌多层引号会把 OpenSearch 报文写坏**——改 `docker cp` 一个 python 文件进容器执行；同时本机链路会吞掉双引号，远程命令一律走 `--script` 传本地脚本。

---

## 5. 怎么验证的

```
$ bash -n /data/ai-governance/scripts/capacity_baseline.sh && echo SYNTAX-OK
SYNTAX-OK
$ /data/ai-governance/scripts/capacity_baseline.sh            # 标准模式，约 95s
[2026-09-19 00:01:28] A 宿主资源与运行面
... （六段全跑完）
报告已写入：/data/ai-governance/reports/capacity-baseline-20260919-000128.md
```

关键自查（均来自报告正文，非口头结论）：
1. §7「临时对象残留 = **无**」——临时库 / Topic / 索引三类对象均确认删除。
2. §7「压测后非 healthy 容器 = **0**」——压测未把任何服务打挂。
3. §4 行列出了 `errors=false` 与「落库校验 50000/50000」——写入是**真成功**，不是"快但全被拒"。
4. `--quick` 模式另跑过一轮，用同一套判据复核通过（防止"只有标准模式能跑"的假象）。

---

## 6. 明确未证明（不得据此下结论）

| # | 未证明项 | 为什么 |
|---|---|---|
| 1 | **真实业务负载下的容量** | 无业务源；本项需 S2-2 接源后才能做 |
| 2 | 吞吐上限 / 拐点 / 稳定性 | 只跑了单一并发档，未做阶梯压测；本环境 4.1.3 明示不适合压测 |
| 3 | 与设计方案 4.1.2 容量表的校准结论 | 校准需真实负载；本记录只给锚点 |
| 4 | OpenSearch / PG 的长期稳定性与 GC 行为 | 单次短时压测不覆盖 |
| 5 | 磁盘 IOPS/顺序写（门禁① 指标） | 门禁① 已人工豁免；本脚本按纪律不测磁盘 |

---

## 7. 版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-19 | 首次产出：S4-4 压测骨架落地（`capacity-baseline/`：主脚本 + OpenSearch 子项 + README）并跑出首版自压基线（PG 1342 tps 读写 / 12,102 tps 只读；Kafka 60,423 rec/s；OS 100,806 docs/s；Ollama 1.5b 33 tok/s、3b 17 tok/s）。记录四条实测踩坑（PgBouncer 池连接挡 DROP、bulk 索引须在 URL、被拒 bulk 仍"很快"会虚高、refresh=false 下 _count 为 0）与五条明确未证明项。新增 `capacity-baseline/` 并入 AGENTS 文档地图与 §7 代码目录表 |

---

*本成果由 AI 生成，仅供决策参考；命令与版本号执行前请按各组件官方文档核对。*
