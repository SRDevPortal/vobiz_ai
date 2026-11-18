import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import frappe
from vobiz_ai import history
from vobiz_ai.maintenance import call_history as ops


class HistoryIdentityTests(unittest.TestCase):
    def test_indian_formats_share_one_key(self):
        for value in ["9876543210","+91 98765-43210","00919876543210","09876543210","919876543210"]:
            self.assertEqual(history.phone_key(value),"919876543210")

    def test_international_country_codes_are_preserved(self):
        for value in ["+1 415 555 2671","0014155552671","14155552671"]:
            self.assertEqual(history.phone_key(value),"14155552671")
        self.assertEqual(history.phone_key("+44 20 8366 1177"),"442083661177")
        self.assertNotEqual(history.phone_key("+1 415 555 2671"),"914155552671")

    def test_invalid_extensions_and_sip_are_not_phone_keys(self):
        for value in ["",None,"00","0000000000","99999999999999999999","sip:9876543210@example.com","+91 9876543210 ext 5","1-800-FLOWERS"]:
            self.assertEqual(history.phone_key(value),"")

    def test_invalid_raw_identity_never_uses_a_different_fallback(self):
        row={"customer_number":"00","normalized_customer_number":"919876543210"}
        self.assertEqual(history.expected_values(row)[history.PHONE],"")
        row["customer_number"]=""
        self.assertEqual(history.expected_values(row)[history.PHONE],"919876543210")

    def test_timestamp_precedence_and_corrections(self):
        row=dict(creation="2026-11-01",event_timestamp="2026-11-02",start_time="2026-11-03")
        self.assertEqual(history.expected_values(row)[history.TIME],datetime(2026,11,3))
        row["start_time"]=None
        self.assertEqual(history.expected_values(row)[history.TIME],datetime(2026,11,2))
        row["event_timestamp"]=None
        self.assertEqual(history.expected_values(row)[history.TIME],datetime(2026,11,1))

    @patch.object(history,"frappe")
    def test_activation_requires_approved_version(self,f):
        f.conf={};self.assertFalse(history.indexed_enabled());f.db.get_global.assert_not_called()
        f.conf={"vobiz_indexed_call_history":1};f.db.get_global.return_value=""
        with self.assertRaises(RuntimeError):history.indexed_enabled()
        f.db.get_global.return_value=history.VERSION;self.assertTrue(history.indexed_enabled())

    @patch.object(history,"frappe")
    def test_indexed_queries_are_exact_and_invalid_queries_do_no_work(self,f):
        self.assertEqual(history.count_calls("00"),0);self.assertEqual(history.latest_call("00"),{})
        f.db.sql.assert_not_called()
        f.db.sql.return_value=[(2,)]
        self.assertEqual(history.count_calls("9876543210"),2)
        self.assertEqual(f.db.sql.call_args.args[1],("919876543210",))
        f.db.sql.return_value=[];history.latest_call("9876543210")
        query=f.db.sql.call_args.args[0]
        self.assertNotIn("LIKE",query);self.assertNotIn("COALESCE",query)
        self.assertIn("vobiz_history_time",query)

    @patch.object(history,"frappe")
    def test_direct_updates_use_latest_locked_source_and_keep_other_values(self,f):
        f.db.has_column.return_value=True
        f.db.sql.return_value=[dict(customer_number="+14155552671",creation="2026-11-01",start_time=None,event_timestamp=None)]
        history.set_call_values("A",{"start_time":"2026-11-03","status":"Connected"})
        values=f.db.set_value.call_args.args[2]
        self.assertEqual(values[history.PHONE],"14155552671")
        self.assertEqual(values[history.TIME],datetime(2026,11,3))
        self.assertEqual(values["status"],"Connected")
        self.assertIn("FOR UPDATE",f.db.sql.call_args.args[0]);f.db.commit.assert_not_called()

    @patch.object(history,"frappe")
    def test_unrelated_direct_update_avoids_extra_row_read(self,f):
        history.set_call_values("A",{"status":"Completed"})
        f.db.sql.assert_not_called()

    def test_document_sync_supports_code_before_migration(self):
        doc=MagicMock();doc.meta.has_field.return_value=False
        history.sync_document(doc);doc.set.assert_not_called()


class HistoryMaintenanceTests(unittest.TestCase):
    @patch.object(ops,"frappe")
    def test_progress_ignores_stale_defaults_cache(self,f):
        f.db.get_global.return_value="stale"
        f.db.sql.return_value=[("committed-cursor",)]
        self.assertEqual(ops._read_progress(ops.CURSOR),"committed-cursor")
        f.db.get_global.assert_not_called()
        self.assertEqual(f.db.sql.call_args.args[1],("__global",ops.CURSOR))
        f.db.sql.return_value=[]
        self.assertIsNone(ops._read_progress(ops.CURSOR))
        f.db.sql.return_value=[("one",),("two",)]
        with self.assertRaises(RuntimeError):ops._read_progress(ops.CURSOR)

    @patch.object(ops,"frappe")
    def test_missing_schema_is_not_backfill_completion(self,f):
        f.conf={};f.db.has_column.return_value=False
        with self.assertRaises(RuntimeError):ops.backfill()
        f.db.set_global.assert_not_called()

    @patch.object(ops,"frappe")
    def test_backfill_requires_inactive_lookup(self,f):
        f.conf={"vobiz_indexed_call_history":1}
        with self.assertRaises(RuntimeError):ops.backfill(dry_run=False)

    def test_mutations_are_not_http_whitelisted(self):
        for fn in [ops.backfill,ops.verify,ops.approve]:self.assertNotIn(fn,frappe.whitelisted)

    @patch.object(ops,"_lock")
    @patch.object(ops,"_schema")
    @patch.object(ops,"frappe")
    def test_approval_rejects_unverified_or_mismatched_history(self,f,schema,lock):
        f.conf={};f.db.sql.return_value=[("1",)]
        base=dict(version=history.VERSION,done=False,missing=0,mismatched=0,time_mismatched=0,invalid_source=0,normalized_fallback=0)
        with patch.object(ops,"_state",return_value=base):
            with self.assertRaises(RuntimeError):ops.approve(True)
            base["done"]=True;base["mismatched"]=1
            with self.assertRaises(RuntimeError):ops.approve(True)
        f.db.set_global.assert_not_called()

    @patch.object(ops,"_lock")
    @patch.object(ops,"_schema")
    @patch.object(ops,"frappe")
    def test_approval_requires_explicit_identity_review(self,f,schema,lock):
        f.conf={};f.db.sql.return_value=[("1",)]
        state=dict(version=history.VERSION,done=True,missing=0,mismatched=0,time_mismatched=0,invalid_source=0,normalized_fallback=0)
        with patch.object(ops,"_state",return_value=state):
            with self.assertRaises(ValueError):ops.approve()
            state["invalid_source"]=1
            with self.assertRaises(ValueError):ops.approve(True)
            state["invalid_source"]=0;state["normalized_fallback"]=1
            with self.assertRaises(ValueError):ops.approve(True)
        f.db.set_global.assert_not_called()


if __name__=="__main__":unittest.main()
