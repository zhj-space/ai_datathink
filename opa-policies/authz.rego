package governance.authz

import rego.v1

# 授权决策骨架（POC 期**不用于在线拦截**，仅作为决策面能力的载体与后续演进的落点）。
# 纪律：fail-closed —— 默认拒绝；条件不全即拒绝；OPA 不可用时调用方必须按「拒绝」处理。

default allow := false

# 低风险读操作：允许（需已认证）
allow if {
	input.authenticated == true
	input.operation == "read"
	not is_high_risk
}

# 高风险操作：必须同时具备审批、用途、工单（任一缺失即拒绝）
allow if {
	input.authenticated == true
	is_high_risk
	has_justification
}

is_high_risk if {
	input.operation in data.governance.config.high_risk_operations
}

is_high_risk if {
	some tag in object.get(input, "asset_tags", [])
	tag in data.governance.config.pii_tags
}

is_high_risk if {
	object.get(input, "row_estimate", 0) >= data.governance.config.bulk_row_threshold
}

has_justification if {
	object.get(input.context, "approval", false) == true
	object.get(input.context, "purpose", "") != ""
	object.get(input.context, "ticket", "") != ""
}

# 拒绝原因（便于审计追溯）
reason := "allowed" if allow

reason := "denied: 未认证" if not input.authenticated

reason := "denied: 高风险操作缺少审批/用途/工单" if {
	input.authenticated == true
	is_high_risk
	not has_justification
}

reason := "denied: 操作类型未在白名单内" if {
	input.authenticated == true
	not is_high_risk
	input.operation != "read"
}

# 显式声明：OPA 不可用时调用方的行为约定（由 Governance Agent 实现，不入策略引擎）
fail_closed_contract := {
	"on_unavailable": "deny",
	"on_error": "deny",
	"on_missing_input": "deny",
}
