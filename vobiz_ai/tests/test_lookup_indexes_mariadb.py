"""Opt-in online-DDL tests; creates/drops only a uniquely named local test table."""
import os
import unittest
import uuid
import frappe
from vobiz_ai.maintenance.lookup_indexes import repair_specs


@unittest.skipUnless(os.environ.get("LOCAL_PHONE_TEST_SITE"),"explicit local site required")
class LookupIndexesMariaDBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        site=os.environ["LOCAL_PHONE_TEST_SITE"]
        if not site.endswith((".local",".localhost")):raise ValueError("Local site required")
        frappe.init(site=site);frappe.connect();frappe.set_user("Administrator")
    @classmethod
    def tearDownClass(cls):frappe.destroy()
    def setUp(self):
        self.table="_index_test_"+uuid.uuid4().hex
        frappe.db.sql(f"CREATE TABLE `{self.table}` (name varchar(140) PRIMARY KEY,phone varchar(140),modified datetime(6)) ENGINE=InnoDB")
        self.specs={"phone":(self.table,"lookup_phone_modified",("phone","modified"))}
    def tearDown(self):
        frappe.db.rollback();frappe.db.sql(f"DROP TABLE `{self.table}`");frappe.db.commit()
    def test_plan_online_apply_and_verified_reapply(self):
        self.assertEqual(repair_specs(self.specs)[0]["status"],"planned")
        self.assertEqual(repair_specs(self.specs,"phone",True)[0]["status"],"created")
        self.assertEqual(repair_specs(self.specs,"phone",True)[0]["status"],"already_present")
    def test_equivalent_name_reused_without_duplicate(self):
        frappe.db.sql(f"ALTER TABLE `{self.table}` ADD INDEX existing(phone,modified,name)")
        self.assertEqual(repair_specs(self.specs,"phone",True)[0]["index"],"existing")
        rows=frappe.db.sql("SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s",(self.table,))
        self.assertEqual({r[0] for r in rows},{"PRIMARY","existing"})

    def test_clinical_history_index_removes_filesort(self):
        # Equivalent query shape, only synthetic rows in this test's own table.
        frappe.db.sql(f"ALTER TABLE `{self.table}` ADD COLUMN encounter_date date, ADD COLUMN creation datetime(6)")
        for number in range(300):
            frappe.db.sql(f"INSERT INTO `{self.table}` (name,phone,encounter_date,creation) VALUES (%s,%s,'2026-01-01','2026-01-01')",(str(number), 'patient-'+str(number % 10)))
        frappe.db.commit()
        query=f"EXPLAIN SELECT name,encounter_date FROM `{self.table}` WHERE phone=%s ORDER BY encounter_date DESC,creation DESC LIMIT 100"
        before=frappe.db.sql(query,('patient-1',),as_dict=True)[0]
        self.assertIn('filesort',before.Extra.lower())
        specs={'history':(self.table,'clinical_history',('phone','encounter_date','creation'))}
        self.assertEqual(repair_specs(specs,'history',True)[0]['status'],'created')
        after=frappe.db.sql(query,('patient-1',),as_dict=True)[0]
        self.assertEqual(after.key,'clinical_history')
        self.assertNotIn('filesort',after.Extra.lower())
        self.assertEqual(repair_specs(specs,'history',True)[0]['status'],'already_present')

    def test_invoice_reference_index_filters_multiple_encounters(self):
        frappe.db.sql(f"ALTER TABLE `{self.table}` ADD COLUMN docstatus int, ADD COLUMN posting_date date, ADD COLUMN creation datetime(6)")
        for number in range(300):
            frappe.db.sql(f"INSERT INTO `{self.table}` (name,phone,docstatus,posting_date,creation) VALUES (%s,%s,1,'2026-01-01','2026-01-01')",(str(number), 'encounter-'+str(number)))
        frappe.db.commit()
        specs={'invoice':(self.table,'invoice_reference',('phone','docstatus','posting_date','creation'))}
        repair_specs(specs,'invoice',True)
        result=frappe.db.sql(f"EXPLAIN SELECT name FROM `{self.table}` WHERE phone IN (%s,%s) AND docstatus=1 ORDER BY posting_date DESC,creation DESC LIMIT 100",('encounter-1','encounter-2'),as_dict=True)[0]
        self.assertEqual(result.key,'invoice_reference')
        self.assertNotEqual(result.type,'ALL')
        # Sorting across multiple encounters may still be necessary.
