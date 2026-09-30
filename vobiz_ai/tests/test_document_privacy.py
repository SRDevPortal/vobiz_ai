import unittest
from unittest.mock import patch
from types import SimpleNamespace
from vobiz_ai import document_privacy as privacy

class DocumentPrivacyTests(unittest.TestCase):
    def test_rest_versions_and_encoded_names(self):
        dt = sorted(privacy.RAW_DOCTYPES)[0]
        for prefix in ('/api/resource/', '/api/v1/resource/', '/api/v2/document/'):
            self.assertTrue(privacy.is_raw_request(prefix + dt.replace(' ', '%20') + '/record', {}))

    def test_generic_read_list_export_and_document_method(self):
        dt = sorted(privacy.RAW_DOCTYPES)[0]
        for method in ('frappe.client.get', 'frappe.desk.reportview.get', 'frappe.desk.reportview.export_query', 'run_doc_method'):
            self.assertTrue(privacy.is_raw_request('/api/method/' + method, {'doc': '{"doctype": "' + dt + '"}'}))
        self.assertTrue(privacy.is_raw_request('/printview', {'doctype': dt}))

    def test_reviewed_rpc_and_unrelated_documents_unchanged(self):
        dt = sorted(privacy.RAW_DOCTYPES)[0]
        self.assertFalse(privacy.is_raw_request('/api/method/vobiz_ai.api.reviewed', {'doctype': dt}))
        self.assertFalse(privacy.is_raw_request('/api/method/frappe.client.get', {'doctype': 'Patient'}))

    def test_only_restricted_http_access_denied(self):
        dt = sorted(privacy.RAW_DOCTYPES)[0]
        import frappe
        with patch.object(privacy, 'frappe') as fake, patch.object(privacy, 'restricted', return_value=True):
            fake.PermissionError = frappe.PermissionError
            fake.local.request = SimpleNamespace(path='/api/resource/' + dt)
            fake.form_dict = {}
            with self.assertRaises(frappe.PermissionError): privacy.guard_request()
        with patch.object(privacy, 'frappe') as fake, patch.object(privacy, 'restricted', return_value=False):
            fake.local.request = SimpleNamespace(path='/api/resource/' + dt)
            fake.form_dict = {}
            privacy.guard_request()
        with patch.object(privacy, 'frappe') as fake:
            fake.local.request = None
            privacy.guard_request()
