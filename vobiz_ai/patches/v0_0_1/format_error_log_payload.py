from __future__ import annotations

import frappe


def execute():
	if frappe.db.exists("DocType", "Vobiz Error Log"):
		frappe.reload_doc("vobiz_ai", "doctype", "vobiz_error_log")
