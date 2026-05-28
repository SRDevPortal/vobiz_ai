from __future__ import annotations

import json
import secrets

import frappe

from vobiz_ai.api.processing import _sync_linked_summaries
from vobiz_ai.api.utils import as_json, find_by_phone, normalize_phone


def make_outbound_call_key() -> str:
	return f"CTC-{secrets.token_urlsafe(18)}"


def create_outbound_call_log(
	*,
	reference_doctype: str,
	reference_name: str,
	phone_field: str | None,
	customer_number: str,
	user_mobile: str,
	caller_id: str,
	call_flow: str,
	user: str | None = None,
	callback_token: str | None = None,
):
	doc = frappe.get_doc(
		{
			"doctype": "Vobiz Call Log",
			"call_key": make_outbound_call_key(),
			"source_app": "vobiz_click_to_call",
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"phone_field": phone_field,
			"user": user or frappe.session.user,
			"user_mobile": user_mobile,
			"agent_number": user_mobile,
			"customer_number": customer_number,
			"normalized_customer_number": normalize_phone(customer_number),
			"caller_id": caller_id,
			"did_number": caller_id,
			"normalized_did": normalize_phone(caller_id),
			"call_flow": call_flow or "Customer First",
			"direction": "Outgoing",
			"status": "Queued",
			"callback_token": callback_token or secrets.token_urlsafe(24),
			"recording_status": "Not Started",
			"transcript_status": "Not Requested",
			"ai_status": "Pending",
			"ai_disposition_status": "Not Requested",
			"cdr_sync_status": "Not Synced",
			"currency": "INR",
		}
	)
	sync_reference_links(doc)
	doc.insert(ignore_permissions=True)
	return doc


def append_callback(call_log: str, event: str, payload: dict) -> None:
	if not frappe.db.exists("Vobiz Call Log", call_log):
		return

	doc = frappe.get_doc("Vobiz Call Log", call_log)
	rows = []
	if doc.get("raw_callbacks"):
		try:
			rows = json.loads(doc.raw_callbacks)
		except Exception:
			rows = []

	safe_payload = dict(payload or {})
	safe_payload.pop("token", None)
	safe_payload.pop("cmd", None)
	rows.append({"event": event, "received_at": frappe.utils.now(), "payload": safe_payload})
	doc.raw_callbacks = json.dumps(rows[-50:], indent=2, default=str)
	doc.raw_payload = as_json({"callbacks": rows[-50:]})
	doc.save(ignore_permissions=True)


def sync_reference_links(call_log_doc) -> None:
	if call_log_doc.reference_doctype == "CRM Lead":
		call_log_doc.crm_lead = call_log_doc.reference_name
	elif call_log_doc.reference_doctype == "Patient":
		call_log_doc.patient = call_log_doc.reference_name

	if call_log_doc.crm_lead or call_log_doc.patient:
		return

	customer_number = call_log_doc.customer_number or call_log_doc.normalized_customer_number
	if not customer_number:
		return

	call_log_doc.patient = find_by_phone("Patient", ("mobile", "phone"), customer_number)
	call_log_doc.crm_lead = find_by_phone("CRM Lead", ("mobile_no", "phone"), customer_number)
	if call_log_doc.patient:
		call_log_doc.caller_classification = "Patient"
	elif call_log_doc.crm_lead:
		call_log_doc.caller_classification = "Old Lead"


def sync_linked_summaries(call_log_doc) -> None:
	try:
		_sync_linked_summaries(call_log_doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Vobiz linked summary sync failed")
