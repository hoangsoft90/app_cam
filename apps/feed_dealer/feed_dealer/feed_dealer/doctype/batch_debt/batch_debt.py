import frappe
from frappe.model.document import Document
from frappe.utils import date_diff, flt, getdate, nowdate


def _returned_sum(debt_name):
	"""POSITIVE total returned against this debt, from submitted credit notes.

	Lives here, not in another module: the DocType controller is imported by
	frappe's sync/orphan scan (`get_controller`) on EVERY migrate, and any
	ImportError here makes frappe treat the DocType as orphaned code and DELETE
	its DocType record (measured: one wrong import path wiped the Batch Debt
	DocType while leaving the data table intact). Zero app-internal imports in
	DocType controllers is the safe rule.

	Sign: return-invoice line net amounts are NEGATIVE (make_return_doc negates
	qty), so the stored value is `-1 * SUM(net_amount)` -- a POSITIVE number.
	`outstanding = allocated - paid - returned` then drops when goods come back
	and rises back when a credit note is cancelled (its lines stop counting).
	One source of truth: nothing else stores a second copy of the figure
	(P1D prompt item 2), and every recalculation path converges here.
	"""
	if not debt_name:
		return 0.0
	result = frappe.get_all(
		"Sales Invoice Item",
		filters={
			"parenttype": "Sales Invoice",
			"batch_debt": debt_name,
			"docstatus": 1,
		},
		fields=[{"SUM": "net_amount", "as": "returned"}],
	)
	return -flt(result[0].returned) if result else 0.0


def _offset_sum(debt_name):
	"""POSITIVE total netted against this debt by submitted Journal Entries (P1F).

	Livestock offset: when a farmer sells animals back, the dealer raises a Purchase
	Invoice (what we owe them) and nets it against what they owe us with a Journal
	Entry -- Dr Accounts Payable / Cr Accounts Receivable. That JE CREDITS the
	customer's receivable, so it belongs to the same "reduces the debt" family as a
	payment, not to `returned_amount` (which is about goods coming back).

	Attribution is explicit: the offset helper writes `batch_debt` onto the
	receivable line of the JE (custom field on Journal Entry Account), so
	credit - debit == the amount this debt was netted by.

	Same one-source-of-truth rule as `_returned_sum`: nothing stores a second copy,
	and cancelling the JE drops its rows from the SUM, so the debt reopens.
	"""
	if not debt_name:
		return 0.0
	result = frappe.get_all(
		"Journal Entry Account",
		filters={"batch_debt": debt_name, "docstatus": 1},
		fields=[
			{"SUM": "credit_in_account_currency", "as": "credit"},
			{"SUM": "debit_in_account_currency", "as": "debit"},
		],
	)
	if not result:
		return 0.0
	return flt(result[0].credit) - flt(result[0].debit)


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
		# P1D: derived from submitted return-invoice lines (see `_returned_sum`).
		# Stored POSITIVE; only this controller writes the field.
		self.returned_amount = self._get_returned_from_credit_notes()
		# P1F: netted by a Journal Entry (livestock offset; see `_offset_sum`).
		self.offset_amount = self._get_offset_from_journal_entries()
		self.outstanding_amount = (
			flt(self.allocated_amount)
			- flt(self.paid_amount)
			- flt(self.returned_amount)
			- flt(self.offset_amount)
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

	def _get_offset_from_journal_entries(self):
		"""P1F: POSITIVE total netted by submitted Journal Entries (see `_offset_sum`)."""
		if self.is_new() or not self.name:
			return 0.0
		return _offset_sum(self.name)

	def _get_returned_from_credit_notes(self):
		"""P1D: POSITIVE total returned, derived from submitted credit-note lines.

		`outstanding = allocated - paid - returned` subtracts it and rises back
		when a credit note is cancelled (its lines stop counting). Every
		recalculation path converges here -- one source of truth, no second copy.
		"""
		if self.is_new() or not self.name:
			return 0.0
		return _returned_sum(self.name)
