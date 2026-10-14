"""Masked users may stream authorized audio; downloads retain the privacy gate."""
import frappe
from vobiz_ai.number_privacy import restricted


def require_recording_access():
    if restricted():
        raise frappe.PermissionError("Call recordings require full-number visibility")


@frappe.whitelist()
def download(call_log: str):
    require_recording_access()
    from vobiz_click_to_call.api.recording import download as original
    return original(call_log)


@frappe.whitelist()
def stream(call_log: str):
    # The original endpoint checks identity/record access before provider I/O.
    # Number visibility is independent of authorized listening.
    from vobiz_click_to_call.api.recording import stream as original
    return original(call_log)
