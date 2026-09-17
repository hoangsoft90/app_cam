"""Sales Return Request -> credit note -> returned_amount on Batch Debt (P1D).

Flow (plan v2.1 B1 + phase_01_core_erp.md Week 2 step 10), all controller-side:

  Draft    — staff records what physically came back. ZERO financial side
             effects: no credit note, no debt movement (p1d T3 guards this).
  Approve  — `approve()` creates a REAL ERPNext return invoice (Sales Invoice
             `is_return=1`, `return_against=<original>`) with `make_return_doc`
             (the same mapper the Desk UI uses), fills `batch_debt` per line
             from this request's rows, submits it, then lets the debts
             recalculate. AR keeps the accounting truth; Batch Debt only
             reflects it.
  Reject   — a status change, nothing else.

Facts this design rests on (all read from ERPNext v16 source, not guessed):

* `make_return_doc` (erpnext/controllers/sales_and_purchase_return.py) negates
  qty (`target_doc.qty = -1 * flt(...)`) and links each row to its origin via
  `sales_invoice_item = source_doc.name`. ERPNext's own
  `validate_returned_items` then counts already-returned rows and refuses an
  over-return (that is the T2 guard, enforced by ERPNext itself).
* The credit note's `grand_total` is NEGATIVE and includes VAT. The money
  convention of this app is principal-only (`allocated_amount = SUM(item.amount)`
  of the original invoice, tax excluded — sales_invoice.py docstring). So
  `returned_amount` maps from the credit note's line **net amounts**, never
  from `grand_total` (P1D prompt constraint 1: no gross grand_total subtraction).
* `validate_return` (sales_and_purchase_return.py:22) requires `return_against`
  and runs `validate_returned_items` when one is set — so `return_against` must
  be set before submit, which `make_return_doc` already does.
* ERPNext posts the credit note AGAINST `return_against` (sales_invoice.py:1796
  `against_voucher = self.return_against`): the receivable itself shrinks. This
  layer only mirrors that onto the batch view. AR stays the source of truth
  (P1D prompt constraint 4).

Where `returned_amount` comes from — ONE source of truth (P1D prompt item 2):

  `BatchDebt.calculate_derived_fields()` derives it as
  `-1 * SUM(net_amount)` of SUBMITTED return invoices whose lines name this
  debt in the custom field `batch_debt` (written by this controller only).
  Line net amounts on a return invoice are negative (qty is negated), so the
  stored `returned_amount` is a POSITIVE number: `outstanding = allocated -
  paid - returned` drops when goods come back and rises back when a credit
  note is cancelled (docstatus=2 rows drop out of the SUM, T5). Everyone who
  recalculates (approve, cancel, admin repair) converges on the same number
  because nobody stores a second copy.

The approval stamp lands AFTER the credit note exists (one save that also
records the approver and the `batch_debt_adjusted` flag), and a failed stamp
rolls the note back — there is no persisted moment where approval and money
disagree. The debt mapping runs in `on_submit` of the credit note via
`on_return_note_submitted`, and again synchronously inside `approve()` so an
API caller sees final numbers.

Cancel rules:
* Request cancel is BLOCKED while its credit note is still submitted
  (before_cancel — must run there, not on_cancel: frappe writes docstatus=2
  before on_cancel fires, the same lesson P1A's invoice guard taught).
* Cancelling the credit note itself is allowed and reverses the return on the
  debts (recount via the same derived-field path).
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now

RETURNABLE_DOCSTATUS = 1  # submitted


# --------------------------------------------------------------- money mapping
def _request_items(note):
	"""This request's rows that produced this credit note, keyed by original line."""
	return {
		row.return_line: row
		for row in frappe.get_all(
			"Sales Return Item",
			filters={"parent": note.feed_dealer_return_request, "parenttype": "Sales Return Request"},
			fields=["return_line", "batch_debt", "idx"],
		)
	}


def _map_debts_from_note(note):
	"""Write `batch_debt` onto the note's lines from this request's rows.

	Maps by `return_line` (the exact original invoice row the goods came back
	from); a line whose row cannot be resolved is refused — silently skipping
	money is how P1A's T6 bug lost a million.
	"""
	request_rows = _request_items(note)
	mapped = []
	for line in note.items:
		row = request_rows.get(line.sales_invoice_item)
		if row is None:
			frappe.throw(
				f"Dòng {line.item_code} ({line.qty:,.0f}): không map được về dòng yêu cầu trả hàng "
				f"(sales_invoice_item={line.sales_invoice_item}).",
				title="Lỗi ánh xạ dòng trả hàng",
			)
		if not row.batch_debt:
			frappe.throw(
				f"Dòng {row.idx}: phải chọn 'Khoản nợ bị trừ' — đây là thứ ánh xạ tiền trả lại "
				f"về đúng khoản nợ theo lứa.",
				title="Thiếu khoản nợ",
			)
		line.batch_debt = row.batch_debt
		mapped.append((line.item_code, line.qty, row.batch_debt))
	return mapped


def _recalculate(debt_name):
	"""Persist one debt's derived columns (the P1B pattern: db.set_value)."""
	from feed_dealer.events.payment_entry import _recalculate

	return _recalculate(debt_name)


def _debts_of_note(note):
	return sorted({line.batch_debt for line in note.items if line.get("batch_debt")})


def _debts_of_request(request):
	return sorted({row.batch_debt for row in request.items if row.batch_debt})


def _recalc_debts(debt_names):
	for debt_name in debt_names:
		_recalculate(debt_name)


def on_return_note_submitted(doc, method=None):
	"""Sales Invoice on_submit hook: refresh the batch view for return notes.

	The P1A hook skips return notes (they must not create debts); this one
	recalculates the debts the note's lines name instead.
	"""
	if not doc.is_return:
		return
	_recalc_debts(_debts_of_note(doc))
	return {"recalculated": _debts_of_note(doc)}


def on_return_note_cancelled(doc, method=None):
	"""Sales Invoice on_cancel hook for return notes: recount and reopen."""
	if not doc.is_return:
		return
	_recalc_debts(_debts_of_note(doc))
	return {"recalculated": _debts_of_note(doc)}


# ------------------------------------------------------------------- validation
def _validate_items(request):
	"""Draft-side validation: shapes + ownership, NO financial side effects."""
	if not request.items:
		frappe.throw("Yêu cầu trả hàng phải có ít nhất một dòng hàng trả lại.", title="Thiếu hàng trả lại")
	# One credit note maps ONE original invoice (make_return_doc takes a single
	# `return_against`). When `sales_invoice` is empty on the request, the invoice
	# is inferred from the FIRST debt — lines pointing at other invoices would be
	# silently dropped from the note while the request still approves. Collect
	# each row's debt invoice IN the loop below (never stash on the child row:
	# a fresh row has no such attribute, and reading it before it is set is the
	# AttributeError that killed T1–T5 once) and refuse any mixed set (p1d T6).
	_invoices = {request.sales_invoice or ""}
	for row in request.items:
		if flt(row.qty) <= 0:
			frappe.throw(f"Dòng {row.idx}: số lượng trả lại phải lớn hơn 0.", title="Số lượng không hợp lệ")
		if not row.batch_debt:
			frappe.throw(
				f"Dòng {row.idx}: phải chọn 'Khoản nợ bị trừ' (batch_debt) — tiền trả lại được ánh xạ "
				f"về đúng khoản nợ theo lứa qua trường này.",
				title="Thiếu khoản nợ",
			)
		debt = frappe.db.get_value(
			"Batch Debt", row.batch_debt, ["customer", "docstatus", "sales_invoice", "batch"], as_dict=True
		)
		if not debt:
			frappe.throw(f"Dòng {row.idx}: khoản nợ {row.batch_debt} không tồn tại.", title="Sai khoản nợ")
		if debt.customer != request.customer:
			frappe.throw(
				f"Dòng {row.idx}: khoản nợ {row.batch_debt} thuộc khách {debt.customer}, "
				f"không phải khách của yêu cầu ({request.customer}).",
				title="Sai khách hàng",
			)
		if debt.docstatus != RETURNABLE_DOCSTATUS:
			frappe.throw(
				f"Dòng {row.idx}: khoản nợ {row.batch_debt} phải là nợ đã submit (docstatus=1).",
				title="Nợ chưa submit",
			)
		if not debt.sales_invoice:
			frappe.throw(
				f"Dòng {row.idx}: khoản nợ {row.batch_debt} là nợ đầu kỳ, không có hoá đơn để trả hàng.",
				title="Nợ không có hoá đơn",
			)
		_invoices.add(debt.sales_invoice)
		if len(_invoices) > 1:
			frappe.throw(
				f"Các dòng trả hàng thuộc nhiều hoá đơn khác nhau ({', '.join(sorted(_invoices))}). "
				f"Mỗi yêu cầu chỉ được trả hàng của MỘT hoá đơn — tách thành các yêu cầu riêng.",
				title="Nhiều hoá đơn trong một yêu cầu",
			)
		if request.sales_invoice and debt.sales_invoice != request.sales_invoice:
			frappe.throw(
				f"Dòng {row.idx}: khoản nợ {row.batch_debt} thuộc hoá đơn {debt.sales_invoice}, "
				f"không phải hoá đơn gốc {request.sales_invoice}.",
				title="Sai hoá đơn gốc",
			)
		if row.return_line:
			line = frappe.db.get_value(
				"Sales Invoice Item", row.return_line, ["parent", "parenttype", "item_code"], as_dict=True
			)
			if not line or line.parenttype != "Sales Invoice":
				frappe.throw(f"Dòng {row.idx}: return_line không phải dòng Sales Invoice.", title="Sai dòng gốc")
			if line.parent != (request.sales_invoice or debt.sales_invoice):
				frappe.throw(
					f"Dòng {row.idx}: return_line thuộc hoá đơn {line.parent}, khác hoá đơn gốc của nợ.",
					title="Sai dòng gốc",
				)
			if line.item_code != row.item_code:
				frappe.throw(
					f"Dòng {row.idx}: return_line là {line.item_code}, không phải {row.item_code}.",
					title="Sai mặt hàng",
				)


def _validate_return_line_required(request):
	"""Every line must carry return_line: without it ERPNext cannot count prior returns."""
	missing = [row.idx for row in request.items if not row.return_line]
	if missing:
		frappe.throw(
			f"Các dòng {missing} thiếu 'Dòng hóa đơn gốc' (return_line). Bắt buộc: credit note cần "
			f"nó để ERPNext đếm số lượng đã trả trước đó và chặn trả quá số lượng.",
			title="Thiếu dòng hóa đơn gốc",
		)


def _validate_amounts(request):
	"""`amount` the request claims must equal the original line's rate * qty.

	The credit note is built from the ORIGINAL invoice lines (make_return_doc),
	so the request cannot dictate an arbitrary price: the rate comes from the
	returned line, qty from the request. `amount` is recomputed, not trusted.
	"""
	for row in request.items:
		if not row.return_line:
			continue
		line = frappe.db.get_value("Sales Invoice Item", row.return_line, ["rate", "qty"], as_dict=True)
		row.rate = flt(line.rate)
		row.amount = flt(row.rate) * flt(row.qty)


def _validate_over_return(request):
	"""No line may return more than the original invoice line minus prior returns.

	ERPNext enforces the same bound on the credit note itself
	(validate_returned_items + validate_quantity); this check runs EARLIER, on
	the draft, so the approver learns about an impossible return before a note
	is half-built. Duplicate return lines are refused here too (the credit note
	folds request rows 1:1 onto original lines).
	"""
	seen_lines = set()
	for row in request.items:
		if not row.return_line:
			# An unfinished draft may still have rows without an original line.
			# They carry nothing to compare — and filtering on "" would match the
			# ORIGINAL invoice rows themselves (their sales_invoice_item is empty),
			# which would refuse every draft. Skip them.
			continue
		if row.return_line in seen_lines:
			frappe.throw(
				f"Dòng {row.idx}: dòng gốc {row.return_line} xuất hiện nhiều lần — gộp thành 1 dòng "
				f"trước khi duyệt.",
				title="Trùng dòng gốc",
			)
		seen_lines.add(row.return_line)
		prior = frappe.get_all(
			"Sales Invoice Item",
			filters={
				"parenttype": "Sales Invoice",
				"sales_invoice_item": row.return_line,
				"docstatus": ["<", 2],
			},
			fields=["qty", "parent"],
		)
		# Return rows carry NEGATIVE qty; abs() them.
		prior_qty = sum(abs(flt(r.qty)) for r in prior)
		original_qty = abs(flt(frappe.db.get_value("Sales Invoice Item", row.return_line, "qty")))
		if flt(row.qty) + prior_qty > original_qty + 1e-9:
			frappe.throw(
				f"Dòng {row.idx} ({row.item_code}): trả lại {flt(row.qty):,.0f} nhưng dòng gốc chỉ còn "
				f"{original_qty - prior_qty:,.0f} chưa được trả (gốc {original_qty:,.0f}, "
				f"đã trả trước đó {prior_qty:,.0f}).",
				title="Trả vượt số lượng dòng gốc",
			)


def _validate_value_vs_outstanding(request):
	"""Value returned per debt must not exceed that debt's remaining outstanding.

	`_validate_over_return` only compares QUANTITY against the original line, so a
	debt the customer already part-paid could still be returned in full and drive
	`outstanding = allocated - paid - returned` NEGATIVE (measured shape: allocated
	1,000,000, paid 600,000, return 500,000 -> -100,000). That is not a cosmetic
	negative: `Feed Batch.total_debt` goes negative and P1C's credit-limit sum
	(SUM(Batch Debt outstanding)) then *reduces* the customer's debt and inflates
	the available limit. Refuse instead, and say how much of that debt is still
	returnable so the approver can fix the quantities.

	Needs the amounts recomputed by `_validate_amounts`, so it runs after it.
	"""
	totals = {}
	first_idx = {}
	for row in request.items:
		if not row.return_line or not row.batch_debt:
			continue
		value = flt(row.amount) or flt(row.rate) * flt(row.qty)
		totals[row.batch_debt] = totals.get(row.batch_debt, 0.0) + value
		first_idx.setdefault(row.batch_debt, row.idx)
	for debt_name, value in totals.items():
		outstanding = flt(frappe.db.get_value("Batch Debt", debt_name, "outstanding_amount"))
		if value > outstanding + 1e-6:
			frappe.throw(
				f"Dòng {first_idx[debt_name]}: trả lại {value:,.0f} cho khoản nợ {debt_name} nhưng khoản nợ "
				f"chỉ còn {outstanding:,.0f} chưa thanh toán (khách đã trả một phần). Giảm số lượng trả lại "
				f"hoặc xử lý phần chênh lệch như hoàn tiền — không thể trả hàng vượt số nợ còn lại.",
				title="Trả vượt số nợ còn lại",
			)


# ------------------------------------------------------------------- the approval
def _build_credit_note(request):
	"""A real ERPNext return invoice via the official mapper, mapped to debts."""
	original_name = request.sales_invoice or frappe.db.get_value(
		"Batch Debt", request.items[0].batch_debt, "sales_invoice"
	)
	if not original_name:
		frappe.throw(
			"Không xác định được hoá đơn gốc: chọn 'Hóa đơn gốc' trên yêu cầu hoặc dùng dòng hàng "
			"trả lại gắn với khoản nợ có hoá đơn.",
			title="Thiếu hoá đơn gốc",
		)
	original = frappe.get_doc("Sales Invoice", original_name)
	if original.docstatus != RETURNABLE_DOCSTATUS:
		frappe.throw(
			f"Hoá đơn gốc {original_name} chưa submit nên không thể trả hàng.",
			title="Hoá đơn chưa submit",
		)

	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	note = make_return_doc("Sales Invoice", original_name)
	note.feed_dealer_return_request = request.name

	# Trim the mapper's rows to what the request actually returns: make_return_doc
	# prefills EVERY line with the not-yet-returned remainder; a partial return
	# must not credit the untouched lines.
	#
	# ORDER MATTERS (bug found by the P1G integrity dataset, 2026-09-17): the
	# mapping used to run BEFORE this trim, so returning one line of a
	# multi-line invoice was refused with "không map được về dòng yêu cầu trả
	# hàng" — the mapper had prefilled the *other* lines and the mapper does not
	# know about the request. Every P1D test returned a whole invoice's lines, so
	# the case never came up. Trim first, then attribute.
	keep = {row.return_line for row in request.items}
	note.items = [line for line in note.items if line.sales_invoice_item in keep]
	if not note.items:
		frappe.throw(
			f"Yêu cầu trả hàng không khớp dòng nào của hoá đơn gốc {original_name} — kiểm tra "
			f"'Dòng hóa đơn gốc' (return_line) trên từng dòng.",
			title="Không có dòng trả lại",
		)
	_map_debts_from_note(note)
	for line in note.items:
		matching = [row for row in request.items if row.return_line == line.sales_invoice_item]
		if len(matching) > 1:
			frappe.throw(
				f"Dòng gốc {line.sales_invoice_item} xuất hiện ở {len(matching)} dòng yêu cầu — "
				f"gộp lại thành 1 dòng trước khi duyệt.",
				title="Trùng dòng gốc",
			)
		line.qty = -abs(flt(matching[0].qty))
	note.set_missing_values()
	return note


def _build_and_submit(request):
	note = _build_credit_note(request)
	note.insert(ignore_permissions=True)
	note.submit()
	return note


def approve(request, user=None):
	"""Approve a Draft request: credit note + debt mapping, or nothing at all.

	Order is load-bearing: the note is built and submitted FIRST, and the
	approval stamp lands in ONE save afterwards (status + reference + the
	`batch_debt_adjusted` flag together, so `validate`'s anti-hand-flip guard
	passes legitimately). If the stamp itself fails, the note is cancelled and
	deleted and the debts recounted — a Draft request with no note, the same
	state the user started from. There is no persisted moment where the request
	says Approved while its money does not exist, and none where money exists
	without an approver recorded (approved_by is set in the same save).
	"""
	if request.approval_status != "Draft":
		frappe.throw(
			f"Chỉ yêu cầu ở trạng thái Draft mới được duyệt (hiện tại: {request.approval_status}).",
			title="Sai trạng thái",
		)
	_validate_items(request)
	_validate_return_line_required(request)
	_validate_amounts(request)
	_validate_over_return(request)
	_validate_value_vs_outstanding(request)

	note = _build_and_submit(request)
	try:
		request.credit_note_reference = note.name
		request.batch_debt_adjusted = 1
		request.approval_status = "Approved"
		request.approved_by = user or frappe.session.user
		request.approved_at = now()
		request.flags.ignore_permissions = True
		request.save()
	except Exception:
		# Roll the note back: a failed stamp must not leave mapped money behind.
		note.cancel()
		frappe.delete_doc("Sales Invoice", note.name, force=True, ignore_permissions=True)
		_recalc_debts(_debts_of_note(note))
		frappe.db.commit()
		raise

	# The note's on_submit hook already recalculated the debts; again here so an
	# API caller reading right after approve() sees final numbers even if the
	# hook path ever changes.
	_recalc_debts(_debts_of_note(note))
	frappe.db.commit()
	return note


def reject(request, user=None):
	"""Reject: a status change only — zero financial side effects."""
	if request.approval_status != "Draft":
		frappe.throw(
			f"Chỉ yêu cầu ở trạng thái Draft mới được từ chối (hiện tại: {request.approval_status}).",
			title="Sai trạng thái",
		)
	request.approval_status = "Rejected"
	request.approved_by = user or frappe.session.user
	request.approved_at = now()
	request.flags.ignore_permissions = True
	request.save()
	frappe.db.commit()
	return request.name


# ------------------------------------------------------------------ document hooks
class SalesReturnRequest(Document):
	def validate(self):
		"""Draft-side validation only."""
		if self.approval_status == "Approved" and not self.batch_debt_adjusted:
			# An approval is only ever reached through approve(); a hand-flip of
			# the status must not pretend money moved.
			frappe.throw(
				"Không thể đặt 'Approved' trực tiếp — dùng hành động Duyệt (tạo credit note + cập nhật nợ).",
				title="Sai đường duyệt",
			)
		if (self.approval_status or "Draft") not in ("Draft", "Approved", "Rejected"):
			frappe.throw("Trạng thái duyệt không hợp lệ.", title="Sai trạng thái")
		_validate_items(self)
		if self.approval_status == "Draft" and self.items:
			# Over-return + duplicate-original-line checks run on the DRAFT, as
			# `_validate_over_return`'s docstring promises: the approver learns
			# about an impossible return before a note is half-built. approve()
			# runs the same guard again, so a caller that skips the draft save
			# cannot slip past.
			_validate_over_return(self)
		if self.approval_status == "Draft" and self.items and all(
			row.return_line for row in self.items
		):
			# Amounts recompute from the original lines; rate is not free text.
			_validate_amounts(self)
			# Value-level bound, after the amounts above are recomputed.
			_validate_value_vs_outstanding(self)

	def before_cancel(self):
		"""Block cancelling a request whose credit note is still alive."""
		if self.credit_note_reference and frappe.db.get_value(
			"Sales Invoice", self.credit_note_reference, "docstatus"
		) == RETURNABLE_DOCSTATUS:
			frappe.throw(
				f"Không thể huỷ yêu cầu trả hàng khi credit note {self.credit_note_reference} "
				f"vẫn còn hiệu lực. Huỷ credit note trước — tiền sẽ tự hoàn về khoản nợ.",
				title="Huỷ yêu cầu bị chặn",
			)


@frappe.whitelist()
def approve_request(request):
	"""API approve: DocType write permission applies."""
	doc = frappe.get_doc("Sales Return Request", request)
	doc.check_permission("write")
	return {"credit_note": approve(doc).name}


@frappe.whitelist()
def reject_request(request):
	doc = frappe.get_doc("Sales Return Request", request)
	doc.check_permission("write")
	return {"request": reject(doc)}


@frappe.whitelist()
def cancel_credit_note(request):
	"""Cancel the return's credit note and recount the debts (p1d T5).

	Without this the flow DEADLOCKS: the submitted request links the note via
	`credit_note_reference`, so frappe's standard back-link check refuses to
	cancel the note — while the request's own `before_cancel` refuses to cancel
	while the note is alive. The link from an approved request is a deliberate
	audit reference, not an accounting dependency, so the cancel goes through
	with `ignore_links` (the same pattern P1B uses to cancel allocations).
	After the note is cancelled its rows stop counting and the debts reopen via
	the `on_cancel` hook; the request keeps its note reference as history.
	"""
	doc = frappe.get_doc("Sales Return Request", request)
	doc.check_permission("write")
	if not doc.credit_note_reference:
		frappe.throw("Yêu cầu này không có credit note để huỷ.", title="Không có credit note")
	if frappe.db.get_value("Sales Invoice", doc.credit_note_reference, "docstatus") != RETURNABLE_DOCSTATUS:
		frappe.throw(
			f"Credit note {doc.credit_note_reference} không còn hiệu lực (đã huỷ/đã xoá).",
			title="Credit note đã huỷ",
		)
	note = frappe.get_doc("Sales Invoice", doc.credit_note_reference)
	note.flags.ignore_links = True
	note.flags.ignore_permissions = True
	note.cancel()
	frappe.db.commit()
	return {"cancelled": note.name, "debts": _debts_of_note(note)}
