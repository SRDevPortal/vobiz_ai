from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from vobiz_ai.api.processing import process_webhook_event
from vobiz_ai.api.utils import (
	as_json,
	extract_call_key,
	extract_event_name,
	extract_request_id,
	get_account_id,
	get_payload_and_headers,
	get_password,
	get_queue_name,
	get_settings,
)


def _validate_secret(headers: dict):
	settings = get_settings()
	if not getattr(settings, "enabled", 0):
		frappe.throw("Vobiz AI integration is disabled", frappe.PermissionError)

	expected = get_password(settings, "webhook_secret")
	if not expected:
		return
	actual = (
		headers.get("x-vobiz-ai-secret")
		or headers.get("X-Vobiz-Ai-Secret")
		or headers.get("x-erp-secret")
		or headers.get("X-Erp-Secret")
		or frappe.form_dict.get("secret")
	)
	if actual != expected:
		frappe.throw("Invalid Vobiz webhook secret", frappe.PermissionError)


def _event_key(event_type: str, request_id: str, call_key: str) -> str:
	base = request_id or f"{call_key}-{event_type}"
	return f"{event_type}:{base}"[:140]


@frappe.whitelist(allow_guest=True)
def receive():
	payload, headers, raw = get_payload_and_headers()
	if not isinstance(payload, dict):
		frappe.throw("Invalid Vobiz payload")

	_validate_secret(headers)

	event_type = extract_event_name(payload, headers)
	request_id = extract_request_id(payload, headers)
	call_key = extract_call_key(payload)
	key = _event_key(event_type, request_id, call_key)

	if frappe.db.exists("Vobiz Webhook Event", key):
		existing = frappe.get_doc("Vobiz Webhook Event", key)
		if existing.status == "Processed":
			return {"status": "duplicate", "event": existing.name, "call_log": existing.call_log}
		if existing.status in {"Queued", "Processing"}:
			return {"status": existing.status.lower(), "event": existing.name, "call_log": existing.call_log}

	event = frappe.get_doc(
		{
			"doctype": "Vobiz Webhook Event",
			"event_key": key,
			"event_type": event_type,
			"request_id": request_id,
			"account_id": get_account_id(payload),
			"call_key": call_key,
			"status": "Received",
			"received_at": now_datetime(),
			"payload": as_json(raw),
		}
	)
	if not frappe.db.exists("Vobiz Webhook Event", key):
		event.insert(ignore_permissions=True)
	else:
		event = frappe.get_doc("Vobiz Webhook Event", key)

	event.status = "Queued"
	event.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		frappe.enqueue(
			"vobiz_ai.api.processing.process_webhook_event",
			queue=get_queue_name("webhook_queue_name", "vobiz_webhook"),
			timeout=300,
			webhook_event=event.name,
		)
		return {"status": "queued", "event": event.name}
	except Exception:
		process_webhook_event(event.name)
		frappe.db.commit()
		event.reload()
		return {"status": event.status.lower(), "event": event.name, "call_log": event.call_log, "error_log": event.error_log}
