from frappe.model.document import Document


class VobizCallLog(Document):
	def on_trash(self):
		try:
			from vobiz_click_to_call.services.delete_cleanup import cleanup_call_log_reverse_links

			cleanup_call_log_reverse_links(self)
		except Exception:
			pass

	def before_save(self):
		if self.request_uuid and not self.request_id:
			self.request_id = self.request_uuid
		elif self.request_id and not self.request_uuid:
			self.request_uuid = self.request_id

		if self.transcript_text and not self.transcription_text:
			self.transcription_text = self.transcript_text
		elif self.transcription_text and not self.transcript_text:
			self.transcript_text = self.transcription_text

		if self.recording_duration and not self.recording_duration_ms:
			self.recording_duration_ms = self.recording_duration
		elif self.recording_duration_ms and not self.recording_duration:
			self.recording_duration = self.recording_duration_ms

		if self.ai_sentiment and not self.sentiment:
			self.sentiment = self.ai_sentiment
		elif self.sentiment and not self.ai_sentiment:
			self.ai_sentiment = self.sentiment

		if self.transcription_text and not self.transcript_hash:
			from vobiz_ai.api.utils import hash_text

			self.transcript_hash = hash_text(self.transcription_text)
