"""P1A acceptance: Sales Invoice -> Batch Debt allocation.

Run:      bench --site <site> execute feed_dealer.setup.p1a_acceptance.run
Debug:    bench --site <site> execute feed_dealer.setup.p1a_acceptance.debug
Cleanup:  bench --site <site> execute feed_dealer.setup.p1a_acceptance.cleanup

Covers the prompt's five cases:
  T1  SI with one batched line        -> exactly 1 Batch Debt, right amount
  T2  SI with two batched lines       -> 2 debts, sum = lines' net amount
  T3  SI with an unbatched line       -> no debt for that line
  T4  cancel an unpaid SI             -> its debts docstatus=2, total_debt refreshed
  T5  cancel with paid_amount > 0     -> invoice cancel blocked (payment guard)
  T6  2 tax templates on ONE batch    -> 2 debts, sum = both lines' net amount

Real Sales Invoice documents are created and submitted against a real item and
taxes-free pricing, so the allocation math is exercised end-to-end (including
frappe's own on_submit machinery and our hooks). Fixtures use the P1A-ACCEPT
prefix; cleanup() removes them. Nothing here touches Payment Entry (P1B).
"""

import json
import traceback

import frappe
from frappe.utils import add_days, flt, nowdate

PREFIX = "P1A-ACCEPT"

# T6 needs one invoice carrying two different tax treatments on the SAME batch.
# ERPNext rewrites a line's `item_tax_template` to a template that is valid for
# the item's own Item/Item Group tax rules (`TaxesAndTotals.validate_item_tax_
# template`), so the difference has to come from the ITEMS, not from the line:
#   * `Cám chăn nuôi` group -> KCT (không chịu thuế) on the feed item
#   * an item in `All Item Groups` (no group tax rule) -> its own line template
# Both templates are pre-existing on this site for company Minh Phát Cám & VLXD.
KCT_TAX_TEMPLATE = "KCT Cám chăn nuôi - MP"
VAT_TAX_TEMPLATE = "Vietnam Tax - MP"
VAT_ITEM = f"{PREFIX} Item VAT"

# <paid_amount_stub>: T5 writes directly to the DB column, bypassing the
# controller, to simulate "a Payment Allocation landed in P1B" without
# implementing that layer here.


class Report:
	def __init__(self):
		self.rows = []

	def check(self, name, fn):
		try:
			detail = fn()
			self.rows.append((name, True, detail or "ok"))
		except Exception as exc:  # noqa: BLE001 - a failing check must not abort the suite
			self.rows.append((name, False, f"{type(exc).__name__}: {exc}"))
		return self.rows[-1][1]

	def render(self):
		width = max(len(name) for name, _ok, _d in self.rows)
		lines = ["", f"{'CHECK'.ljust(width)}  RESULT  DETAIL", "-" * (width + 44)]
		for name, ok, detail in self.rows:
			lines.append(f"{name.ljust(width)}  {'PASS  ' if ok else 'FAIL  '}  {detail}")
		failed = [name for name, ok, _ in self.rows if not ok]
		lines.append("-" * (width + 44))
		lines.append(f"TOTAL: {len(self.rows)}   PASS: {len(self.rows) - len(failed)}   FAIL: {len(failed)}")
		return "\n".join(lines), failed


# ------------------------------------------------------------------- fixtures
def _company():
	name = frappe.db.get_single_value("Feed Dealer Settings", "default_company")
	if name and frappe.db.exists("Company", name):
		return name
	companies = frappe.get_all("Company", pluck="name", order_by="creation")
	return companies[0]


def _customer(name=None):
	name = name or f"{PREFIX} Customer"
	if not frappe.db.exists("Customer", name):
		group = frappe.db.get_value("Customer Group", "Trại lớn") or frappe.db.get_value(
			"Customer Group", "All Customer Groups"
		)
		frappe.get_doc(
			{"doctype": "Customer", "customer_name": name, "customer_group": group}
		).insert(ignore_permissions=True)
	return name


def _item():
	name = f"{PREFIX} Item"
	if not frappe.db.exists("Item", name):
		group = frappe.db.get_value("Item Group", "Cám lợn") or frappe.db.get_value(
			"Item Group", "All Item Groups"
		)
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": name,
				"item_name": name,
				"item_group": group,
				"stock_uom": "Kg",
				"is_stock_item": 0,
				"gst_hsn_code": "",
			}
		).insert(ignore_permissions=True)
	return name


def _vat_item():
	"""Item in a tax-rule-free group, so its line keeps the template it is given."""
	if not frappe.db.exists("Item", VAT_ITEM):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": VAT_ITEM,
				"item_name": VAT_ITEM,
				"item_group": "All Item Groups",
				"stock_uom": "Kg",
				"is_stock_item": 0,
			}
		).insert(ignore_permissions=True)
	return VAT_ITEM


def _batch(customer, tag):
	existing = frappe.db.get_value("Feed Batch", {"customer": customer, "notes": f"{PREFIX} {tag}"}, "name")
	if existing:
		return existing
	doc = frappe.get_doc(
		{
			"doctype": "Feed Batch",
			"customer": customer,
			"animal_type": "Lợn",
			"start_date": nowdate(),
			"notes": f"{PREFIX} {tag}",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _invoice(lines, submit=True, customer=None, due_days=15, posting_days=0):
	"""lines: list of dicts {qty, rate, batch(optional), item(optional), tax(optional)}

	`due_days`/`posting_days` are relative to today. Negative due_days create the
	overdue debts P1B's late-fee check needs; ERPNext refuses a due date before
	the posting date, so such an invoice must also be posted in the past.
	"""
	doc = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": _company(),
			"customer": customer or _customer(),
			"currency": "VND",
			"due_date": add_days(nowdate(), due_days),
			"set_posting_time": 1,
			"posting_date": add_days(nowdate(), posting_days),
			"items": [
				{
					"item_code": line.get("item") or _item(),
					"qty": line["qty"],
					"rate": line["rate"],
					"custom_batch": line.get("batch"),
					"item_tax_template": line.get("tax"),
				}
				for line in lines
			],
		}
	)
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
		frappe.db.commit()
	return doc


def _debts(invoice_name):
	return frappe.get_all(
		"Batch Debt",
		filters={"sales_invoice": invoice_name},
		fields=[
			"name",
			"docstatus",
			"batch",
			"item_tax_template",
			"allocated_amount",
			"outstanding_amount",
			"paid_amount",
		],
		order_by="creation",
	)


# ---------------------------------------------------------------------- tests
def check_single_batch_si():
	"""T1: one batched line -> exactly 1 debt with the line's net amount."""
	batch = _batch(_customer(), "T1")
	# Delta, not absolute: a reused fixture batch may legitimately carry older
	# debts (fixtures are idempotent and survive between runs), so the invariant
	# under test is "submitting this invoice adds exactly its line net".
	before = flt(frappe.db.get_value("Feed Batch", batch, "total_debt"))
	inv = _invoice([{"qty": 10, "rate": 100_000, "batch": batch}])
	debts = _debts(inv.name)
	if len(debts) != 1:
		raise AssertionError(f"expected exactly 1 Batch Debt, got {len(debts)}: {debts}")
	if flt(debts[0].allocated_amount) != 1_000_000:
		raise AssertionError(f"allocated should be 1,000,000, got {debts[0].allocated_amount}")
	if debts[0].docstatus != 1:
		raise AssertionError(f"debt must be submitted, docstatus={debts[0].docstatus}")
	after = flt(frappe.db.get_value("Feed Batch", batch, "total_debt"))
	if after - before != 1_000_000:
		raise AssertionError(f"Feed Batch.total_debt should rise by 1,000,000 ({before} -> {after})")
	return f"{inv.name} -> {debts[0].name}: +1,000,000 VND (batch total {before:,.0f} -> {after:,.0f})"


def check_multi_batch_si():
	"""T2: two batched lines (different batches) -> 2 debts, sum = net total."""
	b1, b2 = _batch(_customer(), "T2a"), _batch(_customer(), "T2b")
	inv = _invoice(
		[
			{"qty": 5, "rate": 200_000, "batch": b1},
			{"qty": 3, "rate": 150_000, "batch": b2},
		]
	)
	debts = _debts(inv.name)
	by_batch = {d.batch: flt(d.allocated_amount) for d in debts}
	if len(debts) != 2 or set(by_batch) != {b1, b2}:
		raise AssertionError(f"expected 2 debts on the 2 batches, got {debts}")
	if by_batch[b1] != 1_000_000 or by_batch[b2] != 450_000:
		raise AssertionError(f"amounts wrong: {by_batch}")
	return f"{inv.name} -> 2 debts: {by_batch[b1]:,.0f} + {by_batch[b2]:,.0f}"


def check_unbatched_line_skipped():
	"""T3: a line without custom_batch must not create any debt."""
	batch = _batch(_customer(), "T3")
	inv = _invoice(
		[
			{"qty": 1, "rate": 300_000, "batch": batch},
			{"qty": 2, "rate": 50_000},  # no batch: accessory / loose medicine
		]
	)
	debts = _debts(inv.name)
	if len(debts) != 1 or flt(debts[0].allocated_amount) != 300_000:
		raise AssertionError(f"only the batched line must create a debt, got {debts}")
	return f"{inv.name} -> 1 debt (300,000); unbatched 100,000 stayed in AR only"


def check_cancel_unpaid_si():
	"""T4: cancelling an unpaid SI cascade-cancels its debts and refreshes the batch."""
	batch = _batch(_customer(), "T4")
	inv = _invoice([{"qty": 4, "rate": 250_000, "batch": batch}])
	before = _debts(inv.name)
	if len(before) != 1 or before[0].docstatus != 1:
		raise AssertionError(f"setup wrong: {before}")
	before = flt(frappe.db.get_value("Feed Batch", batch, "total_debt"))
	inv.cancel()
	frappe.db.commit()
	after_debts = _debts(inv.name)
	if any(d.docstatus != 2 for d in after_debts):
		raise AssertionError(f"debts must be cancelled after SI cancel: {after_debts}")
	after = flt(frappe.db.get_value("Feed Batch", batch, "total_debt"))
	if after != before - 1_000_000:
		raise AssertionError(f"Feed Batch.total_debt should drop by 1,000,000 ({before} -> {after})")
	return f"{inv.name} cancelled -> {after_debts[0].name} docstatus=2, batch total {before:,.0f} -> {after:,.0f}"


def check_cancel_blocked_when_paid():
	"""T5: a debt with paid_amount > 0 must BLOCK the invoice cancel."""
	batch = _batch(_customer(), "T5")
	inv = _invoice([{"qty": 2, "rate": 500_000, "batch": batch}])
	debt = _debts(inv.name)[0]
	# Simulate a P1B payment having landed, bypassing the controller on purpose.
	frappe.db.set_value("Batch Debt", debt.name, "paid_amount", 200_000, update_modified=False)
	try:
		inv.cancel()
	except frappe.ValidationError as exc:
		message = str(exc)
		if "paid_amount" not in message and "thanh toán" not in message:
			raise AssertionError(f"blocked for the wrong reason: {message}") from exc
		# The guard lives on `before_cancel`, which frappe runs BEFORE writing
		# docstatus=2, so the invoice must still be submitted here.
		frappe.db.commit()
		status = frappe.db.get_value("Sales Invoice", inv.name, "docstatus")
		if status != 1:
			raise AssertionError(f"the invoice must remain submitted when blocked, docstatus={status}")
		return f"{inv.name} cancel blocked by {debt.name} (paid 200,000): {message.strip()[:80]}"
	else:
		frappe.db.commit()
		raise AssertionError("cancel was NOT blocked despite paid_amount > 0")


def check_two_tax_groups_one_batch():
	"""T6: one invoice, one batch, two tax treatments -> two debts.

	Regression guard for the idempotency key: while the check keyed on
	(sales_invoice, batch), the second group matched the first debt and its
	amount was silently dropped (1,000,000 VND of debt never created).
	"""
	batch = _batch(_customer(), "T6")
	inv = _invoice(
		[
			{"qty": 10, "rate": 100_000, "batch": batch, "tax": KCT_TAX_TEMPLATE},
			{"qty": 4, "rate": 250_000, "batch": batch, "item": _vat_item(),
			 "tax": VAT_TAX_TEMPLATE},
		]
	)
	lines = {row.item_tax_template: flt(row.amount) for row in inv.items}
	if set(lines) != {KCT_TAX_TEMPLATE, VAT_TAX_TEMPLATE}:
		raise AssertionError(
			f"fixture broken: ERPNext rewrote the line templates to {set(lines)}"
		)
	debts = _debts(inv.name)
	if len(debts) != 2:
		raise AssertionError(f"expected 2 Batch Debts (one per tax group), got {len(debts)}: {debts}")
	by_template = {d.item_tax_template: flt(d.allocated_amount) for d in debts}
	if by_template != lines:
		raise AssertionError(
			f"each tax group must get its own net amount; lines={lines} debts={by_template}"
		)
	if any(d.docstatus != 1 for d in debts):
		raise AssertionError(f"both debts must be submitted: {debts}")
	return (f"{inv.name} -> 2 debts on one batch: "
			f"KCT {by_template[KCT_TAX_TEMPLATE]:,.0f} + VAT {by_template[VAT_TAX_TEMPLATE]:,.0f}")


CHECKS = (
	("T1  1 batch -> 1 debt", check_single_batch_si),
	("T2  2 batches -> 2 debts", check_multi_batch_si),
	("T3  unbatched line skipped", check_unbatched_line_skipped),
	("T4  cancel unpaid SI", check_cancel_unpaid_si),
	("T5  cancel blocked when paid", check_cancel_blocked_when_paid),
	("T6  2 tax groups, 1 batch", check_two_tax_groups_one_batch),
)


def collect():
	report = Report()
	for name, fn in CHECKS:
		report.check(name, fn)
	return report


def run():
	"""Entry point for `bench execute`. Raises when any check fails."""
	frappe.db.commit()
	report = collect()
	text, failed = report.render()
	print(text)
	print(json.dumps({"site": frappe.local.site, "failed": failed}))
	frappe.db.commit()
	if failed:
		frappe.throw(f"P1A acceptance FAILED: {failed}", title="P1A ACCEPTANCE FAILED")
	print("P1A ACCEPTANCE: ALL PASS")
	return {"failed": failed, "passed": len(report.rows) - len(failed)}


def debug():
	"""Print every failing check's real traceback (bench execute masks errors)."""
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


def cleanup():
	"""Remove P1A fixtures: debts first (so paid SI cancels freely), then SI,
	batches, item, customer.

	Invoices are found by content (customer/naming series), NOT by name prefix:
	they are named ACC-SINV-... by the site series, so a `name like P1A%`
	filter silently matches nothing.
	"""
	removed = []
	customers = frappe.get_all("Customer", filters={"customer_name": ["like", f"{PREFIX}%"]}, pluck="name")
	debts = frappe.get_all(
		"Batch Debt",
		filters={"sales_invoice": ["like", "ACC-SINV-%"], "customer": ["in", customers]},
		fields=["name", "docstatus", "paid_amount"],
	)
	for row in debts:
		try:
			if row.docstatus == 1:
				if flt(row.paid_amount) > 0:
					# T5 simulates payments by writing paid_amount directly; reset it
					# so the cancel cascade is not blocked while deleting fixtures.
					frappe.db.set_value("Batch Debt", row.name, "paid_amount", 0, update_modified=False)
				frappe.get_doc("Batch Debt", row.name).cancel()
			frappe.delete_doc("Batch Debt", row.name, force=True, ignore_permissions=True)
			removed.append(f"Batch Debt {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Batch Debt {row.name} FAILED: {exc}")
	invoices = frappe.get_all(
		"Sales Invoice",
		filters={"customer": ["in", customers]},
		fields=["name", "docstatus"],
	)
	for row in invoices:
		try:
			if row.docstatus == 1:
				frappe.get_doc("Sales Invoice", row.name).cancel()
			frappe.delete_doc("Sales Invoice", row.name, force=True, ignore_permissions=True)
			removed.append(f"Sales Invoice {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Sales Invoice {row.name} FAILED: {exc}")
	for name in frappe.get_all("Feed Batch", filters={"notes": ["like", f"{PREFIX}%"]}, pluck="name"):
		frappe.delete_doc("Feed Batch", name, force=True, ignore_permissions=True)
		removed.append(f"Feed Batch {name}")
	for doctype in ("Item", "Customer"):
		for name in frappe.get_all(doctype, filters={"name": ["like", f"{PREFIX}%"]}, pluck="name"):
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
			removed.append(f"{doctype} {name}")
	frappe.db.commit()
	print(f"[feed_dealer] P1A cleanup removed {len(removed)} fixture(s):")
	for item in removed:
		print(f"  - {item}")
	return removed
