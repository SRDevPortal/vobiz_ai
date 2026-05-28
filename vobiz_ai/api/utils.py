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
	return "Outgoing" if direction == "outbound" else "Incoming"


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
	rows = frappe.get_all(
		"Vobiz Account Mapping",
		filters={"active": 1},
		fields=[
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
		],
		limit_page_length=500,
	)

	def match(row, include_account=True, include_did=True, include_trunk=False):
		if include_account and (row.account_id or "") != (account_id or ""):
			return False
		if include_did and (row.normalized_did or normalize_phone(row.did_number)) != normalized_did:
			return False
		if include_trunk and (row.trunk_id or "") != (trunk_id or ""):
			return False
		if row.domain and domain and row.domain != domain:
			return False
		return True

	for include_account, include_did, include_trunk in (
		(True, True, True),
		(True, True, False),
		(False, True, False),
		(True, False, False),
	):
		for row in rows:
			if match(row, include_account, include_did, include_trunk):
				return row
	return None


def find_by_phone(doctype: str, fields: tuple[str, ...], number: str) -> str | None:
	key = last10(number)
	if not key:
		return None
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
