# Presidio 识别器配置（PII 识别规则）

> **定位**：本目录存放 Presidio **识别器注册表配置**（`RECOGNIZER_REGISTRY_CONF_FILE` 指向的文件），属「PII 识别规则」的版本化落点。
> **依据**：《部署步骤_单机版》S2-5 第 1 件（中国证件自定义识别规则）；设计方案 2.1 组件表（Presidio｜MIT）、场景 A（离线批量扫描 + 置信度分层）。
> **部署位置**：服务器 `/data/ai-governance/config/presidio/`，只读挂载进 `presidio-analyzer` 容器（`/config`）。
> **权威文档**：《AI数据治理平台_单机版_S2-5Presidio部署与规则.md》（含部署与验证记录）。

| 文件 | 用途 |
|---|---|
| `chinese_recognizers.yaml` | 识别器注册表：3 个预定义（Email / IP / Url）+ **3 个中国证件自定义识别器**（CN_RESIDENT_ID / CN_MOBILE_PHONE / CN_BANK_CARD） |

**纪律与状态**：

1. **仅离线批量识别**：Presidio 已移出在线路径（设计方案明确），**不接入在线链路、不做在线逐行脱敏**；容器不对公网暴露。
2. **审核状态**：**待人工审核**（AGENTS §11：Presidio 规则与阈值属「起草 + 人工审核」档）。重点审核项：正则的误报漏报、评分分值、上下文词表。
3. **已知局限**（须写进判读）：银行卡**无 Luhn 校验**（基础分 0.4，靠上下文提升）；身份证仅**结构校验**；本镜像只装 en 模型，中文文本以 `language=en` 调用（三个识别器均为纯正则）。
4. **变更流程**：修改本目录文件后须同步《…S2-5Presidio部署与规则.md》的版本记录、更新 sha256，并**重启 analyzer 容器**后重跑验证用例。
