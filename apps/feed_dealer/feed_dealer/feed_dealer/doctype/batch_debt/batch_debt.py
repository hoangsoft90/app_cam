import frappe
from frappe.model.document import Document
from frappe.utils import date_diff, flt, getdate, nowdate


class BatchDebt(Document):
	"""Allocation layer only.

	This DocType does NOT own the truth about money: it maps a slice of an
	Accounts Receivable document to one Batch. Every derived field is recomputed
	here from `Payment Allocation` rows, and the DocType metadata marks those
	fields read_only.
	"""

	def validate(self):
		# `sales_invoice` is reqd=0 in metadata because `mandatory_depends_on` is a
		# client-side rule that any API/import path bypasses. Enforce it here.
		if not self.is_opening_balance and not self.sales_invoice:
			frappe.throw(
				"Phải chọn 'Hóa đơn bán hàng' (sales_invoice) khi đây không phải nợ đầu kỳ. "
				"Nếu là nợ đầu kỳ, tích 'Nợ đầu kỳ' và chọn bút toán nợ đầu kỳ.",
				title="Thiếu hóa đơn bán hàng",
			)
		# NOTE: `opening_journal_entry` is deliberately NOT required here in P0.
		# P0's contract is that an opening-balance debt is creatable with
		# sales_invoice empty (that is how migrated historical debt starts life).
		# The journal entry is created by the P0.5 migration boundary, which is the
		# right place to enforce it: enforcing it at DocType level would block the
		# very import it exists to support.
		self.calculate_derived_fields()

	def before_save(self):
		self.calculate_derived_fields()

	def calculate_derived_fields(self):
		"""Recompute paid / returned / outstanding / overdue / status.

		Order matters: outstanding and overdue are computed BEFORE status,
		otherwise an overdue debt can be persisted with status "Chưa trả".
		"""
		self.paid_amount = self._get_paid_from_allocations()
		# `returned_amount` is fed by credit notes (P1D); it is read-only here so
		# the only legitimate writer is the controller once returns land.
		self.returned_amount = flt(self.returned_amount)
		self.outstanding_amount = flt(self.allocated_amount) - flt(self.paid_amount) - flt(
			self.returned_amount
		)
		self.overdue_days = 0
		self.late_payment_fee = 0
		if self.outstanding_amount > 0 and self.due_date:
			today = getdate(nowdate())
			due = getdate(self.due_date)
			if today > due:
				self.overdue_days = date_diff(today, due)
				# Rate comes from Feed Dealer Settings; the fallback matches the
				# Settings default (0.00022/day) and is deliberately NOT zero, so a
				# missing setting cannot silently waive late fees.
				rate = flt(
					frappe.db.get_single_value("Feed Dealer Settings", "late_payment_interest_rate")
				) or 0.00022
				self.late_payment_fee = self.outstanding_amount * rate * self.overdue_days

		self.status = self._derive_status()

	def _derive_status(self):
		if self.outstanding_amount <= 0:
			return "Đã trả"
		if self.overdue_days > 0:
			return "Quá hạn"
		if flt(self.paid_amount) > 0:
			return "Một phần"
		return "Chưa trả"

	def _get_paid_from_allocations(self):
		"""Sum submitted Payment Allocation rows pointing at this debt.

		`Payment Allocation` is a standalone submitted document (one per FIFO
		slice of a Payment Entry), so "submitted" here means its own docstatus=1 --
		cancelling the Payment Entry cancels these rows and the debt reopens.
		"""
		if self.is_new() or not self.name:
			return 0.0
		result = frappe.db.get_all(
			"Payment Allocation",
			filters={"batch_debt": self.name, "docstatus": 1},
			fields=[{"SUM": "paid_amount", "as": "total"}],
		)
		return flt(result[0]["total"]) if result else 0.0
