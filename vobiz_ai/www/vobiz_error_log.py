from __future__ import annotations

import frappe
from rq.job import Job
from rq.registry import FailedJobRegistry, StartedJobRegistry

from frappe.utils.background_jobs import get_queue, get_workers
from vobiz_ai.api.utils import get_queue_name, has_manager_role


no_cache = 1


def get_context(context):
	if not has_manager_role():
		frappe.throw("Only System Manager or Vobiz AI Manager can view Vobiz worker errors", frappe.PermissionError)

	context.no_cache = 1
	context.show_sidebar = False
	context.title = "Vobiz Error Log"
	context.queue_names = _queue_names()
	context.queue_stats = [_queue_status(queue_name) for queue_name in context.queue_names]
	context.failed_jobs = _failed_jobs(context.queue_names)
	context.running_jobs = _running_jobs(context.queue_names)
	context.error_logs = _error_logs()
	context.webhook_events = _webhook_events()
	context.generated_at = frappe.utils.now_datetime()


def _queue_names() -> list[str]:
	return [
		get_queue_name("webhook_queue_name", "vobiz_webhook"),
		get_queue_name("ai_queue_name", "vobiz_ai"),
		get_queue_name("livekit_queue_name", "vobiz_livekit"),
	]


def _queue_status(queue_name: str) -> dict:
	row = {
		"name": queue_name,
		"available": False,
		"queued": 0,
		"started": 0,
		"failed": 0,
		"workers": 0,
		"error": "",
	}
	try:
		queue = get_queue(queue_name)
		row["available"] = True
		row["queued"] = len(queue)
		row["started"] = len(StartedJobRegistry(queue=queue).get_job_ids())
		row["failed"] = len(FailedJobRegistry(queue=queue).get_job_ids())
		row["workers"] = len(get_workers(queue))
	except Exception as exc:
		row["error"] = str(exc)
	return row


def _failed_jobs(queue_names: list[str], limit: int = 25) -> list[dict]:
	out = []
	for queue_name in queue_names:
		try:
			queue = get_queue(queue_name)
			registry = FailedJobRegistry(queue=queue)
			for job_id in registry.get_job_ids()[:limit]:
				out.append(_job_row(queue_name, job_id, queue.connection))
		except Exception as exc:
			out.append({"queue": queue_name, "id": "", "status": "error", "error": str(exc)})
	return out[:limit]


def _running_jobs(queue_names: list[str], limit: int = 25) -> list[dict]:
	out = []
	for queue_name in queue_names:
		try:
			queue = get_queue(queue_name)
			registry = StartedJobRegistry(queue=queue)
			for job_id in registry.get_job_ids()[:limit]:
				out.append(_job_row(queue_name, job_id, queue.connection))
		except Exception as exc:
			out.append({"queue": queue_name, "id": "", "status": "error", "error": str(exc)})
	return out[:limit]


def _job_row(queue_name: str, job_id: str, connection) -> dict:
	try:
		job = Job.fetch(job_id, connection=connection)
		return {
			"queue": queue_name,
			"id": job.id,
			"status": job.get_status(refresh=False),
			"func_name": job.func_name,
			"description": job.description,
			"origin": job.origin,
			"site": (job.kwargs or {}).get("site") or (job.kwargs or {}).get("kwargs", {}).get("site") or "",
			"created_at": job.created_at,
			"enqueued_at": job.enqueued_at,
			"started_at": job.started_at,
			"ended_at": job.ended_at,
			"exc_info": job.exc_info or "",
			"error": "",
		}
	except Exception as exc:
		return {"queue": queue_name, "id": job_id, "status": "missing", "error": str(exc)}


def _error_logs(limit: int = 100) -> list[dict]:
	if not frappe.db.exists("DocType", "Vobiz Error Log"):
		return []
	return frappe.get_all(
		"Vobiz Error Log",
		fields=[
			"name",
			"creation",
			"modified",
			"process_type",
			"status",
			"severity",
			"resolved",
			"retry_count",
			"max_retry_count",
			"last_retry_time",
			"next_retry_time",
			"webhook_event",
			"call_log",
			"crm_lead",
			"patient",
			"error_message",
			"traceback",
			"payload",
		],
		order_by="modified desc",
		limit_page_length=limit,
	)


def _webhook_events(limit: int = 50) -> list[dict]:
	if not frappe.db.exists("DocType", "Vobiz Webhook Event"):
		return []
	return frappe.get_all(
		"Vobiz Webhook Event",
		filters={"status": ["in", ["Queued", "Processing", "Failed"]]},
		fields=["name", "creation", "modified", "event_type", "status", "call_key", "request_id", "call_log", "error_log"],
		order_by="modified desc",
		limit_page_length=limit,
	)
