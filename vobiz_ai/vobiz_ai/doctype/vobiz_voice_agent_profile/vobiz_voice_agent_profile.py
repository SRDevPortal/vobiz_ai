import frappe
from frappe.model.document import Document

from vobiz_ai.api.utils import get_queue_name, normalize_phone


class VobizVoiceAgentProfile(Document):
	def validate(self):
		self.normalized_did = normalize_phone(self.did_number)
		if self.enabled and self.normalized_did:
			duplicate = frappe.db.get_value(
				"Vobiz Voice Agent Profile",
				{
					"normalized_did": self.normalized_did,
					"enabled": 1,
					"name": ["!=", self.name],
				},
			)
			if duplicate:
				frappe.throw(f"Enabled voice profile already exists for this DID: {duplicate}")

	def on_update(self):
		if not self.enabled or not self.auto_sync_to_livekit:
			return
		if not (self.did_number and self.livekit_inbound_trunk_id and self.livekit_agent_name):
			return
		frappe.enqueue(
			"vobiz_ai.api.livekit.sync_voice_agent_profile_from_save",
			profile=self.name,
			queue=get_queue_name("livekit_queue_name", "vobiz_livekit"),
			timeout=900,
			enqueue_after_commit=True,
			job_name=f"Sync LiveKit voice agent {self.name}",
		)
