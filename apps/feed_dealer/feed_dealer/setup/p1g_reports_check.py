"""P1G acceptance for the seven standard reports.

Run:  bench --site <site> execute feed_dealer.setup.p1g_reports_check.run
      bench --site <site> execute feed_dealer.setup.p1g_reports_check.debug

Why this exists rather than "the JSON is in the folder": a Query Report is only
proven by running it. A typo in a column alias, a join that does not exist on this
site, or `CURDATE()` behaving differently all surface as an exception in the
report runner and nowhere else — the file would still look right.

Each report is executed through `frappe.desk.query_report.run` (the very same
path the Desk uses), the row count is printed as evidence, and anything that
throws fails the check. The P1G integrity dataset is expected to be present, so
the money reports must return rows; the check says so explicitly instead of
passing on an empty result set.
"""

import json
import traceback

import frappe

REPORTS = {
	"debt_by_batch": "Nợ theo lứa (debt per batch)",
	"overdue_batch_debts": "Nợ quá hạn (overdue debts)",
	"payment_allocation_detail": "Phân bổ thanh toán (payment allocation)",
	"cash_flow_30_60_90": "Dòng tiền 30-60-90 (cash flow buckets)",
	"batch_profit_loss": "Lời/Lỗ theo lứa (revenue per batch)",
	"customer_credit_limit": "Hạn mức tín dụng (credit limit)",
	"approval_audit_log": "Nhật ký phê duyệt (approval audit log)",
}

# Reports over Batch Debt / Payment Allocation must have data while the P1G
# dataset exists; the audit log is the exception (its rows come from decisions,
# and the dataset approves returns, so it should have rows too — but a site with
# no returns would legitimately be empty, hence it is checked separately).
MUST_HAVE_ROWS = (
	"debt_by_batch",
	"overdue_batch_debts",
	"payment_allocation_detail",
	"cash_flow_30_60_90",
	"batch_profit_loss",
	"customer_credit_limit",
)


def _run_report(name):
	from frappe.desk.query_report import run as run_report

	result = run_report(report_name=name, ignore_prepared_report=True)
	columns = result.get("columns") or []
	rows = result.get("result") or []
	return columns, rows


def check_reports_are_installed():
	"""Every report must exist as a standard Report document (synced by migrate)."""
	missing = [name for name in REPORTS if not frappe.db.exists("Report", name)]
	if missing:
		raise AssertionError(
			f"missing standard Report(s) {missing} — run `bench migrate` so frappe "
			f"imports feed_dealer/feed_dealer/report/**"
		)
	standard = frappe.get_all(
		"Report",
		filters={"name": ["in", list(REPORTS)]},
		fields=["name", "is_standard", "report_type", "ref_doctype"],
	)
	# Every report is standard; the six money views are Query Reports and the
	# credit-limit one is a Script Report (it must call the P1C gate itself, see
	# .agent/gen_reports.py). Anything else means the wrong file got installed.
	expected_type = {name: "Query Report" for name in REPORTS}
	expected_type["customer_credit_limit"] = "Script Report"
	wrong = [
		f"{row.name}({row.report_type}, standard={row.is_standard})"
		for row in standard
		if row.is_standard != "Yes" or row.report_type != expected_type[row.name]
	]
	if wrong:
		raise AssertionError(f"report metadata wrong: {wrong}")
	return f"{len(standard)} report(s) present: 6 Query + 1 Script, all standard"


def check_reports_execute():
	"""Each report must execute through the Desk's own runner and return rows."""
	summary = {}
	empty = []
	for name in REPORTS:
		columns, rows = _run_report(name)
		summary[name] = (len(columns), len(rows))
		if name in MUST_HAVE_ROWS and not rows:
			empty.append(name)
	if empty:
		raise AssertionError(
			f"report(s) returned no rows while the P1G dataset exists: {empty}"
		)
	readable = ", ".join(f"{name}={rows}r/{cols}c" for name, (cols, rows) in sorted(summary.items()))
	return readable


def check_audit_log_has_decisions():
	"""The audit log must show the approvals the dataset actually made."""
	columns, rows = _run_report("approval_audit_log")
	kinds = {row.get("kind") for row in rows if isinstance(row, dict)}
	if not rows:
		raise AssertionError("the approval audit log is empty although returns were approved")
	if "Sales Return Request" not in kinds:
		raise AssertionError(f"no return-request decision in the audit log: kinds={kinds}")
	return f"{len(rows)} decision row(s), kinds={sorted(kinds)}"


def check_credit_limit_report_matches_the_gate():
	"""The report must state the GATE's numbers, not a debt-only approximation.

	The gate is the source of truth (`feed_dealer.credit_limit.credit_position`) and
	the report only states it. Two assertions, both with teeth:

	* `available` equals the gate's own value, and
	* `committed` includes orders that are submitted-but-not-invoiced and drafts
	  holding limit — so a customer with a draft order must show LESS available
	  here than its batch debt alone would suggest. A report that ignored those
	  (the tempting one-line SQL) would show the owner more credit than the gate
	  will grant, which is precisely the drift this check exists to catch.
	"""
	from feed_dealer.credit_limit import credit_position

	_columns, rows = _run_report("customer_credit_limit")
	checked, mismatched, order_aware = 0, [], 0
	for row in rows:
		if not isinstance(row, dict) or not row.get("customer"):
			continue
		position = credit_position(row["customer"])
		checked += 1
		expected_committed = round(
			float(position["outstanding_debt"])
			+ float(position["submitted_uninvoiced_orders"])
			+ float(position["draft_orders"])
		)
		if round(float(row.get("committed") or 0)) != expected_committed:
			mismatched.append(
				f"{row['customer']}: committed {row.get('committed')} != gate {expected_committed}"
			)
		if round(float(row.get("available") or 0)) != round(float(position["available"])):
			mismatched.append(
				f"{row['customer']}: available {row.get('available')} != gate {position['available']}"
			)
		if float(position["submitted_uninvoiced_orders"]) or float(position["draft_orders"]):
			order_aware += 1
	if mismatched:
		raise AssertionError("credit-limit report disagrees with the gate: " + "; ".join(mismatched[:3]))
	if not checked:
		raise AssertionError("no customer in the credit-limit report to compare")
	if not order_aware:
		raise AssertionError(
			"no dataset customer has an open order — the check cannot tell the gate's "
			"formula apart from a debt-only query, so the dataset is too weak here"
		)
	return (
		f"{checked} customer(s) match the gate exactly; {order_aware} carry order-level "
		f"commitment (so a debt-only query would have diverged)"
	)


CHECKS = (
	("R1  reports installed as standard", check_reports_are_installed),
	("R2  every report executes", check_reports_execute),
	("R3  audit log carries decisions", check_audit_log_has_decisions),
	("R4  credit report == the gate", check_credit_limit_report_matches_the_gate),
)


def collect():
	rows = []
	for name, fn in CHECKS:
		try:
			detail = fn()
			rows.append((name, True, detail or "ok"))
		except Exception as exc:  # noqa: BLE001 - a failing check must not abort the suite
			rows.append((name, False, f"{type(exc).__name__}: {exc}"))
	width = max(len(name) for name, _ok, _d in rows)
	lines = ["", f"{'CHECK'.ljust(width)}  RESULT  DETAIL", "-" * (width + 44)]
	for name, ok, detail in rows:
		lines.append(f"{name.ljust(width)}  {'PASS  ' if ok else 'FAIL  '}  {detail}")
	failed = [name for name, ok, _ in rows if not ok]
	lines.append("-" * (width + 44))
	lines.append(f"TOTAL: {len(rows)}   PASS: {len(rows) - len(failed)}   FAIL: {len(failed)}")
	return "\n".join(lines), failed


def run():
	text, failed = collect()
	print(text)
	print(json.dumps({"site": frappe.local.site, "failed": failed}))
	if failed:
		frappe.throw(f"P1G reports FAILED: {failed}", title="P1G REPORTS FAILED")
	print("P1G REPORTS: ALL PASS")
	return {"failed": failed}


def debug():
	failed = 0
	for name, fn in CHECKS:
		try:
			detail = fn()
		except Exception:  # noqa: BLE001
			failed += 1
			print(f"\n===== FAIL {name} =====")
			traceback.print_exc()
		else:
			print(f"===== PASS {name}: {detail}")
	print(f"\n[debug] {len(CHECKS) - failed}/{len(CHECKS)} checks passed")
	return {"failed": failed}
