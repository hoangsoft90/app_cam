"""Sales Invoice -> Batch Debt allocation (P1A).

Contract (prompt P1A + phase_01_core_erp.md §4.1/4.2):

* on_submit  — group `doc.items` by `(custom_batch, item_tax_template)`, skip
  rows without a batch (accessories, loose medicine), and create+submit ONE
  `Batch Debt` per group. Idempotent on the SAME triple the grouping uses
  `(sales_invoice, batch, item_tax_template)`: a resubmit/retry never
  duplicates a group, and a group whose earlier attempt left a draft behind is
  submitted rather than skipped forever.

  The key must be the grouping key. Keying on `(sales_invoice, batch)` alone
  matched the first tax group of a multi-tax invoice and silently dropped the
  rest — one invoice line's money (1,000,000 VND in the T6 fixture) never
  reached any Batch Debt. `item_tax_template` is therefore persisted on the
  debt, not only read from the invoice row.
* before_cancel — refuse while any linked debt has `paid_amount > 0` (a
  payment must not be orphaned — plan v2.3 A1). This guard MUST run on
  `before_cancel`, not `on_cancel`: frappe's `Document._cancel()` sets
  `docstatus = 2` BEFORE saving, so by the time `on_cancel` fires the cancelled
  status is already committed (bench execute has no auto-rollback either).
  Throwing from `before_cancel` aborts the save itself.
* on_cancel — re-check the same guard (a direct call that skipped
  `before_cancel` must not cascade), then cascade-cancel every debt of this
  invoice and refresh `Feed Batch.total_debt`.

AR stays the source of truth: this layer only slices the invoice into batch
debts; it never invents or destroys money.

Money convention (deliberate, documented):
  `allocated_amount` = SUM(item.amount) per group = qty * rate **after line
  discount** (ERPNext's `amount` is net of line discount already). Tax is NOT
  added: Batch Debt tracks the *principal* owed on the batch, while tax stays
  on the AR document itself — if VAT were folded in, cancel-time allocation
  would stop matching the invoice's taxable base and returns (P1D) would
  over-credit the batch. Invoice-level discount allocation lands with returns
  in P1D, where credit notes make the split exact.
"""

import frappe
from frappe.utils import flt

BATCH_FIELD = "custom_batch"


def _tax_filter(tax_template):
	"""Filter value that matches a group with no tax template.

	`""` and SQL `NULL` must behave the same here: this app writes `""`, but an
	import, a data migration or a raw SQL write can leave `NULL`, and if the two
	diverge the idempotency check misses that debt and a retry creates a second
	one — a duplicated debt is lost money. `["is", "not set"]` covers both.
	"""
	return tax_template or ["is", "not set"]


def _group_items(doc):
	"""Return {(batch, item_tax_template): amount} for rows that carry a batch."""
	groups = {}
	for item in doc.items:
		batch = getattr(item, BATCH_FIELD, None)
		if not batch:
			# No batch on this row (accessories, loose medicine): deliberately no
			# Batch Debt — the amount belongs to AR only.
			continue
		key = (batch, getattr(item, "item_tax_template", None) or "")
		groups[key] = groups.get(key, 0.0) + flt(item.amount)
	return groups


def _refresh_batch_total(batch):
	"""Recompute Feed Batch.total_debt from its submitted debts."""
	if not batch or not frappe.db.exists("Feed Batch", batch):
		return
	rows = frappe.db.get_all(
		"Batch Debt",
		filters={"batch": batch, "docstatus": 1},
		fields=[{"SUM": "outstanding_amount", "as": "outstanding"}],
	)
	total = flt(rows[0].outstanding) if rows else 0.0
	frappe.db.set_value("Feed Batch", batch, "total_debt", total, update_modified=False)


def on_submit(doc, method=None):
	"""Create one submitted Batch Debt per (batch, item_tax_template) group."""
	groups = _group_items(doc)
	if not groups:
		return {"created": 0, "skipped_no_batch_rows": len(doc.items)}

	created, resubmitted, skipped = [], [], []
	for (batch, tax_template), amount in groups.items():
		key = {
			"sales_invoice": doc.name,
			"batch": batch,
			"item_tax_template": _tax_filter(tax_template),
		}
		if frappe.db.exists("Batch Debt", {**key, "docstatus": 1}):
			# Idempotency: this exact group is already allocated, so a re-fired
			# hook (or a retry) must not create a second debt for it.
			skipped.append(f"{batch}/{tax_template or '-'}")
			continue

		draft = frappe.db.get_value("Batch Debt", {**key, "docstatus": 0}, "name")
		if draft:
			# A previous attempt died between insert and submit, leaving a draft.
			# Finish that draft instead of skipping it (which would leave the
			# group unallocated forever) or creating a rival document. `submit()`
			# saves anyway, so no separate save() call is needed here.
			debt = frappe.get_doc("Batch Debt", draft)
			debt.allocated_amount = flt(amount)
			debt.customer = doc.customer
			debt.due_date = doc.due_date
			debt.submit()
			resubmitted.append(debt.name)
		else:
			debt = frappe.get_doc(
				{
					"doctype": "Batch Debt",
					"batch": batch,
					"customer": doc.customer,
					"sales_invoice": doc.name,
					"item_tax_template": tax_template,
					"allocated_amount": flt(amount),
					"due_date": doc.due_date,
				}
			)
			debt.insert(ignore_permissions=True)
			debt.submit()
			created.append(f"{debt.name}: {batch} = {flt(amount):,.0f}")
		_refresh_batch_total(batch)

	frappe.db.commit()  # allocation layer must not half-exist after a retry
	return {"created": created, "resubmitted_drafts": resubmitted, "skipped_existing": skipped}


def _paid_debt_names(invoice):
	"""Names of submitted debts of this invoice that already hold money."""
	return frappe.get_all(
		"Batch Debt",
		filters={"sales_invoice": invoice, "docstatus": 1, "paid_amount": [">", 0]},
		pluck="name",
	)


def before_cancel(doc, method=None):
	"""Block the cancel BEFORE frappe writes docstatus=2."""
	paid = _paid_debt_names(doc.name)
	if paid:
		frappe.throw(
			f"Không thể huỷ Hóa đơn {doc.name}: các nợ theo lứa {paid} đã có thanh toán "
			f"(paid_amount > 0). Hãy huỷ/điều chỉnh Payment Entry trước (P1B).",
			title="Huỷ hóa đơn bị chặn",
		)


def on_cancel(doc, method=None):
	"""Cascade the cancel down to this invoice's Batch Debts.

	Guard re-checked first: a caller that skipped `before_cancel` (direct
	on_cancel invocation) must never cascade a paid debt into oblivion.
	"""
	paid = _paid_debt_names(doc.name)
	if paid:
		frappe.throw(
			f"Không thể huỷ Hóa đơn {doc.name}: các nợ theo lứa {paid} đã có thanh toán "
			f"(paid_amount > 0). Hãy huỷ/điều chỉnh Payment Entry trước (P1B).",
			title="Huỷ hóa đơn bị chặn",
		)

	cancelled = []
	for name in frappe.get_all(
		"Batch Debt", filters={"sales_invoice": doc.name, "docstatus": 1}, pluck="name"
	):
		debt = frappe.get_doc("Batch Debt", name)
		debt.flags.ignore_links = True
		batch = frappe.db.get_value("Batch Debt", name, "batch")
		debt.cancel()
		cancelled.append(name)
		_refresh_batch_total(batch)

	return {"cancelled": cancelled, "blocked_by_payment": paid}
