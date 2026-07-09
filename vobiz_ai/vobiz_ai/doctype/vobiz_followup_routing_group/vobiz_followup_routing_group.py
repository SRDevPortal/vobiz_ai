import frappe
from frappe.model.document import Document

from vobiz_ai.api.utils import normalize_phone


class VobizFollowupRoutingGroup(Document):
	def validate(self):
		self.normalized_did = normalize_phone(self.did_number)
		self._validate_active_duplicate()

	def _validate_active_duplicate(self):
		if not self.active or not self.sr_followup_id:
			return
		filters = {
			"active": 1,
			"sr_followup_id": self.sr_followup_id,
			"normalized_did": self.normalized_did or "",
			"name": ["!=", self.name],
		}
		duplicate = frappe.db.get_value("Vobiz Followup Routing Group", filters)
		if duplicate:
			frappe.throw(f"Active follow-up routing group already exists for this Follow-up ID/DID: {duplicate}")
