from __future__ import annotations

import json

import frappe

from vobiz_ai.api.utils import find_by_phone, normalize_phone
from vobiz_ai.api.voice_agent import (
	_split_actions,
	_validate_voice_agent_access,
	_resolve_profile_from_route,
	get_settings,
)


ACTION_ALIASES = {
	"clinic_address": "send_whatsapp",
	"send_address": "send_whatsapp",
	"send_clinic_address": "send_whatsapp",
	"whatsapp": "send_whatsapp",
	"appointment": "book_appointment_request",
	"book_appointment": "book_appointment_request",
	"doctor_callback": "arrange_doctor_callback",
	"callback": "arrange_doctor_callback",
	"call_back": "arrange_doctor_callback",
	"query": "create_issue",
	"complaint": "create_issue",
}


def _request_payload() -> dict:
	payload = {}
	if frappe.request:
		try:
			payload = frappe.request.get_json(silent=True) or {}
		except Exception:
			payload = {}
	for key, value in frappe.form_dict.items():
		if key not in {"cmd"} and value not in (None, ""):
			payload[key] = value
	return payload


def _normalise_action(action_type: str | None) -> str:
	action = (action_type or "").strip().lower().replace("-", "_").replace(" ", "_")
	return ACTION_ALIASES.get(action, action)


def _allowed_actions(profile) -> set[str]:
	actions = set(_split_actions(profile.allowed_voice_actions if profile else ""))
	return {_normalise_action(action) for action in actions}


def _resolve_party(phone: str) -> dict:
	patient = find_by_phone("Patient", ("mobile", "mobile_no", "phone", "custom_whatsapp_number"), phone)
	lead = find_by_phone("CRM Lead", ("mobile_no", "phone", "custom_whatsapp_number"), phone)
	return {
		"phone": phone,
		"normalized_phone": normalize_phone(phone),
		"patient": patient or "",
		"crm_lead": lead or "",
	}


def _add_reference_comment(reference_doctype: str, reference_name: str, text: str) -> None:
	if not reference_doctype or not reference_name or not frappe.db.exists(reference_doctype, reference_name):
		return
	try:
		doc = frappe.get_doc(reference_doctype, reference_name)
		doc.add_comment("Comment", text)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Vobiz Voice Action Comment Failed")


def _create_issue(action: str, payload: dict, party: dict) -> dict:
	reason = (payload.get("reason") or payload.get("summary") or payload.get("message") or "").strip()
	if not reason:
		frappe.throw("reason is required")

	subject_prefix = {
		"book_appointment_request": "Appointment request",
		"arrange_doctor_callback": "Doctor callback request",
		"create_issue": "Voice call issue",
	}.get(action, "Voice call action")

	reference_doctype = "CRM Lead" if party.get("crm_lead") else ("Patient" if party.get("patient") else "")
	reference_name = party.get("crm_lead") or party.get("patient") or ""
	description = "\n".join(
		part
		for part in [
			f"Action: {action}",
			f"Caller: {party.get('phone') or ''}",
			f"Profile: {payload.get('profile_key') or payload.get('voice_agent_profile') or ''}",
			f"DID: {payload.get('did_number') or ''}",
			f"Reason: {reason}",
			f"Preferred time: {payload.get('preferred_time') or ''}",
			f"Extra details: {payload.get('details') or ''}",
		]
		if part
	)

	issue = frappe.get_doc(
		{
			"doctype": "Issue",
			"subject": f"{subject_prefix}: {reason[:100]}",
			"status": "Open",
			"description": description,
		}
	)
	issue.insert(ignore_permissions=True)

	if reference_doctype and reference_name:
		_add_reference_comment(reference_doctype, reference_name, description)

	return {
		"success": True,
		"action": action,
		"issue": issue.name,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
	}


def _send_whatsapp(payload: dict, party: dict) -> dict:
	body = (payload.get("body") or payload.get("message") or "").strip()
	if not body:
		frappe.throw("message/body is required for send_whatsapp")

	phone = payload.get("phone") or payload.get("caller_phone") or party.get("phone")
	if not phone:
		frappe.throw("phone is required for send_whatsapp")

	reference_doctype = "CRM Lead" if party.get("crm_lead") else ("Patient" if party.get("patient") else "")
	reference_name = party.get("crm_lead") or party.get("patient") or ""

	try:
		from wa_chat_hub.api.voice_tools import send_voice_whatsapp_message
	except Exception:
		frappe.throw("wa_chat_hub is not installed or voice WhatsApp tools are unavailable")

	result = send_voice_whatsapp_message(
		phone=phone,
		body=body,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		display_name=payload.get("display_name") or "",
		action_name="vobiz_voice_send_whatsapp",
		async_send=1,
	)
	if reference_doctype and reference_name:
		_add_reference_comment(reference_doctype, reference_name, f"Voice agent queued WhatsApp:\n\n{body}")
	return result


@frappe.whitelist(allow_guest=True, methods=["POST"])
def perform_voice_action(**kwargs):
	"""Run a tightly-scoped action requested by the live voice agent."""
	payload = _request_payload()
	for key, value in (kwargs or {}).items():
		if value not in (None, ""):
			payload[key] = value
			frappe.form_dict[key] = value

	settings = get_settings()
	_validate_voice_agent_access(settings)
	profile, _route = _resolve_profile_from_route()
	if not profile:
		frappe.throw("Voice Agent Profile was not found")
	if not profile.enabled:
		frappe.throw("Voice Agent Profile is disabled", frappe.PermissionError)

	action = _normalise_action(payload.get("action") or payload.get("action_type"))
	if not action:
		frappe.throw("action is required")

	allowed = _allowed_actions(profile)
	if action not in allowed:
		frappe.throw(f"Voice action is not allowed for this profile: {action}", frappe.PermissionError)

	phone = payload.get("caller_phone") or payload.get("phone") or payload.get("from_number") or ""
	party = _resolve_party(phone)
	payload.setdefault("voice_agent_profile", profile.name)
	payload.setdefault("profile_key", profile.profile_key)

	if action == "send_whatsapp":
		result = _send_whatsapp(payload, party)
	elif action in {"book_appointment_request", "arrange_doctor_callback", "create_issue"}:
		result = _create_issue(action, payload, party)
	else:
		frappe.throw(f"Unsupported voice action: {action}", frappe.PermissionError)

	frappe.logger("vobiz_ai").info("Voice action completed: %s", json.dumps(result, default=str))
	return result
