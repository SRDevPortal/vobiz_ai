from frappe.model.document import Document


class VobizAccountMapping(Document):
	def before_save(self):
		from vobiz_ai.api.utils import normalize_phone

		self.normalized_did = normalize_phone(self.did_number)
