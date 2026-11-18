import unittest
from unittest.mock import MagicMock, patch
from vobiz_ai.maintenance import lookup_indexes as mod


class LookupIndexTests(unittest.TestCase):
    def setUp(self):
        self.frappe = MagicMock()
        self.indexes = []
        self.applied = []
        self.columns = [("vobiz_mobile_last10",), ("modified",)]
        def sql(query, values=None):
            if "information_schema.COLUMNS" in query:
                return self.columns
            if "information_schema.STATISTICS" in query:
                return self.indexes
            if "@@SESSION.lock_wait_timeout" in query:
                return [(50,)]
            return []
        self.frappe.db.sql.side_effect = sql
        def ddl(query):
            self.applied.append(query)
            self.indexes = [("idx_crm_vobiz_mobile_modified", "vobiz_mobile_last10", None, "BTREE"),
                            ("idx_crm_vobiz_mobile_modified", "modified", None, "BTREE")]
        self.frappe.db.sql_ddl.side_effect = ddl
        p = patch.object(mod, "frappe", self.frappe); p.start(); self.addCleanup(p.stop)

    def test_plan_has_no_writes(self):
        self.assertEqual(mod.repair("crm_mobile")[0]["status"], "planned")
        self.frappe.db.sql_ddl.assert_not_called()
        self.assertFalse(any(c.args[0].startswith("SET") for c in self.frappe.db.sql.call_args_list))

    def test_equivalent_wider_index_reused(self):
        self.indexes = [("existing", c, None, "BTREE") for c in ("vobiz_mobile_last10", "modified", "name")]
        self.assertEqual(mod.repair("crm_mobile", True)[0]["index"], "existing")
        self.frappe.db.sql_ddl.assert_not_called()

    def test_prefix_wrong_order_and_fulltext_not_equivalent(self):
        for definition in [
            [("x", "vobiz_mobile_last10", 4, "BTREE"), ("x", "modified", None, "BTREE")],
            [("x", "modified", None, "BTREE"), ("x", "vobiz_mobile_last10", None, "BTREE")],
            [("x", "vobiz_mobile_last10", None, "FULLTEXT"), ("x", "modified", None, "FULLTEXT")],
        ]:
            self.indexes = definition
            self.assertEqual(mod.repair("crm_mobile")[0]["status"], "planned")

    def test_collision_and_missing_columns_fail_before_ddl(self):
        self.indexes = [("idx_crm_vobiz_mobile_modified", "modified", None, "BTREE")]
        with self.assertRaises(ValueError): mod.repair("crm_mobile", True)
        self.indexes = []; self.columns = []
        self.assertEqual(mod.repair("crm_mobile")[0]["status"], "schema_missing")
        with self.assertRaises(ValueError): mod.repair("crm_mobile", True)
        self.frappe.db.sql_ddl.assert_not_called()

    def test_apply_verified_idempotent_and_timeout_restored(self):
        self.assertEqual(mod.repair("crm_mobile", True)[0]["status"], "created")
        self.assertIn("ALGORITHM=INPLACE, LOCK=NONE", self.applied[0])
        self.assertEqual(mod.repair("crm_mobile", True)[0]["status"], "already_present")
        self.assertEqual(len(self.applied), 1)
        self.assertIn(unittest.mock.call("SET SESSION lock_wait_timeout=%s", (50,)),
                      self.frappe.db.sql.call_args_list)

    def test_failure_propagates_and_restores_timeout(self):
        self.frappe.db.sql_ddl.side_effect = RuntimeError("online DDL unavailable")
        with self.assertRaises(RuntimeError): mod.repair("crm_mobile", True)
        self.assertEqual(self.frappe.db.sql.call_args.args, ("SET SESSION lock_wait_timeout=%s", (50,)))
        self.assertEqual(self.frappe.db.sql_ddl.call_count, 1)

    def test_invalid_apply_or_target_rejected(self):
        for kwargs in [dict(apply="false"), dict(apply=True), dict(target="arbitrary")]:
            with self.assertRaises(ValueError): mod.repair(**kwargs)

    def test_unverified_creation_fails(self):
        self.frappe.db.sql_ddl.side_effect = None
        with self.assertRaises(RuntimeError): mod.repair("crm_mobile", True)


if __name__ == "__main__": unittest.main()
