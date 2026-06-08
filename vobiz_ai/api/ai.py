from __future__ import annotations

import json

import frappe
import requests
from frappe.rate_limiter import rate_limit

from vobiz_ai.api.utils import create_error, get_password, get_queue_name, get_settings, hash_text, mark_error_resolved


SYSTEM_PROMPT = """You score clinic call transcripts. Return only JSON with:
lead_score integer 0-100, lead_temperature Hot/Warm/Cold, call_summary, intent, concerns, kamal_involved boolean.
Mark kamal_involved true when the transcript shows the AI voice bot Kamal/KAMal talked."""


def _fallback_score(text: str) -> dict:
	lower = (text or "").lower()
	score = 60
	if any(word in lower for word in ("urgent", "pain", "problem", "report", "appointment")):
		score += 15
	if any(word in text for word in ("कब", "दिक्कत", "प्रॉब्लम", "रिपोर्ट")):
		score += 10
	score = min(score, 100)
	temp = "Hot" if score >= 75 else "Warm" if score >= 45 else "Cold"
	return {
		"lead_score": score,
		"lead_temperature": temp,
		"call_summary": (text or "")[:500],
		"intent": "Call transcript received",
		"concerns": "",
		"kamal_involved": "kamal" in lower or "कमल" in text,
	}


def _call_openai(text: str) -> dict:
	settings = get_settings()
	api_key = get_password(settings, "openai_api_key")
	if not api_key:
		return _fallback_score(text)
	model = settings.openai_model or "gpt-4.1-mini"
	resp = requests.post(
		"https://api.openai.com/v1/chat/completions",
		headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
		json={
			"model": model,
			"messages": [
				{"role": "system", "content": SYSTEM_PROMPT},
				{"role": "user", "content": text},
			],
			"temperature": 0.2,
			"response_format": {"type": "json_object"},
		},
		timeout=60,
	)
	resp.raise_for_status()
	content = resp.json()["choices"][0]["message"]["content"]
	return json.loads(content)


def _as_text(value) -> str:
	if value in (None, ""):
		return ""
	if isinstance(value, list):
		return "\n".join(str(row) for row in value if row not in (None, ""))
	if isinstance(value, dict):
		return json.dumps(value, ensure_ascii=False)
	return str(value)


def score_call_log(call_log: str):
	doc = frappe.get_doc("Vobiz Call Log", call_log)
	try:
		if not doc.transcription_text:
			doc.ai_status = "Skipped"
			doc.save(ignore_permissions=True)
			return
		current_hash = hash_text(doc.transcription_text)
		if doc.ai_status == "Scored" and doc.transcript_hash == current_hash:
			return
		result = _call_openai(doc.transcription_text)
		doc.transcript_hash = current_hash
		doc.lead_score = int(result.get("lead_score") or 0)
		doc.lead_temperature = result.get("lead_temperature") or ""
		doc.ai_summary = _as_text(result.get("call_summary"))
		doc.ai_intent = _as_text(result.get("intent"))
		doc.ai_concerns = _as_text(result.get("concerns"))
		doc.kamal_involved = 1 if result.get("kamal_involved") else 0
		doc.ai_status = "Scored"
		doc.last_error = ""
		doc.save(ignore_permissions=True)
		from vobiz_ai.api.processing import _sync_linked_summaries

		_sync_linked_summaries(doc)
	except Exception as exc:
		frappe.db.set_value("Vobiz Call Log", call_log, {"ai_status": "Failed", "last_error": str(exc)})
		create_error("AI Scoring", str(exc), payload={"call_log": call_log}, exc=exc, call_log=call_log, crm_lead=doc.crm_lead, patient=doc.patient)
		raise


@frappe.whitelist()
@rate_limit(limit=10, seconds=60)
def retry_score(call_log: str):
	if not frappe.has_permission("Vobiz Call Log", "write", call_log):
		frappe.throw("Not permitted", frappe.PermissionError)
	frappe.enqueue("vobiz_ai.api.ai.score_call_log", queue=get_queue_name("ai_queue_name", "vobiz_ai"), call_log=call_log)
	return {"status": "queued"}
