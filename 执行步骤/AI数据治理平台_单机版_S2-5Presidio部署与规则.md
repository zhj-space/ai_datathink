# AI 数据治理平台 · 单机版 S2-5｜Presidio 部署与识别规则

> **定位**：`执行步骤/` 下的部署与规则产物，由《部署步骤_单机版》S2-5 展开，**不含新增架构决策**。
> **依据**：设计方案 2.1 组件表（Presidio｜PII 识别与脱敏｜MIT｜微软出品）、**场景 A**（离线批量扫描 + 按置信度分层）、设计方案明确「**Presidio 仅离线批量识别，已移出在线路径**」。
> **状态**：**已部署并验证通过**；**识别器规则与评分待人工审核**（AGENTS §11：Presidio 规则与阈值属「起草 + 人工审核」档）。
> **配套目录**：仓库 `presidio-recognizers/`（识别器注册表配置，入 Git）。

---

## 1. 结论摘要

| 项 | 结果 |
|---|---|
| 容器 | `presidio-analyzer`（**healthy**）+ `presidio-anonymizer`（**healthy**），均 `restart: unless-stopped` + healthcheck |
| 镜像 | `mcr.microsoft.com/presidio-analyzer:latest`（1.58GB）、`mcr.microsoft.com/presidio-anonymizer:latest`（345MB），**直连 mcr 拉取**（不经 Docker Hub 加速器） |
| 监听 | `127.0.0.1:3000`（analyzer）、`127.0.0.1:3001→容器 3000`（anonymizer）——**仅回环，公网不可达**；**未接 Traefik**（设计明确"仅离线批量识别，移出在线路径"） |
| 资源 | analyzer **1 vCPU / 2GB**；anonymizer **0.5 vCPU / 1GB**（与设计方案 4.2 资源表一致） |
| 识别器 | 共 **7** 个：3 个预定义（Email / Ip / Url）+ SpacyRecognizer（框架自动补）+ **3 个中国证件自定义**（CN_RESIDENT_ID / CN_MOBILE_PHONE / CN_BANK_CARD） |
| 识别效果 | 有上下文：身份证 **1.00**、手机 **0.95**、银行卡 **0.75**；无上下文：**0.85 / 0.60 / 0.40**（基础分）——**差值即"置信度分层"的依据** |
| 脱敏效果 | 身份证 `**************4567`、手机 `*******5678`、银行卡 `***************0123`、邮箱 `<REDACTED>` |
| compose 校验和 | 现行 `2a540bd64035e609f903f2a4717e70bba41a98571ab30b9713c2b1111acc35ac` |

---

## 2. 部署记录

### 2.1 compose 服务定义

```yaml
  presidio-analyzer:
    image: mcr.microsoft.com/presidio-analyzer:latest
    restart: unless-stopped
    environment:
      GUNICORN_CMD_ARGS: "--no-control-socket"   # 见 §4 坑 1
      RECOGNIZER_REGISTRY_CONF_FILE: /config/chinese_recognizers.yaml
    volumes:
      - /data/ai-governance/config/presidio/chinese_recognizers.yaml:/config/chinese_recognizers.yaml:ro
    ports: ["127.0.0.1:3000:3000"]
    networks: [backend-net, frontend-net]        # frontend-net：使回环端口发布生效
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:3000/health >/dev/null"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 90s
    logging: *default-logging
    deploy: { resources: { limits: { cpus: "1", memory: "2G" } } }

  presidio-anonymizer:
    image: mcr.microsoft.com/presidio-anonymizer:latest
    restart: unless-stopped
    environment:
      GUNICORN_CMD_ARGS: "--no-control-socket"
    ports: ["127.0.0.1:3001:3000"]               # 宿主 3001 → 容器 3000
    networks: [backend-net, frontend-net]
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:3000/health >/dev/null"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 30s
    logging: *default-logging
    deploy: { resources: { limits: { cpus: "0.5", memory: "1G" } } }
```

### 2.2 识别规则（策略即代码的姊妹产物）

| 文件（仓库 `presidio-recognizers/`） | 服务器路径 | sha256 |
|---|---|---|
| `chinese_recognizers.yaml` | `/data/ai-governance/config/presidio/chinese_recognizers.yaml` | `08d0d3f1196f561961c40849bbde4a853663ae0c0d4ce60b2757934ab98e9815` |
| `README.md` | 同目录 | `931f59763e6e1638ff9d816daa516dff29d32f72c24c422de7852173a6b41026` |

> 规则要点：身份证 18 位 **0.85** / 15 位 **0.55**；手机号（含 +86）**0.75** / 裸号 **0.60**；银行卡 **0.40**（仅常见号段 62/4/51-55，未做 Luhn 校验）。
> 三个识别器均为**纯正则**（PatternRecognizer），**不依赖 NLP 模型**；中文文本以 `language=en` 调用（详见 §3）。

---

## 3. 验证结果（实测）

### 3.1 识别（有上下文 vs 无上下文）

| 实体 | 有上下文（"身份证号 xxx、手机 xxx、银行卡 xxx"） | 无上下文（裸数据） |
|---|---|---|
| `CN_RESIDENT_ID` | **1.00** | **0.85** |
| `CN_MOBILE_PHONE` | **0.95** | **0.60** |
| `CN_BANK_CARD` | **0.75** | **0.40** |
| `EMAIL_ADDRESS` / `URL` | 1.00 / 0.50 | — |

> **这就是 S2-5 第 2 件「置信度分层」的量化依据**：同一实体在有无上下文词时分数差异显著（如银行卡 0.40 → 0.75）。
> 分层阈值（高/中/低）需在 Governance Agent（S3）中定义并**人工确认**，本文件不预设阈值。

### 3.2 脱敏（anonymizer）

```
原文: 客户张三，身份证号 110101199003074567，手机 13812345678，银行卡 6222021234567890123，邮箱 zhangsan@example.com
脱敏: 客户张三，身份证号 **************4567，手机 *******5678，银行卡 ***************0123，邮箱 <REDACTED>
```

### 3.3 识别器清单（`GET /recognizers?language=en`）

```
CnBankCardRecognizer ★ | CnMobileRecognizer ★ | CnResidentIdRecognizer ★
EmailRecognizer | IpRecognizer | SpacyRecognizer | UrlRecognizer
```

---

## 4. 执行中踩到的坑（如实记录）

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | 容器 `running` 但 **worker 从未启动**：日志只有 gunicorn 启动行、**无 `Booting worker`**，所有 HTTP 请求挂住（连接 ESTABLISHED 但无响应），healthcheck 超时 → unhealthy | gunicorn **25.x 默认启用控制套接字**（`Control socket listening at /app/gunicorn.ctl`），其服务线程与 master `fork()` 存在**间歇性竞争**——实测默认配置 4 次中失败 1~2 次；`--preload` 反而 0/3 全失败 | 环境变量 **`GUNICORN_CMD_ARGS="--no-control-socket"`**（控制套接字只是运维便利功能，本环境不使用）；加后实测 **4/4 成功**，部署容器重建后 worker 正常 boot |
| 2 | 同一次修复只对 analyzer 生效 | 我方服务定义中 **anonymizer 原本没有 `environment:` 段**，插入脚本按"已存在 environment:"匹配，漏掉了它 | 显式补 `environment:` 块（compose 中两处均有，见 §2.1） |
| 3 | 在容器内跑自定义脚本报 `ModuleNotFoundError: No module named 'presidio_analyzer'` | 镜像用 **poetry** 且 `virtualenvs.create=false`；`presidio_analyzer` 是 **`/app` 下的源码目录**（不在 site-packages），脚本放在 `/tmp` 时 `sys.path[0]` 不含 `/app` | 自定义脚本须以 `PYTHONPATH=/app`（或 cwd=/app）运行；`/recognizers` 返回的是**字符串数组**而非对象数组 |

---

## 5. 与设计边界的对齐（防越界）

| 设计口径 | 本环境落实 |
|---|---|
| **仅离线批量识别，已移出在线路径** | 两容器均**只绑回环**、**未接 Traefik**、不对公网暴露；无任何在线调用链 |
| 场景 A：置信度分层（高置信自动打标签 / 中置信走 OM Tasks / 低置信仅记录） | 本文件只提供**识别与分值**；分层动作与阈值在 S3 Governance Agent 实现并须人工确认 |
| 场景 A 备注：官方识别器不含中国证件 | 实测本版本可用类确无 CN 相关（有印度/意大利/韩国等）→ 已按自定义规则补齐，规则入 Git |
| 资源与组件纪律 | 资源按设计方案 4.2；未引入新组件；镜像版本以实拉结果记录，不臆造 |

---

## 6. 人工审核栏（`[A+H]`：规则与阈值）

| 项 | 内容 |
|---|---|
| 三条正则的误报/漏报是否可接受 | ☐ 认可　☐ 需调整（说明：________________） |
| 评分分值（0.85 / 0.55 / 0.75 / 0.60 / 0.40） | ☐ 认可　☐ 调整：________ |
| 上下文词表 | ☐ 认可　☐ 调整：________ |
| 银行卡未做 Luhn 校验（基础分 0.40）是否接受 | ☐ 接受（下游靠分层消解）　☐ 需补校验实现（属新增开发） |
| 中文文本以 `language=en` 调用是否接受 | ☐ 接受　☐ 需加中文/多语言模型（需磁盘与下载评估） |
| 签字 / 日期 | 签字：________________　____ 年 __ 月 __ 日 |

---

## 附：文档版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-17 | 首次产出：Presidio analyzer + anonymizer 部署（1 vCPU/2GB 与 0.5 vCPU/1GB、仅回环、healthcheck）、`presidio-recognizers/chinese_recognizers.yaml`（3 个中国证件识别器）、识别/脱敏实测、三个坑（gunicorn 控制套接字与 fork 竞争、anonymizer 缺 environment、poetry 源码目录导入）、人工审核栏 |

---

*本文件为 S2-5 的 Presidio 部署与规则产物，由《部署步骤_单机版》S2-5 展开，不含新增架构决策；规则与阈值的最终确认权归人工。*

**AI 生成提示**：本成果由 AI 生成，仅供决策参考；版本号与参数执行前请按官方文档核对。
