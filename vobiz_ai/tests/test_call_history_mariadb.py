"""Opt-in tests using uniquely named local tables; never changes business rows."""
import os
import unittest
import uuid
from unittest.mock import patch
import frappe
from vobiz_ai import history
from vobiz_ai.maintenance import call_history as maintenance

@unittest.skipUnless(os.environ.get("LOCAL_PHONE_TEST_SITE"), "explicit local site required")
class CallHistoryMariaDBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        site = os.environ["LOCAL_PHONE_TEST_SITE"]
        if not site.endswith((".local", ".localhost")):
            raise ValueError("Local site required")
        frappe.init(site=site); frappe.connect(); frappe.set_user("Administrator")

    @classmethod
    def tearDownClass(cls):
        frappe.destroy()

    def setUp(self):
        self.table = "_history_test_" + uuid.uuid4().hex
        self.progress = self.table + "_progress"
        self.sql = frappe.db.sql
        self.sql(f"CREATE TABLE `{self.table}` (name varchar(140) PRIMARY KEY, customer_number varchar(140), normalized_customer_number varchar(140), start_time datetime(6), event_timestamp datetime(6), creation datetime(6), vobiz_history_phone varchar(140), vobiz_history_time datetime(6), status varchar(140), direction varchar(140), crm_lead varchar(140), patient varchar(140), INDEX history_lookup(vobiz_history_phone,vobiz_history_time,name)) ENGINE=InnoDB")
        self.sql(f"CREATE TABLE `{self.progress}` (name varchar(140) PRIMARY KEY, value text) ENGINE=InnoDB")
        for name, phone in [("a", "+919876543210"), ("b", "+14155552671"), ("c", "sip:invalid")]:
            self.sql(f"INSERT INTO `{self.table}` (name,customer_number,creation) VALUES (%s,%s,'2026-01-01')", (name, phone))
        frappe.db.commit()
        def redirected(query, values=(), **kwargs):
            if "tabDefaultValue" in query:
                return self.sql(f"SELECT value FROM `{self.progress}` WHERE name=%s", (values[1],), **kwargs)
            return self.sql(query.replace("tabVobiz Call Log", self.table), values, **kwargs)
        def get_global(key):
            rows = self.sql(f"SELECT value FROM `{self.progress}` WHERE name=%s", (key,))
            return rows[0][0] if rows else None
        def set_global(key, value):
            self.sql(f"INSERT INTO `{self.progress}` VALUES (%s,%s) ON DUPLICATE KEY UPDATE value=VALUES(value)", (key, str(value)))
        def page(cursor, size):
            return self.sql(f"SELECT * FROM `{self.table}` WHERE name>%s ORDER BY name LIMIT %s", (cursor, size), as_dict=True)
        def set_value(doctype, name, updates, **kwargs):
            self.sql(f"UPDATE `{self.table}` SET " + ",".join(f"`{key}`=%s" for key in updates) + " WHERE name=%s", (*updates.values(), name))
        self.patches = [patch.object(frappe.db,"sql",side_effect=redirected), patch.object(frappe.db,"get_global",side_effect=get_global), patch.object(frappe.db,"set_global",side_effect=set_global), patch.object(frappe.db,"set_value",side_effect=set_value), patch.object(frappe.db,"has_column",return_value=True), patch.object(maintenance,"_page",side_effect=page), patch.object(frappe,"conf",frappe._dict())]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        frappe.db.rollback()
        self.sql(f"DROP TABLE `{self.table}`")
        self.sql(f"DROP TABLE `{self.progress}`")
        frappe.db.commit()

    def test_backfill_resume_verify_and_approval_gate(self):
        self.assertEqual(maintenance.backfill(batch_size=2)["updated"],2)
        self.assertIsNone(frappe.db.get_global(maintenance.CURSOR))
        self.assertFalse(maintenance.backfill(batch_size=2,dry_run=False)["done"])
        self.assertTrue(maintenance.backfill(batch_size=2,dry_run=False)["done"])
        self.assertEqual(maintenance.backfill(dry_run=False)["status"],"already_backfilled")
        self.assertEqual(history.count_calls("9876543210"),1)
        self.assertEqual(history.count_calls("+14155552671"),1)
        self.assertFalse(maintenance.verify(batch_size=2)["done"])
        state=maintenance.verify(batch_size=2)
        self.assertTrue(state["done"])
        self.assertEqual(state["invalid_source"],1)
        self.assertEqual(state["missing"]+state["mismatched"]+state["time_mismatched"],0)
        with self.assertRaises(ValueError): maintenance.approve(acknowledge_identity_changes=True)
        from vobiz_ai.maintenance.lookup_indexes import _definitions
        definitions=_definitions(self.table)
        with patch("vobiz_ai.maintenance.lookup_indexes._definitions",return_value=definitions):
            result=maintenance.approve(acknowledge_identity_changes=True,accept_excluded_invalid=True)
        self.assertFalse(result["enabled"])
        self.assertFalse(history.indexed_enabled())
        frappe.conf.vobiz_indexed_call_history=1
        self.assertTrue(history.indexed_enabled())
        plan=self.sql(f"EXPLAIN SELECT COUNT(*) FROM `{self.table}` WHERE vobiz_history_phone=%s",("919876543210",),as_dict=True)
        self.assertEqual(plan[0].key,"history_lookup")

    def test_failed_batch_rolls_back_data_and_checkpoint(self):
        original=frappe.db.set_value.side_effect
        def fail_second(doctype,name,updates,**kwargs):
            if name=="b": raise frappe.QueryTimeoutError("test timeout")
            return original(doctype,name,updates,**kwargs)
        with patch.object(frappe.db,"set_value",side_effect=fail_second):
            result=maintenance.backfill(batch_size=2,dry_run=False)
        self.assertEqual(result["status"],"retry")
        self.assertIsNone(frappe.db.get_global(maintenance.CURSOR))
        self.assertIsNone(self.sql(f"SELECT vobiz_history_phone FROM `{self.table}` WHERE name='a'")[0][0])
        self.assertEqual(maintenance.backfill(batch_size=2,dry_run=False)["updated"],2)

    def test_direct_source_update_updates_key_in_same_transaction(self):
        history.set_call_values("a",{"customer_number":"+14155552671"})
        self.assertEqual(history.count_calls("+14155552671"),1)
        frappe.db.rollback()
        self.assertEqual(history.count_calls("+14155552671"),0)
