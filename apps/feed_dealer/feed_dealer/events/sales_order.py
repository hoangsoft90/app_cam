"""Sales Order credit-limit gate (P1C).

Two entry points, on purpose, because frappe's submit is `docstatus = 1` followed
by a save (verified in frappe/model/document.py `_submit`), which means the
submit pass runs `validate` with docstatus already 1:

* `validate`     - every DRAFT save/update is checked against the limit INCLUDING
                   the other open drafts. This is what closes the v2.2 MUST-3
                   bypass (N draft orders each passing while nothing is invoiced).
* `before_submit`- the authoritative, atomic re-check with a row lock on the
                   Credit Score document, so two requests racing for the same
                   customer cannot both pass. It runs BEFORE frappe writes
                   docstatus=1 (document.py calls `before_submit` inside the
                   submit path), so a rejection leaves the order untouched as a
                   draft - unlike `on_submit`, which by then is too late
                   (the P1A cancel guard taught us the same lesson).

The document being checked is always passed as `exclude_order`: it is already in
the database (as a draft row) and would otherwise be counted twice, once as a
draft and once as `order_value`.
"""

import frappe

from feed_dealer.credit_limit import validate_credit_limit


def _order_value(doc):
	return doc.grand_total if doc.grand_total is not None else doc.net_total


def validate(doc, method=None):
	"""Draft-time check: a draft order reserves credit immediately."""
	if doc.docstatus != 0:
		# Submit passes through here with docstatus=1; before_submit owns that
		# check (with the lock and without the draft term).
		return
	validate_credit_limit(
		doc.customer,
		_order_value(doc),
		check_draft=True,
		for_submit=False,
		exclude_order=doc.name or None,
	)


def before_submit(doc, method=None):
	"""Submit-time atomic re-check (row lock on Credit Score)."""
	validate_credit_limit(
		doc.customer,
		_order_value(doc),
		check_draft=False,
		for_submit=True,
		exclude_order=doc.name or None,
	)
