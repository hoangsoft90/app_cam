"""Credit limit gate (P1C) - ONE rule, ONE function.

Every caller goes through `validate_credit_limit`: the Sales Order hook, a Desk
script, a REST call or the P2/P4 mobile API (including a Feed Farmer account).
A caller cannot invent a weaker version of the rule, which is what "Farmer/API
cùng rule" means in the P1C prompt.

Formula - .plan/plan_final_v2.2_mustfix.md MUST-3 (this supersedes the earlier
"outstanding debt only" draft, which was bypassable: each of N draft orders
passed the check because invoicing had not happened yet, and submitting all N
put the customer N times over the limit):

    committed = SUM(Batch Debt.outstanding_amount)   docstatus=1, status != Đã trả
              + SUM(Sales Order.grand_total)         docstatus=1, not Completed/Stopped, per_billed < 100
              + SUM(Sales Order.grand_total)         docstatus=0            [drafts]
    reject when order_value > approved_limit - committed

Properties to know before changing anything here:

* `approved_limit` is `Credit Score.credit_limit`, which the controller writes as
  MIN(limit_by_score, collateral/guarantee/seasonal when set) or `manual_limit`
  when a Manager overrode it. A customer with NO Credit Score document has an
  approved limit of 0 and IS blocked: on 2026-09-16 the decision was to fail
  closed, and the thrown message says exactly which document to create.
* The submitted-order term counts the order's FULL grand_total while
  `per_billed < 100`, so a partially invoiced order is counted twice (here and
  again through the Batch Debt its invoice created). That over-reserves rather
  than under-reserves - the safe direction for a hard limit - and it is what the
  spec prescribes; see design.md D15.
* `for_submit=True` takes a row lock on the Credit Score document. Two orders (or
  an order and a payment) racing for the same customer serialize on that row, so
  the loser re-reads the committed amounts after the winner committed instead of
  trusting the snapshot it read a moment earlier.
* Nothing here hard-codes a company: the rows are selected by customer, and the
  limit comes from the customer's own Credit Score document.
"""

import frappe
from frappe.utils import flt

PAID_STATUS = "Đã trả"
CLOSED_SO_STATUSES = ("Completed", "Stopped")
TITLE = "Vượt hạn mức tín dụng"


def _credit_score(customer, for_update=False):
	"""The customer's Credit Score document, or None. Locks its row when asked."""
	existing = frappe.db.get_value("Credit Score", {"customer": customer}, "name")
	if not existing:
		return None
	if for_update:
		# A plain read would be a snapshot: two concurrent submits would both see
		# the pre-commit number and both pass. `for_update=True` makes frappe
		# issue SELECT ... FOR UPDATE (frappe/database/database.py:get_value), so
		# the second caller waits for the first to commit and only then reads.
		existing = frappe.db.get_value("Credit Score", {"customer": customer}, "name", for_update=True)
	return frappe.get_doc("Credit Score", existing) if existing else None


def _sum(doctype, filters, field):
	rows = frappe.db.get_all(doctype, filters=filters, fields=[{"SUM": field, "as": "total"}])
	return flt(rows[0].total) if rows else 0.0


def outstanding_debt(customer):
	"""Invoice-backed debt: every non-settled submitted Batch Debt."""
	return _sum(
		"Batch Debt",
		{"customer": customer, "docstatus": 1, "status": ["!=", PAID_STATUS]},
		"outstanding_amount",
	)


def submitted_uninvoiced_orders(customer, exclude_order=None):
	"""Submitted orders that have not been fully billed yet (pipeline)."""
	filters = {
		"customer": customer,
		"docstatus": 1,
		"status": ["not in", CLOSED_SO_STATUSES],
		"per_billed": ["<", 100],
	}
	if exclude_order:
		filters["name"] = ["!=", exclude_order]
	return _sum("Sales Order", filters, "grand_total")


def draft_orders(customer, exclude_order=None):
	"""Draft orders hold credit too: that closes the "N drafts" bypass."""
	filters = {"customer": customer, "docstatus": 0}
	if exclude_order:
		filters["name"] = ["!=", exclude_order]
	return _sum("Sales Order", filters, "grand_total")


def credit_position(customer, exclude_order=None, check_draft=True):
	"""Read-only view of the numbers the gate uses (safe for a UI/API caller)."""
	credit = _credit_score(customer)
	limit = flt(credit.credit_limit) if credit else 0.0
	outstanding = outstanding_debt(customer)
	submitted = submitted_uninvoiced_orders(customer, exclude_order)
	drafts = draft_orders(customer, exclude_order) if check_draft else 0.0
	committed = outstanding + submitted + drafts
	return {
		"customer": customer,
		"has_credit_score": bool(credit),
		"approved_limit": limit,
		"outstanding_debt": outstanding,
		"submitted_uninvoiced_orders": submitted,
		"draft_orders": drafts,
		"committed": committed,
		"available": limit - committed,
	}


def _reject_message(position, order_value):
	return (
		f"Vượt hạn mức tín dụng cho khách {position['customer']}.<br>"
		f"Hạn mức đã duyệt: {position['approved_limit']:,.0f}đ<br>"
		f"Nợ theo lứa (đã xuất hoá đơn): {position['outstanding_debt']:,.0f}đ<br>"
		f"Đơn đã submit chưa xuất hoá đơn: {position['submitted_uninvoiced_orders']:,.0f}đ<br>"
		f"Đơn nháp đang giữ hạn mức: {position['draft_orders']:,.0f}đ<br>"
		f"Đơn này: {order_value:,.0f}đ<br>"
		f"Còn khả dụng: {max(0.0, position['available']):,.0f}đ"
	)


def validate_credit_limit(
	customer, order_value, *, check_draft=True, for_submit=False, exclude_order=None
):
	"""Throw when `order_value` would push the customer past the approved limit.

	check_draft:  count other DRAFT orders as committed (draft-time saves).
	for_submit:   take the row lock and skip the draft term, because an order that
	              is being submitted is about to become a submitted order and
	              must not be counted twice (frappe sets docstatus=1 BEFORE the
	              write, but the row is still docstatus=0 during before_submit, so
	              the caller passes `exclude_order` for the document in flight).
	"""
	credit = _credit_score(customer, for_update=for_submit)
	order_value = flt(order_value)

	position = {
		"customer": customer,
		"has_credit_score": bool(credit),
		"approved_limit": flt(credit.credit_limit) if credit else 0.0,
		"outstanding_debt": outstanding_debt(customer),
		"submitted_uninvoiced_orders": submitted_uninvoiced_orders(customer, exclude_order),
		"draft_orders": (draft_orders(customer, exclude_order) if check_draft and not for_submit else 0.0),
	}
	position["committed"] = (
		position["outstanding_debt"] + position["submitted_uninvoiced_orders"] + position["draft_orders"]
	)
	position["available"] = position["approved_limit"] - position["committed"]
	position["order_value"] = order_value

	if not credit:
		frappe.throw(
			f"Khách {customer} chưa có hạn mức tín dụng (chưa tạo hồ sơ 'Credit Score'), "
			f"nên hạn mức hiện tại = 0đ. Tạo hồ sơ Credit Score (hoặc nhờ Quản lý ghi đè "
			f"hạn mức) trước khi bán nợ, hoặc thu tiền mặt cho đơn này.",
			title=TITLE,
		)
	if order_value > position["available"]:
		frappe.throw(_reject_message(position, order_value), title=TITLE)

	return position


def _require_credit_read(customer):
	"""Whitelisted means "any logged-in user", so the caller must be allowed to
	read THIS customer's credit position. Checked against the Credit Score
	permissions (a Feed Farmer only sees their own document); when the customer
	has no Credit Score yet the DocType-level check applies. Fails closed.
	"""
	name = frappe.db.get_value("Credit Score", {"customer": customer}, "name")
	if not frappe.has_permission("Credit Score", "read", doc=name or None):
		frappe.throw(
			f"Bạn không có quyền xem hạn mức tín dụng của khách {customer}.",
			frappe.PermissionError,
		)


@frappe.whitelist()
def get_credit_position(customer):
	"""API view for the internal mobile app (P2) and the Zalo mini app (P4)."""
	_require_credit_read(customer)
	return credit_position(customer)


@frappe.whitelist()
def check_order_credit(customer, order_value):
	"""Dry-run of the gate: throws exactly like a real save would."""
	_require_credit_read(customer)
	return validate_credit_limit(customer, order_value, check_draft=True, for_submit=False)
