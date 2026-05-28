from __future__ import annotations

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime

from vobiz_ai.api.utils import add_retry_delay, get_settings, has_manager_role, parse_json


@frappe.whitelist()
@rate_limit(limit=10, seconds=60)
def retry_error(error_log: str):
	if not has_manager_role():
		frappe.throw("Only System Manager or Vobiz AI Manager can retry Vobiz errors", frappe.PermissionError)
	doc = frappe.get_doc("Vobiz Error Log", error_log)
	if doc.resolved:
		return {"status": "already_resolved"}
	if doc.max_retry_count and doc.retry_count >= doc.max_retry_count:
		frappe.throw("Max retry count reached")

	if doc.process_type == "AI Scoring" and doc.call_log:
		frappe.enqueue("vobiz_ai.api.ai.score_call_log", queue="short", call_log=doc.call_log)
	elif doc.webhook_event:
		frappe.db.set_value("Vobiz Webhook Event", doc.webhook_event, "status", "Queued", update_modified=True)
		frappe.enqueue(
			"vobiz_ai.api.processing.process_webhook_event",
			queue="short",
			timeout=300,
			webhook_event=doc.webhook_event,
		)
	else:
		frappe.enqueue("vobiz_ai.api.retry.retry_error_job", queue="short", timeout=300, error_log=doc.name)

	doc.retry_count = (doc.retry_count or 0) + 1
	doc.last_retry_time = now_datetime()
	doc.next_retry_time = add_retry_delay(getattr(get_settings(), "retry_delay_minutes", 10) or 10)
	doc.status = "Retried"
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"status": "queued", "error_log": doc.name, "call_log": doc.call_log}


def retry_error_job(error_log: str):
	doc = frappe.get_doc("Vobiz Error Log", error_log)
	if doc.resolved:
		return
	payload = parse_json(doc.payload or "{}")
	if isinstance(payload, list) and payload:
		payload = payload[0].get("body") or payload[0]
	elif isinstance(payload, dict) and "body" in payload and isinstance(payload.get("body"), dict):
		payload = payload.get("body")

	from vobiz_ai.api.processing import process_payload

	call_log = process_payload(payload or {}, doc.webhook_event)
	doc.call_log = doc.call_log or call_log
	doc.save(ignore_permissions=True)
	frappe.db.commit()
