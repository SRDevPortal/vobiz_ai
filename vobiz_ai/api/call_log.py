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


@frappe.whitelist()
def get_lead_call_badges(lead_names=None):
	if isinstance(lead_names, str):
		try:
			lead_names = json.loads(lead_names)
		except Exception:
			lead_names = [lead_names]
	lead_names = [name for name in (lead_names or []) if name]
	if not lead_names:
		return {}

	allowed = []
	for name in lead_names[:100]:
		try:
			if _can_read_crm_lead(name):
				allowed.append(name)
		except Exception:
			continue
	if not allowed:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT
			crm_lead,
			COUNT(*) AS total,
			SUM(CASE WHEN status IN ('Completed', 'Connected', 'Customer Answered', 'Agent Answered', 'In Progress') THEN 1 ELSE 0 END) AS connected,
			SUM(CASE WHEN status IN ('No Answer', 'Busy', 'Failed', 'Canceled', 'Cancelled') THEN 1 ELSE 0 END) AS missed,
			MAX(COALESCE(start_time, event_timestamp, modified, creation)) AS latest_query_time
		FROM `tabVobiz Call Log`
		WHERE crm_lead IN %(leads)s
		GROUP BY crm_lead
		""",
		{"leads": tuple(allowed)},
		as_dict=True,
	)
	seen_at = {
		row.name: row.vobiz_calls_seen_at
		for row in frappe.get_all(
			"CRM Lead",
			filters={"name": ["in", allowed]},
			fields=["name", "vobiz_calls_seen_at"],
		)
	}
	unread_counts = _get_unread_call_counts(allowed, seen_at)
	latest_logs = {}
	for row in frappe.get_all(
		"Vobiz Call Log",
		filters={"crm_lead": ["in", allowed]},
		fields=["name", "crm_lead"],
		order_by="COALESCE(start_time, event_timestamp, modified, creation) desc",
	):
		if row.crm_lead not in latest_logs:
			latest_logs[row.crm_lead] = row.name

	out = {}
	for row in rows:
		out[row.crm_lead] = {
			"total": int(row.total or 0),
			"connected": int(row.connected or 0),
			"missed": int(row.missed or 0),
			"notification_count": unread_counts.get(row.crm_lead, 0),
			"latest_query_time": row.latest_query_time,
			"latest_call_log": latest_logs.get(row.crm_lead),
		}
	return out


def _get_unread_call_counts(leads: list[str], seen_at: dict) -> dict:
	out = {lead: 0 for lead in leads}
	if not leads:
		return out

	recent_cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-90)
	rows = frappe.db.sql(
		"""
		SELECT
			log.crm_lead,
			COUNT(*) AS count
		FROM `tabVobiz Call Log` log
		WHERE log.crm_lead IN %(leads)s
		  AND log.creation > COALESCE(
			(
				SELECT lead.vobiz_calls_seen_at
				FROM `tabCRM Lead` lead
				WHERE lead.name = log.crm_lead
				  AND lead.vobiz_calls_seen_at IS NOT NULL
				  AND lead.vobiz_calls_seen_at != ''
			),
			%(recent_cutoff)s
		  )
		GROUP BY log.crm_lead
		""",
		{"leads": tuple(leads), "recent_cutoff": recent_cutoff},
		as_dict=True,
	)
	for row in rows:
		out[row.crm_lead] = int(row.count or 0)
	return out


def recompute_lead_unread_call_alerts():
	if not frappe.db.exists("DocType", "CRM Lead"):
		return {"updated": 0}
	meta = frappe.get_meta("CRM Lead")
	if not meta.get_field("vobiz_call_alert_count"):
		return {"updated": 0}

	leads = frappe.get_all(
		"CRM Lead",
		filters={"name": ["in", frappe.get_all("Vobiz Call Log", filters={"crm_lead": ["is", "set"]}, pluck="crm_lead")]},
		fields=["name", "vobiz_calls_seen_at"],
	)
	updated = 0
	for lead in leads:
		count = _get_unread_call_counts([lead.name], {lead.name: lead.vobiz_calls_seen_at}).get(lead.name, 0)
		frappe.db.set_value("CRM Lead", lead.name, "vobiz_call_alert_count", count, update_modified=False)
		updated += 1
	frappe.db.commit()
	frappe.clear_cache(doctype="CRM Lead")
	return {"updated": updated}


@frappe.whitelist()
def mark_lead_calls_read(lead_name: str | None = None):
	if not lead_name:
		return {"updated": 0}
	if not _can_read_crm_lead(lead_name):
		frappe.throw("Not permitted")
	meta = frappe.get_meta("CRM Lead")
	if not meta.get_field("vobiz_call_alert_count"):
		return {"updated": 0}
	values = {"vobiz_call_alert_count": 0}
	if meta.get_field("vobiz_calls_seen_at"):
		values["vobiz_calls_seen_at"] = frappe.utils.now_datetime()
	frappe.db.set_value("CRM Lead", lead_name, values, update_modified=False)
	frappe.db.commit()
	return {"updated": 1}


def backfill_lead_call_alerts():
	if not frappe.db.exists("DocType", "CRM Lead"):
		return {"updated": 0}
	meta = frappe.get_meta("CRM Lead")
	rows = frappe.db.sql(
		"""
		SELECT
			crm_lead,
			COUNT(*) AS total,
			SUM(CASE WHEN status IN ('Completed', 'Connected', 'Customer Answered', 'Agent Answered', 'In Progress') THEN 1 ELSE 0 END) AS connected,
			SUM(CASE WHEN status IN ('No Answer', 'Busy', 'Failed', 'Canceled', 'Cancelled') THEN 1 ELSE 0 END) AS missed,
			MAX(COALESCE(start_time, event_timestamp, modified, creation)) AS latest_query_time
		FROM `tabVobiz Call Log`
		WHERE IFNULL(crm_lead, '') != ''
		GROUP BY crm_lead
		""",
		as_dict=True,
	)
	updated = 0
	for row in rows:
		values = {
			"vobiz_total_call_attempts": int(row.total or 0),
			"vobiz_connected_call_count": int(row.connected or 0),
			"vobiz_missed_call_count": int(row.missed or 0),
			"vobiz_latest_query_time": row.latest_query_time,
		}
		values = {key: value for key, value in values.items() if meta.get_field(key)}
		if values:
			frappe.db.set_value("CRM Lead", row.crm_lead, values, update_modified=False)
			updated += 1
	return {"updated": updated}


def reset_lead_unread_call_alerts():
	if not frappe.db.exists("DocType", "CRM Lead"):
		return {"updated": 0}
	for fieldname in ("vobiz_latest_query_time", "vobiz_call_alert_count", "vobiz_calls_seen_at"):
		name = frappe.db.get_value("Custom Field", {"dt": "CRM Lead", "fieldname": fieldname})
		if name:
			frappe.db.set_value(
				"Custom Field",
				name,
				{"in_standard_filter": 0, "in_list_view": 0},
				update_modified=False,
			)
	meta = frappe.get_meta("CRM Lead")
	if meta.get_field("vobiz_call_alert_count"):
		updated = frappe.db.count("CRM Lead", {"vobiz_call_alert_count": [">", 0]})
		frappe.db.sql(
			"""
			UPDATE `tabCRM Lead`
			SET vobiz_call_alert_count = 0
			WHERE IFNULL(vobiz_call_alert_count, 0) != 0
			"""
		)
	else:
		updated = 0
	if meta.get_field("vobiz_calls_seen_at"):
		frappe.db.sql(
			"""
			UPDATE `tabCRM Lead`
			SET vobiz_calls_seen_at = NOW()
			WHERE IFNULL(vobiz_calls_seen_at, '') = ''
			"""
		)
	frappe.clear_cache(doctype="CRM Lead")
	return {"updated": updated}


def _can_read_crm_lead(name: str) -> bool:
	try:
		return bool(frappe.has_permission("CRM Lead", "read", doc=name))
	except TypeError:
		return bool(frappe.has_permission("CRM Lead", "read", name))
	except Exception:
		return False
