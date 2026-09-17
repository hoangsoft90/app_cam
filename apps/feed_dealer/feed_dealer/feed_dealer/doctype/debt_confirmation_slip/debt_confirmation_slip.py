"""Debt Confirmation Slip (P1F): a printable snapshot of what a customer owes.

`debt_details` is a READ-ONLY COPY of the allocation view at print time -- the
slip owns no money, it states it. Filling it from Batch Debt means the customer
confirms a figure that matches the ledger view exactly, and the total is always
`SUM(outstanding_amount)` of the rows, recomputed on every save.

Printing: the app ships the Jinja Print Format `Phiếu xác nhận nợ (feed_dealer)`
(installed by patches). The slip is a submitted document, so the print + the
attached photo become the audit pair the plan asks for (Week 3 step 13).
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate

PRINT_FORMAT = "Phiếu xác nhận nợ (feed_dealer)"


def _period_bounds(customer):
	"""The window that actually contains the customer's debts (plus today).

	`period_from`/`period_to` are reqd on the DocType (a human filling the form
	must state the period), but an API caller usually wants "everything open", so
	the default is derived from the data instead of a magic date: the earliest and
	latest due dates among the customer's debts, widened to include today.
	"""
	rows = frappe.get_all(
		"Batch Debt",
		filters={"customer": customer, "docstatus": 1},
		fields=[{"MIN": "due_date", "as": "first"}, {"MAX": "due_date", "as": "last"}],
	)
	today = getdate(nowdate())
	first = getdate(rows[0].first) if rows and rows[0].first else today
	last = getdate(rows[0].last) if rows and rows[0].last else today
	return min(first, today), max(last, today)


def open_debts(customer, period_from=None, period_to=None):
	"""The customer's open Batch Debts, optionally limited to a due-date window."""
	filters = [
		["customer", "=", customer],
		["docstatus", "=", 1],
		["outstanding_amount", ">", 0],
	]
	if period_from:
		filters.append(["due_date", ">=", period_from])
	if period_to:
		filters.append(["due_date", "<=", period_to])
	return frappe.get_all(
		"Batch Debt",
		filters=filters,
		fields=[
			"name",
			"batch",
			"allocated_amount",
			"paid_amount",
			"outstanding_amount",
			"due_date",
			"overdue_days",
		],
		order_by="due_date asc, name asc",
	)


class DebtConfirmationSlip(Document):
	def validate(self):
		if not self.customer:
			frappe.throw("Phải chọn khách hàng cho phiếu xác nhận nợ.")
		self.confirmation_date = self.confirmation_date or nowdate()
		if self.period_from and self.period_to and self.period_from > self.period_to:
			frappe.throw("Khoảng thời gian không hợp lệ: 'Từ ngày' lớn hơn 'Đến ngày'.")
		if self.confirmed_by_customer and not self.signed_photo:
			frappe.throw(
				"Đã tích 'Khách đã xác nhận' thì phải đính kèm ảnh chữ ký (signed_photo).",
				title="Thiếu chữ ký",
			)
		self.total_confirmed_debt = flt(sum(flt(row.outstanding_amount) for row in self.debt_details))

	def fill_from_open_debts(self):
		"""Rebuild `debt_details` from the customer's open Batch Debts."""
		self.debt_details = []
		for debt in open_debts(self.customer, self.period_from, self.period_to):
			self.append(
				"debt_details",
				{
					"batch_debt": debt.name,
					"batch": debt.batch,
					"allocated_amount": debt.allocated_amount,
					"paid_amount": debt.paid_amount,
					"outstanding_amount": debt.outstanding_amount,
					"due_date": debt.due_date,
					"overdue_days": debt.overdue_days,
				},
			)
		self.total_confirmed_debt = flt(sum(flt(row.outstanding_amount) for row in self.debt_details))
		return self.debt_details


@frappe.whitelist()
def build_slip(customer, period_from=None, period_to=None):
	"""Create a slip pre-filled with the customer's open debt (API).

	The period is optional here: when it is not given, it is derived from the
	customer's own debts (`_period_bounds`) rather than a hard-coded date range,
	so "confirm everything they owe" is a one-argument call.
	"""
	if not (period_from and period_to):
		default_from, default_to = _period_bounds(customer)
		period_from = period_from or default_from
		period_to = period_to or default_to
	doc = frappe.get_doc(
		{
			"doctype": "Debt Confirmation Slip",
			"customer": customer,
			"period_from": period_from,
			"period_to": period_to,
		}
	)
	doc.check_permission("create")
	doc.fill_from_open_debts()
	doc.insert()
	return {"name": doc.name, "total_confirmed_debt": doc.total_confirmed_debt, "rows": len(doc.debt_details)}
