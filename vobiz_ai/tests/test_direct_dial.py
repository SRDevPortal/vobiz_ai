import json
import unittest
from unittest.mock import MagicMock, patch
import frappe
from vobiz_ai.api import call_log

class DirectDialReferenceTests(unittest.TestCase):
    def test_background_sync_does_not_attach_unknown_or_ambiguous_direct_destination(self):
        doc = frappe._dict(request_json=json.dumps({'direct_dial': True}), customer_number='+919876543210')
        with patch.object(call_log, 'find_by_phone') as lookup:
            call_log.sync_reference_links(doc)
            lookup.assert_not_called()
            self.assertIsNone(doc.crm_lead)
            self.assertIsNone(doc.patient)

    def test_explicit_authorized_reference_is_kept(self):
        doc = frappe._dict(reference_doctype='Patient', reference_name='PAT',
                           request_json=json.dumps({'direct_dial': True}))
        with patch.object(call_log, 'find_by_phone') as lookup:
            call_log.sync_reference_links(doc)
            lookup.assert_not_called()
            self.assertEqual(doc.patient, 'PAT')

    def test_existing_non_direct_autolinking_is_preserved(self):
        doc = frappe._dict(customer_number='+919876543210')
        with patch.object(call_log, 'find_by_phone', side_effect=['PAT', 'LEAD']) as lookup:
            call_log.sync_reference_links(doc)
            self.assertEqual(lookup.call_count, 2)
            self.assertEqual(doc.patient, 'PAT')
            self.assertEqual(doc.crm_lead, 'LEAD')
