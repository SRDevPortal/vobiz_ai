"""Restore callback indexes before schema sync; metadata now preserves them.

On large sites, preview and apply from a dedicated bench execute process before
migrate. repair() defaults to a read-only plan. No request/scheduler calls this.
"""
import frappe

TABLE = "tabVobiz Call Log"
FIELDS = ("call_uuid", "sip_call_id", "request_id", "request_uuid", "b_leg_uuid")


def execute():
    repair(apply=True)


def repair(apply=False):
    if not isinstance(apply, bool):
        raise ValueError("apply must be a JSON boolean")
    columns = {row[0] for row in frappe.db.sql(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (TABLE,)
    )}
    if not columns:
        return {"status": "table_absent", "fields": []}

    indexes = {}
    for name, column, position, prefix in frappe.db.sql(
        "SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX, SUB_PART "
        "FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY INDEX_NAME, SEQ_IN_INDEX",
        (TABLE,),
    ):
        indexes.setdefault(name, []).append((column, position, prefix))
    # Reuse full leading columns, including the reviewed UUID/start_time indexes.
    # A UUID occurring second in an index does not serve this exact lookup.
    indexed = {column for rows in indexes.values() for column, position, prefix in rows
               if position == 1 and prefix is None}
    missing = [field for field in FIELDS if field in columns and field not in indexed]
    if not missing:
        return {"status": "already_present", "fields": []}

    additions = []
    for field in missing:
        name = "idx_vobiz_identity_" + field
        if name in indexes:
            raise ValueError("Index name has an incompatible definition: " + name)
        additions.append(f"ADD INDEX `{name}` (`{field}`)")
    sql = f"ALTER TABLE `{TABLE}` " + ", ".join(additions) + ", ALGORITHM=INPLACE, LOCK=NONE"
    if not apply:
        return {"status": "planned", "fields": missing, "sql": sql}

    old_timeout = frappe.db.sql("SELECT @@SESSION.lock_wait_timeout")[0][0]
    try:
        # Bound metadata-lock acquisition, not the duration of building indexes.
        # If online DDL is unsupported, fail visibly; never retry with blocking DDL.
        frappe.db.sql("SET SESSION lock_wait_timeout=10")
        frappe.db.sql(sql)
    finally:
        frappe.db.sql("SET SESSION lock_wait_timeout=%s", (old_timeout,))
    return {"status": "created", "fields": missing}
