"""User-response projection only; never apply to provider or stored payloads."""
from copy import deepcopy
import frappe


def restricted():
    if not frappe.conf.get("privacy_shield_desk_enabled", False):
        return False
    if "privacy_shield" not in frappe.get_installed_apps():
        return False
    from privacy_shield.policy import current_capabilities
    return not current_capabilities().view_full


def project_response(payload):
    result = deepcopy(payload)
    if not restricted():
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
