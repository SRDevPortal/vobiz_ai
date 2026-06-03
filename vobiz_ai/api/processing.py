from __future__ import annotations

from html import escape

import frappe

from vobiz_ai.api import ai
from vobiz_ai.api.utils import (
	as_json,
	create_error,
	extract_call_key,
	extract_event_name,
	extract_request_id,
	find_account_mapping,
	find_by_phone,
	get_account_id,
	get_direction,
	get_domain,
	get_from_number,
	get_settings,
	get_to_number,
	get_trunk_id,
	hash_text,
	last10,
	map_status,
	normalize_phone,
	parse_json,
	parse_dt,
	seconds_to_duration,
)
try:
	from vobiz_click_to_call.api.recording import recording_proxy_url
except ImportError:
	def recording_proxy_url(call_log: str) -> str:
		return frappe.db.get_value("Vobiz Call Log", call_log, "recording_url") or ""

WEBHOOK_BATCH_SIZE = 10


def process_payload(payload: dict, webhook_event: str | None = None) -> str:
	call_key = extract_call_key(payload)
	if not call_key:
		frappe.throw("Vobiz payload has no call identifier")

	call = _get_or_create_call(call_key)
	is_new_call = call.is_new()
	_update_call_from_payload(call, payload)
	_resolve_mapping(call, payload)
	_link_lead_and_patient(call)
	call.raw_payload = as_json(payload)
	call.save(ignore_permissions=True)
	_create_or_update_patient_issue(call)
	_sync_linked_summaries(call, is_new_call=is_new_call)
	_maybe_queue_ai(call)
	return call.name


def process_webhook_event(webhook_event: str) -> str | None:
	if not frappe.db.exists("Vobiz Webhook Event", webhook_event):
		return None

	event = frappe.get_doc("Vobiz Webhook Event", webhook_event)
	if event.status == "Processed" and event.call_log:
		return event.call_log

	event.status = "Processing"
	event.save(ignore_permissions=True)
	frappe.db.commit()

	raw = parse_json(event.payload or "{}")
	payload = raw
	if isinstance(raw, list) and raw:
		payload = raw[0].get("body") or raw[0]
	elif isinstance(raw, dict) and "body" in raw and isinstance(raw.get("body"), dict):
		payload = raw.get("body")

	try:
		call_log = process_payload(payload or {}, event.name)
		event.status = "Processed"
		event.call_log = call_log
		event.save(ignore_permissions=True)
		frappe.db.commit()
		return call_log
	except Exception as exc:
		error_log = create_error("Webhook", str(exc), payload=raw, exc=exc, webhook_event=event.name)
		event.status = "Failed"
		event.error_log = error_log
		event.save(ignore_permissions=True)
		frappe.db.commit()
		raise


def enqueue_queued_webhook_events(batch_size: int = WEBHOOK_BATCH_SIZE) -> dict:
	events = frappe.get_all(
		"Vobiz Webhook Event",
		filters={"status": "Queued"},
		fields=["name"],
		order_by="received_at asc, creation asc",
		limit=max(1, min(int(batch_size or WEBHOOK_BATCH_SIZE), WEBHOOK_BATCH_SIZE)),
	)
	for row in events:
		frappe.enqueue(
			"vobiz_ai.api.processing.process_webhook_event",
			queue="short",
			timeout=300,
			webhook_event=row.name,
		)
	return {"queued": len(events), "batch_size": WEBHOOK_BATCH_SIZE}


def _get_or_create_call(call_key: str):
	if frappe.db.exists("Vobiz Call Log", call_key):
		return frappe.get_doc("Vobiz Call Log", call_key)
	return frappe.get_doc({"doctype": "Vobiz Call Log", "call_key": call_key})


def _update_call_from_payload(call, payload: dict):
	event = extract_event_name(payload, {})
	from_number = get_from_number(payload)
	to_number = get_to_number(payload)
	direction = _infer_direction(call, payload, from_number, to_number)
	customer_number = to_number if direction == "Outgoing" else from_number
	agent_number = from_number if direction == "Outgoing" else to_number

	call.event = event
	call.call_uuid = payload.get("CallUUID") or payload.get("call_uuid") or call.call_uuid
	call.sip_call_id = payload.get("SIPCallID") or call.sip_call_id
	call.request_id = extract_request_id(payload, {}) or call.request_id
	call.status = map_status(payload) or call.status
	call.direction = direction or call.direction
	call.account_id = get_account_id(payload) or call.account_id
	call.trunk_id = get_trunk_id(payload) or call.trunk_id
	call.domain = get_domain(payload) or call.domain
	call.did_number = agent_number or call.did_number
	call.normalized_did = normalize_phone(agent_number) or call.normalized_did
	call.from_number = from_number or call.from_number
	call.to_number = to_number or call.to_number
	call.customer_number = customer_number or call.customer_number
	call.normalized_customer_number = normalize_phone(customer_number) or call.normalized_customer_number
	call.agent_number = agent_number or call.agent_number
	call.event_timestamp = parse_dt(payload.get("Timestamp") or payload.get("timestamp")) or call.event_timestamp
	call.start_time = parse_dt(payload.get("StartTime")) or call.start_time or call.event_timestamp
	call.end_time = parse_dt(payload.get("EndTime")) or call.end_time
	call.duration = seconds_to_duration(payload.get("Duration")) or call.duration
	call.billsec = seconds_to_duration(payload.get("Billsec")) or call.billsec
	call.ring_time = seconds_to_duration(payload.get("RingTime")) or call.ring_time
	call.cost = payload.get("Cost") or call.cost
	call.currency = payload.get("Currency") or call.currency

	if payload.get("recording_url"):
		call.recording_url = payload.get("recording_url")
		call.recording_channel = payload.get("recording_channel")
		call.recording_duration_ms = seconds_to_duration(payload.get("recording_duration_ms"))
		call.recording_file_size = str(payload.get("recording_file_size") or "")
	if payload.get("transcription_text"):
		new_hash = hash_text(payload.get("transcription_text"))
		call.transcription_text = payload.get("transcription_text")
		call.transcription_duration_sec = seconds_to_duration(payload.get("transcription_duration_sec"))
		call.sentiment = payload.get("sentiment")
		if call.transcript_hash != new_hash or call.ai_status not in ("Queued", "Scored"):
			call.ai_status = "Pending"
		call.transcript_hash = new_hash


def _infer_direction(call, payload: dict, from_number: str, to_number: str) -> str:
	if payload.get("Direction") or payload.get("direction"):
		return get_direction(payload)

	account_id = get_account_id(payload) or call.account_id
	trunk_id = get_trunk_id(payload) or call.trunk_id
	domain = get_domain(payload) or call.domain
	from_is_did = bool(find_account_mapping(account_id, from_number, trunk_id, domain))
	to_is_did = bool(find_account_mapping(account_id, to_number, trunk_id, domain))
	if from_is_did and not to_is_did:
		return "Outgoing"
	if to_is_did and not from_is_did:
		return "Incoming"

	from_patient = bool(find_by_phone("Patient", ("mobile", "phone"), from_number))
	to_patient = bool(find_by_phone("Patient", ("mobile", "phone"), to_number))
	if from_patient and not to_patient:
		return "Incoming"
	if to_patient and not from_patient:
		return "Outgoing"

	from_lead = bool(find_by_phone("CRM Lead", ("mobile_no", "phone"), from_number))
	to_lead = bool(find_by_phone("CRM Lead", ("mobile_no", "phone"), to_number))
	if from_lead and not to_lead:
		return "Incoming"
	if to_lead and not from_lead:
		return "Outgoing"

	return call.direction or "Incoming"


def _resolve_mapping(call, payload: dict):
	mapping = find_account_mapping(call.account_id, call.did_number, call.trunk_id, call.domain)
	if not mapping:
		return
	call.account_mapping = mapping.name
	call.linked_owner = mapping.default_owner or call.linked_owner
	call.medical_department = mapping.medical_department or call.medical_department


def _lead_defaults(call):
	settings = get_settings()
	mapping = frappe.get_doc("Vobiz Account Mapping", call.account_mapping) if call.account_mapping else None
	return {
		"source": getattr(mapping, "default_source", None) or getattr(settings, "default_lead_source", None),
		"status": getattr(settings, "default_lead_status", None) or _first_doc("CRM Lead Status", {"type": "Open"}) or _first_doc("CRM Lead Status"),
		"lead_owner": getattr(mapping, "default_owner", None) or getattr(settings, "default_lead_owner", None),
		"pipeline": getattr(mapping, "default_pipeline", None) or getattr(settings, "default_pipeline", None) or _first_doc("SR Lead Pipeline"),
		"platform": getattr(mapping, "default_platform", None) or getattr(settings, "default_platform", None) or _first_doc("SR Lead Platform"),
		"medical_department": getattr(mapping, "medical_department", None) or getattr(settings, "default_medical_department", None),
		"name_format": getattr(settings, "default_lead_name_format", None) or "Vobiz Call {customer_number}",
	}


def _first_doc(doctype: str, filters: dict | None = None) -> str | None:
	if not frappe.db.exists("DocType", doctype):
		return None
	rows = frappe.get_all(doctype, filters=filters or {}, pluck="name", limit=1)
	return rows[0] if rows else None


def _link_lead_and_patient(call):
	customer = call.customer_number
	patient = find_by_phone("Patient", ("mobile", "phone"), customer)
	lead = find_by_phone("CRM Lead", ("mobile_no", "phone"), customer)

	if patient:
		call.patient = patient
		call.caller_classification = "Patient"
		if not lead:
			lead = find_by_phone("CRM Lead", ("mobile_no", "phone"), frappe.db.get_value("Patient", patient, "mobile"))
	elif lead:
		call.caller_classification = "Old Lead"
	else:
		call.caller_classification = "New Lead"
		if getattr(get_settings(), "create_lead_on_all_calls", 1):
			lead = _create_lead(call)

	if lead:
		call.crm_lead = lead
		call.linked_owner = frappe.db.get_value("CRM Lead", lead, "lead_owner") or call.linked_owner
	if patient:
		owner = frappe.db.get_value("Patient", patient, "owner")
		call.linked_owner = call.linked_owner or owner


def _create_lead(call) -> str:
	existing = find_by_phone("CRM Lead", ("mobile_no", "phone"), call.customer_number)
	if existing:
		return existing

	defaults = _lead_defaults(call)
	meta = frappe.get_meta("CRM Lead")
	fields = {df.fieldname for df in meta.fields}
	customer = call.customer_number or call.normalized_customer_number
	first_name = defaults["name_format"].format(customer_number=customer, did_number=call.did_number or "")
	lead = frappe.new_doc("CRM Lead")
	if "first_name" in fields:
		lead.first_name = first_name[:140]
	if "lead_name" in fields:
		lead.lead_name = first_name[:140]
	if "mobile_no" in fields:
		lead.mobile_no = customer
	if "phone" in fields:
		lead.phone = customer
	if "source" in fields and defaults["source"]:
		lead.source = defaults["source"]
	if "status" in fields and defaults["status"]:
		lead.status = defaults["status"]
	if "lead_owner" in fields and defaults["lead_owner"]:
		lead.lead_owner = defaults["lead_owner"]
	if "sr_lead_pipeline" in fields and defaults["pipeline"]:
		lead.sr_lead_pipeline = defaults["pipeline"]
	if "sr_lead_platform" in fields and defaults["platform"]:
		lead.sr_lead_platform = defaults["platform"]
	if "sr_lead_department" in fields and defaults["medical_department"]:
		lead.sr_lead_department = defaults["medical_department"]
	lead.insert(ignore_permissions=True)
	return lead.name


def _sync_linked_summaries(call, is_new_call: bool = False):
	values = {
		"vobiz_latest_call_log": call.name,
		"vobiz_latest_call_time": call.start_time or call.event_timestamp,
		"vobiz_latest_query_time": call.start_time or call.event_timestamp,
		"vobiz_last_call_status": call.status,
		"vobiz_last_call_event": call.event,
		"vobiz_call_direction": call.direction,
		"vobiz_call_duration": call.duration,
		"vobiz_caller_classification": call.caller_classification,
		"vobiz_kamal_involved": call.kamal_involved,
		"vobiz_recording_url": call.recording_url,
		"vobiz_transcription_text": call.transcription_text or call.transcript_text,
		"vobiz_ai_summary": call.ai_summary,
		"vobiz_ai_intent": call.ai_intent,
		"vobiz_ai_concerns": call.ai_concerns,
	}
	lead_values = {
		"lead_temperature": call.lead_temperature,
		"lead_score": call.lead_score,
		"lead_lan": _detect_transcript_language(call.transcription_text),
	}
	patient_values = {
		"vobiz_call_indicator": _get_patient_hit_indicator(call.patient),
		"vobiz_lead_temperature": call.lead_temperature,
		"vobiz_lead_score": call.lead_score,
		"vobiz_lead_language": _detect_transcript_language(call.transcription_text),
		"vobiz_call_count": _get_patient_call_count(call.patient),
	}
	for doctype, name in (("CRM Lead", call.crm_lead), ("Patient", call.patient)):
		if not name:
			continue
		meta = frappe.get_meta(doctype)
		update = {k: v for k, v in values.items() if meta.get_field(k) and v not in (None, "")}
		if doctype == "CRM Lead":
			update.update({k: v for k, v in lead_values.items() if meta.get_field(k) and v not in (None, "")})
			update.update(_lead_call_counts(name, meta))
			if is_new_call and meta.get_field("vobiz_call_alert_count"):
				seen = frappe.db.get_value("CRM Lead", name, "vobiz_calls_seen_at") if meta.get_field("vobiz_calls_seen_at") else None
				if seen:
					update["vobiz_call_alert_count"] = frappe.db.count(
						"Vobiz Call Log",
						{"crm_lead": name, "creation": [">", seen]},
					)
				else:
					recent_cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-90)
					update["vobiz_call_alert_count"] = frappe.db.count(
						"Vobiz Call Log",
						{"crm_lead": name, "creation": [">", recent_cutoff]},
					)
		else:
			update.update({k: v for k, v in patient_values.items() if meta.get_field(k) and v not in (None, "")})
		if update:
			frappe.db.set_value(doctype, name, update, update_modified=False)


def _lead_call_counts(lead: str, meta) -> dict:
	if not lead:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT
			COUNT(*) AS total,
			SUM(CASE WHEN status IN ('Completed', 'Connected', 'Customer Answered', 'Agent Answered', 'In Progress') THEN 1 ELSE 0 END) AS connected,
			SUM(CASE WHEN status IN ('No Answer', 'Busy', 'Failed', 'Canceled', 'Cancelled') THEN 1 ELSE 0 END) AS missed
		FROM `tabVobiz Call Log`
		WHERE crm_lead = %s
		""",
		(lead,),
		as_dict=True,
	)
	row = rows[0] if rows else {}
	values = {
		"vobiz_total_call_attempts": int(row.get("total") or 0),
		"vobiz_connected_call_count": int(row.get("connected") or 0),
		"vobiz_missed_call_count": int(row.get("missed") or 0),
	}
	return {key: value for key, value in values.items() if meta.get_field(key)}


def _detect_transcript_language(text: str | None) -> str:
	if not text:
		return ""
	if any("\u0900" <= char <= "\u097f" for char in text):
		if any(("a" <= char.lower() <= "z") for char in text):
			return "Hindi/English"
		return "Hindi"
	return "English"


def _get_patient_call_count(patient: str | None) -> int | None:
	if not patient:
		return None
	return frappe.db.count("Vobiz Call Log", {"patient": patient})


def _get_patient_hit_indicator(patient: str | None) -> str:
	count = _get_patient_call_count(patient) or 0
	return f"New Vobiz Hit ({count})" if count else ""


def _create_or_update_patient_issue(call):
	if not call.patient:
		return
	if not frappe.db.exists("DocType", "Issue"):
		return
	try:
		issue_name = call.issue or _find_issue_for_call(call.name)
		if issue_name:
			issue = frappe.get_doc("Issue", issue_name)
		else:
			issue = frappe.new_doc("Issue")
			issue.subject = _issue_subject(call)
			if frappe.get_meta("Issue").get_field("status"):
				issue.status = "Open"

		_apply_issue_values(issue, call)
		if issue.is_new():
			issue.insert(ignore_permissions=True)
		else:
			issue.save(ignore_permissions=True)
		if call.issue != issue.name:
			call.issue = issue.name
			call.save(ignore_permissions=True)
	except Exception as exc:
		create_error("Patient Issue Creation", str(exc), payload={"call_log": call.name}, exc=exc, call_log=call.name, crm_lead=call.crm_lead, patient=call.patient)


def _find_issue_for_call(call_log: str) -> str | None:
	if not frappe.db.has_column("Issue", "vobiz_call_log"):
		return None
	return frappe.db.get_value("Issue", {"vobiz_call_log": call_log}, "name")


def _apply_issue_values(issue, call):
	meta = frappe.get_meta("Issue")
	values = {
		"vobiz_call_log": call.name,
		"vobiz_patient": call.patient,
		"vobiz_crm_lead": call.crm_lead,
		"vobiz_call_time": call.start_time or call.event_timestamp,
		"vobiz_call_direction": call.direction,
		"vobiz_call_status": call.status,
		"vobiz_lead_temperature": call.lead_temperature,
		"vobiz_lead_score": call.lead_score,
	}
	for fieldname, value in values.items():
		if meta.get_field(fieldname):
			issue.set(fieldname, value)
	if meta.get_field("description"):
		issue.description = _issue_description(call)
	if meta.get_field("raised_by") and not issue.get("raised_by"):
		issue.raised_by = _patient_email(call.patient)


def _issue_subject(call) -> str:
	patient_name = frappe.db.get_value("Patient", call.patient, "patient_name") or call.patient
	when = call.start_time or call.event_timestamp
	when_text = when.strftime("%d-%m-%Y %H:%M") if hasattr(when, "strftime") else ""
	return f"Vobiz call from patient {patient_name} {when_text}".strip()[:140]


def _issue_description(call) -> str:
	rows = [
		"<h4>Vobiz Patient Call</h4>",
		"<ul>",
		f"<li><b>Call Log:</b> {escape(call.name)}</li>",
		f"<li><b>Patient:</b> {escape(call.patient or '')}</li>",
		f"<li><b>CRM Lead:</b> {escape(call.crm_lead or '')}</li>",
		f"<li><b>Direction:</b> {escape(call.direction or '')}</li>",
		f"<li><b>Status:</b> {escape(call.status or '')}</li>",
		f"<li><b>Customer Number:</b> {escape(call.customer_number or '')}</li>",
		f"<li><b>Agent / Business Number:</b> {escape(call.agent_number or '')}</li>",
		f"<li><b>DID:</b> {escape(call.did_number or '')}</li>",
		f"<li><b>Start Time:</b> {escape(str(call.start_time or call.event_timestamp or ''))}</li>",
		f"<li><b>Duration:</b> {escape(str(call.duration or ''))}</li>",
		f"<li><b>Lead Temperature:</b> {escape(call.lead_temperature or '')}</li>",
		f"<li><b>Lead Score:</b> {escape(str(call.lead_score or ''))}</li>",
		f"<li><b>Kamal Involved:</b> {'Yes' if call.kamal_involved else 'No'}</li>",
		"</ul>",
	]
	if call.recording_url:
		url = escape(call.recording_url)
		rows.append(f'<p><b>Recording:</b> <a href="{url}" target="_blank">{url}</a></p>')
	if call.ai_summary:
		rows.append(f"<p><b>AI Summary:</b><br>{escape(call.ai_summary)}</p>")
	if call.ai_intent:
		rows.append(f"<p><b>Intent:</b><br>{escape(call.ai_intent)}</p>")
	if call.ai_concerns:
		rows.append(f"<p><b>Concerns:</b><br>{escape(call.ai_concerns)}</p>")
	if call.transcription_text:
		rows.append(f"<p><b>Transcript:</b><br>{escape(call.transcription_text)}</p>")
	return "\n".join(rows)


def _patient_email(patient: str | None) -> str:
	if not patient:
		return ""
	for fieldname in ("email", "email_id"):
		if frappe.db.has_column("Patient", fieldname):
			value = frappe.db.get_value("Patient", patient, fieldname)
			if value:
				return value
	return ""


def _maybe_queue_ai(call):
	if not call.transcription_text:
		return
	if not getattr(get_settings(), "score_transcripts", 1):
		return
	if call.ai_status == "Queued":
		return
	if call.ai_status == "Scored" and call.transcript_hash == hash_text(call.transcription_text):
		return
	call.ai_status = "Queued"
	call.save(ignore_permissions=True)
	frappe.enqueue("vobiz_ai.api.ai.score_call_log", queue="short", call_log=call.name)


@frappe.whitelist()
def get_related_calls(doctype: str, name: str):
	if doctype not in ("CRM Lead", "Patient"):
		frappe.throw("Unsupported doctype")
	filters = {"crm_lead": name} if doctype == "CRM Lead" else {"patient": name}
	rows = frappe.get_all(
		"Vobiz Call Log",
		filters=filters,
		fields=[
			"name",
			"start_time",
			"direction",
			"status",
			"customer_number",
			"did_number",
			"caller_classification",
			"lead_temperature",
			"lead_score",
			"kamal_involved",
			"recording_url",
			"transcription_text",
			"transcript_text",
			"ai_summary",
			"ai_intent",
		],
		order_by="start_time desc, modified desc",
		limit_page_length=50,
	)
	for row in rows:
		row["recording_download_url"] = recording_proxy_url(row.name) if row.get("recording_url") else ""
	return rows
