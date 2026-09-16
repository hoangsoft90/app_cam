"""Unreconcile Payment -> reverse this app's allocation layer (P1C, closes a P1B gap).

ERPNext v16's `Unreconcile Payment` does NOT cancel the Payment Entry. Its
`on_submit` (verified in the installed source,
`erpnext/accounts/doctype/unreconcile_payment/unreconcile_payment.py`) calls:

    unlink_ref_doc_from_payment_entries(invoice, voucher_no)   # delink in AR
    cancel_exchange_gain_loss_journal(invoice, voucher_type, voucher_no)
    update_voucher_outstanding(...)

so afterwards AR says the invoice is outstanding again while our `Payment
Allocation` rows - and the debts' `paid_amount` / `status` - still claim it was
paid. `Payment Entry.on_cancel` never runs in this flow, so P1B's reversal does
not happen by itself.

This hook closes that gap: for the payment being unreconciled, cancel the
Payment Allocations whose Batch Debt belongs to an invoice the unreconcile
delinked, then recalculate those debts - the same end state a Payment Entry
cancel produces. Nothing else is touched, and a payment with no allocations of
ours is left completely alone.

Known gap, deliberately not papered over: cancelling an `Unreconcile Payment`
re-links nothing in ERPNext v16 (that DocType has no `on_cancel`), so this hook
does not re-create allocations on cancel either. Re-doing the reconciliation in
AR must be followed by re-running the allocation (the Payment Entry path, or the
P1B allocate API).
"""

import frappe

from feed_dealer.events.payment_entry import _recalculate


def on_submit(doc, method=None):
	"""Reverse the allocations this unreconcile delinked."""
	if doc.voucher_type != "Payment Entry":
		# Only a customer RECEIPT creates our allocations; unreconciling a
		# Journal Entry leaves nothing of ours to reverse.
		return {"skipped": f"voucher_type={doc.voucher_type!r}"}

	invoices = {
		row.reference_name
		for row in doc.get("allocations") or []
		if row.reference_doctype == "Sales Invoice" and row.reference_name
	}
	if not invoices:
		return {"skipped": "no Sales Invoice reference in this unreconcile"}

	reversed_allocs, debts = [], set()
	for row in frappe.get_all(
		"Payment Allocation",
		filters={"payment_entry": doc.voucher_no, "docstatus": 1},
		fields=["name", "batch_debt"],
	):
		if frappe.db.get_value("Batch Debt", row.batch_debt, "sales_invoice") not in invoices:
			# This slice belongs to another invoice: ERPNext delinked only the
			# invoices listed in `allocations`, so it must keep standing.
			continue
		allocation = frappe.get_doc("Payment Allocation", row.name)
		allocation.flags.ignore_links = True
		allocation.cancel()
		reversed_allocs.append(row.name)
		debts.add(row.batch_debt)

	for debt_name in sorted(debts):
		# Cancelling the allocation already removes it from the debt's
		# `paid_amount` sum; this recomputes outstanding/overdue/status from it.
		_recalculate(debt_name)

	frappe.db.commit()
	return {
		"reversed": reversed_allocs,
		"recalculated_debts": sorted(debts),
		"invoices": sorted(invoices),
	}
