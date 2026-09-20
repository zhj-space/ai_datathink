# capacity-baseline/ · 单机容量基线（自压）

> **用途**：《部署步骤_单机版》**S4-4**「真实负载压测，对照 4.1.2 容量表校准」的**前置自压骨架**。
> **性质**：不接触业务源、不改任何组件配置；临时对象（库/Topic/索引）用完即删。

## 1. 文件清单

| 文件 | 作用 |
|---|---|
| `run_baseline.sh` | 主脚本：A 宿主面 / B PG（经 PgBouncer）/ C Kafka / D OpenSearch / E Ollama / F 服务 API → Markdown 报告 |
| `os_bulk_bench.py` | OpenSearch 写入子项（**在 opensearch 容器内**用 python3 跑，避免多层 shell 引号把 NDJSON 写坏） |
| `README.md` | 本文件 |

## 2. 口径与边界（先读这段）

1. **是自压基线，不是真实业务负载**；数值是**实测锚点**，**不是 SLA**。
2. 本环境**门禁①（fio/容量）已由人工豁免**（设计方案 4.1.3：不满足压测档）⇒ **不得**用本报告做准出判读。
3. 只测「链路能不能跑、量级多少」；**不证明**吞吐上限、并发能力、稳定性。
4. 不写任何配置；**压测结束必须零残留**（脚本第 7 节会自查并写进报告）。

## 3. 运行

```bash
# 标准（PG 30s x 2 轮 + Kafka 30 万条 + OS 5 万文档 + Ollama 双模型）
bash /data/ai-governance/scripts/capacity_baseline.sh
# 冒烟（时长减半）
bash /data/ai-governance/scripts/capacity_baseline.sh --quick
```

可覆盖参数：`PG_SCALE` `PG_CLIENTS` `KAFKA_RECORDS` `KAFKA_SIZE` `OS_DOCS` `REPORT_DIR`。
报告写入 `/data/ai-governance/reports/capacity-baseline-<ts>.md`（该目录已纳入 S1-8 配置备份与出机）。

## 4. 实测纪律与踩坑（都是真踩过的）

1. **DROP DATABASE 会被 PgBouncer 池内连接挡住**。经 PgBouncer 建库后，池里保留到该库的服务端连接，直接 DROP 报 database is being accessed by other users，于是**留下残留库**。必须先 `RECONNECT` 回收池连接再 DROP（与 AGENTS §7「权限变更后必须回收池连接」同一条纪律）。脚本用 `ensure_db_gone()`（DROP → 仍存在则 RECONNECT → 再 DROP）在建库前与收尾各执行一次。
2. **bulk 的索引必须出现在 URL 里**。用 `{"index":{}}` 这种 action 元数据时，请求必须打到带索引的路径；只写 `/_bulk` 会被 OpenSearch 判 Validation Failed: index is missing（每行一条错误，实测 5000 文档全被拒）。**注意：被拒时 bulk 仍返回很快**，只看耗时就会得出虚高的 docs/s（曾误得 18 万 docs/s）。
3. **bulk 用 refresh=false 时 _count 会是 0**（文档在 translog、尚未可搜）。必须显式 `_refresh` 后再计数，且 refresh 耗时**不计入**写入吞吐。
4. **不要把 OpenSearch 的 bulk 报文拼在多层 shell 引号里**。`sh -c` + `docker exec` + awk 的嵌套转义极易把 NDJSON 写坏；改为 `docker cp` 一个 python 文件进容器执行。
5. **pgbench 用 -M simple**：刻意避开扩展协议，与门禁③ 的 prepared statement 口径无关，避免把两件事混在一起。
6. **Ollama 冷启/热跑要用不同 prompt**（含纳秒后缀），否则 prompt cache 会让复测值失真（M0 第 6 项的教训）。
7. **本机链路会把双引号吞掉**（PowerShell → ssh 多层）：远程命令尽量用 ssh_exec 的 `--script` 传本地脚本，不要玩内联引号。

## 5. 本环境基线（2026-09-19 00:01，标准模式）

| 项 | 结果 |
|---|---|
| 宿主 | 24 vCPU / 94,874 MiB / 根分区可用 53,093 MiB；19 容器 0 非 healthy |
| PG 读写混合（8 客户端 / 30s，经 PgBouncer） | **1342 tps**，平均延迟 5.96 ms |
| PG 只读（16 客户端 / 30s） | **12,102 tps**，平均延迟 1.32 ms |
| Kafka 生产（30 万条 x 1KB，acks=1） | **60,423 records/s**，**59.0 MB/s**，p95 700 ms |
| OpenSearch 写入（5 万文档，单分片 0 副本） | **100,806 docs/s**，errors=false，落库 50000/50000 |
| Ollama qwen2.5:1.5b | 近似首 token **1.58s**，生成 **32.9 tok/s** |
| Ollama qwen2.5:3b | 近似首 token **2.88s**，生成 **17.3 tok/s** |
| 服务 API | OM / Superset / 门户 / GA / Prometheus 全 200，0.5~33 ms |
| 临时对象残留 | **无** |

完整证据见《执行步骤/AI数据治理平台_单机版_S4-4容量基线记录.md》。
