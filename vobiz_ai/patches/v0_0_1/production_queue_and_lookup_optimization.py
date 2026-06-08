from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from vobiz_ai.api.utils import get_phone_search_fields, last10, normalize_phone


def execute():
	ensure_phone_search_fields()
	ensure_settings_defaults()
	backfill_account_mapping_dids()
	backfill_phone_search_fields("CRM Lead")
	backfill_phone_search_fields("Patient")
	ensure_indexes()


def ensure_phone_search_fields():
	fields = {}
	for doctype in ("CRM Lead", "Patient"):
		if not frappe.db.exists("DocType", doctype):
			continue
		fields[doctype] = [
			{
				"fieldname": "vobiz_normalized_phone",
				"label": "Vobiz Normalized Phone",
				"fieldtype": "Data",
				"read_only": 1,
				"hidden": 1,
				"module": "Vobiz AI",
			},
			{
				"fieldname": "vobiz_mobile_last10",
				"label": "Vobiz Mobile Last 10",
				"fieldtype": "Data",
				"read_only": 1,
				"hidden": 1,
				"module": "Vobiz AI",
			},
			{
				"fieldname": "vobiz_phone_last10",
				"label": "Vobiz Phone Last 10",
				"fieldtype": "Data",
				"read_only": 1,
				"hidden": 1,
				"module": "Vobiz AI",
			},
			{
				"fieldname": "vobiz_whatsapp_last10",
				"label": "Vobiz WhatsApp Last 10",
				"fieldtype": "Data",
				"read_only": 1,
				"hidden": 1,
				"module": "Vobiz AI",
			},
		]
	if fields:
		create_custom_fields(fields, update=True, ignore_validate=True)


def ensure_settings_defaults():
	if not frappe.db.exists("DocType", "Vobiz AI Settings"):
		return
	settings = frappe.get_single("Vobiz AI Settings")
	meta = frappe.get_meta("Vobiz AI Settings")
	changed = False
	for fieldname, value in {
		"webhook_queue_name": "vobiz_webhook",
		"ai_queue_name": "vobiz_ai",
		"livekit_queue_name": "vobiz_livekit",
		"webhook_batch_size": 100,
	}.items():
		if meta.get_field(fieldname) and settings.get(fieldname) in (None, ""):
			settings.set(fieldname, value)
			changed = True
	if changed:
		settings.save(ignore_permissions=True)


def backfill_phone_search_fields(doctype: str, batch_size: int = 5000):
	if not frappe.db.exists("DocType", doctype):
		return
	if not frappe.db.has_column(doctype, "vobiz_phone_last10"):
		return

	source_fields = [field for field in get_phone_search_fields(doctype) if frappe.db.has_column(doctype, field)]
	if not source_fields:
		return

	start = 0
	while True:
		rows = frappe.get_all(
			doctype,
			fields=["name", *source_fields],
			order_by="name asc",
			limit_start=start,
			limit_page_length=batch_size,
		)
		if not rows:
			break
		for row in rows:
			values = _phone_keys(row, source_fields)
			frappe.db.set_value(
				doctype,
				row.name,
				values,
				update_modified=False,
			)
		frappe.db.commit()
		start += batch_size


def backfill_account_mapping_dids():
	if not frappe.db.exists("DocType", "Vobiz Account Mapping"):
		return
	if not frappe.db.has_column("Vobiz Account Mapping", "normalized_did"):
		return
	rows = frappe.get_all("Vobiz Account Mapping", fields=["name", "did_number"], limit_page_length=0)
	for row in rows:
		normalized = normalize_phone(row.did_number)
		if normalized:
			frappe.db.set_value(
				"Vobiz Account Mapping",
				row.name,
				"normalized_did",
				normalized,
				update_modified=False,
			)
	frappe.db.commit()


def _phone_keys(row, source_fields: list[str]) -> dict[str, str]:
	values = {
		"vobiz_normalized_phone": "",
		"vobiz_mobile_last10": _last10_from_row(row, ("mobile_no", "mobile")),
		"vobiz_phone_last10": _last10_from_row(row, ("phone",)),
		"vobiz_whatsapp_last10": _last10_from_row(row, ("custom_whatsapp_number",)),
	}
	for field in source_fields:
		value = row.get(field)
		normalized = normalize_phone(value)
		if normalized:
			values["vobiz_normalized_phone"] = normalized
			break
	return values


def _last10_from_row(row, fields: tuple[str, ...]) -> str:
	for field in fields:
		value = row.get(field)
		if last10(value):
			return last10(value)
	return ""


def ensure_indexes():
	for doctype, fields, index_name in (
		("CRM Lead", ["vobiz_phone_last10"], "idx_vobiz_crm_lead_phone10"),
		("CRM Lead", ["vobiz_mobile_last10"], "idx_vobiz_crm_lead_mobile10"),
		("CRM Lead", ["vobiz_whatsapp_last10"], "idx_vobiz_crm_lead_whatsapp10"),
		("CRM Lead", ["vobiz_normalized_phone"], "idx_vobiz_crm_lead_norm_phone"),
		("Patient", ["vobiz_phone_last10"], "idx_vobiz_patient_phone10"),
		("Patient", ["vobiz_mobile_last10"], "idx_vobiz_patient_mobile10"),
		("Patient", ["vobiz_whatsapp_last10"], "idx_vobiz_patient_whatsapp10"),
		("Patient", ["vobiz_normalized_phone"], "idx_vobiz_patient_norm_phone"),
		("Vobiz Call Log", ["crm_lead", "creation"], "idx_vobiz_call_lead_creation"),
		("Vobiz Call Log", ["crm_lead", "start_time"], "idx_vobiz_call_lead_start"),
		("Vobiz Call Log", ["patient", "creation"], "idx_vobiz_call_patient_creation"),
		("Vobiz Call Log", ["patient", "start_time"], "idx_vobiz_call_patient_start"),
		("Vobiz Webhook Event", ["status", "received_at"], "idx_vobiz_event_status_received"),
		("Vobiz Account Mapping", ["active", "account_id", "normalized_did", "trunk_id"], "idx_vobiz_map_route"),
		("Vobiz Account Mapping", ["active", "normalized_did"], "idx_vobiz_map_did"),
		("Vobiz Account Mapping", ["active", "account_id"], "idx_vobiz_map_account"),
	):
		_add_index_if_possible(doctype, fields, index_name)


def _add_index_if_possible(doctype: str, fields: list[str], index_name: str):
	if not frappe.db.exists("DocType", doctype):
		return
	for field in fields:
		if not frappe.db.has_column(doctype, field):
			return
	try:
		frappe.db.add_index(doctype, fields, index_name)
	except Exception:
		message = frappe.get_traceback()
		if "Duplicate key name" not in message and "already exists" not in message:
			frappe.log_error(message, f"Vobiz index creation failed: {doctype}.{','.join(fields)}")
