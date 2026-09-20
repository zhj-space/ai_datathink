package governance.access_audit

import rego.v1

# 事后审计决策：给定一条「访问/使用事件」，判定是否告警以及告警级别。
# 依据：设计方案场景 E（POC 期只做事后审计 + 主动告警，不做事前拦截）；ADR-A3。
# 输入（input）示例：
# {
#   "operation": "export",                       # read | export | download | bulk_read ...
#   "asset_fqn": "sample.ecommerce.orders",
#   "asset_tags": ["PII.Sensitive", "Tier.Tier1"],
#   "user": "alice", "user_roles": ["DataConsumer"],
#   "row_estimate": 250000,
#   "hour_utc": 23,
#   "context": {"purpose": "ad-hoc analysis", "ticket": "", "approval": false}
# }

default alert := false

alert if count(reasons) > 0

severity := "high" if count(high_reasons) > 0
severity := "medium" if {
	count(high_reasons) == 0
	count(medium_reasons) > 0
}
severity := "low" if {
	count(high_reasons) == 0
	count(medium_reasons) == 0
	count(low_reasons) > 0
}
severity := "none" if {
	count(high_reasons) == 0
	count(medium_reasons) == 0
	count(low_reasons) == 0
}

# 严重级别必须互斥：多条规则同时成立会导致 Rego 规则冲突（决策为 undefined）——
# 这是 2026-09-17 实测踩到的坑，详见《S2-5 OPA 策略与部署》§5。
high_risk if count(high_reasons) > 0

# --- 高风险判据 ---

high_reasons contains "PII 资产被批量导出/下载" if {
	is_pii
	is_bulk_operation
}

high_reasons contains "高风险操作缺少审批与工单" if {
	is_high_risk_operation
	not has_approval
}

# --- 中风险判据 ---

medium_reasons contains "非工作时段访问 PII 资产" if {
	is_pii
	is_off_hours
}

medium_reasons contains "大批量读取（超过阈值）" if {
	not is_pii
	is_bulk_operation
}

medium_reasons contains "非白名单角色执行高风险操作" if {
	is_high_risk_operation
	not is_privileged_role
}

# --- 低风险判据（仅记录，不升级告警） ---

low_reasons contains "PII 资产被单条读取" if {
	is_pii
	not is_bulk_operation
	not is_off_hours
}

# --- 汇总 ---

reasons contains r if { some r in high_reasons }
reasons contains r if { some r in medium_reasons }
reasons contains r if { some r in low_reasons }

# --- 基础谓词（含 fail-closed：输入缺失即按高风险处理） ---

is_pii if {
	some tag in input.asset_tags
	tag in data.governance.config.pii_tags
}

is_high_risk_operation if {
	input.operation in data.governance.config.high_risk_operations
}

is_bulk_operation if {
	is_high_risk_operation
}

is_bulk_operation if {
	object.get(input, "row_estimate", 0) >= data.governance.config.bulk_row_threshold
}

has_approval if {
	object.get(input.context, "approval", false) == true
	object.get(input.context, "purpose", "") != ""
	object.get(input.context, "ticket", "") != ""
}

is_off_hours if {
	h := object.get(input, "hour_utc", 12)
	start := data.governance.config.off_hours_utc.start
	end := data.governance.config.off_hours_utc.end
	h >= start
}

is_off_hours if {
	h := object.get(input, "hour_utc", 12)
	h < data.governance.config.off_hours_utc.end
}

is_privileged_role if {
	some role in object.get(input, "user_roles", [])
	role in ["Admin", "DataSteward", "GovernanceTeam"]
}

# 决策摘要：供 Governance Agent 直接消费（POC 只用于告警通道，不做拦截）
decision := {
	"alert": alert,
	"severity": severity,
	"reasons": sort([r | some r in reasons]),
	"channel": channel,
	"mode": "audit-only",
}

channel := data.governance.config.alert_channels[severity] if {
	severity in ["high", "medium"]
}

channel := data.governance.config.alert_channels.low if {
	severity == "low"
}

channel := "" if {
	severity == "none"
}
