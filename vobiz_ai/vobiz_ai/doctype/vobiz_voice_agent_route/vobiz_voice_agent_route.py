import frappe
from frappe.model.document import Document

from vobiz_ai.api.utils import normalize_phone


class VobizVoiceAgentRoute(Document):
	def validate(self):
		self.normalized_did = normalize_phone(self.did_number)
		if self.active and self.normalized_did:
			duplicate = frappe.db.get_value(
				"Vobiz Voice Agent Route",
				{
					"normalized_did": self.normalized_did,
					"active": 1,
					"name": ["!=", self.name],
				},
			)
			if duplicate:
				frappe.throw(f"Active voice route already exists for this DID: {duplicate}")
