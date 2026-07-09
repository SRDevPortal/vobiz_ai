from __future__ import annotations

import json
from html import escape
from typing import Any

import frappe
import requests
from frappe.utils import getdate, now_datetime

from vobiz_ai.api.utils import create_error, get_password, get_queue_name, get_settings


ENCOUNTER_ALLOWED_STATUSES = {"Completed", "Connected", "Customer Answered", "Agent Answered", "In Progress"}


def _enabled(settings) -> bool:
	return bool(int(settings.get("auto_create_patient_encounter") or 0))


def maybe_queue_patient_encounter(call_log: str) -> None:
	settings = get_settings()
	if not _enabled(settings):
		return
	if not frappe.db.exists("Vobiz Call Log", call_log):
		return
	doc = frappe.get_doc("Vobiz Call Log", call_log)
	if not _should_create_encounter(doc, settings):
		return
	frappe.db.set_value(
		"Vobiz Call Log",
		call_log,
		{"encounter_creation_status": "Queued", "encounter_review_required": 1},
		update_modified=False,
	)
	frappe.enqueue(
		"vobiz_ai.api.patient_encounter.create_patient_encounter_from_call",
		queue=get_queue_name("ai_queue_name", "vobiz_ai"),
		timeout=300,
		call_log=call_log,
	)


def _should_create_encounter(doc, settings) -> bool:
	if not doc.patient:
		return False
	if doc.patient_encounter:
		return False
	if doc.status and doc.status not in ENCOUNTER_ALLOWED_STATUSES:
		return False
	transcript = doc.transcription_text or doc.transcript_text or ""
	minimum = int(settings.get("minimum_transcript_length_for_encounter") or 120)
	if len(transcript.strip()) < minimum:
		return False
	return frappe.db.exists("DocType", "Patient Encounter")


def _fallback_extract(doc) -> dict[str, Any]:
	transcript = doc.transcription_text or doc.transcript_text or ""
	return {
		"chief_complaint": "",
		"symptoms": [],
		"symptom_duration": "",
		"patient_concerns": doc.ai_concerns or "",
		"advice_given": "",
		"follow_up_recommendation": doc.ai_next_action or "",
		"urgency_level": "",
		"medicines_mentioned": [],
		"appointment_or_callback_required": "",
		"encounter_summary": doc.ai_summary or transcript[:1200],
	}


def _extract_with_openai(doc) -> dict[str, Any]:
	settings = get_settings()
	api_key = get_password(settings, "openai_api_key")
	if not api_key:
		return _fallback_extract(doc)
	model = settings.openai_model or "gpt-4.1-mini"
	transcript = doc.transcription_text or doc.transcript_text or ""
	resp = requests.post(
		"https://api.openai.com/v1/chat/completions",
		headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
		json={
			"model": model,
			"messages": [
				{
					"role": "system",
					"content": (
						"Extract draft clinical encounter notes from a patient-agent call transcript. "
						"Return only JSON with keys: chief_complaint, symptoms, symptom_duration, "
						"patient_concerns, advice_given, follow_up_recommendation, urgency_level, "
						"medicines_mentioned, appointment_or_callback_required, encounter_summary. "
						"Do not invent diagnosis or prescriptions."
					),
				},
				{"role": "user", "content": transcript},
			],
			"temperature": 0.1,
			"response_format": {"type": "json_object"},
		},
		timeout=60,
	)
	resp.raise_for_status()
	return json.loads(resp.json()["choices"][0]["message"]["content"])


def _resolve_practitioner(doc, settings) -> str:
	if settings.get("default_encounter_practitioner"):
		return settings.get("default_encounter_practitioner")
	user = doc.patient_routing_user or doc.followup_routing_user or doc.linked_owner
	if user and frappe.db.has_column("Healthcare Practitioner", "user_id"):
		practitioner = frappe.db.get_value("Healthcare Practitioner", {"user_id": user})
		if practitioner:
			return practitioner
	department = doc.patient_medical_department or doc.medical_department or settings.get("default_encounter_department")
	if department:
		practitioner = frappe.db.get_value("Healthcare Practitioner", {"department": department})
		if practitioner:
			return practitioner
	return frappe.db.get_value("Healthcare Practitioner", {}, "name") or ""


def _resolve_department(doc, practitioner: str, settings) -> str:
	return (
		doc.patient_medical_department
		or doc.medical_department
		or settings.get("default_encounter_department")
		or (frappe.db.get_value("Healthcare Practitioner", practitioner, "department") if practitioner else "")
		or ""
	)


def _clinical_notes_html(call_log: str, extracted: dict[str, Any]) -> str:
	rows = [
		"<h4>AI Draft Encounter Notes</h4>",
		"<p><b>Review required before submission.</b></p>",
		f"<p><b>Vobiz Call Log:</b> {escape(call_log)}</p>",
	]
	for label, key in (
		("Chief Complaint", "chief_complaint"),
		("Symptoms", "symptoms"),
		("Symptom Duration", "symptom_duration"),
		("Patient Concerns", "patient_concerns"),
		("Advice Given", "advice_given"),
		("Follow-up Recommendation", "follow_up_recommendation"),
		("Urgency Level", "urgency_level"),
		("Medicines Mentioned", "medicines_mentioned"),
		("Appointment / Callback", "appointment_or_callback_required"),
		("Summary", "encounter_summary"),
	):
		value = extracted.get(key)
		if isinstance(value, list):
			value = ", ".join(str(item) for item in value if item)
		if value:
			rows.append(f"<p><b>{escape(label)}:</b><br>{escape(str(value))}</p>")
	return "\n".join(rows)


def create_patient_encounter_from_call(call_log: str) -> str | None:
	if not frappe.db.exists("Vobiz Call Log", call_log):
		return None
	doc = frappe.get_doc("Vobiz Call Log", call_log)
	settings = get_settings()
	if not _enabled(settings) or not _should_create_encounter(doc, settings):
		return None

	try:
		extracted = _extract_with_openai(doc)
		practitioner = _resolve_practitioner(doc, settings)
		if not practitioner:
			frappe.throw("Default Encounter Practitioner is required before creating Patient Encounter.")
		department = _resolve_department(doc, practitioner, settings)
		when = doc.start_time or doc.event_timestamp or now_datetime()
		encounter = frappe.new_doc("Patient Encounter")
		encounter.patient = doc.patient
		encounter.practitioner = practitioner
		encounter.encounter_date = getdate(when)
		encounter.encounter_time = getattr(when, "time", lambda: None)()
		if department:
			encounter.medical_department = department
		if frappe.get_meta("Patient Encounter").get_field("encounter_comment"):
			encounter.encounter_comment = f"AI draft from Vobiz Call Log {doc.name}. Review required before submission."[:140]
		if frappe.get_meta("Patient Encounter").get_field("clinical_notes"):
			encounter.clinical_notes = _clinical_notes_html(doc.name, extracted)
		encounter.insert(ignore_permissions=True)

		frappe.db.set_value(
			"Vobiz Call Log",
			doc.name,
			{
				"patient_encounter": encounter.name,
				"encounter_creation_status": "Review Required",
				"encounter_ai_json": json.dumps(extracted, indent=2, ensure_ascii=False, default=str),
				"encounter_error": "",
				"encounter_created_at": now_datetime(),
				"encounter_review_required": 1,
			},
			update_modified=False,
		)
		frappe.db.commit()
		return encounter.name
	except Exception as exc:
		frappe.db.set_value(
			"Vobiz Call Log",
			doc.name,
			{
				"encounter_creation_status": "Failed",
				"encounter_error": str(exc)[:140],
				"encounter_review_required": 1,
			},
			update_modified=False,
		)
		create_error("Patient Encounter Creation", str(exc), payload={"call_log": doc.name}, exc=exc, call_log=doc.name, crm_lead=doc.crm_lead, patient=doc.patient)
		frappe.db.commit()
		raise
