# AI 数据治理平台 · 单机版 S4-4 水位周报机制记录

> **文档版本**：v1.0（2026-09-18）
> **定位**：《部署步骤_单机版》**S4-4**「单机水位周报」的落地记录与证据归档。
> **执行方**：脚本与采集 `[A]`（Agent 自主）；**触发判定与 M-Prod 决策 `[H]`（人工专属）**。
> **结论先行**：机制已建立并跑通（脚本 + cron + 首份周报）；**本环境首次判定为「数据不足」**——Prometheus 实测数据跨度仅 **2.22 天**，不足 14 天，按纪律**不给出「未触发」或「触发」的任何倾向性结论**。

---

## 1. 交付物

| # | 交付物 | 仓库位置 | 服务器落点 |
|---|---|---|---|
| 1 | 周报脚本 | `watermark-report/watermark_report.sh` | `/data/ai-governance/scripts/watermark_report.sh`（0755） |
| 2 | cron 安装器（幂等） | `watermark-report/install_cron.sh` | `/data/ai-governance/scripts/install_watermark_cron.sh` |
| 3 | cron 任务 | —— | `/etc/cron.d/ai-governance-backup`：`0 8 * * 1 root …watermark_report.sh >> /data/backup/log/watermark_report.log 2>&1` |
| 4 | 报告输出 | —— | `/data/ai-governance/reports/watermark-<YYYY-Www>.md`（+ 每次运行打印到 stdout） |
| 5 | 目录说明 | `watermark-report/README.md` | —— |

**脚本 sha256**（现行）：`ed3becc7544f676b9f49653bb3c614269f598883fc5ce4e7baa0327046301d13`

---

## 2. 口径（全部取自源文档，未自造）

| 判据 | 取值 | 出处 |
|---|---|---|
| M-Prod 触发条件 2 | 单机资源水位**持续 >70%（CPU/内存/磁盘任一）**；**Prometheus 周报连续 2 周超标** | 《实施执行计划_v2.0》触发条件表第 2 行；Phase 4 第 1 周「单机水位周报机制建立」 |
| 周报内容 | CPU/内存/磁盘**水位 + 趋势** | 《实施执行计划_v2.0》运维机制段 |
| 磁盘高水位处置 | **达 80% 记录、停止、转人工**（硬约束 5：不自动降级） | 《部署步骤_单机版》0.3 |
| 加盘触发 | 可用 **< 20 GiB** | 本环境口径（《…S3前置部署记录.md》） |
| 执行/判读分工 | 脚本与采集 Agent 自主；**判读人工专属** | 《实施执行计划_v2.0》§8、Phase 4 第 1 周 |

---

## 3. 关键实现决策（含两次实测纠偏）

### 3.1 窗口均值不能用 `rate(metric[7d])`

首版用 `avg(rate(node_cpu_seconds_total{mode="idle"}[7d]))` 求"7 天 CPU 水位"，得到 **68.5%**（并据此写"未触发"）——**假值**。同一窗口用 `avg_over_time(rate(...[5m])[7d:5m])` 得到空闲率 **0.9917**，即真实占用 **0.83%**，相差近 **80 倍**。

> **原因**：Prometheus 的 `rate` 在设计上会把结果**外推到整个窗口**；窗口内有样本缺口时（本环境实测 7 天窗口内单核仅 6401 个样本 ≈ 30s 间隔的 2.2 天量），外推与真实增长量的比例失真。
> **纪律**：长窗均值一律走「短窗速率/比值 → 子查询 → `avg_over_time`」。

### 3.2 「连续两周」= 两个相邻 7 天窗口，不是 7d + 14d

首版把「7 天均值」与「14 天均值」都 >70% 当作触发，这在语义上不等于"连续两周"。现改为两个相邻窗口分别判定：

- 近 7 天：`[7d:5m]`
- 前 7 天（第 8–14 天）：`[7d:5m] offset 7d`

**两者都 >70% 才算触发。**

### 3.3 必须先实测"数据实际跨度"，再决定能不能判定

Prometheus 的保留期是**上限**，不是保证。实测：

```
(time() - min_over_time(timestamp(up{job="node"})[14d:1h])) / 86400  ->  2.221248969907562
```

即当前 TSDB 只有 **2.22 天**数据（`count_over_time(up{job="node"}[7d])` = `count_over_time(…[14d])` = 6413，两边同源，可交叉佐证）。因此脚本新增 **`SPAN`（数据实际跨度）**，跨度 <7d / <14d 时对应单元格写 **「数据不足」**，判定列写 **「数据不足，无法判定（需连续 14 天）」**。

> **这一条是本次最重要的纠偏**：首版报告在只有 2.2 天数据的情况下，把 `[7d]`/`[14d]` 的失真值（CPU 68.5%/84.2%）当作"未触发"上报，属于**用不足的数据下倾向性结论**，违反 AGENTS「不确定即声明」。

**跨度短的根因已定位（非数据丢失）**：Prometheus 最早样本为 **2026-09-16 17:59:58 CST**（epoch `1789552798`），即它随 S1 阶段上线才有数据；`/data/prometheus` 为宿主 bind mount，block 从 `01M2P7692WEZFMP6EYD9Q5Y1DY` 起连续存在，容器重启未丢历史。保留期 `--storage.tsdb.retention.time=15d` ≥ 判定所需 14d，**机制本身可用**，只是历史还不够长。

### 3.4 磁盘趋势不做线性外推

首版用 `deriv(…[7d])` 与 `predict_linear(…, 86400*30)` 给出"−10.5 GiB/天""30 天后 −252 GiB"，后者**无物理意义**。根因：磁盘下降是**台阶式**（镜像一次性拉取），不是匀速。

现改为只报**实测值**：近 24h 净变化、全窗口净变化、折算速率，以及据此到 20 GiB 阈值的**区间**余量（快/慢两条速率），并标注"工程经验估算，需实测校准"。

---

## 4. 验收证据（命令 + 实际输出 + 判读）

### 4.1 语法与首次运行

```
$ bash -n /data/ai-governance/scripts/watermark_report.sh && echo SYNTAX-OK
SYNTAX-OK
$ /data/ai-governance/scripts/watermark_report.sh > /tmp/wm.out 2>/tmp/wm.err; echo rc=$?
（stderr 为空）
报告已写入：/data/ai-governance/reports/watermark-2026-W38.md
```

**判读**：脚本无语法错误、无 stderr 输出（首版曾有 `deriv: command not found`，系正文反引号被 bash 当命令替换 —— 已修复）。

### 4.2 cron 安装（幂等）

```
$ bash /data/ai-governance/scripts/install_watermark_cron.sh
已安装。当前水位周报相关行：
19:0 8 * * 1 root /data/ai-governance/scripts/watermark_report.sh >> /data/backup/log/watermark_report.log 2>&1

$ bash /data/ai-governance/scripts/install_watermark_cron.sh >/dev/null && grep -c watermark_report.sh /etc/cron.d/ai-governance-backup
1
$ grep -c -E 'backup_(pg|opensearch|offsite|config).sh|job_freshness.sh' /etc/cron.d/ai-governance-backup
5
$ systemctl is-active cron
active
$ ls -l /etc/cron.d/ai-governance-backup
-rw-r--r-- 1 root root 1379 Sep 18 23:20 /etc/cron.d/ai-governance-backup
```

**判读**：连跑两次后本任务仍**只有 1 行**（幂等成立）；原 S1-8 的 **5 条**备份/采集任务**未受影响**；cron 服务 `active`；文件权限 0644（cron.d 要求）。

### 4.3 cron 环境模拟（`env -i`）

```
$ env -i SHELL=/bin/bash PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin \
    /data/ai-governance/scripts/watermark_report.sh > /tmp/cronenv.out 2>/tmp/cronenv.err; echo rc=$?
rc=0
$ cat /tmp/cronenv.err
（空）
```

**判读**：在**无 HOME、最小 PATH**的 cron 环境下同样跑通，说明不依赖交互会话环境（`docker` / `python3` / `curl` 均在 cron.d 声明的 PATH 内）。

### 4.4 首份报告（2026-W38）关键内容

```
# 单机水位周报 · 2026-09-18（2026-W38）
> 数据来源：Prometheus API（node_exporter），实测数据实际跨度 2.22 天

## 1. 结论（对照 M-Prod 触发条件 2）
| 指标 | 当前 | 可用窗口均值（2.22 天） | 近 7 天均值 | 前 7 天均值（第 8–14 天） | 阈值 | 判定 |
| CPU | 1.2% | 0.8% | 数据不足 | 数据不足 | >70% 连续两周 | 数据不足，无法判定（需连续 14 天） |
| 内存 | 10.7% | 7.8% | 数据不足 | 数据不足 | >70% 连续两周 | 数据不足，无法判定（需连续 14 天） |
| 磁盘（/） | 47.1% | 24.2% | 数据不足 | 数据不足 | >70% 连续两周 / 80% 转人工 | 数据不足，无法判定（需连续 14 天） |

## 2. 磁盘容量与趋势
| 根分区容量 / 可用 | 98.1 GiB / 51.9 GiB |
| 加盘触发条件 | 可用 < 20 GiB ⇒ 当前 未命中 |
| 近 24 小时净变化 | -20.7 GiB |
| 全窗口（2.22 天）净变化 | -34.2 GiB |
| 镜像占用 / 可回收 | 29 个 / 34.08GB（可回收 4.826GB (14%)） |

## 3. 平台运行面
| 容器数 / 非 healthy | 19 / 0 |
| 告警规则数 | 21 |
| 抓取目标 up | 5/5 |
| 备份/ETL 新鲜度 | backup_config=575min, backup_opensearch=5min, backup_pg=15min, etl_gov_metrics=55min, etl_rag=40min |
```

报告 sha256（首份）：`200d1c063e097517ae133520614819e083e90be86c0995c23fa7fa8414ad0bf7`

**判读**：
1. 判定列如实记「数据不足」——**Agent 未给触发与否的倾向性结论**，符合"判读人工专属"。
2. 磁盘 **47.1%（<80%）**、可用 **51.9 GiB（>20 GiB）** ⇒ 未命中加盘条件，未触及 0.3 的"记录、停止、转人工"。
3. **磁盘净减少 34.2 GiB 的归因已核实**（见 §4.5），**不是**稳态泄漏。

### 4.5 磁盘下降归因核实

```
$ df -h /
/dev/nvme0n1p3   99G   42G   52G  45% /
$ docker system df
Images          29        18        34.08GB   4.826GB (14%)
Build Cache     66        9         1.403GB   188.9MB
```

**判读**：29 个镜像合计 **34.08 GB**（本轮 S1~S4 全部组件拉取），与"全窗口净减少 34.2 GiB"量级一致 ⇒ **下降主因是镜像一次性获取**。这也解释了为何对台阶式变化做线性外推会失真（§3.4）。

---

## 5. 踩坑清单（可复用）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | "7 天 CPU 水位 68.5%" | `rate(metric[7d])` 的外推补偿 + 窗口内样本缺口 | 换 `avg_over_time(rate(metric[5m])[d:5m])` |
| 2 | "7 天/14 天均值"实为 2.2 天均值却标"未触发" | 未实测 Prometheus 数据跨度 | 新增 `SPAN` 实测 + 跨度不足写"数据不足" |
| 3 | 外推 30 天得 −252 GiB | 台阶式增长被线性拉直 | 只报实测净变化，不做事先外推 |
| 4 | 报告 stderr 出现 `deriv: command not found` | 正文反引号被 bash 当命令替换 | 正文不用反引号写命令名 |
| 5 | `抓取目标 up`、`新鲜度` 显示 `N/A` | `curl \| python3 -c '…'` 多层引号下 `\"` 转义失败 | 统一改走 python3 urllib 取数 |

> 第 5 条的表现尤其隐蔽：**取数失败不报错、只是静默变成 `N/A`**；因此脚本对关键项保留 `N/A` 字样（不吞掉），便于人工发现。

---

## 6. 遗留项（未做 / 待人工）

| # | 项 | 说明 |
|---|---|---|
| 1 | **判定要等数据攒够** | 需连续 14 天数据才能给出"触发/未触发"。Prometheus 最早样本 **2026-09-16 17:59 CST** ⇒ 满 14 天为 **2026-09-30 18:00**，因此**首个具备判定条件的周报是 2026-10-05（周一）**。此前各期一律记"数据不足"。 |
| 2 | ~~Prometheus 数据跨度异常~~ **已定位，无异常** | 跨度 2.22 天**不是数据丢失**：Prometheus 随 S1 于 2026-09-16 上线才有数据；保留期 15d ≥ 所需 14d，机制可用。**持续关注点**：若后续出现 block 断裂（容器重建且 bind mount 被清）导致跨度回退，周报会退回"数据不足"，届时按 Prometheus 卷与 `retention` 排查。 |
| 3 | ~~周报未纳入备份/出机~~ **已闭环（2026-09-18）** | `backup_config.sh` 采集范围已追加 `reports/`（按目录存在性拼接，不改频率与保留策略）⇒ 周报随配置归档一起出机。实测 `files=31 size=25346B`，归档内含 `reports/watermark-2026-W38.md`。详见《…S1-8备份恢复脚本.md》§2.4 与 v1.4 |
| 4 | 真实负载压测校准容量表 | S4-4 同段的另一项动作，**未开始**（需业务源与压测窗口）。 |
| 5 | 镜像可回收 4.83 GB | 清理属变更操作，需人工批准，本次未执行。 |
| 6 | 真实备份恢复演练 / 门禁② 原件 | 与本机制无关的既有挂账（S1 准出），仍待人工。 |

---

## 7. 附：主要查询语句（便于复核）

```promql
# 数据实际跨度（天）
(time() - min_over_time(timestamp(up{job="node"})[15d:1h])) / 86400

# CPU 占用（近 7 天窗口）
100 * (1 - avg_over_time(avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))[7d:5m]))

# 内存占用（前 7 天窗口，即第 8–14 天）
100 * (1 - avg_over_time((node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)[7d:5m] offset 7d))

# 磁盘使用率（当前；必须限定 mountpoint，否则会带入 /boot/efi）
100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})

# 磁盘净变化（GiB）
delta(node_filesystem_avail_bytes{mountpoint="/"}[24h]) / 1024/1024/1024
```

---

## 8. 版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-18 | 首次产出：S4-4 水位周报机制落地（脚本 + 幂等 cron 安装器 + 首份报告）。记录两次实测纠偏（长窗 `rate` 外推失真 → 子查询；未实测数据跨度即下结论 → 新增 `SPAN` 判定门槛）与磁盘台阶式增长归因（镜像 34.08 GB）。**判定为「数据不足」**，未做倾向性结论。**数据跨度根因已定位**：Prometheus 随 S1 于 2026-09-16 17:59 上线，非数据丢失；满 14 天 ⇒ 首个可判定周报 2026-10-05。新增 `watermark-report/` 目录并入 AGENTS 文档地图与 §7 代码目录表。**附：周报纳入 S1-8 配置备份与出机**（`backup_config.sh` 采集范围追加 `reports/`，见《…S1-8备份恢复脚本.md》v1.4） |

---

*本成果由 AI 生成，仅供决策参考；命令与版本号执行前请按各组件官方文档核对。*
