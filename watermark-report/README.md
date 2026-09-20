# watermark-report/ · 单机水位周报

> **用途**：《部署步骤_单机版》**S4-4**「单机水位周报（CPU/内存/磁盘，对照 M-Prod 触发条件 2）」的脚本与运行说明。
> **性质**：脚本与采集为 **Agent 自主执行**；**触发与否、是否推 M-Prod 属人工判读**（《实施执行计划》§8、Phase 4 第 1 周）。

## 1. 文件清单

| 文件 | 作用 |
|---|---|
| `watermark_report.sh` | 出报告：从 Prometheus API 取数 → 渲染 Markdown → 控制台 + `/data/ai-governance/reports/watermark-<YYYY-Www>.md` |
| `install_cron.sh` | 幂等安装/卸载 cron 任务（周一 08:00），落点 `/etc/cron.d/ai-governance-backup` |
| `README.md` | 本文件 |

## 2. 口径来源（全部取自源文档，未自造）

| 判据 | 出处 |
|---|---|
| M-Prod 触发条件 2 = 单机资源水位**持续 >70%（CPU/内存/磁盘任一）**、**Prometheus 周报连续 2 周超标** | 《实施执行计划_v2.0》触发条件表 / Phase 4 第 1 周 |
| 磁盘**达 80% 记录、停止、转人工** | 《部署步骤_单机版》0.3 |
| 加盘触发：**可用 < 20 GiB** | 本环境口径，见《…S3前置部署记录.md》 |
| 水位周报应含 **CPU/内存/磁盘水位 + 趋势** | 《实施执行计划_v2.0》运维机制段 |

阈值一律从源文档引用，**不在脚本里另立**；容量与趋势为**工程经验估算**，不作 SLA。

## 3. 部署与运行

服务器侧路径固定为 `/data/ai-governance/scripts/watermark_report.sh` 与 `/data/ai-governance/scripts/install_watermark_cron.sh`；上传用 `ssh_exec.py --put`（见仓库根 `AGENTS.md` §7 的命令纪律）。

```bash
# 首次运行（服务器上）
bash /data/ai-governance/scripts/watermark_report.sh

# 安装定时任务（幂等，可重复执行；落点 /etc/cron.d/ai-governance-backup）
bash /data/ai-governance/scripts/install_watermark_cron.sh

# 卸载
bash /data/ai-governance/scripts/install_watermark_cron.sh --remove
```

环境变量可覆盖默认值：`PROM`（默认 `http://127.0.0.1:9090`）、`OUTDIR`（默认 `/data/ai-governance/reports`）。

## 4. 纪律与踩坑（实测）

1. **不要用 `rate(metric[7d])` 求长窗均值**。Prometheus 的 `rate` 会把结果外推到整个窗口，样本有缺口时严重失真——实测 `avg(rate(node_cpu_seconds_total{mode="idle"}[7d]))` = 0.315，而同一窗口真实空闲率是 **0.992**。正确写法：`avg_over_time(rate(metric[5m])[7d:5m])`。
2. **必须实测「数据实际跨度」再下结论**。Prometheus 保留期是**上限**不是保证；本环境实测数据仅 **2.22 天**。跨度不足时必须写「数据不足」，不得把 `[7d]`/`[14d]` 的结果当「未触发」上报。
3. **「连续两周」要两个相邻窗口**，不是「7 天均值 + 14 天均值」。脚本用 `[7d]` 与 `[7d] offset 7d`（第 8–14 天）两个窗口分别判定。
4. **磁盘不要做线性外推**。镜像拉取是**台阶式**增长，`deriv` / `predict_linear` 会把台阶拉直并产出无物理意义的负值（实测外推 30 天 ⇒ −252 GiB）。脚本改为只报**实测净变化**（24h / 全窗口）与**折算速率**。
5. **文本类指标必须限定 `mountpoint`**，否则 `/boot/efi` 之类小分区会污染磁盘水位（与 `alerting/` 的同类教训一致）。
6. **报告正文不要用反引号**写命令名。脚本用 `{ ... } > "$OUT"` 渲染，反引号会被 bash 当命令替换执行（实测踩坑）。
7. **取数统一走 python3 urllib**，不用 `curl | python3 -c '...'`——后者在多层引号下会因 `\"` 转义而静默失败（表现为报告里出现 `N/A`）。

## 5. 本环境实测结论（2026-09-18 首次运行）

- 数据跨度 **2.22 天** ⇒ 三项判定一律记 **「数据不足，无法判定（需连续 14 天）」**，**未做倾向性结论**。
  - 根因已核实：Prometheus 最早样本 **2026-09-16 17:59（随 S1 上线）**，**非数据丢失**；保留期 15d ≥ 所需 14d ⇒ **首个具备判定条件的周报为 2026-10-05（周一）**。
- 磁盘 `/`：98.1 GiB / 可用 **51.9 GiB**（47.1%），**未命中**加盘阈值（<20 GiB）。
- 净变化：近 24h **−20.7 GiB**、全窗口 **−34.2 GiB**；主因是**镜像一次性拉取**（`docker system df`：29 个镜像 / 34.08 GB，可回收 4.83 GB）。
- 平台面：19 容器 / 0 非 healthy；规则 21；抓取目标 5/5。
- **证据留存**：报告目录已纳入 S1-8 配置备份（`backup_config.sh` 的 tar 清单含 `reports/`）⇒ 随每日出机一起离开本机（实测归档 31 条目 / 25,346 B，含 `reports/watermark-2026-W38.md`）。

完整证据见《执行步骤/AI数据治理平台_单机版_S4-4水位周报机制记录.md》。
