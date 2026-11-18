"""Versioned caller-history identity. Legacy caller fields and routing are untouched."""
import re
import phonenumbers
import frappe
from frappe.utils import cint, get_datetime

VERSION = "1"
PHONE = "vobiz_history_phone"
TIME = "vobiz_history_time"
READY = "vobiz_history_ready_version"
SOURCE_FIELDS = ("customer_number", "normalized_customer_number", "start_time", "event_timestamp", "creation")
FIELDS = (PHONE, TIME)


def phone_key(value):
    """E.164 digits. Unprefixed national numbers use India, matching this site's convention.

    Explicit international prefixes are preserved. Extensions, SIP identities and invalid
    numbers are excluded, never truncated to another country's last ten digits.
    Parser metadata is pinned so a dependency update cannot silently change stored keys.
    """
    raw = str(value or "").strip()
    if not raw or not re.fullmatch(r"[+0-9(). \-]+", raw):
        return ""
    digits = re.sub(r"[^0-9]", "", raw)
    if digits.startswith("00"):
        raw = "+" + digits[2:]
    elif not raw.startswith("+") and len(digits) == 12 and digits.startswith("91"):
        raw = "+" + digits
    try:
        number = phonenumbers.parse(raw, "IN")
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(number) and not raw.startswith("+") and len(digits) > 10 and not digits.startswith("0"):
        # Providers also supply E.164 digits without '+'. Use that interpretation
        # only when the national interpretation is invalid; never strip country codes.
        try:
            number = phonenumbers.parse("+" + digits, None)
        except phonenumbers.NumberParseException:
            return ""
    if number.extension or not phonenumbers.is_valid_number(number):
        return ""
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)[1:]


def expected_values(row):
    # A present raw identity is authoritative. Never hide an invalid raw value by
    # falling back to an old normalized field which may represent a different caller.
    raw = str(row.get("customer_number") or "").strip()
    source = raw or row.get("normalized_customer_number")
    when = row.get("start_time") or row.get("event_timestamp") or row.get("creation")
    return {PHONE: phone_key(source), TIME: get_datetime(when) if when else None}


def sync_document(doc):
    meta = getattr(doc, "meta", None)
    if not meta or not all(meta.has_field(field) for field in FIELDS):
        return  # Code-before-migration remains compatible; activation checks are separate.
    for key, value in expected_values(doc).items():
        doc.set(key, value)


def set_call_values(name, values, **kwargs):
    """For source-changing db.set_value callers: update source and derived fields atomically.

    Does not commit or call a provider. The caller owns the transaction and lock policy.
    """
    values = dict(values)
    if set(SOURCE_FIELDS).intersection(values) and all(frappe.db.has_column("Vobiz Call Log", f) for f in FIELDS):
        rows = frappe.db.sql("SELECT " + ",".join("`"+f+"`" for f in SOURCE_FIELDS) +
                             " FROM `tabVobiz Call Log` WHERE name=%s FOR UPDATE", (name,), as_dict=True)
        if not rows:
            raise frappe.DoesNotExistError("Call log no longer exists")
        current = dict(rows[0]); current.update(values)
        values.update(expected_values(current))
    return frappe.db.set_value("Vobiz Call Log", name, values, **kwargs)


def indexed_enabled():
    if not cint(frappe.conf.get("vobiz_indexed_call_history")):
        return False
    if str(frappe.db.get_global(READY) or "") != VERSION:
        raise RuntimeError("Indexed call history is not approved; complete manual backfill and verification")
    return True


def count_calls(phone):
    key = phone_key(phone)
    if not key:
        return 0
    return int(frappe.db.sql(f"SELECT COUNT(*) FROM `tabVobiz Call Log` WHERE `{PHONE}`=%s", (key,))[0][0])


def latest_call(phone):
    key = phone_key(phone)
    if not key:
        return {}
    rows = frappe.db.sql(
        f"SELECT name,start_time,event_timestamp,status,direction,crm_lead,patient FROM `tabVobiz Call Log` "
        f"WHERE `{PHONE}`=%s ORDER BY `{TIME}` DESC,name DESC LIMIT 1", (key,), as_dict=True)
    return rows[0] if rows else {}
