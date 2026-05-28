from __future__ import annotations

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


MODULE = "Vobiz AI"


def after_install():
    setup()


def after_migrate():
    setup()


def setup():
    ensure_module_def()
    ensure_roles()
    ensure_custom_fields()
    remove_duplicate_crm_lead_score_fields()
    ensure_workspace()


def ensure_module_def():
    if not frappe.db.exists("Module Def", MODULE):
        frappe.get_doc({"doctype": "Module Def", "module_name": MODULE, "app_name": "vobiz_ai"}).insert(
            ignore_permissions=True
        )


def ensure_roles():
    if not frappe.db.exists("Role", "Vobiz AI Manager"):
        frappe.get_doc({"doctype": "Role", "role_name": "Vobiz AI Manager", "desk_access": 1}).insert(
            ignore_permissions=True
        )


def _field(fieldname, label, fieldtype, **kwargs):
    row = {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "module": MODULE}
    row.update(kwargs)
    return row


def ensure_custom_fields():
    fields = {}
    if frappe.db.exists("DocType", "CRM Lead"):
        fields["CRM Lead"] = [
            _field("vobiz_ai_tab", "Vobiz Calls", "Tab Break", insert_after="lost_notes"),
            _field("vobiz_ai_summary_sb", "Vobiz Summary", "Section Break", insert_after="vobiz_ai_tab"),
            _field("vobiz_latest_call_time", "Latest Vobiz Call Time", "Datetime", read_only=1, insert_after="vobiz_ai_summary_sb"),
            _field("vobiz_last_call_status", "Last Vobiz Call Status", "Data", read_only=1, insert_after="vobiz_latest_call_time"),
            _field("vobiz_caller_classification", "Vobiz Caller Type", "Select", options="\nNew Lead\nOld Lead\nPatient", read_only=1, insert_after="vobiz_last_call_status"),
            _field("vobiz_summary_cb", "", "Column Break", insert_after="vobiz_caller_classification"),
            _field("vobiz_kamal_involved", "Kamal Involved", "Check", read_only=1, insert_after="vobiz_summary_cb"),
            _field("vobiz_ai_calls_html", "Vobiz Call History", "HTML", insert_after="vobiz_kamal_involved"),
        ]
    if frappe.db.exists("DocType", "Patient"):
        fields["Patient"] = [
            _field("vobiz_ai_tab", "Vobiz Calls", "Tab Break", insert_after="medical_history_tab"),
            _field("vobiz_ai_summary_sb", "Vobiz Summary", "Section Break", insert_after="vobiz_ai_tab"),
            _field("vobiz_call_indicator", "Vobiz", "Data", read_only=1, in_list_view=1, in_standard_filter=1, insert_after="vobiz_ai_summary_sb"),
            _field("vobiz_latest_call_time", "Latest Vobiz Call Time", "Datetime", read_only=1, in_list_view=1, in_standard_filter=1, insert_after="vobiz_call_indicator"),
            _field("vobiz_last_call_status", "Last Vobiz Call Status", "Data", read_only=1, in_standard_filter=1, insert_after="vobiz_latest_call_time"),
            _field("vobiz_caller_classification", "Vobiz Caller Type", "Select", options="\nNew Lead\nOld Lead\nPatient", read_only=1, in_standard_filter=1, insert_after="vobiz_last_call_status"),
            _field("vobiz_summary_cb", "", "Column Break", insert_after="vobiz_caller_classification"),
            _field("vobiz_lead_temperature", "Vobiz Lead Temperature", "Select", options="\nHot\nWarm\nCold", read_only=1, in_list_view=1, in_standard_filter=1, insert_after="vobiz_summary_cb"),
            _field("vobiz_lead_score", "Vobiz Lead Score", "Int", read_only=1, in_list_view=1, in_standard_filter=1, insert_after="vobiz_lead_temperature"),
            _field("vobiz_lead_language", "Vobiz Language", "Data", read_only=1, in_list_view=1, in_standard_filter=1, insert_after="vobiz_lead_score"),
            _field("vobiz_call_count", "Vobiz Call Count", "Int", read_only=1, in_list_view=1, in_standard_filter=1, insert_after="vobiz_lead_language"),
            _field("vobiz_kamal_involved", "Kamal Involved", "Check", read_only=1, in_standard_filter=1, insert_after="vobiz_call_count"),
            _field("vobiz_ai_calls_html", "Vobiz Call History", "HTML", insert_after="vobiz_kamal_involved"),
        ]
    if frappe.db.exists("DocType", "Issue"):
        fields["Issue"] = [
            _field("vobiz_issue_section", "Vobiz Call", "Section Break", insert_after="description"),
            _field("vobiz_call_log", "Vobiz Call Log", "Link", options="Vobiz Call Log", read_only=1, in_list_view=1, in_standard_filter=1, insert_after="vobiz_issue_section"),
            _field("vobiz_patient", "Vobiz Patient", "Link", options="Patient", read_only=1, in_list_view=1, in_standard_filter=1, insert_after="vobiz_call_log"),
            _field("vobiz_crm_lead", "Vobiz CRM Lead", "Link", options="CRM Lead", read_only=1, in_standard_filter=1, insert_after="vobiz_patient"),
            _field("vobiz_call_time", "Vobiz Call Time", "Datetime", read_only=1, in_standard_filter=1, insert_after="vobiz_crm_lead"),
            _field("vobiz_call_direction", "Vobiz Direction", "Data", read_only=1, in_standard_filter=1, insert_after="vobiz_call_time"),
            _field("vobiz_call_status", "Vobiz Call Status", "Data", read_only=1, in_standard_filter=1, insert_after="vobiz_call_direction"),
            _field("vobiz_lead_temperature", "Vobiz Lead Temperature", "Data", read_only=1, in_standard_filter=1, insert_after="vobiz_call_status"),
            _field("vobiz_lead_score", "Vobiz Lead Score", "Int", read_only=1, in_standard_filter=1, insert_after="vobiz_lead_temperature"),
        ]
    if fields:
        create_custom_fields(fields, update=True, ignore_validate=True)


def remove_duplicate_crm_lead_score_fields():
    if not frappe.db.exists("DocType", "CRM Lead"):
        return
    for fieldname in ("vobiz_lead_temperature", "vobiz_lead_score"):
        name = frappe.db.get_value("Custom Field", {"dt": "CRM Lead", "fieldname": fieldname})
        if name:
            frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
    frappe.clear_cache(doctype="CRM Lead")


def ensure_workspace():
    shortcuts = [
        {"type": "DocType", "label": "Call Logs", "link_to": "Vobiz Call Log", "doc_view": "List", "icon": "phone"},
        {"type": "DocType", "label": "Account Mapping", "link_to": "Vobiz Account Mapping", "doc_view": "List", "icon": "settings"},
        {"type": "DocType", "label": "Error Logs", "link_to": "Vobiz Error Log", "doc_view": "List", "icon": "alert-triangle"},
        {"type": "DocType", "label": "Settings", "link_to": "Vobiz AI Settings", "doc_view": "List", "icon": "sliders"},
        {"type": "DocType", "label": "Webhook Events", "link_to": "Vobiz Webhook Event", "doc_view": "List", "icon": "activity"},
    ]
    links = [
        {"type": "Link", "label": "Call Logs", "link_to": "Vobiz Call Log", "link_type": "DocType"},
        {"type": "Link", "label": "Account Mapping", "link_to": "Vobiz Account Mapping", "link_type": "DocType"},
        {"type": "Link", "label": "Error Logs", "link_to": "Vobiz Error Log", "link_type": "DocType"},
        {"type": "Link", "label": "Webhook Events", "link_to": "Vobiz Webhook Event", "link_type": "DocType"},
        {"type": "Link", "label": "Settings", "link_to": "Vobiz AI Settings", "link_type": "DocType"},
    ]
    content = [
        {"id": "vobiz_header", "type": "header", "data": {"text": "Vobiz AI", "level": 4, "col": 12}},
        {"id": "vobiz_call_logs", "type": "shortcut", "data": {"shortcut_name": "Call Logs", "col": 3}},
        {"id": "vobiz_mapping", "type": "shortcut", "data": {"shortcut_name": "Account Mapping", "col": 3}},
        {"id": "vobiz_errors", "type": "shortcut", "data": {"shortcut_name": "Error Logs", "col": 3}},
        {"id": "vobiz_settings", "type": "shortcut", "data": {"shortcut_name": "Settings", "col": 3}},
        {"id": "vobiz_spacer", "type": "spacer", "data": {"col": 12}},
        {"id": "vobiz_ops_header", "type": "header", "data": {"text": "Operations", "level": 5, "col": 12}},
        {"id": "vobiz_events", "type": "shortcut", "data": {"shortcut_name": "Webhook Events", "col": 3}},
    ]
    values = {
        "doctype": "Workspace",
        "module": MODULE,
        "title": "Vobiz AI",
        "label": "Vobiz AI",
        "public": 1,
        "for_user": "",
        "icon": "icon-call",
        "content": json.dumps(content),
    }

    if frappe.db.exists("Workspace", "Vobiz AI"):
        workspace = frappe.get_doc("Workspace", "Vobiz AI")
        workspace.update(values)
        workspace.set("links", links)
        workspace.set("shortcuts", shortcuts)
        workspace.save(ignore_permissions=True)
    else:
        values["links"] = links
        values["shortcuts"] = shortcuts
        frappe.get_doc(values).insert(ignore_permissions=True)
