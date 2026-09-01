from __future__ import annotations

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


MODULE = "Vobiz AI"
DEFAULT_VOICE_AGENT_SYSTEM_PROMPT = (
    "You are KAMAL, SRIAAS virtual care coordinator. Reply in the customer's language, "
    "keep responses short, do not diagnose or prescribe, and move interested callers "
    "to doctor callback or consultation."
)
DEFAULT_VOICE_AGENT_GREETING = (
    "The call has just connected. Immediately greet the customer warmly in Hindi and "
    "introduce yourself and SRIAAS."
)
DEFAULT_MEDICAL_GUARDRAIL_POLICY = (
    "The assistant is not a doctor. It must not diagnose, prescribe, guarantee cures, "
    "or create medical urgency for sales. It should route medical decisions to the doctor team."
)
DEFAULT_ESCALATION_POLICY = (
    "Escalate urgent symptoms, severe pain, bleeding, breathing difficulty, chest pain, "
    "suicidal language, or life-risk messages to emergency care immediately."
)


def after_install():
    setup()


def after_migrate():
    setup()


def setup():
    ensure_module_def()
    ensure_roles()
    custom_fields = ensure_custom_fields()
    ensure_voice_agent_defaults()
    ensure_default_voice_agent_profile()
    remove_duplicate_crm_lead_score_fields()
    remove_obsolete_layout_fields()
    ensure_custom_fields_at_end({dt: fields for dt, fields in custom_fields.items() if dt != "CRM Lead"})
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


def _chain_at_end(doctype: str, custom_fields: list[dict]) -> list[dict]:
    target_fieldnames = {field["fieldname"] for field in custom_fields}
    anchor = None

    for field in reversed(frappe.get_meta(doctype, cached=False).fields):
        if field.fieldname not in target_fieldnames:
            anchor = field.fieldname
            break

    previous = anchor
    chained_fields = []
    for field in custom_fields:
        row = field.copy()
        if previous:
            row["insert_after"] = previous
        else:
            row.pop("insert_after", None)
        previous = row["fieldname"]
        chained_fields.append(row)

    return chained_fields


def ensure_custom_fields():
    fields = {}
    if frappe.db.exists("DocType", "CRM Lead"):
        fields["CRM Lead"] = [
            _field("vobiz_ai_tab", "Vobiz Calls", "Tab Break", insert_after="lost_notes"),
            _field("vobiz_ai_summary_sb", "Vobiz Summary", "Section Break", insert_after="vobiz_ai_tab"),
            _field("vobiz_latest_call_time", "Latest Vobiz Call Time", "Datetime", read_only=1, insert_after="vobiz_ai_summary_sb"),
            _field("vobiz_latest_call_log", "Latest Vobiz Call Log", "Link", options="Vobiz Call Log", read_only=1, insert_after="vobiz_latest_call_time"),
            _field("vobiz_last_call_status", "Last Vobiz Call Status", "Data", read_only=1, insert_after="vobiz_latest_call_time"),
            _field("vobiz_latest_query_time", "Latest Vobiz Query Time", "Datetime", read_only=1, insert_after="vobiz_last_call_status"),
            _field("vobiz_call_alert_count", "Vobiz Call Alerts", "Int", read_only=1, insert_after="vobiz_latest_query_time"),
            _field("vobiz_calls_seen_at", "Vobiz Calls Seen At", "Datetime", read_only=1, hidden=1, insert_after="vobiz_call_alert_count"),
            _field("vobiz_last_call_event", "Last Vobiz Event", "Data", read_only=1, insert_after="vobiz_last_call_status"),
            _field("vobiz_call_direction", "Last Vobiz Direction", "Data", read_only=1, insert_after="vobiz_last_call_event"),
            _field("vobiz_call_duration", "Last Vobiz Duration", "Duration", read_only=1, insert_after="vobiz_call_direction"),
            _field("vobiz_caller_classification", "Vobiz Caller Type", "Select", options="\nNew Lead\nOld Lead\nPatient", read_only=1, insert_after="vobiz_call_duration"),
            _field("vobiz_summary_cb", "", "Column Break", insert_after="vobiz_caller_classification"),
            _field("vobiz_kamal_involved", "Kamal Involved", "Check", read_only=1, insert_after="vobiz_summary_cb"),
            _field("vobiz_recording_url", "Latest Recording URL", "Small Text", read_only=1, insert_after="vobiz_kamal_involved"),
            _field("vobiz_transcription_text", "Latest Transcript", "Long Text", read_only=1, insert_after="vobiz_recording_url"),
            _field("vobiz_ai_summary", "Latest AI Summary", "Long Text", read_only=1, insert_after="vobiz_transcription_text"),
            _field("vobiz_ai_intent", "Latest AI Intent", "Small Text", read_only=1, insert_after="vobiz_ai_summary"),
            _field("vobiz_ai_concerns", "Latest AI Concerns", "Long Text", read_only=1, insert_after="vobiz_ai_intent"),
            _field("vobiz_calling_details_html", "Vobiz Calling Details", "HTML", insert_after="sr_lead_disease"),
            _field("vobiz_ai_calls_html", "Vobiz Call History", "HTML", hidden=1, insert_after="vobiz_ai_concerns"),
        ]
    if frappe.db.exists("DocType", "Patient"):
        fields["Patient"] = _chain_at_end(
            "Patient",
            [
                _field("vobiz_ai_tab", "Vobiz Calls", "Tab Break"),
                _field("vobiz_ai_summary_sb", "Vobiz Summary", "Section Break"),
                _field("vobiz_call_indicator", "Vobiz", "Data", read_only=1, in_list_view=1, in_standard_filter=1),
                _field("vobiz_latest_call_time", "Latest Vobiz Call Time", "Datetime", read_only=1, in_list_view=1, in_standard_filter=1),
                _field("vobiz_last_call_status", "Last Vobiz Call Status", "Data", read_only=1, in_standard_filter=1),
                _field("vobiz_caller_classification", "Vobiz Caller Type", "Select", options="\nNew Lead\nOld Lead\nPatient", read_only=1, in_standard_filter=1),
                _field("vobiz_lead_temperature", "Vobiz Lead Temperature", "Select", options="\nHot\nWarm\nCold", read_only=1, in_list_view=1, in_standard_filter=1),
                _field("vobiz_lead_score", "Vobiz Lead Score", "Int", read_only=1, in_list_view=1, in_standard_filter=1),
                _field("vobiz_lead_language", "Vobiz Language", "Data", read_only=1, in_list_view=1, in_standard_filter=1),
                _field("vobiz_call_count", "Vobiz Call Count", "Int", read_only=1, in_list_view=1, in_standard_filter=1),
                _field("vobiz_kamal_involved", "Kamal Involved", "Check", read_only=1, in_standard_filter=1),
                _field("vobiz_call_history_sb", "Vobiz Call History", "Section Break"),
                _field("vobiz_ai_calls_html", "", "HTML"),
            ],
        )
    if frappe.db.exists("DocType", "Issue"):
        fields["Issue"] = _chain_at_end(
            "Issue",
            [
                _field("vobiz_issue_section", "Vobiz Call", "Section Break"),
                _field("vobiz_call_log", "Vobiz Call Log", "Link", options="Vobiz Call Log", read_only=1, in_list_view=1, in_standard_filter=1),
                _field("vobiz_patient", "Vobiz Patient", "Link", options="Patient", read_only=1, in_list_view=1, in_standard_filter=1),
                _field("vobiz_crm_lead", "Vobiz CRM Lead", "Link", options="CRM Lead", read_only=1, in_standard_filter=1),
                _field("vobiz_call_time", "Vobiz Call Time", "Datetime", read_only=1, in_standard_filter=1),
                _field("vobiz_call_direction", "Vobiz Direction", "Data", read_only=1, in_standard_filter=1),
                _field("vobiz_call_status", "Vobiz Call Status", "Data", read_only=1, in_standard_filter=1),
                _field("vobiz_lead_temperature", "Vobiz Lead Temperature", "Data", read_only=1, in_standard_filter=1),
                _field("vobiz_lead_score", "Vobiz Lead Score", "Int", read_only=1, in_standard_filter=1),
            ],
        )
    if fields:
        create_custom_fields(fields, update=True, ignore_validate=True)
    return fields


def ensure_custom_fields_at_end(fields_by_doctype: dict[str, list[dict]]):
    for doctype, custom_fields in fields_by_doctype.items():
        desired_fieldnames = [field["fieldname"] for field in custom_fields]
        desired_fieldname_set = set(desired_fieldnames)
        meta = frappe.get_meta(doctype, cached=False)
        current_order = [field.fieldname for field in meta.fields if field.fieldname]
        existing_desired = [fieldname for fieldname in desired_fieldnames if fieldname in current_order]

        if not existing_desired:
            continue

        new_order = [fieldname for fieldname in current_order if fieldname not in desired_fieldname_set]
        new_order.extend(existing_desired)

        if current_order != new_order:
            frappe.db.delete("Property Setter", {"doc_type": doctype, "property": "field_order"})
            frappe.make_property_setter(
                {
                    "doctype": doctype,
                    "doctype_or_field": "DocType",
                    "property": "field_order",
                    "property_type": "Small Text",
                    "value": json.dumps(new_order),
                },
                ignore_validate=True,
                validate_fields_for_doctype=False,
            )

        previous = new_order[new_order.index(existing_desired[0]) - 1] if new_order.index(existing_desired[0]) else None
        for fieldname in existing_desired:
            name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": fieldname})
            if name:
                frappe.db.set_value("Custom Field", name, "insert_after", previous, update_modified=False)
            previous = fieldname

        frappe.clear_cache(doctype=doctype)


def ensure_voice_agent_defaults():
    if not frappe.db.exists("DocType", "Vobiz AI Settings"):
        return

    defaults = {
        "enable_voice_agent": 1,
        "voice_agent_name": "KAMAL",
        "system_prompt": DEFAULT_VOICE_AGENT_SYSTEM_PROMPT,
        "greeting_instruction": DEFAULT_VOICE_AGENT_GREETING,
        "gemini_live_model": "gemini-live-2.5-flash-native-audio",
        "gemini_live_voice": "Puck",
        "vertex_location": "us-central1",
        "livekit_cli_project": "gemini-live",
        "frappe_base_url": "",
        "livekit_agent_name": "vobiz-gemini-live",
        "sip_provider": "Vobiz",
        "room_name_pattern": "gemini_live_{caller}_{random}",
        "lead_creation_tool_name": "mcp_create_lead",
        "medical_guardrail_policy": DEFAULT_MEDICAL_GUARDRAIL_POLICY,
        "escalation_policy": DEFAULT_ESCALATION_POLICY,
        "allowed_voice_actions": "create_lead",
    }

    current = frappe.get_single("Vobiz AI Settings")
    changed = False
    for fieldname, value in defaults.items():
        if current.get(fieldname) in (None, ""):
            current.set(fieldname, value)
            changed = True

    if changed:
        current.save(ignore_permissions=True)


def ensure_default_voice_agent_profile():
    if not frappe.db.exists("DocType", "Vobiz Voice Agent Profile"):
        return
    if frappe.db.exists("Vobiz Voice Agent Profile", "kamal-male-infertility"):
        return

    settings = frappe.get_single("Vobiz AI Settings")
    frappe.get_doc(
        {
            "doctype": "Vobiz Voice Agent Profile",
            "enabled": 1,
            "profile_key": "kamal-male-infertility",
            "agent_name": settings.get("voice_agent_name") or "KAMAL",
            "description": "Default SRIAAS male infertility and sexual health voice agent.",
            "system_prompt": settings.get("system_prompt") or DEFAULT_VOICE_AGENT_SYSTEM_PROMPT,
            "greeting_instruction": settings.get("greeting_instruction") or DEFAULT_VOICE_AGENT_GREETING,
            "gemini_live_model": settings.get("gemini_live_model") or "gemini-live-2.5-flash-native-audio",
            "gemini_live_voice": settings.get("gemini_live_voice") or "Puck",
            "vertex_location": settings.get("vertex_location") or "us-central1",
            "google_cloud_project": settings.get("google_cloud_project") or "",
            "mcp_server_url": settings.get("mcp_server_url") or "",
            "lead_creation_tool_name": settings.get("lead_creation_tool_name") or "mcp_create_lead",
            "medical_guardrail_policy": settings.get("medical_guardrail_policy")
            or DEFAULT_MEDICAL_GUARDRAIL_POLICY,
            "escalation_policy": settings.get("escalation_policy") or DEFAULT_ESCALATION_POLICY,
            "allowed_voice_actions": settings.get("allowed_voice_actions") or "create_lead",
            "livekit_agent_name": settings.livekit_agent_name or "vobiz-gemini-live",
            "livekit_sync_status": "Not Synced",
        }
    ).insert(ignore_permissions=True)


def remove_duplicate_crm_lead_score_fields():
    if not frappe.db.exists("DocType", "CRM Lead"):
        return
    for fieldname in ("vobiz_lead_temperature", "vobiz_lead_score"):
        name = frappe.db.get_value("Custom Field", {"dt": "CRM Lead", "fieldname": fieldname})
        if name:
            frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
    frappe.clear_cache(doctype="CRM Lead")


def remove_obsolete_layout_fields():
    for doctype in ("CRM Lead", "Patient"):
        name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": "vobiz_summary_cb"})
        if name:
            frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
            frappe.clear_cache(doctype=doctype)


def ensure_workspace():
    shortcuts = [
        {"type": "DocType", "label": "Call Logs", "link_to": "Vobiz Call Log", "doc_view": "List", "icon": "phone"},
        {"type": "DocType", "label": "Account Mapping", "link_to": "Vobiz Account Mapping", "doc_view": "List", "icon": "settings"},
        {"type": "DocType", "label": "Voice Agents", "link_to": "Vobiz Voice Agent Profile", "doc_view": "List", "icon": "bot"},
        {"type": "DocType", "label": "Voice Routes", "link_to": "Vobiz Voice Agent Route", "doc_view": "List", "icon": "git-branch"},
        {"type": "DocType", "label": "Error Logs", "link_to": "Vobiz Error Log", "doc_view": "List", "icon": "alert-triangle"},
        {"type": "DocType", "label": "Settings", "link_to": "Vobiz AI Settings", "doc_view": "List", "icon": "sliders"},
        {"type": "DocType", "label": "Webhook Events", "link_to": "Vobiz Webhook Event", "doc_view": "List", "icon": "activity"},
    ]
    links = [
        {"type": "Link", "label": "Call Logs", "link_to": "Vobiz Call Log", "link_type": "DocType"},
        {"type": "Link", "label": "Account Mapping", "link_to": "Vobiz Account Mapping", "link_type": "DocType"},
        {"type": "Link", "label": "Voice Agents", "link_to": "Vobiz Voice Agent Profile", "link_type": "DocType"},
        {"type": "Link", "label": "Voice Routes", "link_to": "Vobiz Voice Agent Route", "link_type": "DocType"},
        {"type": "Link", "label": "Error Logs", "link_to": "Vobiz Error Log", "link_type": "DocType"},
        {"type": "Link", "label": "Webhook Events", "link_to": "Vobiz Webhook Event", "link_type": "DocType"},
        {"type": "Link", "label": "Settings", "link_to": "Vobiz AI Settings", "link_type": "DocType"},
    ]
    content = [
        {"id": "vobiz_header", "type": "header", "data": {"text": "Vobiz AI", "level": 4, "col": 12}},
        {"id": "vobiz_call_logs", "type": "shortcut", "data": {"shortcut_name": "Call Logs", "col": 3}},
        {"id": "vobiz_mapping", "type": "shortcut", "data": {"shortcut_name": "Account Mapping", "col": 3}},
        {"id": "vobiz_voice_agents", "type": "shortcut", "data": {"shortcut_name": "Voice Agents", "col": 3}},
        {"id": "vobiz_voice_routes", "type": "shortcut", "data": {"shortcut_name": "Voice Routes", "col": 3}},
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
