"""Livestock Sale row (child of Feed Batch) -- P1F.

A row records animals the farmer sells BACK to the dealer. With `offset_to_debt`
set, the value is netted against the farmer's batch debt by a standard
double-entry Journal Entry (Dr Accounts Payable / Cr Accounts Receivable) -- see
`feed_dealer.events.livestock_offset`. This controller only validates the row:
the money movement stays in an explicit, permission-checked API so nobody's debt
changes as a side effect of editing a grid.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class LivestockSale(Document):
	def validate(self):
		if flt(self.total_amount) < 0:
			frappe.throw(f"Dòng {self.idx}: thành tiền không được âm.")
		if self.purchase_invoice and not flt(self.total_amount):
			self.total_amount = flt(
				frappe.db.get_value("Purchase Invoice", self.purchase_invoice, "grand_total")
			)
		if self.offset_to_debt:
			if not self.purchase_invoice:
				frappe.throw(
					f"Dòng {self.idx}: muốn cấn trừ nợ thì phải có 'Hoá đơn mua vào' "
					f"(purchase_invoice) — cấn trừ dựa trên chứng từ mua thật.",
					title="Thiếu hoá đơn mua vào",
				)
			if flt(self.total_amount) <= 0:
				frappe.throw(f"Dòng {self.idx}: thành tiền phải lớn hơn 0 để cấn trừ.")
