from __future__ import annotations

import frappe

from vobiz_ai.api.utils import get_settings, normalize_phone
from vobiz_ai.api.voice_agent import (
	_fallback_patient_routing,
	_matching_patient_groups,
	_ordered_patient_agents,
	_patient_followup_id,
	_patient_medical_department,
	_public_agent,
	_settings_enabled,
)


def resolve_patient_routing_for_chat(patient: str, did_number: str | None = None) -> dict:
	"""Resolve Patient routing for asynchronous chat assignment.

	This intentionally does not reserve agents or update call counters; WhatsApp
	chats can remain open much longer than calls.
	"""
	base = {
		"success": True,
		"enabled": False,
		"matched": False,
		"transfer_allowed": False,
		"patient": patient or "",
		"status": "no_patient" if not patient else "disabled",
		"routing_basis": "",
		"routing_group": "",
		"routing_group_label": "",
		"patient_routing_match_mode": "",
		"agent_row": "",
		"agent_user": "",
		"agent_phone": "",
		"normalized_agent_phone": "",
		"fallback_user": "",
		"fallback_phone": "",
		"fallback_message": "",
	}
	if not patient:
		return base
	if not frappe.db.exists("Patient", patient):
		return {**base, "status": "no_patient"}
	if not frappe.db.exists("DocType", "Vobiz AI Settings"):
		return {**base, "status": "disabled"}

	try:
		settings = get_settings()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Vobiz Chat Patient Routing Settings Failed")
		return {**base, "success": False, "status": "error"}

	if not _settings_enabled(settings, "enable_patient_routing"):
		return base

	followup_id = _patient_followup_id(patient)
	medical_department = _patient_medical_department(patient)
	base.update(
		{
			"enabled": True,
			"sr_followup_id": followup_id,
			"medical_department": medical_department,
			"status": "no_route_factors",
		}
	)
	if not followup_id and not medical_department:
		return _chat_fallback_patient_routing(base, settings, status="no_route_factors")

	last_group = None
	for group in _matching_patient_groups(followup_id, medical_department, did_number or "", settings):
		last_group = group
		agents = _ordered_patient_agents(group)
		if not agents:
			base.update(
				{
					"matched": True,
					"status": "no_available_agent",
					"routing_group": group.name,
					"routing_group_label": group.get("display_label") or group.name,
					"patient_routing_match_mode": group.get("_match_label") or group.get("match_mode") or "",
					"strategy": group.get("strategy") or "Round Robin",
					"fallback_user": group.get("fallback_user") or "",
					"fallback_phone": group.get("fallback_phone") or "",
					"fallback_message": group.get("fallback_message") or "",
				}
			)
			continue

		selected = agents[0]
		return {
			**base,
			"matched": True,
			"transfer_allowed": True,
			"status": "selected",
			"routing_basis": "Patient Routing",
			"routing_group": group.name,
			"routing_group_label": group.get("display_label") or group.name,
			"patient_routing_match_mode": group.get("_match_label") or group.get("match_mode") or "",
			"strategy": group.get("strategy") or "Round Robin",
			"reservation_token": "",
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

	return _fallback_patient_routing(base, settings, group=last_group, status="no_matching_route")


def _chat_fallback_patient_routing(base: dict, settings, group: frappe._dict | None = None, status: str = "fallback") -> dict:
	routing = _fallback_patient_routing(base, settings, group=group, status=status)
	fallback_user = routing.get("fallback_user") or ""
	if fallback_user and routing.get("status") != "fallback":
		routing.update(
			{
				"transfer_allowed": True,
				"status": "fallback",
				"routing_basis": "Fallback",
				"patient_routing_match_mode": "Fallback",
				"agent_user": fallback_user,
			}
		)
	return routing
