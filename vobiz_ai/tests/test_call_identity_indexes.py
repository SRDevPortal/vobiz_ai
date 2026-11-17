import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.database.schema import DbColumn
from vobiz_ai.patches.v0_0_1 import restore_call_identity_indexes as migration


class CallIdentityIndexTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.db.type_map = {"Data": ("varchar", 140)}
        self.rows = []
        self.columns = [(field,) for field in migration.FIELDS]
        self.failure = None

        def sql(query, values=None):
            if "information_schema.COLUMNS" in query:
                return self.columns
            if "information_schema.STATISTICS" in query:
                return self.rows
            if query == "SELECT @@SESSION.lock_wait_timeout":
                return [(86400,)]
            if query.startswith("ALTER") and self.failure:
                raise self.failure
            return []
        self.db.sql.side_effect = sql
        p = patch.object(frappe, "db", self.db)
        p.start()
        self.addCleanup(p.stop)

    def test_read_only_plan_and_missing_table(self):
        result = migration.repair()
        self.assertEqual(result["fields"], list(migration.FIELDS))
        self.assertIn("ALGORITHM=INPLACE, LOCK=NONE", result["sql"])
        self.assertFalse(any(c.args[0].startswith(("ALTER", "SET")) for c in self.db.sql.call_args_list))
        self.columns = []
        self.assertEqual(migration.repair(apply=True)["status"], "table_absent")

    def test_existing_single_and_composite_leading_indexes_are_reused(self):
        self.rows = [("old_"+field, field, 1, None) for field in migration.FIELDS]
        self.rows += [("old_call_uuid", "start_time", 2, None)]
        self.assertEqual(migration.repair(apply=True)["status"], "already_present")
        self.assertFalse(any(c.args[0].startswith("ALTER") for c in self.db.sql.call_args_list))

    def test_prefix_and_nonleading_uuid_indexes_do_not_mask_missing_index(self):
        self.rows = [("prefix", "call_uuid", 1, 8), ("wrong_order", "source_app", 1, None),
                     ("wrong_order", "call_uuid", 2, None)]
        self.assertIn("call_uuid", migration.repair()["fields"])

    def test_name_collision_fails_before_ddl(self):
        self.rows = [("idx_vobiz_identity_call_uuid", "source_app", 1, None)]
        with self.assertRaisesRegex(ValueError, "incompatible"):
            migration.repair(apply=True)
        self.assertFalse(any(c.args[0].startswith(("ALTER", "SET")) for c in self.db.sql.call_args_list))

    def test_ddl_failure_propagates_and_restores_timeout_without_blocking_fallback(self):
        self.failure = RuntimeError("online DDL refused")
        with self.assertRaisesRegex(RuntimeError, "online DDL refused"):
            migration.execute()
        self.assertEqual(sum(c.args[0].startswith("ALTER") for c in self.db.sql.call_args_list), 1)
        self.assertEqual(self.db.sql.call_args.args, ("SET SESSION lock_wait_timeout=%s", (86400,)))

    def test_success_restores_timeout_and_rejects_truthy_string(self):
        self.assertEqual(migration.repair(apply=True)["status"], "created")
        self.assertEqual(self.db.sql.call_args.args, ("SET SESSION lock_wait_timeout=%s", (86400,)))
        with self.assertRaises(ValueError):
            migration.repair(apply="false")

    def test_schema_preserves_all_callback_identity_indexes(self):
        root = Path(__file__).resolve().parents[1]
        fields = json.loads((root / "vobiz_ai/doctype/vobiz_call_log/vobiz_call_log.json").read_text())["fields"]
        by_name = {field["fieldname"]: field for field in fields}
        for name in migration.FIELDS:
            with self.subTest(name=name):
                self.assertEqual(by_name[name].get("search_index"), 1)
        # Run Frappe's actual column reconciliation against an existing index.
        # With the old metadata this scheduled index removal on every sync.
        table = MagicMock()
        table.drop_index = []
        table.add_index = []
        column = DbColumn(table, "call_uuid", "Data", None, None,
                          by_name["call_uuid"]["search_index"], None, False, None)
        column.build_for_alter_table({"type": "varchar(140)", "default": None, "index": 1, "unique": 0})
        self.assertEqual(table.drop_index, [])
        self.assertEqual(table.add_index, [])
        old_column = DbColumn(table, "call_uuid", "Data", None, None, 0, None, False, None)
        old_column.build_for_alter_table({"type": "varchar(140)", "default": None, "index": 1, "unique": 0})
        self.assertEqual(table.drop_index, [old_column])


if __name__ == "__main__":
    unittest.main()
