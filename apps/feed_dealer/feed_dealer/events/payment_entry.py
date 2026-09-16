"""Payment Entry -> Payment Allocation (P1B, FIFO allocation).

Contract (prompt P1B + phase_01_core_erp.md §4.3, Week 2):

* on_submit  — hand the received amount out FIFO across the customer's open
  `Batch Debt`s (oldest `due_date` first) and record one submitted
  `Payment Allocation` per slice, then refresh the debt's derived fields.
  Idempotent: a re-fired hook pays the *remainder* of the payment instead of
  paying the same debt twice.
* on_cancel  — cancel the allocations this payment created and recalculate the
  debts they touched, so no Batch Debt stays stuck at "Đã trả".

Why `outstanding_amount > 0` and not `status in (Chưa trả, Một phần, Quá hạn)`:
the two are equivalent by construction (`_derive_status()` returns "Đã trả"
exactly when outstanding <= 0), but the stored status is a snapshot that can go
stale, while the column is recomputed from the allocations on every save.

Why FIFO is across the whole customer and not per invoice: that is what the
phase plan specifies (a dealer's payment settles the oldest debt first), and
`Batch Debt` is the allocation view of the customer's AR. Per-invoice
attribution stays with ERPNext's own `references` + reconciliation; this layer
never invents an invoice that was not paid.

Money never leaves the AR document: the amount handed out can never exceed
`Payment Entry.paid_amount`, so the batch view is always a slice of what the
receivable actually holds. The leftover (if any) simply stays unallocated.

Known gap, deliberately NOT covered here: ERPNext's "Unreconcile Payment" tool
de-reconciles an invoice by posting a new Journal Entry while the Payment Entry
stays submitted — nothing fires on this module in that flow, so the allocation
would keep claiming the debt is paid. Reversing it needs the payment-ledger /
P1C work. Do not assume on_cancel covers it.
"""

import frappe
from frappe.utils import flt

from feed_dealer.events.sales_invoice import _refresh_batch_total


DERIVED_FIELDS = (
	"paid_amount",
	"outstanding_amount",
	"overdue_days",
	"late_payment_fee",
	"status",
)


def _recalculate(debt_name):
	"""Refresh one debt's derived fields from its (already written) allocations.

	Written with `frappe.db.set_value`, not `doc.save()`: a Batch Debt is a
	submitted document, and saving one from inside another document's submit did
	not persist the recomputed columns here (the row kept its old
	paid/outstanding/status after the save returned). These columns are
	server-owned and read-only in the UI, and the allocation layer is their only
	writer — the same "derived column, written by its owner" pattern P1A already
	uses for `Feed Batch.total_debt`. The values still come from the controller's
	`calculate_derived_fields()`, so the formula lives in exactly one place.
	"""
	debt = frappe.get_doc("Batch Debt", debt_name)
	debt.calculate_derived_fields()
	frappe.db.set_value(
		"Batch Debt",
		debt_name,
		{field: debt.get(field) for field in DERIVED_FIELDS},
		update_modified=False,
	)
	_refresh_batch_total(debt.batch)
	return debt


def _open_debts(customer):
	"""Submitted debts of this customer that still owe money, oldest first."""
	return frappe.get_all(
		"Batch Debt",
		filters={"customer": customer, "docstatus": 1, "outstanding_amount": [">", 0]},
		fields=["name", "batch", "due_date", "allocated_amount", "paid_amount", "outstanding_amount"],
		order_by="due_date asc, creation asc",
	)


def on_submit(doc, method=None):
	"""Allocate this payment across the customer's open debts (FIFO)."""
	if doc.get("party_type") != "Customer":
		return {"skipped": f"party_type={doc.get('party_type')!r} is not a customer"}

	existing = frappe.get_all(
		"Payment Allocation",
		filters={"payment_entry": doc.name, "docstatus": 1},
		fields=["batch_debt", "paid_amount"],
	)
	already_allocated = sum(flt(row.paid_amount) for row in existing)
	remaining = flt(doc.paid_amount) - already_allocated
	if remaining <= 0:
		# Re-fired hook (or a retry): everything this payment can cover is
		# already allocated, so there is nothing left to hand out.
		return {"skipped": f"already allocated {already_allocated:,.0f} of {flt(doc.paid_amount):,.0f}"}

	paid_debts = {row.batch_debt for row in existing}
	created = []
	for debt in _open_debts(doc.party):
		if remaining <= 0:
			break
		if debt.name in paid_debts:
			continue
		pay = min(remaining, flt(debt.outstanding_amount))
		if pay <= 0:
			continue
		allocation = frappe.get_doc(
			{
				"doctype": "Payment Allocation",
				"payment_entry": doc.name,
				"batch_debt": debt.name,
				"batch": debt.batch,
				"due_date": debt.due_date,
				"allocated_amount": flt(debt.allocated_amount),
				"previous_paid": flt(debt.paid_amount),
				"paid_amount": pay,
				"outstanding_after": flt(debt.outstanding_amount) - pay,
			}
		)
		allocation.insert(ignore_permissions=True)
		allocation.submit()
		remaining -= pay
		# Recalculate AFTER the allocation exists: `paid_amount` is derived from
		# the submitted allocations, so the debt picks this slice up itself.
		_recalculate(debt.name)
		created.append(f"{allocation.name}: {debt.name} = {pay:,.0f}")

	frappe.db.commit()  # same rule as P1A: this layer must not half-exist
	return {"created": created, "unallocated": remaining}


def on_cancel(doc, method=None):
	"""Reverse this payment's allocations and reopen the debts they closed."""
	cancelled, debts = [], set()
	for row in frappe.get_all(
		"Payment Allocation",
		filters={"payment_entry": doc.name, "docstatus": 1},
		fields=["name", "batch_debt"],
	):
		allocation = frappe.get_doc("Payment Allocation", row.name)
		allocation.flags.ignore_links = True
		allocation.cancel()
		cancelled.append(row.name)
		debts.add(row.batch_debt)

	for debt_name in sorted(debts):
		_recalculate(debt_name)

	frappe.db.commit()
	return {"cancelled": cancelled, "recalculated_debts": sorted(debts)}
