"""Force-reload the Feed Dealer standard reports after an edit (P1G).

WHY THIS PATCH EXISTS (measured, not theoretical): `bench migrate` imports a
standard document from the app folder only when the file is NEWER than the row in
the database. `.agent/gen_reports.py` writes a fixed `modified` stamp on purpose
(so `--check` can diff generator output against disk byte-for-byte, which is what
keeps the generator the single writer), and a fixed stamp means that after the
first install the database row is never older than the file — so a later query
change is silently ignored. Measured symptom: `approval_audit_log` in `tabReport`
still held the version whose query started with a `--` comment (which frappe's
`check_safe_sql_query` rejects), while the JSON on disk was already fixed and
`--check` reported no drift.

`frappe.reload_doc(module, "report", name, force=True)` is frappe's own way to
re-import regardless of the timestamp. Idempotent: it re-imports the current file
every time it runs, which is also what we want when the reports evolve.
"""

import frappe

MODULE = "Feed Dealer"

REPORTS = (
	"debt_by_batch",
	"overdue_batch_debts",
	"payment_allocation_detail",
	"cash_flow_30_60_90",
	"batch_profit_loss",
	"customer_credit_limit",
	"approval_audit_log",
)


def execute():
	reloaded = []
	for name in REPORTS:
		frappe.reload_doc(MODULE, "report", name, force=True)
		reloaded.append(name)
	print(f"[feed_dealer] reimported {len(reloaded)} standard report(s): {', '.join(reloaded)}")
