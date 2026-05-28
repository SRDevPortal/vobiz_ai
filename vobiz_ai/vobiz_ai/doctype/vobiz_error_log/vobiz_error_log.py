from frappe.model.document import Document

from vobiz_ai.api.utils import as_json, parse_json


class VobizErrorLog(Document):
	def before_save(self):
		if not self.payload:
			return
		try:
			self.payload = as_json(parse_json(self.payload))
		except Exception:
			pass
