import frappe
from frappe.model.document import Document

from vobiz_ai.api.utils import normalize_phone


class VobizPatientRoutingGroup(Document):
	def validate(self):
		self.normalized_did = normalize_phone(self.did_number)
		self._validate_match_fields()
		self._validate_active_duplicate()

	def _validate_match_fields(self):
		if not self.sr_followup_id and not self.medical_department:
			frappe.throw("Set at least one of Follow-up ID or Medical Department.")
		if self.match_mode == "Follow-up + Department" and (not self.sr_followup_id or not self.medical_department):
			frappe.throw("Follow-up ID and Medical Department are required for Follow-up + Department routes.")

	def _validate_active_duplicate(self):
		if not self.active:
			return
		filters = {
			"active": 1,
			"sr_followup_id": self.sr_followup_id or "",
			"medical_department": self.medical_department or "",
			"normalized_did": self.normalized_did or "",
			"name": ["!=", self.name],
		}
		duplicate = frappe.db.get_value("Vobiz Patient Routing Group", filters)
		if duplicate:
			frappe.throw(f"Active patient routing group already exists for this Follow-up ID/Department/DID: {duplicate}")
