"""Credit Score (P1C) - deterministic rating and the approved debt limit.

Nothing here is AI/LLM driven: the score, the tier and every limit component are
plain arithmetic over stored documents, so the same inputs always produce the
same limit (P1C constraint 1).

Frozen business rules (.plan/plan1.md §3.4, plan_final.md §5.2.4,
plan_final_hardening.md §4.5 - unchanged by v2.1-v2.3):

    score = 50 + 5 * (số lần trả đúng hạn) - 10 * (số lần trả trễ)
              + 2 * (số lứa đã hoàn thành)          -> clamped to 0..100
    tier  = Đồng 0-39 | Bạc 40-59 | Vàng 60-79 | Kim Cương 80-100
    limit_by_score = %của hạng (Feed Dealer Settings) * average order value
    credit_limit   = manual_limit, IF manual_override
                   = MIN(limit_by_score, các hạng mục KHÁC đang > 0)

`limit_by_collateral` comes from `Collateral.effective_value` (×0.7, computed by
its own controller), `limit_by_guarantee` from the customer's guarantee amount
when such a field exists on this site, and `seasonal_limit_cap` is a manual
input a Manager can set per customer. A zero/absent component simply drops out of
the MIN, so an unused dimension can never silently cap a customer at 0.

Known limitation, deliberately not papered over: `Batch Debt` zeroes
`overdue_days` when a debt is settled (the P1B contract), so "số lần trả trễ"
can only see debts that are STILL overdue, and "trả đúng hạn" counts settled
debts. The counters therefore track the customer's live standing rather than
their whole history. Persisting the worst overdue streak on the debt at settle
time is the fix; it needs a schema field, so it is deferred (see design.md D15)
instead of being silently approximated here.

Override policy (P1C constraint 3): only a Feed Dealer Manager (or System
Manager) may set `manual_override`, a reason is mandatory, and `override_by` /
`override_date` are stamped from the session, not from the payload. Every save is
versioned (`track_changes = 1` on the DocType), which is the audit trail.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now

OVERRIDE_ROLES = ("Feed Dealer Manager", "System Manager")
COMPLETED_BATCH_STATUS = "Đã xuất bán"
PAID_STATUS = "Đã trả"

TIER_PERCENT_SETTING = {
	"Đồng": "credit_limit_percent_dong",
	"Bạc": "credit_limit_percent_bac",
	"Vàng": "credit_limit_percent_vang",
	"Kim Cương": "credit_limit_percent_kim_cuong",
}


def tier_for_score(score):
	"""Deterministic score -> tier mapping (the frozen table above)."""
	score = int(flt(score))
	if score >= 80:
		return "Kim Cương"
	if score >= 60:
		return "Vàng"
	if score >= 40:
		return "Bạc"
	return "Đồng"


def tier_percent(tier):
	"""The tier's limit percent, as a fraction (0.3 for Đồng at the default 30%)."""
	setting = TIER_PERCENT_SETTING.get(tier or "Đồng", TIER_PERCENT_SETTING["Đồng"])
	return flt(frappe.db.get_single_value("Feed Dealer Settings", setting)) / 100.0


def average_order_value(customer):
	"""Average submitted (non-return) Sales Invoice total for this customer.

	The decision recorded on 2026-09-16: a "lứa" is what the customer actually
	bought, so the average is taken over their submitted invoices. A customer with
	no invoice history averages 0, which yields limit_by_score = 0 - the honest
	answer for an unknown customer, and the reason the manager override exists.
	"""
	filters = {"customer": customer, "docstatus": 1, "is_return": 0}
	if not frappe.db.count("Sales Invoice", filters):
		return 0.0
	rows = frappe.db.get_all(
		"Sales Invoice", filters=filters, fields=[{"AVG": "grand_total", "as": "avg_total"}]
	)
	return flt(rows[0].avg_total) if rows else 0.0


def collateral_value(customer):
	"""Submitted-and-active collateral, at its effective (×0.7) value."""
	rows = frappe.db.get_all(
		"Collateral",
		filters={"customer": customer, "status": "Đang thế chấp"},
		fields=[{"SUM": "effective_value", "as": "total"}],
	)
	return flt(rows[0].total) if rows else 0.0


def guarantee_value(customer):
	"""Guarantee amount, when this site has the field (it is optional)."""
	fieldname = "custom_guarantee_amount"
	if not frappe.db.has_column("Customer", fieldname):
		return 0.0
	return flt(frappe.db.get_value("Customer", customer, fieldname))


def payment_history(customer):
	"""(on_time, late, completed batches) for scoring - see the module docstring."""
	on_time = frappe.db.count(
		"Batch Debt",
		{"customer": customer, "docstatus": 1, "status": PAID_STATUS, "overdue_days": 0},
	)
	late = frappe.db.count("Batch Debt", {"customer": customer, "docstatus": 1, "overdue_days": [">", 0]})
	batches = frappe.db.count("Feed Batch", {"customer": customer, "status": COMPLETED_BATCH_STATUS})
	return on_time, late, batches


def score_for(customer):
	on_time, late, batches = payment_history(customer)
	score = 50 + (5 * on_time) - (10 * late) + (2 * batches)
	return max(0, min(100, score))


def recalculate(customer, save=True):
	"""Recompute score/tier/limits for a customer and (optionally) persist them."""
	name = frappe.db.get_value("Credit Score", {"customer": customer}, "name")
	doc = frappe.get_doc("Credit Score", name) if name else frappe.new_doc("Credit Score")
	if not name:
		doc.customer = customer
	_compute(doc)
	if save:
		doc.save(ignore_permissions=True)
	return doc


def _compute(doc):
	"""Fill every derived field on `doc` (no save, no permission check)."""
	on_time, late, batches = payment_history(doc.customer)
	doc.score = score_for(doc.customer)
	doc.tier = tier_for_score(doc.score)
	doc.on_time_payments = on_time
	doc.late_payments = late
	doc.total_batches_completed = batches
	doc.last_calculated = now()

	doc.limit_by_score = average_order_value(doc.customer) * tier_percent(doc.tier)
	doc.limit_by_collateral = collateral_value(doc.customer)
	doc.limit_by_guarantee = guarantee_value(doc.customer)
	# `seasonal_limit_cap` is a manager input; keep whatever they typed.

	if doc.manual_override:
		doc.credit_limit = flt(doc.manual_limit)
	else:
		doc.credit_limit = min(_positive_candidates(doc))


def _positive_candidates(doc):
	"""The dimensions that actually apply to this customer."""
	values = [flt(doc.limit_by_score)]
	for value in (doc.limit_by_collateral, doc.limit_by_guarantee, doc.seasonal_limit_cap):
		if flt(value) > 0:
			values.append(flt(value))
	return values or [0.0]


class CreditScore(Document):
	def validate(self):
		self._validate_override()
		_compute(self)

	def _validate_override(self):
		"""Manager-only, reason-mandatory manual override (P1C constraint 3)."""
		if not self.manual_override:
			return
		roles = set(frappe.get_roles())
		if not roles.intersection(OVERRIDE_ROLES):
			frappe.throw(
				f"Chỉ Quản lý ({', '.join(OVERRIDE_ROLES)}) được ghi đè hạn mức tín dụng. "
				f"Tài khoản hiện tại: {frappe.session.user}.",
				title="Không có quyền ghi đè hạn mức",
			)
		if flt(self.manual_limit) <= 0:
			frappe.throw(
				"Ghi đè hạn mức phải kèm 'Hạn mức ghi đè' lớn hơn 0.",
				title="Thiếu hạn mức ghi đè",
			)
		if not (self.override_reason or "").strip():
			frappe.throw(
				"Ghi đè hạn mức phải kèm lý do ('Lý do ghi đè').",
				title="Thiếu lý do ghi đè",
			)
		# Stamped from the session, never from the payload.
		self.override_by = frappe.session.user
		self.override_date = now()


@frappe.whitelist()
def recalculate_credit_score(customer):
	"""API: recompute a customer's rating. Writes, so DocType permissions apply."""
	doc = recalculate(customer)
	return doc.as_dict()


@frappe.whitelist()
def tier_of_score(score):
	"""Expose the mapping so the mobile app shows the same tier the server uses."""
	return {"score": int(flt(score)), "tier": tier_for_score(score)}
