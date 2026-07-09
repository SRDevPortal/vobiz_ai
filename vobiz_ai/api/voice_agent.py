from __future__ import annotations

import secrets

import frappe
from frappe.utils import get_datetime, now_datetime

from vobiz_ai.api.utils import find_account_mapping, find_by_phone, get_password, get_settings, has_manager_role, normalize_phone


def _get_secret_from_request() -> str:
	headers = dict(frappe.request.headers) if frappe.request else {}
	return (
		headers.get("x-vobiz-ai-secret")
		or headers.get("X-Vobiz-Ai-Secret")
		or headers.get("x-voice-agent-secret")
		or headers.get("X-Voice-Agent-Secret")
		or frappe.form_dict.get("secret")
		or ""
	)


def _validate_voice_agent_access(settings) -> None:
	enabled = getattr(settings, "enable_voice_agent", None)
	if enabled in (None, ""):
		enabled = 1
	if not int(enabled):
		frappe.throw("Voice AI agent is disabled", frappe.PermissionError)

	expected = get_password(settings, "voice_agent_config_secret") or get_password(settings, "webhook_secret")
	if expected:
		if _get_secret_from_request() != expected:
			frappe.throw("Invalid voice agent config secret", frappe.PermissionError)
		return

	if frappe.session.user == "Guest" or not has_manager_role():
		frappe.throw("Voice agent config secret is required", frappe.PermissionError)


def _split_actions(value: str | None) -> list[str]:
	return [item.strip() for item in (value or "").split(",") if item.strip()]


def _request_value(*names: str) -> str:
	for name in names:
		value = frappe.form_dict.get(name)
		if value:
			return value
	return ""


def _resolve_profile_from_route():
	profile_name = _request_value("voice_agent_profile", "profile", "profile_key", "voiceAgentProfile")
	if profile_name:
		if frappe.db.exists("Vobiz Voice Agent Profile", profile_name):
			return frappe.get_doc("Vobiz Voice Agent Profile", profile_name), None
		name = frappe.db.get_value("Vobiz Voice Agent Profile", {"profile_key": profile_name})
		if name:
			return frappe.get_doc("Vobiz Voice Agent Profile", name), None
		frappe.throw(f"Voice Agent Profile was not found: {profile_name}", frappe.DoesNotExistError)

	did_number = _request_value("did_number", "didNumber", "to_number", "toNumber")
	if not did_number:
		return None, None

	from vobiz_ai.api.utils import normalize_phone

	normalized_did = normalize_phone(did_number)
	route_name = frappe.db.get_value(
		"Vobiz Voice Agent Route",
		{"active": 1, "normalized_did": normalized_did},
		"name",
	)
	if not route_name:
		profile_name = frappe.db.get_value(
			"Vobiz Voice Agent Profile",
			{"enabled": 1, "normalized_did": normalized_did},
			"name",
		)
		if profile_name:
			return frappe.get_doc("Vobiz Voice Agent Profile", profile_name), None
		return None, None

	route = frappe.get_doc("Vobiz Voice Agent Route", route_name)
	if route.voice_agent_profile and frappe.db.exists("Vobiz Voice Agent Profile", route.voice_agent_profile):
		return frappe.get_doc("Vobiz Voice Agent Profile", route.voice_agent_profile), route
	return None, route


def _resolve_account_prompt(settings):
	account_mapping = _request_value("account_mapping", "accountMapping")
	if not account_mapping:
		mapping = find_account_mapping(
			_request_value("account_id", "accountId"),
			_request_value("did_number", "didNumber", "to_number", "toNumber"),
			_request_value("trunk_id", "trunkId"),
			_request_value("domain"),
		)
		account_mapping = mapping.name if mapping else ""

	if not account_mapping:
		return None, ""

	for row in settings.get("voice_account_prompt_maps") or []:
		if row.account_mapping == account_mapping and row.is_active:
			return row, account_mapping
	return None, account_mapping


def _value(row, settings, fieldname: str, default=None):
	if row and row.get(fieldname) not in (None, ""):
		return row.get(fieldname)
	value = settings.get(fieldname)
	return default if value in (None, "") else value


def _profile_value(profile, settings, fieldname: str, default=None):
	if profile and profile.get(fieldname) not in (None, ""):
		return profile.get(fieldname)
	value = settings.get(fieldname)
	return default if value in (None, "") else value


def _caller_phone_from_request() -> str:
	return _request_value(
		"caller_phone",
		"callerPhone",
		"customer_number",
		"customerNumber",
		"from_number",
		"fromNumber",
		"phone",
	)


def _call_count_for_phone(phone: str) -> int:
	normalized = normalize_phone(phone)
	if not normalized:
		return 0
	last10 = normalized[-10:]
	rows = frappe.db.sql(
		"""
		SELECT COUNT(*) AS count
		FROM `tabVobiz Call Log`
		WHERE normalized_customer_number = %(normalized)s
		   OR customer_number LIKE %(last10_like)s
		""",
		{"normalized": normalized, "last10_like": f"%{last10}"},
		as_dict=True,
	)
	return int(rows[0].count or 0) if rows else 0


def _latest_call_for_phone(phone: str) -> dict:
	normalized = normalize_phone(phone)
	if not normalized:
		return {}
	last10 = normalized[-10:]
	rows = frappe.db.sql(
		"""
		SELECT name, start_time, event_timestamp, status, direction, crm_lead, patient
		FROM `tabVobiz Call Log`
		WHERE normalized_customer_number = %(normalized)s
		   OR customer_number LIKE %(last10_like)s
		ORDER BY COALESCE(start_time, event_timestamp, creation) DESC
		LIMIT 1
		""",
		{"normalized": normalized, "last10_like": f"%{last10}"},
		as_dict=True,
	)
	if not rows:
		return {}
	row = rows[0]
	when = row.start_time or row.event_timestamp
	return {
		"name": row.name,
		"status": row.status or "",
		"direction": row.direction or "",
		"crm_lead": row.crm_lead or "",
		"patient": row.patient or "",
		"when": str(get_datetime(when)) if when else "",
	}


def _patient_followup_id(patient: str | None) -> str:
	if not patient:
		return ""
	try:
		if frappe.db.has_column("Patient", "sr_followup_id"):
			return frappe.db.get_value("Patient", patient, "sr_followup_id") or ""
	except Exception:
		return ""
	return ""


def _route_did_from_request() -> str:
	return _request_value("did_number", "didNumber", "to_number", "toNumber")


def _matching_followup_groups(followup_id: str, did_number: str) -> list[frappe._dict]:
	if not followup_id or not frappe.db.exists("DocType", "Vobiz Followup Routing Group"):
		return []
	normalized_did = normalize_phone(did_number)
	groups = frappe.get_all(
		"Vobiz Followup Routing Group",
		filters={"active": 1, "sr_followup_id": followup_id},
		fields=[
			"name",
			"display_label",
			"sr_followup_id",
			"did_number",
			"normalized_did",
			"strategy",
			"priority",
			"reserve_on_config_fetch",
			"fallback_user",
			"fallback_phone",
			"fallback_message",
		],
		order_by="priority asc, modified desc",
		limit_page_length=50,
	)
	filtered = []
	for group in groups:
		group_did = group.get("normalized_did") or ""
		if group_did and normalized_did and group_did != normalized_did:
			continue
		if group_did and not normalized_did:
			continue
		group["_did_match_rank"] = 0 if group_did else 1
		filtered.append(group)
	filtered.sort(key=lambda row: (row.get("_did_match_rank", 1), int(row.get("priority") or 100)))
	return filtered


def _available_followup_agents(group_name: str) -> list[frappe._dict]:
	rows = frappe.get_all(
		"Vobiz Followup Routing Agent",
		filters={"parent": group_name, "parenttype": "Vobiz Followup Routing Group", "enabled": 1},
		fields=[
			"name",
			"agent_user",
			"agent_phone",
			"normalized_agent_phone",
			"availability_status",
			"priority",
			"weight",
			"max_active_calls",
			"active_call_count",
			"today_call_count",
			"last_patched_at",
			"last_transfer_status",
			"idx",
		],
		order_by="idx asc",
		limit_page_length=100,
	)
	available = []
	for row in rows:
		if (row.get("availability_status") or "Available") != "Available":
			continue
		max_active = int(row.get("max_active_calls") or 0)
		active = int(row.get("active_call_count") or 0)
		if max_active and active >= max_active:
			continue
		if not (row.get("agent_phone") or row.get("normalized_agent_phone")):
			continue
		available.append(row)
	return available


def _agent_sort_key(row: frappe._dict, strategy: str) -> tuple:
	priority = int(row.get("priority") or 100)
	active = int(row.get("active_call_count") or 0)
	today = int(row.get("today_call_count") or 0)
	last = str(row.get("last_patched_at") or "")
	idx = int(row.get("idx") or 0)
	weight = float(row.get("weight") or 1) or 1
	if strategy == "First Available":
		return (priority, idx)
	if strategy == "Least Busy":
		return (active, today, priority, last, idx)
	if strategy == "Weighted Balanced":
		return (today / weight, active, priority, last, idx)
	return (priority, last, today, idx)


def _ordered_followup_agents(group: frappe._dict) -> list[frappe._dict]:
	agents = _available_followup_agents(group.name)
	strategy = group.get("strategy") or "Round Robin"
	agents.sort(key=lambda row: _agent_sort_key(row, strategy))
	return agents


def _reserve_followup_agent(agent_row: frappe._dict) -> str:
	token = secrets.token_urlsafe(18)
	now = now_datetime()
	frappe.db.set_value(
		"Vobiz Followup Routing Agent",
		agent_row.name,
		{
			"active_call_count": int(agent_row.get("active_call_count") or 0) + 1,
			"today_call_count": int(agent_row.get("today_call_count") or 0) + 1,
			"last_patched_at": now,
			"last_transfer_status": "Reserved",
			"reservation_token": token,
		},
		update_modified=False,
	)
	frappe.db.commit()
	return token


def _public_agent(row: frappe._dict) -> dict:
	return {
		"agent_row": row.get("name") or "",
		"agent_user": row.get("agent_user") or "",
		"agent_phone": row.get("agent_phone") or "",
		"normalized_agent_phone": row.get("normalized_agent_phone") or normalize_phone(row.get("agent_phone")),
		"availability_status": row.get("availability_status") or "Available",
		"active_call_count": int(row.get("active_call_count") or 0),
		"max_active_calls": int(row.get("max_active_calls") or 0),
		"priority": int(row.get("priority") or 100),
	}


def _build_patient_routing(context: dict) -> dict:
	patient = context.get("patient")
	followup_id = _patient_followup_id(patient)
	base = {
		"matched": False,
		"transfer_allowed": False,
		"patient": patient or "",
		"sr_followup_id": followup_id,
		"status": "no_patient" if not patient else "no_followup_id",
	}
	if not patient or not followup_id:
		return base

	for group in _matching_followup_groups(followup_id, _route_did_from_request()):
		agents = _ordered_followup_agents(group)
		if not agents:
			base.update(
				{
					"matched": True,
					"status": "no_available_agent",
					"routing_group": group.name,
					"routing_group_label": group.get("display_label") or group.name,
					"strategy": group.get("strategy") or "Round Robin",
					"fallback_user": group.get("fallback_user") or "",
					"fallback_phone": group.get("fallback_phone") or "",
					"fallback_message": group.get("fallback_message") or "",
				}
			)
			continue

		selected = agents[0]
		reservation_token = _reserve_followup_agent(selected) if int(group.get("reserve_on_config_fetch") or 0) else ""
		return {
			**base,
			"matched": True,
			"transfer_allowed": True,
			"status": "selected",
			"routing_group": group.name,
			"routing_group_label": group.get("display_label") or group.name,
			"strategy": group.get("strategy") or "Round Robin",
			"reservation_token": reservation_token,
			"selected_agent": _public_agent(selected),
			"agent_row": selected.name,
			"agent_user": selected.get("agent_user") or "",
			"agent_phone": selected.get("agent_phone") or "",
			"normalized_agent_phone": selected.get("normalized_agent_phone") or normalize_phone(selected.get("agent_phone")),
			"fallback_user": group.get("fallback_user") or "",
			"fallback_phone": group.get("fallback_phone") or "",
			"fallback_message": group.get("fallback_message") or "",
			"available_agents": [_public_agent(row) for row in agents],
		}

	return base if base.get("status") != "no_available_agent" else base


def _build_caller_context() -> dict:
	phone = _caller_phone_from_request()
	if not phone:
		return {}

	patient = find_by_phone("Patient", ("mobile", "mobile_no", "phone", "custom_whatsapp_number"), phone)
	lead = find_by_phone("CRM Lead", ("mobile_no", "phone", "custom_whatsapp_number"), phone)
	call_count = _call_count_for_phone(phone)
	latest_call = _latest_call_for_phone(phone)

	classification = "New Caller"
	if patient:
		classification = "Existing Patient"
	elif lead:
		classification = "Existing Lead"
	elif call_count:
		classification = "Repeat Caller"

	display_name = ""
	if patient:
		display_name = frappe.db.get_value("Patient", patient, "patient_name") or patient
	elif lead:
		display_name = (
			frappe.db.get_value("CRM Lead", lead, "lead_name")
			or frappe.db.get_value("CRM Lead", lead, "first_name")
			or lead
		)

	return {
		"phone": phone,
		"normalized_phone": normalize_phone(phone),
		"classification": classification,
		"patient": patient or "",
		"sr_followup_id": _patient_followup_id(patient),
		"crm_lead": lead or "",
		"display_name": display_name or "",
		"previous_call_count": call_count,
		"is_repeat_caller": bool(call_count),
		"latest_call": latest_call,
	}


def _caller_context_prompt(context: dict) -> str:
	if not context:
		return ""

	lines = [
		"## Caller Context",
		f"- Caller phone: {context.get('phone') or ''}",
		f"- Caller classification: {context.get('classification') or 'New Caller'}",
		f"- Previous Vobiz call count: {context.get('previous_call_count') or 0}",
	]
	if context.get("display_name"):
		lines.append(f"- Known name: {context.get('display_name')}")
	if context.get("patient"):
		lines.append(f"- Existing Patient record: {context.get('patient')}")
	if context.get("sr_followup_id"):
		lines.append(f"- Patient Follow-up ID: {context.get('sr_followup_id')}")
	if context.get("crm_lead"):
		lines.append(f"- Existing CRM Lead record: {context.get('crm_lead')}")
	latest = context.get("latest_call") or {}
	if latest.get("when"):
		lines.append(f"- Latest call: {latest.get('when')} ({latest.get('status') or 'status unknown'})")
	lines.extend(
		[
			"- Use this context naturally. Do not say internal record IDs unless the caller asks.",
			"- If this is an existing lead/patient/repeat caller, acknowledge continuity briefly and avoid asking for details already known.",
		]
	)
	return "\n".join(lines)


@frappe.whitelist(allow_guest=True)
def get_config(**kwargs):
	"""Return the active Gemini Live voice-agent configuration from Frappe.

	The endpoint is intentionally small so the voice side can treat Frappe as the
	source of truth for prompt, model, voice, guardrails, LiveKit, and MCP config.
	"""
	for key, value in (kwargs or {}).items():
		if value not in (None, ""):
			frappe.form_dict[key] = value

	settings = get_settings()
	_validate_voice_agent_access(settings)
	profile, route = _resolve_profile_from_route()
	if profile and not profile.enabled:
		frappe.throw("Voice Agent Profile is disabled", frappe.PermissionError)

	account_prompt, account_mapping = _resolve_account_prompt(settings)

	system_prompt = ""
	if profile:
		system_prompt = profile.system_prompt or ""
	elif account_prompt:
		system_prompt = _value(account_prompt, settings, "system_prompt", "") or ""
	else:
		system_prompt = settings.get("system_prompt") or ""

	policies = [
		_profile_value(profile, settings, "medical_guardrail_policy", "") if profile else _value(account_prompt, settings, "medical_guardrail_policy", ""),
		_profile_value(profile, settings, "escalation_policy", "") if profile else _value(account_prompt, settings, "escalation_policy", ""),
	]
	caller_context = _build_caller_context()
	patient_routing = _build_patient_routing(caller_context)
	full_prompt = "\n\n".join([part for part in [system_prompt, _caller_context_prompt(caller_context), *policies] if part])

	return {
		"enabled": bool(settings.get("enable_voice_agent")),
		"agent_name": profile.agent_name if profile else (_value(account_prompt, settings, "agent_name", getattr(settings, "agent_name", "") or "") or ""),
		"voice_agent_profile": profile.name if profile else "",
		"profile_key": profile.profile_key if profile else "",
		"route": route.name if route else "",
		"account_mapping": account_mapping,
		"using_account_prompt": bool(account_prompt or profile),
		"system_prompt": full_prompt,
		"base_system_prompt": system_prompt,
		"caller_context": caller_context,
		"patient_routing": patient_routing,
		"greeting_instruction": _profile_value(profile, settings, "greeting_instruction", "") if profile else (_value(account_prompt, settings, "greeting_instruction", "") or ""),
		"gemini": {
			"model": _profile_value(profile, settings, "gemini_live_model", "gemini-live-2.5-flash-native-audio") if profile else _value(account_prompt, settings, "gemini_live_model", "gemini-live-2.5-flash-native-audio"),
			"voice": _profile_value(profile, settings, "gemini_live_voice", "Puck") if profile else _value(account_prompt, settings, "gemini_live_voice", "Puck"),
			"vertex_location": _profile_value(profile, settings, "vertex_location", "us-central1") if profile else _value(account_prompt, settings, "vertex_location", "us-central1"),
			"google_cloud_project": _profile_value(profile, settings, "google_cloud_project", "") if profile else _value(account_prompt, settings, "google_cloud_project", ""),
		},
		"livekit": {
			"url": settings.get("livekit_url") or "",
			"api_key": get_password(settings, "livekit_api_key"),
			"api_secret": get_password(settings, "livekit_api_secret"),
			"sip_provider": "Vobiz",
			"room_name_pattern": "gemini_live_{caller}_{random}",
			"agent_dispatch_name": _profile_value(profile, settings, "livekit_agent_name", "vobiz-gemini-live") if profile else (settings.get("livekit_agent_name") or "vobiz-gemini-live"),
			"inbound_trunk_id": profile.livekit_inbound_trunk_id if profile else (route.livekit_inbound_trunk_id if route else ""),
			"dispatch_rule_id": profile.livekit_dispatch_rule_id if profile else (route.livekit_dispatch_rule_id if route else ""),
			"did_number": profile.did_number if profile else (route.did_number if route else ""),
			"vobiz_trunk_id": profile.trunk_id if profile else (route.trunk_id if route else ""),
			"domain": profile.domain if profile else (route.domain if route else ""),
		},
		"mcp": {
			"server_url": profile.mcp_server_url if profile and profile.mcp_server_url else (settings.get("mcp_server_url") or ""),
			"bearer_token": get_password(profile, "mcp_bearer_token") if profile and get_password(profile, "mcp_bearer_token") else get_password(settings, "mcp_bearer_token"),
			"lead_creation_tool_name": profile.lead_creation_tool_name if profile and profile.lead_creation_tool_name else (settings.get("lead_creation_tool_name") or "mcp_create_lead"),
		},
		"guardrails": {
			"medical": _profile_value(profile, settings, "medical_guardrail_policy", "") if profile else (_value(account_prompt, settings, "medical_guardrail_policy", "") or ""),
			"escalation": _profile_value(profile, settings, "escalation_policy", "") if profile else (_value(account_prompt, settings, "escalation_policy", "") or ""),
			"allowed_actions": _split_actions(_profile_value(profile, settings, "allowed_voice_actions", "") if profile else _value(account_prompt, settings, "allowed_voice_actions", "")),
		},
	}


@frappe.whitelist(allow_guest=True)
def get_voice_agent_config(**kwargs):
	return get_config(**kwargs)


@frappe.whitelist(allow_guest=True)
def update_patient_routing_status(
	agent_row: str | None = None,
	reservation_token: str | None = None,
	status: str | None = None,
	call_log: str | None = None,
	**kwargs,
):
	settings = get_settings()
	_validate_voice_agent_access(settings)
	status = (status or kwargs.get("transfer_status") or "").strip()[:60]
	if not status:
		frappe.throw("Routing status is required")

	filters = {}
	if reservation_token:
		filters["reservation_token"] = reservation_token
	elif agent_row:
		filters["name"] = agent_row
	else:
		frappe.throw("Agent row or reservation token is required")

	row_name = frappe.db.get_value("Vobiz Followup Routing Agent", filters, "name")
	if not row_name:
		return {"ok": False, "status": "not_found"}

	row = frappe.get_doc("Vobiz Followup Routing Agent", row_name)
	release_statuses = {"completed", "failed", "busy", "no answer", "no_answer", "cancelled", "canceled", "released"}
	values = {"last_transfer_status": status}
	if status.lower() in release_statuses:
		values["active_call_count"] = max(0, int(row.active_call_count or 0) - 1)
		values["reservation_token"] = ""
	frappe.db.set_value("Vobiz Followup Routing Agent", row.name, values, update_modified=False)
	if call_log and frappe.db.exists("Vobiz Call Log", call_log):
		_update_call_log_routing_status(call_log, row, status)
	frappe.db.commit()
	return {"ok": True, "agent_row": row.name, "status": status}


def _update_call_log_routing_status(call_log: str, agent_row, status: str) -> None:
	meta = frappe.get_meta("Vobiz Call Log")
	group = frappe.get_doc("Vobiz Followup Routing Group", agent_row.parent) if agent_row.parent else None
	values = {
		"sr_followup_id": group.sr_followup_id if group else "",
		"followup_routing_group": group.name if group else "",
		"followup_routing_agent": agent_row.name,
		"followup_routing_user": agent_row.agent_user,
		"followup_routing_phone": agent_row.agent_phone,
		"followup_transfer_status": status,
	}
	values = {key: value for key, value in values.items() if meta.get_field(key)}
	if values:
		frappe.db.set_value("Vobiz Call Log", call_log, values, update_modified=False)


def reset_followup_routing_daily_counts():
	if not frappe.db.exists("DocType", "Vobiz Followup Routing Agent"):
		return
	frappe.db.sql(
		"""
		UPDATE `tabVobiz Followup Routing Agent`
		SET today_call_count = 0
		WHERE IFNULL(today_call_count, 0) != 0
		"""
	)
