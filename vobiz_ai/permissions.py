from __future__ import annotations

import frappe

from vobiz_ai.api.utils import has_manager_role


def _lead_access_sql(user: str) -> str:
	esc_user = frappe.db.escape(user)
	return f"""
		`tabVobiz Call Log`.crm_lead IN (
			SELECT name FROM `tabCRM Lead`
			WHERE owner = {esc_user}
			   OR lead_owner = {esc_user}
			   OR name IN (
					SELECT reference_name FROM `tabToDo`
					WHERE reference_type='CRM Lead' AND allocated_to={esc_user} AND status='Open'
			   )
			   OR name IN (
					SELECT share_name FROM `tabDocShare`
					WHERE share_doctype='CRM Lead' AND user={esc_user} AND read=1
			   )
		)
	"""


def _patient_access_sql(user: str) -> str:
	esc_user = frappe.db.escape(user)
	return f"""
		`tabVobiz Call Log`.patient IN (
			SELECT name FROM `tabPatient`
			WHERE owner = {esc_user}
			   OR user_id = {esc_user}
			   OR name IN (
					SELECT reference_name FROM `tabToDo`
					WHERE reference_type='Patient' AND allocated_to={esc_user} AND status='Open'
			   )
			   OR name IN (
					SELECT share_name FROM `tabDocShare`
					WHERE share_doctype='Patient' AND user={esc_user} AND read=1
			   )
		)
	"""


def get_call_log_permission_query_conditions(user: str | None = None) -> str:
	user = user or frappe.session.user
	if has_manager_role(user):
		return ""
	esc_user = frappe.db.escape(user)
	return f"(`tabVobiz Call Log`.user = {esc_user} OR ({_lead_access_sql(user)}) OR ({_patient_access_sql(user)}))"


def has_call_log_permission(doc, user: str | None = None, ptype: str | None = None) -> bool:
	user = user or frappe.session.user
	if has_manager_role(user):
		return True
	if getattr(doc, "user", None) == user:
		return True
	if getattr(doc, "crm_lead", None):
		try:
			if frappe.has_permission("CRM Lead", "read", doc=frappe.get_doc("CRM Lead", doc.crm_lead), user=user):
				return True
		except Exception:
			pass
	if getattr(doc, "patient", None):
		try:
			if frappe.has_permission("Patient", "read", doc=frappe.get_doc("Patient", doc.patient), user=user):
				return True
		except Exception:
			pass
	return False


def get_error_log_permission_query_conditions(user: str | None = None) -> str:
	user = user or frappe.session.user
	if has_manager_role(user):
		return ""
	call_cond = get_call_log_permission_query_conditions(user)
	if not call_cond:
		return ""
	return f"`tabVobiz Error Log`.call_log IN (SELECT name FROM `tabVobiz Call Log` WHERE {call_cond})"


def has_error_log_permission(doc, user: str | None = None, ptype: str | None = None) -> bool:
	user = user or frappe.session.user
	if has_manager_role(user):
		return True
	if getattr(doc, "call_log", None):
		call_doc = frappe.get_doc("Vobiz Call Log", doc.call_log)
		return has_call_log_permission(call_doc, user, ptype)
	return False
