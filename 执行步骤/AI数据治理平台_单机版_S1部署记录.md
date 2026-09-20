# AI 数据治理平台 · 单机版 S1 部署记录（最小测试环境）

> **记录对象**：POC 服务器 `<POC服务器主机名>`（<POC服务器公网IP>）上的 S1 单机底座部署。
> **依据**：《部署步骤_单机版》S1（含 0.3 本环境实测配置与降级声明）+《实施执行计划 v2.0-r1》P0-B 准出门槛。
> **执行日期**：2026-09-16
> **复查日期**：2026-09-17（① 只读安全面与可复现性核查，见 §3.7/§3.7.5；② S1-8 备份与恢复落地 + 首次演练，见 §7；③ 运维接入 SSH 隧道，见 §8。服务器侧配置变更共两处，均记录在案：compose 加 `path.repo`、加回环 ports）
> **定位**：**功能验证环境**（最小测试环境 + 磁盘降级变体），非生产、不用于压测、验收结论不得引用本环境数据（设计方案 4.1.3）。

---

## 1. 结论

| 项 | 结论 | 证据 |
|---|---|---|
| S1 容器健康 | **7/7 healthy**（6 基础容器 + cAdvisor） | §3.1 |
| PgBouncer 池化模式 | `pool_mode=transaction`，`max_prepared_statements=200`，`cl_waiting=0` | §3.2 |
| OpenSearch | `status: green`，单节点 | §3.3 |
| Prometheus 采集 | 目标 **3/3 up**（prometheus / node_exporter / cadvisor） | §3.4 |
| Traefik TLS | 443 握手成功（TLSv1.3，自签证书） | §3.5 |
| 门禁① 服务器验收 | **人工豁免并确认放行**（2026-09-17）（无数据盘，无 fio 验收对象）——见《部署步骤_单机版》0.3 | — |
| 门禁② 备份可恢复 | **人工确认通过（2026-09-17）**：RPO 口径取 **≤1h**（执行计划 v2.0-r2）；小时级 PG 备份 + OpenSearch 快照 + cron 上线，恢复演练通过（PG 0.07s / OS 158ms），两条腿均已自动执行成功。**判读时记录的两项限制：出机介质未配置、S1 阶段无业务数据** | §7 |

**S1 准出门禁判定**：容器健康 ｜ PgBouncer 无 waiting ｜ 备份可恢复 —— **三项全部通过；门禁① 豁免与门禁② 均由人工于 2026-09-17 确认放行**，故 **S1 段整体准出**，可进入 S2 的 OpenMetadata 部署。
> 说明：Agent 只记录人工决策，**不代签**；书面签署与归档以《AI数据治理平台_单机版_S1准出材料包.md》为准（签字栏由对应角色本人填写）。

---

## 2. 本环境变更清单（服务器侧实际落地）

| # | 变更 | 位置 | 说明 |
|---|---|---|---|
| 1 | 安装 Docker 29.8.1 + Compose v5.5.1 | `download.docker.com` 官方源（Ubuntu 26.04 代号 `resolute` 已有发行版） | 备选方案为发行版包 `docker.io` + `docker-compose-v2`，未采用 |
| 2 | 配置镜像加速与日志轮转 | `/etc/docker/daemon.json` | registry-mirrors：`docker.m.daocloud.io`、`docker.1panel.live`；`max-size=10m × 3`；`live-restore=true` |
| 3 | 目录规划 | `/data/{postgres,opensearch,kafka,flink-checkpoint,backup,superset,prometheus,ollama,tls}`、`/data/alertmanager`、`/data/ai-governance/{config,scripts}` | **`/data` 为根文件系统上的普通目录**，非独立数据盘（0.4 降级声明 2） |
| 4 | 内核参数持久化 | `/etc/sysctl.d/99-zz-ai-governance.conf` | `vm.max_map_count=262144`、`vm.swappiness=1`；**文件名前缀 `99-zz-` 是为覆盖阿里云自带的 `99-apsara-sysctl.conf`**（后者把 swappiness 设 0） |
| 5 | 句柄上限持久化 | `/etc/security/limits.d/99-ai-governance.conf` | `nofile 65535`、`memlock unlimited` |
| 6 | Prometheus 宿主导出器 | `/usr/local/bin/node_exporter` + `node_exporter.service` | node_exporter **1.12.1**，宿主二进制（维持"Exporters 不容器化"口径） |
| 7 | 自签 TLS 证书 | `/data/tls/server.crt|key`（SAN 含公网/内网 IP、localhost，825 天） | **POC 临时方案**；生产需企业 PKI/ACME（待确认） |
| 8 | Compose 项目 | `/data/ai-governance/docker-compose.yml` | 当前含 S1 段 7 个服务；S2 起按设计方案 6.3 由 OpenMetadata 官方 quickstart 叠加 |
| 9 | 密钥 | `/data/ai-governance/.env`（`chmod 600`） | 仅 4 个键名：`POSTGRES_USER/PASSWORD/DB`、`OPENSEARCH_INITIAL_ADMIN_PASSWORD`；**口令随机生成、未落仓库、未在记录中留存** |
| 10 | 容器网络 | `frontend-net`（非 internal）、`backend-net`/`data-net`（internal） | 由 Compose 创建；**未**手工 `docker network create`（避免与 Compose 标签校验冲突） |

**未做（有意）**：未引入任何 K8s/k3s 配置（硬约束 1）；未改 PgBouncer 池化模式（硬约束 5）；未执行 `mkfs.xfs`/挂盘（无数据盘）。

---

## 3. 校验证据（命令 + 实际输出）

### 3.1 容器健康

```bash
docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
```

```
alertmanager   Up About a minute (healthy)   9093/tcp
cadvisor       Up 6 minutes (healthy)        8080/tcp
opensearch     Up About a minute (healthy)   9200/tcp, 9300/tcp, 9600/tcp, 9650/tcp
pgbouncer      Up About a minute (healthy)   6432/tcp
postgres       Up About a minute (healthy)   5432/tcp
prometheus     Up 40 seconds (healthy)       9090/tcp
traefik        Up 6 minutes (healthy)        80/tcp, 0.0.0.0:443->443/tcp, [::]:443->443/tcp
```

判读：7/7 healthy，全部 `restart: unless-stopped` + healthcheck（硬约束 3 满足，且 `docker compose ps --format '{{.Health}}'` 可编程校验）。

### 3.2 PgBouncer

```bash
# 注意：bitnami/pgbouncer 镜像内无 psql（见 §4 偏差 1），改由 postgres 容器发起
docker exec -e PGPASSWORD="$PW" ai-governance-poc-postgres-1 \
  psql -h pgbouncer -p 6432 -U postgres -d pgbouncer -c "SHOW POOLS;"
docker exec -e PGPASSWORD="$PW" ai-governance-poc-postgres-1 \
  psql -h pgbouncer -p 6432 -U postgres -d pgbouncer -tAc "SHOW CONFIG;"
```

```
 database  |   user    | cl_active | cl_waiting | sv_active | sv_idle | pool_mode
 pgbouncer | pgbouncer |         1 |          0 |         0 |       0 | statement

default_pool_size|25
max_client_conn|200
max_prepared_statements|200
pool_mode|transaction
```

判读：`cl_waiting = 0`（S1 准出门禁满足）。注意 `SHOW POOLS` 中 `pgbouncer` **管理库自身**显示 `statement` 属预期，业务库池模式以 `SHOW CONFIG` 的 `pool_mode=transaction` 为准。

> **2026-09-17 补强（S2-1 触发，回写 S1-4 基线）**：S2-1 新建 PG 角色 `openmetadata_user` 后，经 6432 连接报 `FATAL: SASL authentication failed`（直连 5432 正常）。
> 根因：PgBouncer 以 `auth_file=/opt/bitnami/pgbouncer/conf/userlist.txt` 认证，该文件只含 `postgres`；而 **`auth_user` 为空** ⇒ 已配置的 `auth_query` 无法执行，新角色一律认证失败。
> 处置：compose 的 pgbouncer 服务增加 **`PGBOUNCER_AUTH_USER: postgres`**（bitnami 镜像 `libpgbouncer.sh:231` 支持），使 `auth_query` 生效，此后**任意 PG 登录角色均可经 6432 认证**。
> 影响：`pool_mode=transaction`、`max_prepared_statements=200`、`cl_waiting=0` **均未改变**，门禁③ 的前提不变；S1 阶段仅单角色故未暴露此问题。

### 3.3 OpenSearch

```bash
docker exec ai-governance-poc-opensearch-1 curl -sk -u "admin:$OSPW" \
  "https://localhost:9200/_cluster/health?pretty"
```

```
"status" : "green",  "number_of_nodes" : 1,  "active_primary_shards" : 5
```

判读：单节点 green（未关闭 security plugin）。

### 3.4 Prometheus 采集

```bash
docker exec ai-governance-poc-prometheus-1 wget -qO- http://127.0.0.1:9090/api/v1/targets
```

```
cadvisor    up   http://cadvisor:8080/metrics
node        up   http://host.docker.internal:9100/metrics
prometheus  up   http://localhost:9090/metrics
up count: 3 / total: 3
```

### 3.5 Traefik TLS

```bash
echo | openssl s_client -connect localhost:443 -servername localhost
```

```
subject=C=CN, O=AI-Governance-POC, CN=ai-governance-poc
Protocol: TLSv1.3
Verify return code: 18 (self-signed certificate)
```

判读：TLS 终止生效；自签证书导致校验码 18，属 POC 预期。

### 3.6 资源与磁盘

```
磁盘：/ 99G，已用 7.9G（9%），可用 87G          （0.3.1 估算 44G，余量充足）
内存：92G，已用 3G
/var/lib/docker 2.8G（镜像）｜/data 41M（数据卷初始）
```

### 3.7 安全面与可复现性核查（2026-09-17 复查补录）

**结论先行**：公网暴露面实测**仅 22 / 443**，与安全基线④「仅暴露 443」的差距只剩 SSH 端口（运维口），其余端口由阿里云安全组挡住；
但**认证面（root 口令 + 无 fail2ban，24h 内 60 次认证失败）**与**可复现性（5 个服务用 `:latest`、compose 注释版本与实际不符）**两项存在缺陷，须处理后才宜进入 S2。

> **本节结论已被 §3.7.5 部分更新（2026-09-17 01:06）**：22 已由人工限源 IP、未发现未授权登录、sshd 自带 `PerSourcePenalties` 已在挡爆破；
> 失败次数精确口径为 **37**（非 60）。认证面缺陷（口令认证/root 登录）仍为残留待放行项。

```bash
# 宿主监听（服务器侧）
ss -lntH
# 公网可达性（本地侧，逐个 TCP connect，超时 4s）
# 详见 §3.7.1 判读
# SSH 生效配置与认证失败计数（服务器侧）
sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication|pubkeyauthentication|port|maxauthtries)'
grep -rnE 'PermitRootLogin|PasswordAuthentication' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf
journalctl -u ssh --since '24 hours ago' -o cat | grep -ci failed
ufw status; systemctl is-active fail2ban
# 镜像 tag 与在跑镜像
docker ps --format '{{.Names}}  {{.Image}}'; docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}'
```

#### 3.7.1 公网暴露面（本地侧实测，2026-09-17）

| 端口 | 服务 | 公网实测 | 判读 |
|---|---|---|---|
| 22 | sshd | **OPEN** | 运维口，须限源 IP（见 3.7.2） |
| 443 | Traefik | **OPEN** | 符合安全基线④ |
| 9100 | node_exporter | 超时（不可达） | 被安全组挡住；宿主侧仍为全网卡监听（见 3.7.3） |
| 9090 | Prometheus | 超时（不可达） | 未发布端口，符合预期 |
| 9200 | OpenSearch | 超时（不可达） | 未发布端口，符合预期 |
| 6432 | PgBouncer | 超时（不可达） | 未发布端口，符合预期 |

宿主监听（`ss -lntH` 实测输出，此处按地址归纳排列）：

```
127.0.0.53%lo:53   0.0.0.0:22   0.0.0.0:443   127.0.0.1:39703
127.0.0.54:53      [::]:22      [::]:443      *:9100
```

判读：**对外监听仅 22 / 443 / 9100**，其中 9100 未过安全组。`ufw` 为 inactive（**状态**），公网过滤实际由**阿里云安全组**承担——
即「安全组仅放行必要端口」**已由实测验证**，但宿主侧无第二道防线，属纵深防御缺口（安全基线④的落地方式与文档假设不同，见 §4 偏差 10）。
容器侧除 Traefik `443:443` 外无任何端口发布（`docker-compose.yml:101`）。

#### 3.7.2 SSH 认证面（高风险，实测已存在失败爆破）

| 项 | 实测值 | 判定 |
|---|---|---|
| `permitrootlogin` | `yes` | ❌ 应改 `prohibit-password` |
| `passwordauthentication` | `yes` | ❌ 应改 `no`（改密钥登录） |
| `maxauthtries` | `6` | 记录 |
| 24h 认证失败次数 | **37**（判据 `Failed password`）；00:35 首次统计写作 60 用的是 `failed` 宽松匹配口径，**以 37 为准** | ⚠️ 截至 2026-09-16 22:35 有外部爆破，之后未再出现（见 3.7.5） |
| `fail2ban` | 未安装、inactive | ⚠️ **但并非无自动封禁**：OpenSSH 10.2 自带 `PerSourcePenalties` 已生效——日志有 `srclimit_penalise: <ip>: activating ipv4 penalty of 17 seconds` 与 `drop connection ... penalty: failed authentication`。**口径纠正**：原记录写"无自动封禁"不准确 |

**根因（已定位到行）**：`/etc/ssh/sshd_config:149` 本身是 `PasswordAuthentication no`，但 `/etc/ssh/sshd_config.d/50-cloud-init.conf:1` 写 `PasswordAuthentication yes`（cloud-init drop-in 后加载生效），
`/etc/ssh/sshd_config:148` 另有 `PermitRootLogin yes`。⇒ 修复须改 drop-in 或该两行，**不是**改主配置的注释行。

> 处置顺序（Agent 不得单独完成全部，涉及访问路径）：① 装公钥并实测密钥登录成功 → ② 改 `PasswordAuthentication no`、`PermitRootLogin prohibit-password`
> → ③ 安全组把 22 限源 IP（**控制台操作，人工专属**）→ ④ 轮换口令、装 fail2ban。①②由 Agent 起草执行，③④须人工放行。
>
> **③ 已于 2026-09-17 由人工执行**（见 3.7.5）；①②④**仍待放行**——人工明确"默认这样即可"，故本轮只记录、不动作。

#### 3.7.5 安全组 22 限源后的核查（2026-09-17 01:06）

**背景**：人工于 2026-09-17 完成安全组加白/限源（22 端口不再对全网开放）。Agent 侧做三项只读核查。

**核查 1 · 白名单路径仍可用**（关键：限源不能把运维通道切断）

```
2026-09-17 01:06:15  Accepted password for root from 120.230.139.195 port 23008 ssh2
2026-09-17 01:06:16  pam_unix(sshd:session): session opened for user root
```

判读：本机（出口 120.230.139.195）访问正常 —— **自动化运维通道未受影响**。

**核查 2 · 有无他人成功登录（最坏情况排查）**

| 判据 | 结果 |
|---|---|
| 近 24h 成功登录来源 IP 统计（`Accepted password/publickey`） | **仅 `120.230.139.195`，84 次**（全部为本机自动化会话） |
| 近 7 天非本机 IP 的成功登录 | **0 条** —— 无未授权登录 |
| `180.76.233.159`（高频来源，58 条日志）成功登录次数 | **0**；全部为失败尝试（32×`Failed password`、26×`Invalid user`），并被 `PerSourcePenalties` 丢弃 |

判读：**未发现入侵迹象**；高频来源均为扫描器。

**核查 3 · 爆破是否真的停了（避免用错因果）**

```
近 24h  Failed password：37
近 2h / 1h / 30m：0 / 0 / 0
最后一次外部失败尝试：2026-09-16T22:35:12+08:00（183.224.219.194）
```

判读（**须谨慎**）：扫描在 **22:35 就自行停止**，早于人工改安全组约 2.5 小时，因此"近 2 小时零尝试"**不能**作为安全组已生效的证据——属于扫射波次结束，与本轮变更无因果。安全组规则的实际内容（放行哪些源 IP、是否只留 443）**Agent 从服务器侧无法验证**，须由人工在控制台核对并留档（建议截图或规则导出归档）。

**结论**：SSH 风险面已由"公网全开 + 口令认证 + 活跃爆破"降级为"限源 IP + 口令认证 + sshd 自带惩罚机制"；**未发现未授权登录**。残留项：口令认证仍开启、口令仍为明文留存（本机敏感文件）、`fail2ban` 未装（但 sshd 自带惩罚已在挡），均标记为**待放行**而非缺陷。

#### 3.7.3 node_exporter 监听地址

`ExecStart=/usr/local/bin/node_exporter --web.listen-address=:9100`（全网卡）。宿主 `docker0` 网关为 `172.17.0.1/16`，
compose 已配 `extra_hosts: ["host.docker.internal:host-gateway"]`（`docker-compose.yml:126`）⇒ **可收紧为 `172.17.0.1:9100`**，Prometheus 抓取链路不变。
当前未收紧，仅靠安全组兜底（纵深防御缺口，与 3.7.1 同一项）。

#### 3.7.4 可复现性缺陷（影响 S1 证据的再现在性）

| # | 发现 | 影响 |
|---|---|---|
| 1 | 7 个在跑服务中 **5 个用 `:latest`**：`bitnami/pgbouncer`、`opensearchproject/opensearch`、`prom/prometheus`、`prom/alertmanager`、`zcube/cadvisor`（另 `pgvector/pgvector:pg16` 为浮动 tag） | 违反 AGENTS §7「固定 tag/digest，不用 `latest`」；容器重建可能拉到不同版本 → 本记录 §6 的镜像 ID 与校验不可复现 |
| 2 | `docker-compose.yml:75` 注释写「实际版本 **2.19.x**」，实测为 **3.8.0**（本记录 §4 偏差 6） | 部署真相源自述与实际不符，易误导后续判读 |
| 3 | 宿主保留未使用镜像 `edoburu/pgbouncer:latest`（22.9MB，无容器使用；为 S1-4 试用期遗留） | 供应链与磁盘冗余，清理候选（非阻断） |

> 已实测的可固定版本（取自本记录 §1/§6）：traefik `v3.0`→实拉 3.0.4｜pgbouncer 1.24.1｜opensearch 3.8.0｜prometheus 3.14.0｜alertmanager 0.34.0｜node_exporter 1.12.1（宿主二进制）。
> 固定方式（改为精确 tag 或 `@sha256:`）**须在下次维护窗口执行并重取校验和**——当前 compose 校验和 `3cec4d63…` 对应的是含 `:latest` 的版本，直接改动会使本记录 §6 校验和失效，故本次不改。

---

## 4. 执行中发现的偏差与处理（回写部署基线）

| # | 发现 | 影响 | 处理 |
|---|---|---|---|
| 1 | `bitnami/pgbouncer` 镜像内**没有 psql**（也没有 `pg_isready`、`nc`、`bash`） | 「部署步骤」原校验命令 `docker exec pgbouncer psql ...` **跑不通**；healthcheck 用 `pg_isready` 会 exit 127 → unhealthy | healthcheck 改为 `test -S /tmp/.s.PGSQL.6432 && ps aux \| grep -q '[p]gbouncer'`；校验命令改为经 postgres 容器发起。**服务本身始终正常**，未做任何降级 |
| 2 | Docker Hub `registry-1.docker.io` **不可达**（超时） | 无法拉取任何镜像 | `/etc/docker/daemon.json` 配置加速器（`docker.m.daocloud.io` 实测可用）；此项属**环境依赖**，需写入部署基线 |
| 3 | `gcr.io` **不可达** | cAdvisor 官方镜像无法获取 | 暂用社区镜像 `zcube/cadvisor`；**供应链风险登记为待确认项**（安全基线⑤要求上线前扫描） |
| 4 | 仅挂 internal 网络的容器，其 `127.0.0.1` 回环端口发布**不生效**（Docker 29.8.1；HostConfig 有绑定但 NetworkSettings.Ports 为 null） | 宿主 `curl localhost:9200/9090` 类校验命令跑不通 | **去掉回环发布**，全部改为容器内校验；对外仍仅暴露 443（安全基线④更严格） |
| 5 | Prometheus 仅挂 internal 网络时无法抓取宿主 node_exporter（`network is unreachable`） | S1-7「采集宿主机」不达标 | Prometheus 增加 `frontend-net`（非 internal，提供默认路由），不加任何端口发布 |
| 6 | OpenSearch 实际版本为 **3.8.0**（文档基线按 2.x 写） | 索引/快照/插件行为可能与 2.x 文档有差异 | 版本入部署基线；S2 OpenMetadata 兼容性列入待确认 |
| 7 | Prometheus **3.14.0**、Alertmanager **0.34.0**（均为新一代主版本） | 告警规则语法与 v2 略有差异（如 `keep_firing_for`） | 版本入基线；S3-6 告警规则编写时按 3.x 文档核对 |
| 8 | 阿里云预置 `/etc/sysctl.d/99-apsara-sysctl.conf` 会把 `vm.swappiness` 覆盖回 0 | 内核参数持久化实际未生效 | 本平台参数文件改名为 `99-zz-ai-governance.conf` 以确保后加载 |
| 9 | `traefik:v3.0` 镜像实拉为 **3.0.4** | — | 版本入基线（固定 tag 而非 latest） |
| 10 | 宿主 `ufw` 未启用（inactive），公网过滤**全部由阿里云安全组承担** | 「安全基线④ 最小暴露」的落地位置与文档假设不同：宿主侧无第二道防线 | 以实测结论入基线（§3.7.1）：安全组实测仅放行 22/443；不要把"ufw 已配"当作已完成的证据 |
| 11 | SSH `PasswordAuthentication yes` 的真正来源是 cloud-init drop-in（`/etc/ssh/sshd_config.d/50-cloud-init.conf`），主配置第 149 行其实是 `no` | 按主配置排查会得出错误结论、改了不生效 | 修复须改 drop-in（§3.7.2）；本项回写基线，避免后续重复踩坑 |
| 12 | 5 个服务以 `:latest` 运行（§3.7.4）；compose 第 75 行注释版本（2.19.x）与实际（3.8.0）不符 | S1 证据不可复现；违反 AGENTS §7 命令纪律 | **本次不改**（改动会使 §6 校验和失效）→ 列入 §5 待办，维护窗口统一固定 tag/digest 并重取校验和 |
| 13 | **S1-7 的 SLO 阈值没有规则载体**：`prometheus.yml` 无 `rule_files`、`/api/v1/rules` → `groups: []`（2026-09-18 复核实测） | 4.5 约定的 CPU<80% / 内存<85% 只写在文档里，**从未被任何规则执行**；S1-7 验收当时只查了 `/targets` 全 UP，未查规则 ⇒ 属"验收口径漏项" | **2026-09-18 已补齐**：新增 `alerting/poc-alerts.yml`（3 条 SLO 规则 + 磁盘护栏）→ 服务器规则目录，Prometheus 加 `rule_files` 与 `--web.enable-lifecycle`；`promtool check rules` SUCCESS、`/api/v1/rules` 显示 `poc-slo(3)` `health=ok`。详见《AI数据治理平台_单机版_S3前置部署记录.md》§4 |

---

## 5. 遗留与待确认

| # | 项 | 状态 | 阻塞谁 |
|---|---|---|---|
| 1 | **门禁② RPO 口径** | **已决策（2026-09-17）：≤1h**（人工，消解设计方案 4.6 与执行计划 P0-B 的源文档冲突；执行计划已发 v2.0-r2）。手段=小时级逻辑备份，不引入 WAL 归档 | — |
| 2 | 出机备份介质（异地/对象存储地址） | **待确认，S1-8 未闭环**：《单机版_S1-8备份恢复脚本》`backup_offsite.sh` 已就位，介质配置后即刻生效；当前无 `OFFSITE_TARGET` ⇒ 每 03:00 退出码 2 并记录 | 门禁② 判读（须列为未完成）、ADR-A4「每日出机」 |
| 3 | cAdvisor 镜像来源（gcr.io 不可达） | 待确认（建议自建或换可信源） | 安全基线⑤ 镜像扫描 |
| 4 | 自签证书 → 企业 PKI/ACME 与域名 | 待确认 | S1-6 正式验收、S2-3 SSO |
| 5 | 公网暴露面 | **已实测收敛**（§3.7.1）：公网仅 22/443 可达，9100/9090/9200/6432 均不可达；**22 已由人工限源**（§3.7.5）。宿主 ufw inactive，属纵深防御缺口（§4 偏差 10/§3.7.3） | **2026-09-18 两项收口**：① node_exporter 收紧为 `172.17.0.1:9100`（抓取 4/4 仍 up）；② **443 不再对外发布**（宿主无 443 监听）——OM 改回环 8585 + SSH 隧道，**当前对外仅 22（已限源），无任何对外业务端口**。详见《…S3前置部署记录.md》§10。剩余仅为"宿主 ufw inactive"与"22 限源 vs 出口 IP 变化"的纵深防御议题 |
| 6 | **SSH 认证面**：`PermitRootLogin yes` + 口令认证（§3.7.2，根因已定位到行）；**22 已限源 + 无未授权登录 + sshd 自带 PerSourcePenalties 生效**（§3.7.5） | **已闭环（2026-09-18，人工放行）**：① 公钥已装入 `/root/.ssh/authorized_keys`（指纹 `SHA256:3rhGACAny62…`）② `PasswordAuthentication no` + `PermitRootLogin prohibit-password`（改的是生效的 cloud-init drop-in，`sshd -t` 校验通过后 reload；**实测密钥登录可用、口令登录被拒**：`Bad authentication type; allowed types: ['publickey']`）③ root 口令已轮换（新口令存仓库外敏感文件）④ `fail2ban` 已装并启用（jail `sshd`，maxretry 3 / findtime 10m / bantime 1h） | 本项闭合；回滚点 `/root/50-cloud-init.conf.bak-20260918-132449` |
| 7 | 全部 18 个镜像的精确版本（当前仅 S1 的 7 个已实测） | 部分收敛（附 C 第 1 项） | S2~S4 |
| 8 | PgBouncer 版本是否 ≥1.21 | **已收敛：1.24.1 ✓**（附 C 第 2 项可关闭） | — |
| 9 | 5 个服务以 `:latest` 运行 + compose 注释版本与实际不符（§3.7.4） | **已处置（2026-09-18）**：compose 中 **15 个镜像全部固定 digest**（含这 5 个），**未重建容器**（运行态零变更，下次重建生效），校验和重取 `b70ae7a9…`。**仍缺：镜像漏洞扫描未执行**。详见《AI数据治理平台_单机版_S3前置部署记录.md》§5 | 仅剩供应链扫描 |
| 10 | 未使用镜像 `edoburu/pgbouncer:latest`（22.9MB） | 清理候选（`docker image rm`，需人工确认，属破坏性操作） | — |
| 11 | **门禁② 演练的证明力** | S1 阶段 PG/OpenSearch 均无业务数据（PG 对象数 0）→ 演练只证明机制可跑通，**不构成"业务数据可恢复"的证明**；S2 起必须用真实数据复演 | 门禁② 最终判读、S2 |
| 12 | 备份失败无主动告警 | 现仅落日志 + 退出码 + `*_last_success` 状态文件；设计方案 4.5「备份成功率 100% 失败即时告警」落地在 S3-6 | S3-6 |
| 13 | compose 变更需回写 | 2026-09-17 为启用 OpenSearch 快照加了 `path.repo`（§7.2），校验和由 `3cec4d63…` → `895cb471…`；已同步 §6 与《单机版_S1-8备份恢复脚本》§6 | — |

---

## 6. 关键文件与校验和

| 文件 | 位置 | 校验 |
|---|---|---|
| Compose 部署真相源 | `/data/ai-governance/docker-compose.yml` | `sha256:07654a18994100c57ff19eb0f0a16618f0abf1e5db4ee6dd744bfd8a86c69090`（2026-09-17 加入运维接入回环端口后；**历史版本**：`895cb471…` 加 `path.repo` 后、`3cec4d63…` S1 初始。备份文件 `docker-compose.yml.bak-20260917-*`） |
| 密钥 | `/data/ai-governance/.env` | `chmod 600`，口令随机生成，不入库 |
| Prometheus / Alertmanager / Traefik 配置 | `/data/ai-governance/config/**` | — |
| 执行脚本（S1 部署） | 本地 `C:\Users\Yuanhui\.ai-datathink\remote\00x_*.sh` | 未入仓库，可按需归档 |
| 只读核查脚本（2026-09-17 复查） | 本地 `C:\Users\Yuanhui\.ai-datathink\remote\021_evidence2.sh`、`022_listeners.sh` | 只读，无状态变更 |
| S1-8 备份/恢复脚本 | 服务器 `/data/ai-governance/scripts/*.sh`；仓库原文《AI数据治理平台_单机版_S1-8备份恢复脚本.md》 | §7.3；文档内 4 段脚本与服务器文件逐字节一致 |
| S1-8 调度 | `/etc/cron.d/ai-governance-backup`（root:root 644） | 3 条：PG `:05`、OS 快照 `:15`、出机 `03:00` |

**镜像 ID（截断）**：postgres `ccc6e83d6e35`（pgvector, PG 16.15 / pgvector 0.8.6）｜pgbouncer `823c47c0615e`（1.24.1）｜opensearch `fafe3fc35870`（3.8.0）｜traefik `a208c74fd80a`（3.0.4）｜prometheus `5ce7540c3c00`（3.14.0）｜alertmanager `690c7b525f43`（0.34.0）｜cadvisor `f5d9acd3d30d`（zcube）

---

## 7. S1-8 备份与恢复落地（2026-09-17 执行）

### 7.1 口径与设计

| 项 | 值 | 来源 |
|---|---|---|
| RPO | **≤1h** | 2026-09-17 人工决策（取设计方案 4.6；消解与执行计划 P0-B「15min」的冲突） |
| RTO | ≤4h | 设计方案 4.6（未变） |
| 手段 | 小时级 **逻辑备份/快照**；**不启用 WAL 归档 PITR** | 执行计划 P0-B 第 3 周原文「PG 逻辑备份 + OpenSearch snapshot + Flink Checkpoint 目录出机」 |
| 实现 | 4 脚本 + cron 3 条调度 | 《AI数据治理平台_单机版_S1-8备份恢复脚本.md》 |

### 7.2 变更点（本轮唯一服务器侧配置变更）

| # | 变更 | 内容 | 影响面 |
|---|---|---|---|
| 1 | compose `opensearch.environment` 增加 `path.repo: "/usr/share/opensearch/data/snapshots"` | fs 快照仓库根目录（**未配置时注册仓库直接 500**，已实测：`location [...] doesn't match any of the locations specified by path.repo`） | **仅重建 opensearch 容器**（实测 25s 内 healthy），其余 6 个容器未动；数据在 bind mount `/data/opensearch` 上，未丢 |
| 2 | 校验和变化 | `3cec4d63…` → `895cb47130980283165d48c093d580304752b2e4a524004a8aa7e9a9e48ac30a` | 已同步 §6；旧版备份 `docker-compose.yml.bak-20260917-004438` |
| 3 | 新增宿主文件 | `/data/ai-governance/scripts/*.sh`（4 个，`chmod +x`）、`/etc/cron.d/ai-governance-backup`（644）、`/data/backup/{pg,pg/daily,log}` | 未新增容器、未新增组件、未改镜像 |

**快照与索引同盘声明**：快照落 `/data/opensearch/snapshots`（与索引数据同一盘、同一故障域），这是 ADR-A4 单机形态的既定取舍——**靠「每日出机」兜底**，而出机尚未闭环（§7.5）。

### 7.3 脚本与调度（落地清单）

| 项 | 值 |
|---|---|
| 脚本 | `backup_pg.sh`、`backup_opensearch.sh`、`backup_offsite.sh`、`restore_drill.sh` |
| 调度 | `5 * * * *` PG ｜ `15 * * * *` OS 快照 ｜ `0 3 * * *` 出机 |
| 保留 | PG 小时级 26h、日级 14 天；OS 快照 26 份 |
| 校验判据 | `pg_restore -l` 可解析 **且** `TOC Entries > 0`（**注意**：空库归档只有注释级条目、无编号行，用"行首数字计数"会误判为失败——本项已踩坑并修正） |
| 状态文件 | `/data/backup/log/{pg,opensearch}_last_success`（供后续告警接入） |

### 7.4 首次恢复演练（门禁② 证据）

```bash
/data/ai-governance/scripts/restore_drill.sh
# 日志：/data/backup/log/restore_drill_20260917-004650.log
```

```
[00:46:50] dump 文件：/data/backup/pg/postgres_20260917-0046.dump（1084B）
[00:46:50] 源库对象数=0
[00:46:50] ⚠ 注意：源库当前无用户对象（S1 阶段尚未部署业务/元数据表）。本次演练只证明【备份→恢复机制】可跑通，
[00:46:50]    不构成「业务数据可恢复」的证明；S2 起（OpenMetadata 落库后）须用真实数据复演并由 DBA 判读。
[00:46:50] pg_restore 完成，耗时 0.07 秒
[00:46:50] 恢复库对象数=0
[00:46:50] PG 段结论：对象数一致 ✔（pg_restore 观测耗时 0.07s；含建库/清理的端到端 RTO 远低于 4h 目标）
[00:46:50] OS 段：造索引 drill_src_20260917-004650（3 条文档）→ 快照 SUCCESS → 删索引
[00:46:51] 恢复后文档数=3（快照前 3；恢复耗时 158 ms）
[00:46:51] OS 段结论：恢复后文档数与快照前一致 ✔
[00:46:51] 清理演练产物：删快照 drill-snap-20260917-004650、删索引 drill_src_20260917-004650
```

清理复查（同轮）：`select datname from pg_database where datname like 'restore_drill%'` → 空；`_cat/indices/drill*` → 空；容器 **7/7 healthy**；`/data/backup` 60KB、`/data/opensearch/snapshots` 1.7MB；`/` 已用 7.9G / 可用 86G。

### 7.5 判读边界与遗留（须写入门禁② 结论）

| # | 限制 | 说明 |
|---|---|---|
| 1 | **无业务数据** | S1 阶段 PG 对象数 0、OpenSearch 无治理索引 → 演练**不构成"业务数据可恢复"的证明**；S2 落库后必须复演 |
| 2 | **出机未闭环** | `OFFSITE_TARGET` 未配置（介质挂账待确认）⇒ ADR-A4「每日出机」未完成，脚本按退出码 2 显式暴露，不伪造成功 |
| 3 | **无主动告警** | 备份失败仅落日志/退出码；「备份成功率 100% 即时告警」属 S3-6 |
| 4 | **快照与索引同盘** | 单机形态既定取舍，靠出机兜底；出机未闭环期间该风险敞口持续存在 |
| 5 | **RTO 未在真实数据量下测** | 0.07s 是空库观测值，不可外推；S2 起按真实数据复测 |
| 6 | ~~cron 自动执行待观察~~ | **已验（§7.6）**：临时条目实测全链路通过（00:50:01，验完即删）；正式调度 **01:05:01 PG 备份** 与 **01:15:01 OpenSearch 快照** 均自动执行成功。仅 **03:00 出机腿**未到点（介质未配置，预期退出码 2） |

**门禁② 结论（Agent 侧）**：脚本、调度、演练证据齐备；**是否放行由 DBA 判读签字**——建议判读时把上表 1/2 两项列为"未完成/带条件"。

### 7.6 cron 首次自动执行核验

**方法**：正常调度首次触发在 01:05，为不等整点，临时装载一次性条目 `/etc/cron.d/zz-s18-cron-selftest`（`* * * * *` 调 `backup_pg.sh`），验证**「cron 解析 → 脚本执行 → docker 访问 → 落盘校验」整条链路**，验完**立即删除**。

```
2026-09-17 00:49:08  临时条目装载
2026-09-17 00:50:01  cron 自动触发，写入 /data/backup/log/cron_selftest.log：
[2026-09-17 00:50:01] === 备份开始 ===
[2026-09-17 00:50:01] globals OK -> globals_20260917-0050.sql
[2026-09-17 00:50:01] pg_dump OK  db=postgres  toc_entries=5  size=1084B  file=postgres_20260917-0050.dump
[2026-09-17 00:50:01] === 备份成功 ===
2026-09-17 00:50:2x  rm -f /etc/cron.d/zz-s18-cron-selftest
                  → ls /etc/cron.d/ 只剩 ai-governance-backup（3 条调度）与系统自带 e2scrub_all
```

判读：**cron 链路可用**（含 cron 环境下的 PATH 与 docker 访问），正式调度条目结构与其一致。

**正式调度首次自动执行**（PG 腿原文如下，两条腿汇总见其后表格）：

```
2026-09-17 01:05:01  /data/backup/log/cron.log
[2026-09-17 01:05:01] === 备份开始 ===
[2026-09-17 01:05:01] globals OK -> globals_20260917-0105.sql
[2026-09-17 01:05:02] pg_dump OK  db=postgres  toc_entries=5  size=1084B  file=postgres_20260917-0105.dump
[2026-09-17 01:05:02] === 备份成功 ===
（/data/backup/log/pg_last_success = 2026-09-17 01:05:02）
```

| 腿 | 首次自动触发 | 结果 |
|---|---|---|
| PG 备份（`:05`） | 2026-09-17 **01:05:01** | 成功（`toc_entries=5`） |
| OpenSearch 快照（`:15`） | 2026-09-17 **01:15:01** | 成功（`poc-snap-20260917-0115`，`state=SUCCESS`，5 shards） |
| 出机（`03:00`） | — | **未到点**；介质未配置时预期退出码 2（不伪造成功） |

判读：**两条腿均已由正式调度自动执行成功**（非仅手工可跑）。出机腿的复核命令：

```bash
tail -20 /data/backup/log/cron.log
grep -c '出机未配置' /data/backup/log/backup_offsite.log   # 介质未配置时每次 03:00 增 1
```

---

## 8. 运维接入：SSH 隧道看真实 UI（2026-09-17「方案 C」）

**背景**：S1 阶段没有任何 UI——Traefik 的 `api.dashboard: false` 且动态配置只有 `tls.yml`（无任何 router），`https://<host>/` 与 `/dashboard/` 实测均返回 **404**；Prometheus 9090 / Alertmanager 9093 原先只在容器内网，未发布。
**目标**：让运维能看到真实界面，同时**不新增对外暴露面**（安全基线④ 仅暴露 443）。

### 8.1 变更内容

| # | 变更 | 说明 |
|---|---|---|
| 1 | `prometheus` 增加 `ports: ["127.0.0.1:9090:9090"]` | **只绑回环**，公网不可达 |
| 2 | `alertmanager` 增加 `ports: ["127.0.0.1:9093:9093"]` | 同上 |
| 3 | `alertmanager` 的网络由 `[backend-net]` 改为 `[backend-net, frontend-net]` | **回环发布生效的必要条件**：仅挂 internal 网络的容器，其 `127.0.0.1` 端口发布不生效——本环境二次实测复现（同 §4 偏差 4；Prometheus 当初为抓宿主已做过同款处理，见偏差 5） |
| 4 | 容器数不变 | 未新增/删除容器，口径仍为 18（本环境 19） |

校验和链路：`3cec4d63…`（S1 初始）→ `895cb471…`（加 `path.repo`）→ **`07654a18…`（本次，现行）**；备份文件 `docker-compose.yml.bak-20260917-*`。

**执行过程中的一次失误（如实记录）**：首次插入 `ports` 时把 `\n` 写成了字面量，导致 YAML 解析失败（`did not find expected key`）。因 `docker compose` 解析失败即中止，**未重建任何容器，栈未受影响**；随后从备份回滚、改用带 `docker compose config -q` 前置校验且失败即自动回滚的脚本重做成功。

### 8.2 验证证据

```
# ① 宿主监听：仅回环
LISTEN 0 1024 127.0.0.1:9090 0.0.0.0:*
LISTEN 0 1024 127.0.0.1:9093 0.0.0.0:*

# ② 公网可达性（本地侧实探）
<POC服务器公网IP>:9090 → 不可达（符合预期）
<POC服务器公网IP>:9093 → 不可达（符合预期）
<POC服务器公网IP>:443  → 可达
<POC服务器公网IP>:22   → 可达（已限源）

# ③ SSH 端口转发端到端（等价 ssh -L，实测）
127.0.0.1:9090/-/healthy                  → HTTP 200 "Prometheus Server is Healthy."
127.0.0.1:9090/api/v1/targets?state=active → HTTP 200（返回 activeTargets JSON）
127.0.0.1:9093/-/healthy                  → HTTP 200 "OK"
127.0.0.1:9093/api/v2/status              → HTTP 200（cluster status: ready）
127.0.0.1:9200/（反向对照：未发布端口）    → Connection refused ✔（隧道只到实际绑定的端口）

# ④ 全栈状态
7/7 healthy（prometheus、alertmanager 重建后均 healthy）
```

### 8.3 人工使用方式

```bash
# 本机执行（Windows PowerShell / CMD / Git Bash 均可；-N 表示只转发不开 shell）
ssh -N -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 root@<POC服务器IP>
# 浏览器：http://localhost:9090（Prometheus）｜http://localhost:9093（Alertmanager）
```

**注意**：22 已限源 IP，执行前确认本机出口 IP 在安全组白名单内；窗口关闭即隧道断开（可加 `-o ServerAliveInterval=30`）。
**现状限制**：Alertmanager 尚未配置任何通知渠道（IM/邮件打通在 S3-6），因此现在**只能看、不会主动通知**。

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-16 | 首次产出：S1 段（本环境最小测试环境降级形态）部署证据归档，含变更清单、可编程校验输出、9 项执行偏差、8 项遗留待确认 |
| v1.1 | 2026-09-17 | 复查补录：新增 **§3.7 安全面与可复现性核查**（公网暴露面实测仅 22/443、SSH 认证面缺陷与根因定位到行、node_exporter 全网卡监听、5 服务 `:latest` 与注释版本不符）；§4 偏差增至 12 项（新增 10/11/12）；§5 遗留改 11 项（第 5 项由"待办"改为"已实测收敛"，第 6 项升为最高优先级）；§6 增列只读核查脚本。**未做任何服务器侧状态变更** |
| v1.2 | 2026-09-17 | **S1-8 落地归档**：新增 **§7 备份与恢复落地**（口径 ≤1h、compose `path.repo` 变更与 `3cec4d63…→895cb471…`、4 脚本 + 3 条 cron 清单、首次演练原文输出 PG 0.07s / OS 158ms、6 项判读边界、cron 链路实测 §7.6）；§1 门禁② 结论由"未执行"改为"已落地待签字"；§5 第 1/2/11 项改写并新增 12/13 项；§6 校验和与脚本清单更新。脚本原文另见《AI数据治理平台_单机版_S1-8备份恢复脚本.md》 |
| v1.3 | 2026-09-17 | **安全组限源后核查与两处口径更正**：新增 **§3.7.5**（白名单路径可用、近 24h 成功登录仅本机 84 次、非本机成功登录 0、高频来源 `180.76.233.159` 全为失败尝试）；更正 §3.7.2 两处——24h 失败次数精确值 **37**（原 60 为宽松匹配口径）、**"无自动封禁"表述不准确**（OpenSSH 10.2 自带 `PerSourcePenalties` 已生效）；§5 第 5/6 项降级为"残留待放行"。另注明：扫描在 22:35 自行停止，**不得**作为安全组生效的证据 |
| v1.4 | 2026-09-17 | **运维接入（方案 C）**：新增 **§8**（回环发布 9090/9093 + Alertmanager 加 `frontend-net`、校验和 `895cb471…→07654a18…`、公网不可达与隧道端到端实测、人工使用命令、一次 YAML 写坏并回滚的如实记录）；§6 校验和更新。**对外暴露面零新增**（仍仅 443），容器数口径不变 |
| v1.5 | 2026-09-17 | §7.6 补齐正式调度两条腿的自动执行证据（**01:05:01 PG 备份**、**01:15:01 OpenSearch 快照**，均 SUCCESS），§7.5 第 6 项关闭；新增《AI数据治理平台_单机版_S1准出材料包.md》（待签署），四道书面产物进入可签状态 |
| v1.6 | 2026-09-17 | **门禁① 豁免与门禁② 经人工确认放行 → S1 段整体准出**（§1 结论表与准出判定段更新；Agent 只记录决策、不代签，书面签署见准出材料包）。随后进入 S2 |
| v1.7 | 2026-09-17 | §3.2 增加 **PgBouncer 认证补强记录**（S2-1 触发：`auth_user` 为空导致新角色 SASL 失败 → 增加 `PGBOUNCER_AUTH_USER=postgres` 使 `auth_query` 生效）；池化模式与 prepared statements 参数未变。compose 校验和现行 `aee7c2c4…`（见 S2 接入方案 §8.4） |
| v1.8 | 2026-09-18 | **补录 S1-7 验收口径漏项并关闭两项遗留**（本轮为可读性补记，未改 S1 结论）：① 新增 §4 偏差 **13**——S1-7 的 SLO 阈值**没有规则载体**（`rule_files` 为空、`/api/v1/rules` 无 groups），当时验收只查 `/targets` 全 UP；**2026-09-18 已补齐**（3 条 SLO 规则 + 磁盘护栏，`promtool` 通过）；② §5 第 9 项（5 个 `:latest`）由"待维护窗口"改为**已处置**（15 个镜像固定 digest，**未重建容器**，校验和 `b70ae7a9…`；漏洞扫描仍未执行）。证据见《AI数据治理平台_单机版_S3前置部署记录.md》§4/§5 |
| v1.9 | 2026-09-18 | **关闭两项"残留待放行"（人工放行后执行）**：① §5 第 6 项 **SSH 认证面闭环**——装机密钥、`PasswordAuthentication no` + `PermitRootLogin prohibit-password`（改生效的 cloud-init drop-in，`sshd -t` 通过后 reload；实测**密钥可用、口令被拒**）、root 口令轮换、`fail2ban` 启用（jail `sshd` 3/10m/1h）；② §5 第 5 项 **node_exporter 收紧**为 `172.17.0.1:9100`（Prometheus 抓取 4/4 仍 up）。另记：**FERNET_KEY 已重做为自持 key**（属 S2/S3 主题，证据见《…S3前置部署记录.md》§7），由此新增**配置出机**要求（`.env` 必须随备份）已在 S1-8 文档登记。**S1 结论与门禁口径未变** |
| v1.10 | 2026-09-18 | **443 收敛回写**：§5 第 5 项更新为"当前**对外仅 22（已限源），无任何对外业务端口**"——443 不再发布（宿主无监听），OM 改回环 8585 + SSH 隧道（无固定企业 VPN/出口 IP，故不改限源）；同时注明 22 的限源与"出口 IP 可能变化"是同源风险（应急走控制台 VNC）。**S1 结论、门禁与 RPO/RTO 口径均未变** |
| v1.11 | 2026-09-21 | **脱敏整改（同步 GitHub 前）**：把服务器**公网 IP** 与**主机名**替换为占位符（`<POC服务器公网IP>` / `<POC服务器主机名>`），以满足 AGENTS §9「域名/敏感信息一律占位符」；**未改任何口径、结论与判据**（仓库已推送 GitHub，历史提交中的原值另行处理） |

---

*本文为 S1 部署执行记录，由 Agent 自主执行并按《部署步骤_单机版》S1 校验；门禁与准出判读权归人工。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；命令与版本号执行前请按各组件官方文档核对。
