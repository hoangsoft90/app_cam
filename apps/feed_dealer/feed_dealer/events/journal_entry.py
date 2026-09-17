"""Journal Entry -> Batch Debt recalculation (P1F).

The livestock-offset helper writes `batch_debt` onto the receivable line of the
Journal Entry (a custom field on Journal Entry Account). This hook is what makes
the Batch Debt view follow that document, exactly the shape of P1D's return-note
hook: recalculate the debts the document names, never cascade money.

Only lines that name a debt do anything, so an ordinary accounting JE is untouched.
"""

import frappe

from feed_dealer.events.payment_entry import _recalculate


def _debts(doc):
	return sorted({row.batch_debt for row in doc.accounts if row.get("batch_debt")})


def on_submit(doc, method=None):
	debts = _debts(doc)
	for name in debts:
		_recalculate(name)
	frappe.db.commit()
	return {"recalculated": debts}


def on_cancel(doc, method=None):
	"""Cancelling the JE drops its lines from the SUM, so the debts reopen."""
	return on_submit(doc, method=method)
