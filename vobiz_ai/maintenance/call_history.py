"""Explicit Bench operations for caller history. Never enqueued by migration."""
import hashlib
import json
from contextlib import contextmanager
import frappe
from frappe.utils import cint, get_datetime
from vobiz_ai.history import VERSION, PHONE, TIME, READY, SOURCE_FIELDS, expected_values

CURSOR = "vobiz_history_backfill_cursor_v1"
DONE = "vobiz_history_backfill_done_v1"
AUDIT = "vobiz_history_verification_v1"
ALL_FIELDS = ("name", *SOURCE_FIELDS, PHONE, TIME)


def _schema():
    missing = [f for f in ALL_FIELDS if not frappe.db.has_column("Vobiz Call Log", f)]
    if missing:
        raise RuntimeError("Migrate the custom app first; missing history columns: " + ", ".join(missing))


def _inactive():
    if cint(frappe.conf.get("vobiz_indexed_call_history")):
        raise RuntimeError("Disable indexed call history and reload workers before repair or approval")


@contextmanager
def _lock():
    name = "vobiz-history:" + hashlib.sha256(str(frappe.local.site).encode()).hexdigest()[:48]
    if frappe.db.sql("SELECT GET_LOCK(%s,0)", (name,))[0][0] != 1:
        raise RuntimeError("Another call-history maintenance operation is running")
    try:
        yield
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (name,))


def _page(after_name, batch_size):
    return frappe.get_all("Vobiz Call Log", filters={"name":[">",after_name]} if after_name else None,
                          fields=list(ALL_FIELDS), order_by="name asc", limit_page_length=batch_size)


def _same(field, left, right):
    if field == TIME:
        return (get_datetime(left) if left else None) == (get_datetime(right) if right else None)
    return str(left or "") == str(right or "")


def _state():
    raw = frappe.db.get_global(AUDIT)
    return json.loads(raw) if raw else dict(version=VERSION, last_name="", done=False, processed=0,
        missing=0, mismatched=0, time_mismatched=0, invalid_source=0, blank_source=0,
        normalized_fallback=0, legacy_identity_changed=0)


def backfill(batch_size=200, dry_run=True, reset=False):
    """Exactly one bounded page; raw phone/timestamp fields are never changed."""
    frappe.only_for("System Manager")
    if not isinstance(dry_run,bool) or not isinstance(reset,bool):
        raise ValueError("dry_run/reset must be JSON booleans")
    if dry_run and reset:
        raise ValueError("Dry run cannot reset persisted progress")
    _inactive(); _schema()
    batch_size=max(1,min(cint(batch_size) or 200,500))
    with _lock():
        if reset:
            frappe.db.set_global(CURSOR,"")
            frappe.db.set_global(DONE,0)
            frappe.db.set_global(AUDIT,"")
            frappe.db.set_global(READY,"")
            frappe.db.commit()
        cursor=str(frappe.db.get_global(CURSOR) or "")
        if cint(frappe.db.get_global(DONE)):
            return dict(done=True,status="already_backfilled",last_name=cursor)
        rows=_page(cursor,batch_size)
        old=frappe.db.sql("SELECT @@SESSION.innodb_lock_wait_timeout")[0][0]
        changed=0; blocked=None
        try:
            if not dry_run:
                frappe.db.sql("SET SESSION innodb_lock_wait_timeout=3")
                frappe.db.set_global(READY,"")
                frappe.db.set_global(AUDIT,"")
            for candidate in rows:
                blocked=candidate.name
                if dry_run:
                    row=candidate
                else:
                    current=frappe.db.sql("SELECT " + ",".join("`"+f+"`" for f in ALL_FIELDS) +
                        " FROM `tabVobiz Call Log` WHERE name=%s FOR UPDATE",(candidate.name,),as_dict=True)
                    if not current:continue
                    row=current[0]
                updates={f:v for f,v in expected_values(row).items() if not _same(f,row.get(f),v)}
                if updates:
                    changed+=1
                    if not dry_run:
                        frappe.db.set_value("Vobiz Call Log",row.name,updates,update_modified=False)
            done=len(rows)<batch_size
            next_cursor=rows[-1].name if rows else cursor
            if not dry_run:
                frappe.db.set_global(CURSOR,next_cursor)
                frappe.db.set_global(DONE,int(done))
                frappe.db.commit()
            return dict(done=done,dry_run=dry_run,processed=len(rows),updated=changed,last_name=next_cursor)
        except (frappe.QueryTimeoutError,frappe.QueryDeadlockError):
            frappe.db.rollback()
            return dict(done=False,status="retry",last_name=cursor,blocked_name=blocked,updated=0)
        except Exception:
            if not dry_run:frappe.db.rollback()
            raise
        finally:
            if not dry_run:frappe.db.sql("SET SESSION innodb_lock_wait_timeout=%s",(old,))


def verify(batch_size=200, reset=False):
    """One verification page. Persists aggregate counters, never edits Call Log rows."""
    frappe.only_for("System Manager")
    if not isinstance(reset,bool):raise ValueError("reset must be a JSON boolean")
    _inactive(); _schema()
    with _lock():
        if reset:
            frappe.db.set_global(AUDIT,"")
            frappe.db.set_global(READY,"")
        state=_state()
        if state["done"]:return state
        size=max(1,min(cint(batch_size) or 200,500))
        rows=_page(state["last_name"],size)
        from vobiz_ai.api.utils import normalize_phone
        for row in rows:
            expected=expected_values(row)
            raw=str(row.get("customer_number") or "").strip()
            fallback=str(row.get("normalized_customer_number") or "").strip()
            if not raw and fallback:state["normalized_fallback"]+=1
            if not raw and not fallback:state["blank_source"]+=1
            elif not expected[PHONE]:state["invalid_source"]+=1
            if expected[PHONE] and not row.get(PHONE):state["missing"]+=1
            elif not _same(PHONE,row.get(PHONE),expected[PHONE]):state["mismatched"]+=1
            if not _same(TIME,row.get(TIME),expected[TIME]):state["time_mismatched"]+=1
            if expected[PHONE] and normalize_phone(raw or fallback)!=expected[PHONE]:
                state["legacy_identity_changed"]+=1
        state["processed"]+=len(rows)
        state["last_name"]=rows[-1].name if rows else state["last_name"]
        state["done"]=len(rows)<size
        frappe.db.set_global(AUDIT,json.dumps(state))
        frappe.db.commit()
        return state


def approve(acknowledge_identity_changes=False, accept_excluded_invalid=False, accept_normalized_fallback=False):
    """Approve verified data; does NOT enable the feature or modify site configuration."""
    frappe.only_for("System Manager")
    if any(not isinstance(v,bool) for v in (acknowledge_identity_changes,accept_excluded_invalid,accept_normalized_fallback)):
        raise ValueError("Acknowledgements must be JSON booleans")
    _inactive(); _schema()
    with _lock():
        state=_state()
        if not cint(frappe.db.get_global(DONE)) or not state["done"] or state["version"]!=VERSION:
            raise RuntimeError("Complete backfill and full verification first")
        if any(state[k] for k in ("missing","mismatched","time_mismatched")):
            raise RuntimeError("History verification has unresolved mismatches")
        if not acknowledge_identity_changes:
            raise ValueError("Review country-aware matching and acknowledge identity changes explicitly")
        if state["invalid_source"] and not accept_excluded_invalid:
            raise ValueError("Invalid source records would be excluded; correct or explicitly accept them")
        if state["normalized_fallback"] and not accept_normalized_fallback:
            raise ValueError("Review records using only legacy normalized identities before approval")
        from vobiz_ai.maintenance.lookup_indexes import _definitions,_matching,TARGETS
        table,_,columns=TARGETS["call_history"]
        if not _matching(_definitions(table),columns):
            raise RuntimeError("Required caller-history index has not been installed")
        frappe.db.set_global(READY,VERSION)
        frappe.db.commit()
        return dict(approved=True,enabled=False,version=VERSION,verification=state)


def status():
    frappe.only_for("System Manager")
    return dict(version=VERSION,backfill_done=bool(cint(frappe.db.get_global(DONE))),
        last_name=frappe.db.get_global(CURSOR) or "",verification=_state(),
        approved=str(frappe.db.get_global(READY) or "")==VERSION,
        enabled=bool(cint(frappe.conf.get("vobiz_indexed_call_history"))))
