from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase

from vobiz_ai.api.processing import _create_lead
from vobiz_ai.api.utils import find_by_phone


class TestCRMLeadMatching(FrappeTestCase):
	def test_matches_existing_lead_by_normalized_phone(self):
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Normalized Phone",
				"mobile_no": "+91 (98765) 40123",
				"status": "New",
			}
		).insert(ignore_permissions=True)

		self.assertEqual(
			find_by_phone("CRM Lead", ("mobile_no", "phone"), "98765-40123"),
			lead.name,
		)

	def test_create_lead_is_idempotent_for_same_normalized_phone(self):
		call = SimpleNamespace(
			account_mapping=None,
			customer_number="+91 98765 40987",
			normalized_customer_number="919876540987",
			did_number="",
		)
		before = frappe.db.count("CRM Lead")

		first = _create_lead(call)
		second = _create_lead(
			SimpleNamespace(
				account_mapping=None,
				customer_number="98765-40987",
				normalized_customer_number="9876540987",
				did_number="",
			)
		)

		self.assertEqual(second, first)
		self.assertEqual(frappe.db.count("CRM Lead"), before + 1)
