# AI 数据治理平台 · 单机版 S2-5｜OPA 部署与策略（策略即代码）

> **定位**：`执行步骤/` 下的部署与策略产物，由《部署步骤_单机版》S2-5 展开，**不含新增架构决策**。
> **依据**：设计方案 2.1 组件表（OPA｜策略即代码、访问控制/审批规则｜Apache-2.0）、**场景 E**（决策面 OPA／执行面查询引擎原生能力／审计面异步）、**ADR-A3**（POC 期只做事后审计 + 主动告警，不做事前拦截）、设计方案「配置即代码」纪律。
> **纪律**：**Rego 入 Git**（本仓库 `opa-policies/` 目录）；**fail-closed**——默认拒绝，且 **OPA 不可用时调用方必须按"拒绝/告警"处理**。
> **状态**：**已部署并验证通过**；**策略内容与阈值待人工审核**（AGENTS §11：OPA 策略与规则阈值属"起草 + 人工审核"档）。

---

## 1. 结论摘要

| 项 | 结果 |
|---|---|
| 容器 | `ai-governance-poc-opa-1`，**healthy**，`restart: unless-stopped` + healthcheck |
| 镜像/版本 | `openpolicyagent/opa:latest` → 实拉 **1.20.2**（Rego v1，distroless 镜像） |
| 监听 | `127.0.0.1:8181`（**仅回环**，公网不可达；无 UI，接口为 REST） |
| 策略 | 2 个包已加载：`governance.authz`、`governance.access_audit`；数据文件 `data.governance.config` 已加载 |
| 决策验证 | 授权用例 4/4 正确；审计告警用例 4/4 正确；空输入 → 拒绝 |
| fail-closed | 停止 OPA 后调用方请求**直接失败**（curl 退出码 7 / HTTP 000），拿不到"允许" ⇒ 只能按拒绝处理 |
| 资源 | 0.5 vCPU / 512MB（与设计方案 4.2 组件资源表一致） |
| 网络 | `backend-net` + `frontend-net`（后者用于使回环端口发布生效，与 alertmanager/prometheus 同款处理） |
| compose 校验和 | 现行 `76d44ba83e959d6dd48eeb91beea8d80bef8570d1aafc6600e62e7729e9a428b` |

---

## 2. 部署记录

### 2.1 compose 服务定义

```yaml
  opa:
    image: openpolicyagent/opa:latest        # 实拉 1.20.2（Rego v1）；distroless 镜像无 shell
    restart: unless-stopped
    command: ["run", "--server", "--addr=:8181", "--log-level=info", "/policies"]
    volumes:
      - /data/ai-governance/config/opa/policies:/policies:ro
    ports: ["127.0.0.1:8181:8181"]           # 仅回环：宿主调试/隧道用；OPA 无 UI，接口为 REST
    networks: [backend-net, frontend-net]
    healthcheck:
      # 镜像无 shell/curl，故用 OPA 自身校验：进程可用 + /policies 下策略可编译（策略写错即 unhealthy）
      test: ["CMD", "/opa", "eval", "--format=raw", "--data", "/policies", "true"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 15s
    logging: *default-logging
    deploy: { resources: { limits: { cpus: "0.5", memory: "512M" } } }
```

### 2.2 healthcheck 的设计理由与**局限**（须判读者知悉）

- `openpolicyagent/opa` 官方镜像是 **distroless**（无 `sh`/`curl`/`wget`），无法用常规方式探测 `/health` 端点；`latest-debug` 变体虽有 busybox，但**没有 wget/nc**，且 debug 变体不宜用于常规运行。
- 因此 healthcheck 采用 **OPA 自身**：`opa eval --data /policies 'true'`——实测在策略存在语法错误时**退出码 2**（可检出坏策略），正常时为 0。
- **局限（明确声明）**：该 healthcheck 校验的是"进程可运行 + 策略可编译"，**不等价于 HTTP 服务可用**。HTTP 监听器由**宿主侧探测**补充验证（本轮已验：`/health` → 200，`/v1/policies` → 2 条）。
- 若要 healthcheck 直接打 `/health`，需要引入额外的 HTTP 客户端（新组件或自建镜像）——**本环境不为此引入新组件**，改以上述组合方式覆盖。

### 2.3 策略文件（策略即代码，入 Git）

| 文件（仓库 `opa-policies/`） | 服务器路径 | sha256 |
|---|---|---|
| `access_audit.rego` | `/data/ai-governance/config/opa/policies/access_audit.rego` | `9d9766f5ffefbeaf54a8c5fa47462ad10f633dc9f48c1d78409389fb31b49e6b` |
| `authz.rego` | 同目录 | `e2076de9659d41f6f24ca04f21f1701a2341e88a5ab942c65d2cf69caa51dfd1` |
| `data.json` | 同目录 | `ae2bbf69b2a44dbf2e86d5e7554a8856df58e5905ee116a46dac559e60c2895b` |
| `README.md` | 同目录 | `784991a56608e9062a6a75a81c302167826acb643afedeb6d8e0546796e148b4` |

> 部署方式：仓库文件 → 上传至服务器策略目录 → 只读挂载进容器（`/policies`）。修改策略后**必须重启 OPA 容器**（本环境未启用 bundle 自动拉取）。

---

## 3. 决策验证（实测）

### 3.1 授权决策 `governance.authz`

| 用例 | 输入要点 | 期望 | 实测 |
|---|---|---|---|
| 低风险读 | 已认证 + `read` + 非 PII | 允许 | `allow=true`，`reason=allowed` ✅ |
| PII 导出（无审批） | `export` + `PII.Sensitive` + 25 万行 | 拒绝 | `allow=false`，`reason=denied: 高风险操作缺少审批/用途/工单` ✅ |
| PII 导出（有审批） | 同上 + `approval=true` + purpose + ticket | 允许 | `allow=true` ✅ |
| 未认证 | `authenticated=false` | 拒绝 | `allow=false`，`reason=denied: 未认证` ✅ |
| **空输入**（调用方漏传字段） | `{}` | 拒绝（fail-closed） | `allow=false` ✅ |

### 3.2 事后审计告警决策 `governance.access_audit`

| 用例 | 期望 | 实测 |
|---|---|---|
| PII 资产批量导出 | 高告警 | `alert=true`，`severity=high`，`channel=governance.alerts#p1`，3 条 reasons ✅ |
| 非工作时段读取 PII | 中告警 | `alert=true`，`severity=medium`，`channel=…p2`，reason=`非工作时段访问 PII 资产` ✅ |
| 普通读取（非 PII、工作时段） | 不告警 | `alert=false`，`severity=none` ✅ |
| 非 PII 大批量读取（50 万行） | 中告警 | `alert=true`，`severity=medium`，reason=`大批量读取（超过阈值）` ✅ |

**2026-09-17 23:52 独立复测（走隧道 `127.0.0.1:18181`，只读）**：`POST /v1/data/governance/access_audit` 四例，结果与上表一致，并补上"低风险仅落审计日志"这一档：

| 用例（输入要点） | 实测 |
|---|---|
| PII 单条读、工作时段（`hour_utc=14`） | `severity=low`、`channel=audit_log_only`、`alert=true` |
| PII 读、非工作时段（`hour_utc=23`） | `severity=medium`、`channel=governance.alerts#p2` |
| PII 批量导出 + 无审批 + 非白名单角色 + 非工作时段 | `severity=high`、`channel=governance.alerts#p1`、4 条 reasons |
| 常规读（非 PII、工作时段） | `severity=none`、`alert=false`、`channel=""` |

> **给 S3 Governance Agent 的字段纪律（实测踩到）**：PII 判定按 `data.governance.config.pii_tags`（`PII.Sensitive` / `PII.NonSensitive` / `PersonalData.SpecialCategory`），**传 `"PII"` 不会命中**；角色字段是 **`user_roles`**（不是 `roles`）；工作时段按 **`hour_utc`**（不传默认 12，即工作时段）。调用方漏传字段会静默落入"低风险/无告警"，因此 Agent 侧必须显式断言字段齐全。

### 3.3 fail-closed 契约

```
GET /v1/data/governance/authz/fail_closed_contract
→ {"on_unavailable":"deny","on_error":"deny","on_missing_input":"deny"}

# 实测：停止 OPA 容器后，调用方请求直接失败
curl -X POST http://127.0.0.1:8181/v1/data/governance/authz → 退出码 7 / HTTP 000（连接被拒）
⇒ 调用方拿不到"允许"，只能按"拒绝/告警"处理（Govnernance Agent 在 S3 按此实现）
```

---

## 4. 与设计边界的对齐（防越界）

| 设计口径 | 本环境落实 |
|---|---|
| OPA 无 UI（设计 2.1 备注：策略即代码 + Git） | **未部署任何 UI**；仅 REST 接口 + 只读回环端口（供调试/隧道） |
| POC 期只做事后审计 + 主动告警（场景 E / ADR-A3） | `access_audit.rego` 输出 `mode: "audit-only"`；`authz.rego` **不接入在线链路**，仅作决策面载体 |
| fail-closed | 默认拒绝 + 契约声明 + 不可用即失败（实测） |
| 组件与版本纪律 | 未引入新组件；镜像版本以实拉结果记录（1.20.2），不臆造 |

---

## 5. 执行中踩到的坑（如实记录）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 审计策略对 3 个用例返回 `null`（决策 undefined） | `severity` 写成 4 条**可能同时成立**的规则（如 `severity := "none" if count(reasons)==0` 与 `severity := "low" if not high_risk` 同时为真）⇒ **Rego 完整规则冲突**，结果 undefined | 改为**互斥**分级：每一级显式排除更高一级（`count(high_reasons)==0` 等） |
| 2 | 该问题**编译期不报错**（`opa check`/healthcheck 均通过） | Rego 的规则冲突是**运行期**语义问题，不是语法问题 | 记入本节；并明确 healthcheck 的覆盖边界（§2.2）；决策用例测试成为**策略变更的必做验证** |

---

## 6. 人工审核栏（`[A+H]`：策略与阈值）

| 项 | 内容 |
|---|---|
| 策略逻辑是否认可 | ☐ 认可　☐ 需修改（说明：________________） |
| `bulk_row_threshold = 10000`（工程经验取值） | ☐ 认可　☐ 调整：________ |
| `off_hours_utc = 22:00~06:00`（工程经验取值） | ☐ 认可　☐ 调整：________ |
| 告警通道映射（high→`governance.alerts#p1`，medium→`#p2`，low→仅审计日志） | ☐ 认可　☐ 调整：________ |
| PII 标签清单（`PII.Sensitive`/`PII.NonSensitive`/`PersonalData.SpecialCategory`） | ☐ 认可　☐ 调整：________ |
| 签字 / 日期 | 签字：________________　____ 年 __ 月 __ 日 |

> 审核通过后，本文件状态改为"已审核"，并把最终阈值回填到 `opa-policies/data.json`（同步更新本文 sha256 与版本记录）。

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-17 | 首次产出：OPA 1.20.2 部署（distroless + 自校验 healthcheck、仅回环、0.5vCPU/512MB）、策略即代码目录 `opa-policies/`（authz + access_audit + data.json + README）、决策用例 8/8 通过、fail-closed 实测、两个坑（severity 规则冲突 / 编译通过≠逻辑正确）、人工审核栏 |
| v1.1 | 2026-09-17 | §3.1/§3.2 追加 **23:52 独立复测**（`governance.authz` 5 例 + `access_audit` 4 例，含新增"低风险 → `audit_log_only`"档位），结果与首次一致；新增**调用方字段纪律**（`pii_tags` 须用 `PII.Sensitive` 等标签、角色字段 `user_roles`、时段字段 `hour_utc`；漏传字段会静默降为低风险/无告警，Agent 侧须显式断言） |

---

*本文件为 S2-5 的 OPA 部署与策略产物，由《部署步骤_单机版》S2-5 展开，不含新增架构决策；策略与阈值的最终确认权归人工。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；版本号与参数执行前请按官方文档核对。
