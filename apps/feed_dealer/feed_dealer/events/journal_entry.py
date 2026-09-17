"""Journal Entry -> Batch Debt recalculation (P1F).

Two responsibilities, split by WHEN frappe runs them:

* `validate`  — attribution gate. Runs before the write (draft save AND submit), so a JE that
  names another party's debt is refused without ever being stored.
* `on_submit` — recalculate the named debts. Correctly after the write.


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


def _validate_attribution(doc):
	"""A JE may only touch a debt that belongs to the party on its own line (fail closed).

	`batch_debt` is a read-only field in the UI but an API/import can still set it, and an
	unchecked value would let one customer's receivable be reduced by a document naming somebody
	else's debt. Same attribution rule P1D applies to return lines.
	"""
	for row in doc.accounts:
		if not row.get("batch_debt"):
			continue
		customer = frappe.db.get_value("Batch Debt", row.batch_debt, "customer")
		if not customer:
			frappe.throw(
				f"Dòng bút toán trỏ tới khoản nợ {row.batch_debt} không tồn tại.",
				title="Sai khoản nợ",
			)
		if row.get("party") != customer:
			frappe.throw(
				f"Dòng bút toán cấn trừ khoản nợ {row.batch_debt} của khách {customer} nhưng party trên "
				f"dòng là {row.get('party') or '(trống)'}. Từ chối để không cấn trừ nợ sai người.",
				title="Sai chủ thể cấn trừ",
			)


def validate(doc, method=None):
	"""Reject a bad attribution BEFORE the write, not after it.

	Review finding (proved with T9): the guard first lived in `on_submit`, but frappe's
	`_submit()` is `docstatus = 1; save()` and `save()` runs `on_submit` AFTER the row is
	written. A caller that swallows the exception (a script, our own test runner, an API that
	catches errors) was left with a SUBMITTED journal entry that nets the wrong party's debt.
	`validate` runs in `run_before_save_methods()` for both a draft save and a submit, i.e.
	before the INSERT/UPDATE, so a bad JE never reaches the database at all.
	"""
	_validate_attribution(doc)


def on_submit(doc, method=None):
	debts = _debts(doc)
	for name in debts:
		_recalculate(name)
	frappe.db.commit()
	return {"recalculated": debts}


def on_cancel(doc, method=None):
	"""Cancelling the JE drops its lines from the SUM, so the debts reopen."""
	return on_submit(doc, method=method)
