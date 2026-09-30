"""Restrict raw document HTTP access without changing internal ORM permissions.

Reviewed app RPCs retain their own authorization and response projections.
"""
import json
from urllib.parse import unquote
import frappe
from vobiz_ai.number_privacy import restricted

RAW_DOCTYPES = frozenset(('Vobiz Call Log', 'Vobiz Error Log', 'Vobiz Webhook Event'))
GENERIC_PREFIXES = ("frappe.client.", "frappe.desk.", "frappe.model.",
                    "frappe.core.", "frappe.utils.print_format.")


def contains_raw_target(value):
    if isinstance(value, dict):
        return any(contains_raw_target(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_raw_target(v) for v in value)
    if not isinstance(value, str):
        return False
    if value in RAW_DOCTYPES:
        return True
    if value.lstrip().startswith(("{", "[")):
        try:
            return contains_raw_target(json.loads(value))
        except (ValueError, TypeError):
            pass
    return False


def is_raw_request(path, args):
    path = unquote(path)
    parts = path.strip("/").split("/")
    # Both REST versions, including nested document-method routes.
    if len(parts) >= 3 and parts[:2] == ["api", "resource"]:
        return parts[2] in RAW_DOCTYPES
    if len(parts) >= 4 and parts[:3] in (["api", "v1", "resource"], ["api", "v2", "document"]):
        return parts[3] in RAW_DOCTYPES
    method = args.get("cmd") or ""
    for prefix in ("/api/method/", "/api/v1/method/", "/api/v2/method/"):
        if path.startswith(prefix):
            method = path[len(prefix):]
            break
    generic = method.startswith(GENERIC_PREFIXES) or method in ("run_doc_method", "frappe.handler.run_doc_method")
    return (generic or path.rstrip("/") == "/printview") and contains_raw_target(args)


def guard_request():
    request = getattr(frappe.local, "request", None)
    if request and is_raw_request(request.path, frappe.form_dict) and restricted():
        raise frappe.PermissionError(
            "Raw call and queue records require full-number access. Use the masked call history where available."
        )
