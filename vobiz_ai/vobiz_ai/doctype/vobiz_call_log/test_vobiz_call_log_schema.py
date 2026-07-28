from __future__ import annotations

import json
import unittest
from pathlib import Path


DOCTYPE_JSON = Path(__file__).with_name("vobiz_call_log.json")


class TestVobizCallLogSchema(unittest.TestCase):
    def test_disposition_is_data_field(self):
        schema = json.loads(DOCTYPE_JSON.read_text(encoding="utf-8"))
        disposition = next(field for field in schema["fields"] if field.get("fieldname") == "disposition")

        self.assertEqual(disposition["fieldtype"], "Data")
        self.assertNotIn("options", disposition)
