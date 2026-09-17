import frappe
from frappe.model.document import Document
from frappe.utils import flt


class SalesReturnItem(Document):
	"""Row of a Sales Return Request (P1D).

	`amount` is recomputed from the ORIGINAL invoice line's rate (the request
	cannot dictate a price); qty is the returned quantity (positive on the
	request; the credit note negates it, exactly like ERPNext's own mapper).
	The returned total lives in ONE place: `BatchDebt.calculate_derived_fields`
	(see `_returned_sum` there). This module stores no copy of it.
	"""

	def validate(self):
		if flt(self.qty) <= 0:
			frappe.throw(
				f"Dòng {self.idx}: số lượng trả lại phải lớn hơn 0.",
				title="Số lượng không hợp lệ",
			)
		if self.return_line and not flt(self.rate):
			self.rate = flt(
				frappe.db.get_value("Sales Invoice Item", self.return_line, "rate")
			)
		self.amount = flt(self.rate) * flt(self.qty)
