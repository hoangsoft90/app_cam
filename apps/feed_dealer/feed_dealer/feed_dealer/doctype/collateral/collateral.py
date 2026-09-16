import frappe
from frappe.model.document import Document
from frappe.utils import flt

# The haircut is part of the credit formula (plan_final_hardening.md §4.6:
# "Hạn mức theo tài sản thế chấp (×0.7)"). It lives here, on the document the
# value belongs to, so the Credit Score controller never re-derives it.
EFFECTIVE_FACTOR = 0.7


class Collateral(Document):
	def validate(self):
		self.effective_value = flt(self.estimated_value) * EFFECTIVE_FACTOR
