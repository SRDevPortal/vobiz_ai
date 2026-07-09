import frappe
from frappe.model.document import Document

from vobiz_ai.api.utils import normalize_phone


class VobizFollowupRoutingAgent(Document):
	def validate(self):
		self.normalized_agent_phone = normalize_phone(self.agent_phone)
		if self.enabled and not self.normalized_agent_phone:
			frappe.throw("Agent Phone is required for enabled follow-up routing agents.")
