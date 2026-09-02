import json

import frappe


GENERIC_SYSTEM_PROMPT = (
    "You are KAMAL, SRIAAS virtual sales and support coordinator. Reply in the customer's language, "
    "keep responses short, protect personal information, and move interested callers "
    "to the appropriate sales or support representative."
)
GENERIC_SAFETY_POLICY = (
    "The assistant must not make unsupported claims, expose private information, or pressure callers. "
    "It should route decisions outside its authority to the appropriate human team."
)
GENERIC_ESCALATION_POLICY = (
    "Escalate threats, safety concerns, legal complaints, account-security issues, "
    "or requests outside the assistant's authority to a human immediately."
)


def execute():
    _remove_healthcare_custom_fields()
    _clean_patient_field_order()
    _rewrite_seeded_voice_defaults()


def _remove_healthcare_custom_fields():
    names = frappe.get_all(
        "Custom Field",
        filters={
            "dt": "Patient",
            "fieldname": ["like", "vobiz_%"],
        },
        pluck="name",
    )
    issue_field = frappe.db.get_value(
        "Custom Field",
        {"dt": "Issue", "fieldname": "vobiz_patient"},
    )
    if issue_field:
        names.append(issue_field)

    for name in dict.fromkeys(names):
        frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)

    for doctype in ("Patient", "Issue"):
        if frappe.db.exists("DocType", doctype):
            frappe.clear_cache(doctype=doctype)


def _clean_patient_field_order():
    setters = frappe.get_all(
        "Property Setter",
        filters={
            "doc_type": "Patient",
            "property": "field_order",
        },
        fields=["name", "value"],
    )
    for setter in setters:
        try:
            field_order = json.loads(setter.value or "[]")
        except (TypeError, ValueError):
            continue
        cleaned = [
            fieldname
            for fieldname in field_order
            if not str(fieldname).startswith("vobiz_")
        ]
        if cleaned != field_order:
            frappe.db.set_value(
                "Property Setter",
                setter.name,
                "value",
                json.dumps(cleaned),
                update_modified=False,
            )


def _rewrite_seeded_voice_defaults():
    old_name = "kamal-male-infertility"
    new_name = "kamal-default"
    if frappe.db.exists("Vobiz Voice Agent Profile", old_name):
        if frappe.db.exists("Vobiz Voice Agent Profile", new_name):
            frappe.delete_doc(
                "Vobiz Voice Agent Profile",
                old_name,
                ignore_permissions=True,
                force=True,
            )
        else:
            frappe.rename_doc(
                "Vobiz Voice Agent Profile",
                old_name,
                new_name,
                force=True,
            )

    if frappe.db.exists("Vobiz Voice Agent Profile", new_name):
        frappe.db.set_value(
            "Vobiz Voice Agent Profile",
            new_name,
            {
                "profile_key": new_name,
                "description": "Default SRIAAS sales and support voice agent.",
                "system_prompt": GENERIC_SYSTEM_PROMPT,
                "medical_guardrail_policy": GENERIC_SAFETY_POLICY,
                "escalation_policy": GENERIC_ESCALATION_POLICY,
            },
            update_modified=False,
        )

    if not frappe.db.exists("DocType", "Vobiz AI Settings"):
        return
    settings = frappe.get_single("Vobiz AI Settings")
    legacy_fragments = {
        "system_prompt": ("virtual care coordinator", GENERIC_SYSTEM_PROMPT),
        "medical_guardrail_policy": ("assistant is not a doctor", GENERIC_SAFETY_POLICY),
        "escalation_policy": ("urgent symptoms", GENERIC_ESCALATION_POLICY),
    }
    changed = False
    for fieldname, (legacy_fragment, replacement) in legacy_fragments.items():
        if settings.meta.has_field(fieldname) and legacy_fragment in (settings.get(fieldname) or "").lower():
            settings.set(fieldname, replacement)
            changed = True
    if changed:
        settings.save(ignore_permissions=True)
