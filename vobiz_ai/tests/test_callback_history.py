from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import frappe
from vobiz_ai.api.call_log import append_callback


class CallbackHistoryTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        for name, value in (("db", self.db), ("get_doc", MagicMock())):
            p = patch.object(frappe, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(frappe.utils, "now", return_value="2026-09-11 14:00:00")
        p.start()
        self.addCleanup(p.stop)

    def test_append_preserves_state_and_retains_latest_history(self):
        self.db.sql.side_effect = [[(json.dumps([{"event": str(n)} for n in range(50)]),)], []]
        payload = {"token": "secret", "cmd": "method", "CallUUID": "provider-id"}
        append_callback("CALL", "hangup", payload)
        frappe.get_doc.assert_not_called()
        self.assertIn("FOR UPDATE", self.db.sql.call_args_list[0].args[0])
        sql, params = self.db.sql.call_args.args
        self.assertNotIn("modified", sql)
        self.assertNotIn("status", sql)
        rows = json.loads(params[0])
        self.assertEqual(len(rows), 50)
        self.assertEqual(rows[0]["event"], "1")
        self.assertEqual(rows[-1]["payload"], {"CallUUID": "provider-id"})
        self.assertEqual(json.loads(params[1])["callbacks"], rows)
        self.assertEqual(payload["token"], "secret")
        self.db.commit.assert_not_called()

    def test_deleted_call_does_not_get_recreated(self):
        self.db.sql.return_value = []
        append_callback("DELETED", "hangup", {})
        self.assertEqual(self.db.sql.call_count, 1)
        frappe.get_doc.assert_not_called()

    def test_malformed_history_is_repaired(self):
        for value in (None, "not-json", "{}", "null"):
            with self.subTest(value=value):
                self.db.sql.side_effect = [[(value,)], []]
                append_callback("CALL", "ring", {})
                self.assertEqual(len(json.loads(self.db.sql.call_args.args[1][0])), 1)

    def test_lock_errors_retry_the_entire_job(self):
        for error in (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
            with self.subTest(error=error.__name__):
                self.db.sql.side_effect = error("conflict")
                with self.assertRaises(frappe.RetryBackgroundJobError):
                    append_callback("CALL", "hangup", {})
        self.db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
