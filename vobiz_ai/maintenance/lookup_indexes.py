"""Explicit Bench-only index planning/repair. Never invoked by migration or requests."""
import frappe

TARGETS = {
    "call_history": ("tabVobiz Call Log", "idx_vobiz_history_phone_time", ("vobiz_history_phone", "vobiz_history_time", "name")),
    "crm_mobile": ("tabCRM Lead", "idx_crm_vobiz_mobile_modified", ("vobiz_mobile_last10", "modified")),
    "crm_phone": ("tabCRM Lead", "idx_crm_vobiz_phone_modified", ("vobiz_phone_last10", "modified")),
    "crm_whatsapp": ("tabCRM Lead", "idx_crm_vobiz_whatsapp_modified", ("vobiz_whatsapp_last10", "modified")),
    "crm_normalized": ("tabCRM Lead", "idx_crm_vobiz_normalized_modified", ("vobiz_normalized_phone", "modified")),
}


def _definitions(table):
    indexes = {}
    for name, column, prefix, kind in frappe.db.sql(
        "SELECT INDEX_NAME,COLUMN_NAME,SUB_PART,INDEX_TYPE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY INDEX_NAME,SEQ_IN_INDEX", (table,)
    ):
        indexes.setdefault(name, []).append((column, prefix, kind))
    return indexes


def _matching(indexes, columns):
    for name, definition in indexes.items():
        if len(definition) >= len(columns) and all(
            column == expected and prefix is None and kind.upper() == "BTREE"
            for (column, prefix, kind), expected in zip(definition, columns)
        ):
            return name
    return None


def repair_specs(specs, target=None, apply=False):
    """Internal fixed-spec helper; callers must never pass user-provided SQL identifiers."""
    frappe.only_for("System Manager")
    if not isinstance(apply, bool):
        raise ValueError("apply must be a JSON boolean")
    if target is not None and (not isinstance(target, str) or target not in specs):
        raise ValueError("Unknown index target")
    if apply and target is None:
        raise ValueError("Apply one explicit target at a time")
    results = []
    for key in ([target] if target else specs):
        table, name, columns = specs[key]
        present = {row[0] for row in frappe.db.sql(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table,))}
        missing = sorted(set(columns) - present)
        if missing:
            if apply:
                raise ValueError("Required columns absent: " + ", ".join(missing))
            results.append(dict(target=key, status="schema_missing", columns=missing))
            continue
        indexes = _definitions(table)
        if name in indexes and not _matching({name: indexes[name]}, columns):
            raise ValueError("Conflicting index definition: " + name)
        equivalent = _matching(indexes, columns)
        if equivalent:
            results.append(dict(target=key, status="already_present", index=equivalent))
            continue
        sql = ("ALTER TABLE `" + table + "` ADD INDEX `" + name + "` (" +
               ", ".join("`" + c + "`" for c in columns) + "), ALGORITHM=INPLACE, LOCK=NONE")
        if not apply:
            results.append(dict(target=key, status="planned", sql=sql))
            continue
        old_timeout = frappe.db.sql("SELECT @@SESSION.lock_wait_timeout")[0][0]
        try:
            frappe.db.sql("SET SESSION lock_wait_timeout=10")
            frappe.db.sql_ddl(sql)
        finally:
            frappe.db.sql("SET SESSION lock_wait_timeout=%s", (old_timeout,))
        equivalent = _matching(_definitions(table), columns)
        if not equivalent:
            raise RuntimeError("Index creation did not pass verification: " + name)
        results.append(dict(target=key, status="created", index=equivalent))
    return results


def repair(target=None, apply=False):
    return repair_specs(TARGETS, target, apply)
