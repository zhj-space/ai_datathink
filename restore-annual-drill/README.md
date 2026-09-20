# restore-annual-drill/ · 年度化恢复演练（本地段）

> **用途**：《部署步骤_单机版》**S4-4**「年度化备份恢复演练」的**本地段执行器**。
> **性质**：只读复制 + 临时对象；**结果判读与签字为人工专属**；报告写入 `/data/ai-governance/reports/`（随配置备份出机）。

## 1. 文件清单

| 文件 | 作用 |
|---|---|
| `run_annual_drill.sh` | 执行器：§0 新鲜度 → §1 PG 逐库 → §2 OpenSearch 快照 → §3 配置归档 → §4 Flink Checkpoint → §5 汇总 → §6/§7 声明 |
| `README.md` | 本文件 |

## 2. 复用而非重写

PG 与 OpenSearch 两段**直接调用既有脚本**，不重复实现：

| 段 | 调用的脚本 | 覆盖 |
|---|---|---|
| §1 | `/data/ai-governance/scripts/restore_drill_pg_all.sh` | 6 个平台库逐个恢复 + 对象数比对 + 计时 + 临时库清理核验 |
| §2 | `/data/ai-governance/scripts/restore_drill.sh` | OpenSearch：造索引 → 打快照 → 删索引 → 恢复 → 文档数比对（其 PG 单库段顺带复核） |
| §3 | 本脚本内置 | 解开最新 config 归档：`.env` 非空、compose 含 services、`config/` 文件数、**密钥键名集合与现网逐一比对** |
| §4 | 本脚本内置 | Flink Checkpoint 可测性（目录数 + 作业列表）；**无对象就如实写"不可演练"**，不伪造 |

## 3. 运行

```bash
bash /data/ai-governance/scripts/restore_annual_drill.sh
```

报告：`/data/ai-governance/reports/restore-annual-drill-<ts>.md`。

## 4. 纪律与边界（容易被误读，务必连读）

1. **不等于"全灾备可用"**。报告 §7 固定列出 5 条未覆盖：PG PITR、Flink Checkpoint 出机恢复、出机副本恢复、主机级重建 RTO、真实业务数据量下的恢复。
2. **PG PITR 在本环境不存在**：S1 设计未启用 WAL 归档，实际保证是「最近一次小时级逻辑 dump」（RPO ≤1h）。**不要把 dump 恢复说成 PITR。**
3. **对象级恢复耗时 ≠ 主机级 RTO**。本脚本测的是"从备份把对象恢复出来"，不是"从零重建一台机器"。
4. **配置归档含真实密钥**（`.env` 内有 FERNET_KEY / SECRET_KEY / 各库口令）。解包目录必须建在 `/tmp` 且**用毕即删**；脚本对临时目录名做了前缀断言，路径不符直接中止，绝不 `rm -rf` 未验证路径。
5. **临时对象零残留是本脚本的自检项**：PG 段会打印 `drill_*`/`restore_drill_*` 残留查询结果，必须为 `(none)`。
6. **数值比较不要用字符串比较**：`12.5 < 1.5` 在词法比较下会判成真；脚本统一用 awk 做数值比较。

## 5. 本环境首次执行（2026-09-19 00:06）

| 段 | 结果 |
|---|---|
| §0 新鲜度 | PG 0.02 h（阈值 1.5h）✔ / 配置 0.71 h（阈值 30h）✔ / OS 快照 0.01 h（阈值 30h）✔ |
| §1 PG 逐库（6 库） | **全部对象数一致**（openmetadata_db 203、airflow_db 101、superset 103、gov_metrics 4、governance_agent 6、postgres 0）；合计 **6s**；残留 `(none)` |
| §2 OpenSearch | 恢复后文档数与快照前**一致 ✔**；用时 **2s** |
| §3 配置归档 | 31 条目；`.env` 非空；compose 含 services；`config/` 16 文件；**密钥键名 24/24 与现网一致** |
| §4 Flink Checkpoint | **0 个 checkpoint、0 个作业 ⇒ 本环境不可演练**（如实记录，未伪造） |
| §5 计时 | 对象级合计 **8s**（RTO 目标 4h） |

完整证据见《执行步骤/AI数据治理平台_单机版_S1-8备份恢复脚本.md》§5.2。
