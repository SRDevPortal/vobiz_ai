from __future__ import annotations

import hashlib
import json
import re
import traceback
from datetime import timedelta
from typing import Any

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime


ADMIN_ROLES = {"System Manager", "Vobiz AI Manager"}


def has_manager_role(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(ADMIN_ROLES.intersection(set(frappe.get_roles(user))))


def as_json(data: Any) -> str:
	return json.dumps(data or {}, ensure_ascii=False, indent=2, default=str)


def parse_json(data: Any) -> Any:
	if isinstance(data, str):
		return frappe.parse_json(data)
	return data


def clean_number(value: str | None) -> str:
	return re.sub(r"\D+", "", value or "")


def normalize_phone(value: str | None) -> str:
	digits = clean_number(value)
	if not digits:
		return ""
	if digits.startswith("00"):
		digits = digits[2:]
	if len(digits) == 12 and digits.startswith("91"):
		return digits
	if len(digits) == 10:
		return "91" + digits
	if len(digits) > 10 and digits.endswith(digits[-10:]):
		return "91" + digits[-10:]
	return digits


def last10(value: str | None) -> str:
	digits = clean_number(value)
	return digits[-10:] if len(digits) >= 10 else digits


def hash_text(text: str | None) -> str:
	return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def parse_dt(value: Any):
	if not value:
		return None
	try:
		text = str(value)
		if text.endswith("Z"):
			text = text[:-1]
		if "." in text:
			head, tail = text.split(".", 1)
			tail = re.sub(r"[^0-9].*$", "", tail)[:6]
			text = f"{head}.{tail}"
		return get_datetime(text)
	except Exception:
		return None


def seconds_to_duration(value: Any) -> int | None:
	try:
		if value in (None, ""):
			return None
		return int(float(value))
	except Exception:
		return None


def get_settings():
	return frappe.get_single("Vobiz AI Settings")


def get_setting_value(fieldname: str, default=None):
	try:
		settings = get_settings()
		value = settings.get(fieldname)
	except Exception:
		return default
	return default if value in (None, "") else value


def get_queue_name(fieldname: str, default: str) -> str:
	return str(get_setting_value(fieldname, default) or default).strip() or default


def get_webhook_batch_size(default: int = 100) -> int:
	try:
		value = int(get_setting_value("webhook_batch_size", default) or default)
	except Exception:
		value = default
	return max(1, min(value, 1000))


def get_password(doc, fieldname: str) -> str:
	try:
		if getattr(doc, "get_password", None):
			return doc.get_password(fieldname, raise_exception=False) or ""
	except Exception:
		pass
	return (doc.get(fieldname) or "").strip()


def get_payload_and_headers():
	raw = frappe.request.get_json(silent=True) if frappe.request else None
	headers = dict(frappe.request.headers) if frappe.request else {}
	if isinstance(raw, list) and raw:
		row = raw[0] or {}
		headers.update(row.get("headers") or {})
		return row.get("body") or row, headers, raw
	if isinstance(raw, dict) and "body" in raw and isinstance(raw.get("body"), dict):
		headers.update(raw.get("headers") or {})
		return raw.get("body"), headers, raw
	return raw or {}, headers, raw or {}


def extract_event_name(payload: dict[str, Any], headers: dict[str, Any]) -> str:
	return (
		payload.get("Event")
		or payload.get("event")
		or headers.get("x-vobiz-event")
		or headers.get("X-Vobiz-Event")
		or ""
	)


def extract_request_id(payload: dict[str, Any], headers: dict[str, Any]) -> str:
	return (
		payload.get("RequestID")
		or payload.get("request_id")
		or payload.get("CallUUID")
		or payload.get("call_uuid")
		or headers.get("x-vobiz-request-id")
		or headers.get("X-Vobiz-Request-Id")
		or ""
	)


def extract_call_key(payload: dict[str, Any]) -> str:
	return (
		payload.get("SIPCallID")
		or payload.get("sip_call_id")
		or payload.get("call_uuid")
		or payload.get("CallUUID")
		or payload.get("recording_id")
		or payload.get("transcription_id")
		or payload.get("RequestID")
		or payload.get("request_id")
		or ""
	)


def get_account_id(payload: dict[str, Any]) -> str:
	return payload.get("AccountId") or payload.get("account_id") or ""


def get_trunk_id(payload: dict[str, Any]) -> str:
	return payload.get("TrunkID") or payload.get("trunk_id") or ""


def get_domain(payload: dict[str, Any]) -> str:
	return payload.get("Domain") or payload.get("domain") or ""


def get_from_number(payload: dict[str, Any]) -> str:
	return payload.get("From") or payload.get("from_number") or ""


def get_to_number(payload: dict[str, Any]) -> str:
	return payload.get("To") or payload.get("to_number") or ""


def get_direction(payload: dict[str, Any]) -> str:
	direction = (payload.get("Direction") or payload.get("direction") or "").lower()
	return "Outgoing" if direction in {"outbound", "outgoing"} else "Incoming"


def get_customer_number(payload: dict[str, Any]) -> str:
	direction = get_direction(payload)
	return get_to_number(payload) if direction == "Outgoing" else get_from_number(payload)


def get_agent_number(payload: dict[str, Any]) -> str:
	direction = get_direction(payload)
	return get_from_number(payload) if direction == "Outgoing" else get_to_number(payload)


def map_status(payload: dict[str, Any]) -> str:
	status = (payload.get("Status") or payload.get("status") or payload.get("event") or payload.get("Event") or "").lower()
	reason = (payload.get("Reason") or "").lower()
	if "initiated" in status:
		return "Initiated"
	if "completed" in status or "normal" in reason or "hangup" in status:
		return "Completed"
	if "no answer" in status or "no_answer" in status:
		return "No Answer"
	if "busy" in status:
		return "Busy"
	if "fail" in status:
		return "Failed"
	if "cancel" in status:
		return "Canceled"
	return "In Progress" if status else ""


def find_account_mapping(account_id: str, did_number: str, trunk_id: str, domain: str):
	normalized_did = normalize_phone(did_number)
	fields = [
		"name",
		"account_id",
		"did_number",
		"normalized_did",
		"trunk_id",
		"domain",
		"default_owner",
		"default_team",
		"default_source",
		"default_pipeline",
		"default_platform",
		"medical_department",
	]
	filter_sets = (
		{"active": 1, "account_id": account_id or "", "normalized_did": normalized_did, "trunk_id": trunk_id or ""},
		{"active": 1, "account_id": account_id or "", "normalized_did": normalized_did},
		{"active": 1, "normalized_did": normalized_did},
		{"active": 1, "account_id": account_id or ""},
	)
	for filters in filter_sets:
		if filters.get("normalized_did") == "" and "normalized_did" in filters:
			continue
		if filters.get("account_id") == "" and "account_id" in filters and len(filters) == 2:
			continue
		rows = frappe.get_all(
			"Vobiz Account Mapping",
			filters=filters,
			fields=fields,
			order_by="modified desc",
			limit_page_length=20,
		)
		for row in rows:
			if row.domain and domain and row.domain != domain:
				continue
			return row
	return None


def get_phone_search_fields(doctype: str) -> tuple[str, ...]:
	if doctype == "CRM Lead":
		return ("mobile_no", "phone", "custom_whatsapp_number", "mobile")
	if doctype == "Patient":
		return ("mobile", "phone", "mobile_no", "custom_whatsapp_number")
	return ("mobile", "mobile_no", "phone", "custom_whatsapp_number")


def update_phone_search_fields(doc, method: str | None = None):
	if not frappe.db.has_column(doc.doctype, "vobiz_phone_last10"):
		return
	fields = get_phone_search_fields(doc.doctype)
	if frappe.db.has_column(doc.doctype, "vobiz_mobile_last10"):
		doc.vobiz_mobile_last10 = _last10_from_doc(doc, ("mobile_no", "mobile"))
	if frappe.db.has_column(doc.doctype, "vobiz_phone_last10"):
		doc.vobiz_phone_last10 = _last10_from_doc(doc, ("phone",))
	if frappe.db.has_column(doc.doctype, "vobiz_whatsapp_last10"):
		doc.vobiz_whatsapp_last10 = _last10_from_doc(doc, ("custom_whatsapp_number",))
	for field in fields:
		value = doc.get(field) if hasattr(doc, "get") else None
		normalized = normalize_phone(value)
		if normalized:
			if frappe.db.has_column(doc.doctype, "vobiz_normalized_phone"):
				doc.vobiz_normalized_phone = normalized
			return
	if frappe.db.has_column(doc.doctype, "vobiz_normalized_phone"):
		doc.vobiz_normalized_phone = ""


def _last10_from_doc(doc, fields: tuple[str, ...]) -> str:
	for field in fields:
		value = doc.get(field) if hasattr(doc, "get") else None
		if last10(value):
			return last10(value)
	return ""


def _phone_index_field(source_field: str) -> str | None:
	if source_field in {"mobile", "mobile_no"}:
		return "vobiz_mobile_last10"
	if source_field == "phone":
		return "vobiz_phone_last10"
	if source_field == "custom_whatsapp_number":
		return "vobiz_whatsapp_last10"
	return None


def find_by_phone(doctype: str, fields: tuple[str, ...], number: str) -> str | None:
	key = last10(number)
	if not key:
		return None
	index_conditions = []
	index_values = []
	for field in fields:
		index_field = _phone_index_field(field)
		if index_field and frappe.db.has_column(doctype, index_field):
			index_conditions.append(f"`{index_field}` = %s")
			index_values.append(key)
	if index_conditions:
		rows = frappe.db.sql(
			f"SELECT name FROM `tab{doctype}` WHERE ({' OR '.join(index_conditions)}) ORDER BY modified DESC LIMIT 1",
			index_values,
			as_dict=True,
		)
		return rows[0].name if rows else None
	conditions = []
	values = []
	for field in fields:
		if not frappe.db.has_column(doctype, field):
			continue
		conditions.append(f"REPLACE(REPLACE(REPLACE(REPLACE(`{field}`, '+', ''), ' ', ''), '-', ''), '(', '') LIKE %s")
		values.append(f"%{key}")
	if not conditions:
		return None
	rows = frappe.db.sql(
		f"SELECT name FROM `tab{doctype}` WHERE ({' OR '.join(conditions)}) ORDER BY modified DESC LIMIT 1",
		values,
		as_dict=True,
	)
	return rows[0].name if rows else None


def create_error(process_type: str, message: str, payload=None, exc: Exception | None = None, **links) -> str:
	settings = get_settings() if frappe.db.exists("DocType", "Vobiz AI Settings") else None
	doc = frappe.get_doc(
		{
			"doctype": "Vobiz Error Log",
			"process_type": process_type,
			"status": "Open",
			"severity": "Error",
			"error_message": message[:1000],
			"traceback": traceback.format_exc() if exc else "",
			"payload": as_json(payload),
			"retry_count": 0,
			"max_retry_count": getattr(settings, "max_retry_count", 3) or 3,
			"next_retry_time": add_to_date(now_datetime(), minutes=getattr(settings, "retry_delay_minutes", 10) or 10),
		}
	)
	for key, value in links.items():
		if value:
			doc.set(key, value)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def mark_error_resolved(name: str | None):
	if not name or not frappe.db.exists("Vobiz Error Log", name):
		return
	frappe.db.set_value(
		"Vobiz Error Log",
		name,
		{"status": "Resolved", "resolved": 1},
		update_modified=True,
	)


def add_retry_delay(minutes: int) -> Any:
	return now_datetime() + timedelta(minutes=minutes or 10)
