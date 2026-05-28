from __future__ import annotations

import frappe


def execute():
	if frappe.db.exists("DocType", "Vobiz Call Log"):
		frappe.reload_doc("vobiz_ai", "doctype", "vobiz_call_log")
