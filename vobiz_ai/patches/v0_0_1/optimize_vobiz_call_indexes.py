from __future__ import annotations

import frappe


def execute():
	if frappe.db.exists("DocType", "Vobiz Webhook Event"):
		frappe.reload_doc("vobiz_ai", "doctype", "vobiz_webhook_event")

	for doctype, fieldname, index_name in (
		("Vobiz Call Log", "source_app", "idx_vobiz_call_source_app"),
		("Vobiz Call Log", "user", "idx_vobiz_call_user"),
		("Vobiz Call Log", "crm_lead", "idx_vobiz_call_crm_lead"),
		("Vobiz Call Log", "cdr_sync_status", "idx_vobiz_call_cdr_status"),
		("Vobiz Call Log", "start_time", "idx_vobiz_call_start_time"),
		("Vobiz Call Log", "call_uuid", "idx_vobiz_call_uuid"),
		("Vobiz Call Log", "request_uuid", "idx_vobiz_call_request_uuid"),
		("Vobiz Call Log", "normalized_customer_number", "idx_vobiz_call_norm_customer"),
		("Vobiz Call Log", "customer_number", "idx_vobiz_call_customer"),
		("Vobiz Webhook Event", "status", "idx_vobiz_event_status"),
		("Vobiz Webhook Event", "call_key", "idx_vobiz_event_call_key"),
		("Vobiz Webhook Event", "request_id", "idx_vobiz_event_request_id"),
	):
		_add_index_if_possible(doctype, fieldname, index_name)


def _add_index_if_possible(doctype: str, fieldname: str, index_name: str) -> None:
	if not frappe.db.exists("DocType", doctype):
		return
	if not frappe.db.has_column(doctype, fieldname):
		return
	try:
		frappe.db.add_index(doctype, [fieldname], index_name)
	except Exception:
		message = frappe.get_traceback()
		if "Duplicate key name" not in message and "already exists" not in message:
			frappe.log_error(message, f"Vobiz index creation failed: {doctype}.{fieldname}")
