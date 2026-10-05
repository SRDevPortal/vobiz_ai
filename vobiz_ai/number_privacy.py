"""User-response projection only; never apply to provider or stored payloads."""
from copy import deepcopy
import frappe


def restricted(user=None):
    if not frappe.conf.get("privacy_shield_desk_enabled", False):
        return False
    if "privacy_shield" not in frappe.get_installed_apps():
        return False
    from privacy_shield.policy import current_capabilities
    capabilities = current_capabilities(user) if user else current_capabilities()
    return not capabilities.view_full


def project_response(payload, user=None):
    result = deepcopy(payload)
    if not restricted(user):
        return result
    from privacy_shield.masking import mask_number
    rows = result if isinstance(result, list) else [result]
    for row in rows:
        for field in ("customer_number", "customer_phone"):
            if field in row:
                row[field] = mask_number(row[field])
        # Audio and arbitrary free text can repeat an original number.
        for field in ("recording_url", "recording_download_url", "transcription_text",
                      "transcript_text", "ai_summary", "ai_intent"):
            if field in row:
                row[field] = ""
    return result


def project_patient_routed_notification(payload, user):
    result = deepcopy(payload)
    if not restricted(user):
        return result
    from privacy_shield.masking import mask_number
    from privacy_shield.display_text import mask_display
    if "customer_number" in result:
        result["customer_number"] = mask_number(result["customer_number"])
    if "patient_name" in result:
        result["patient_name"] = mask_display(result["patient_name"])
    return result
