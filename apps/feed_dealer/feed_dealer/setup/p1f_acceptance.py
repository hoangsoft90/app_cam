"""P1F acceptance: Consent + Debt Confirmation Slip + Livestock Offset + Batch Ops.

Run:      bench --site <site> execute feed_dealer.setup.p1f_acceptance.run
Debug:    bench --site <site> execute feed_dealer.setup.p1f_acceptance.debug
Cleanup:  bench --site <site> execute feed_dealer.setup.p1f_acceptance.cleanup

Covers the prompt's P1F acceptance list on real submitted documents:

  T1  consent lifecycle: no record = no consent and no marketing; grant = both true;
      withdraw (API) = both false again, and the withdrawal is a NEW record
  T2  consent is append-only: a withdrawn record cannot be flipped back
  T3  debt slip: rows + total come from the open Batch Debts; the Jinja print format
      renders the customer and the total; "confirmed" without a signature photo refused
  T4  batch split by quantity: 2 target batches, debt conservation exact, source
      released (cancelled) and marked has_been_split
  T5  split refuses a debt that already has money on it (payment/return/offset)
  T6  livestock offset: Purchase Invoice + Journal Entry (Dr AP / Cr AR), one credit
      line per debt slice, offsets the debt by the invoice amount
  T7  offset is capped at what is owed (the remainder stays payable) and cancelling
      the Journal Entry reopens the debt

Fixtures live under the P1F-ACCEPT prefix so the other suites' cleanups never touch
them (and this cleanup never touches theirs).
"""

import json
import traceback

import frappe
from frappe.utils import flt, nowdate

from feed_dealer.setup.p1a_acceptance import _company, _debts, _invoice, _item
from feed_dealer.setup.p1b_acceptance import Report, _payment

PREFIX = "P1F-ACCEPT"


# ------------------------------------------------------------------- fixtures
def _customers():
	return frappe.get_all("Customer", filters={"customer_name": ["like", f"{PREFIX}%"]}, pluck="name")


def _customer(tag=None):
	name = f"{PREFIX} {tag}" if tag else f"{PREFIX} Customer"
	if not frappe.db.exists("Customer", name):
		group = frappe.db.get_value("Customer Group", "Trại lớn") or frappe.db.get_value(
			"Customer Group", "All Customer Groups"
		)
		frappe.get_doc(
			{"doctype": "Customer", "customer_name": name, "customer_group": group}
		).insert(ignore_permissions=True)
	return name


def _supplier(name):
	if not frappe.db.exists("Supplier", name):
		group = frappe.db.get_value("Supplier Group", "All Supplier Groups") or frappe.db.get_value(
			"Supplier Group", {"is_group": 0}
		)
		frappe.get_doc(
			{"doctype": "Supplier", "supplier_name": name, "supplier_group": group}
		).insert(ignore_permissions=True)
	return name


def _batch(tag, customer):
	existing = frappe.db.get_value(
		"Feed Batch", {"customer": customer, "notes": f"{PREFIX} {tag}"}, "name"
	)
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


def _batched_invoice(tag, qty, rate, customer_tag=None, due_days=15):
	"""A real submitted Sales Invoice for one batch -> exactly one Batch Debt."""
	customer = _customer(customer_tag or tag)
	batch = _batch(tag, customer)
	inv = _invoice([{"qty": qty, "rate": rate, "batch": batch}], customer=customer, due_days=due_days)
	debts = _debts(inv.name)
	if len(debts) != 1:
		raise AssertionError(f"fixture broken: expected 1 debt for {inv.name}, got {len(debts)}")
	return inv, batch, debts[0].name


def _debt(name):
	return frappe.db.get_value(
		"Batch Debt", name, ["allocated_amount", "outstanding_amount", "offset_amount"], as_dict=True
	)


def _expense_account():
	company = _company()
	account = frappe.get_cached_value("Company", company, "default_expense_account")
	if account:
		return account
	rows = frappe.get_all(
		"Account",
		filters={"company": company, "root_type": "Expense", "is_group": 0},
		pluck="name",
		limit=1,
	)
	if not rows:
		raise AssertionError(f"fixture broken: company {company} has no expense account")
	return rows[0]


def _service_item():
	name = f"{PREFIX} Livestock Item"
	if not frappe.db.exists("Item", name):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": name,
				"item_name": name,
				"item_group": frappe.db.get_value("Item Group", {"is_group": 0})
				or frappe.db.get_value("Item Group", "All Item Groups"),
				"is_stock_item": 0,
				"stock_uom": "Kg",
				"expense_account": _expense_account(),
			}
		).insert(ignore_permissions=True)
	return name


def _purchase_invoice(supplier, amount):
	pi = frappe.get_doc(
		{
			"doctype": "Purchase Invoice",
			"supplier": supplier,
			"company": _company(),
			"posting_date": nowdate(),
			"items": [
				{
					"item_code": _service_item(),
					"qty": 1,
					"rate": flt(amount),
					"expense_account": _expense_account(),
				}
			],
		}
	)
	pi.insert(ignore_permissions=True)
	pi.submit()
	frappe.db.commit()
	return pi


def _livestock_row(batch, purchase_invoice, amount, offset=True):
	"""Append a Livestock Sale row to the Feed Batch and return its row name."""
	doc = frappe.get_doc("Feed Batch", batch)
	doc.append(
		"livestock_sales",
		{
			"sale_date": nowdate(),
			"quantity": 1,
			"unit": "Con",
			"total_weight": 100,
			"unit_price": flt(amount),
			"total_amount": flt(amount),
			"purchase_invoice": purchase_invoice,
			"offset_to_debt": 1 if offset else 0,
		},
	)
	doc.flags.ignore_permissions = True
	doc.save()
	frappe.db.commit()
	return doc.livestock_sales[-1].name


def _operation(operation_type, sources, targets, method="Theo số con"):
	doc = frappe.get_doc(
		{
			"doctype": "Batch Operation",
			"operation_type": operation_type,
			"operation_date": nowdate(),
			"debt_allocation_method": method,
			"source_batches": [{"batch": batch, "quantity": qty} for batch, qty in sources],
			"target_batches": [{"batch": batch, "quantity": qty} for batch, qty in targets],
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	frappe.db.commit()
	return doc


def _batch_open_debt(batch):
	rows = frappe.get_all(
		"Batch Debt",
		filters={"batch": batch, "docstatus": 1},
		fields=[{"SUM": "allocated_amount", "as": "allocated"}],
	)
	return flt(rows[0].allocated) if rows else 0.0


def _je_lines(je_name):
	return frappe.get_all(
		"Journal Entry Account",
		filters={"parent": je_name, "parenttype": "Journal Entry"},
		fields=["account", "batch_debt", "debit_in_account_currency", "credit_in_account_currency"],
	)


# ---------------------------------------------------------------------- tests
def check_consent_lifecycle():
	"""T1: grant -> marketing allowed; withdraw -> both false, and it is a NEW record."""
	from feed_dealer.feed_dealer.doctype.data_processing_consent import data_processing_consent as consent

	customer = _customer("T1")
	frappe.db.delete(
		"Data Processing Consent", {"customer": customer}
	)  # re-runnable fixture
	if consent.has_active_consent(customer) or consent.marketing_allowed(customer):
		raise AssertionError("a fresh customer must have neither consent nor marketing")

	granted = frappe.get_doc(
		{
			"doctype": "Data Processing Consent",
			"customer": customer,
			"consent_type": consent.MARKETING_SCOPE,
			"granted_via": "T1 fixture",
		}
	)
	granted.insert(ignore_permissions=True)
	frappe.db.commit()
	if not consent.has_active_consent(customer) or not consent.marketing_allowed(customer):
		raise AssertionError("a granted consent must enable both consent and marketing")
	if not granted.consent_date or not granted.consent_scope:
		raise AssertionError(f"the controller must stamp consent_date and a scope row: {granted.as_dict()}")

	result = consent.withdraw(customer, consent_type=consent.MARKETING_SCOPE, reason="T1 withdraw")
	if consent.has_active_consent(customer) or consent.marketing_allowed(customer):
		raise AssertionError(f"after withdrawal nothing may be allowed: {result}")
	records = frappe.get_all(
		"Data Processing Consent",
		filters={"customer": customer},
		fields=["name", "withdrawn"],
		order_by="creation asc",
	)
	if len(records) != 2 or not records[-1].withdrawn:
		raise AssertionError(f"withdrawal must APPEND a withdrawn record, got {records}")
	return f"{customer}: granted -> marketing True; withdrew via {result['name']} -> marketing False (2 records kept)"


def check_consent_cannot_be_revived():
	"""T2: a withdrawn consent record is history and cannot be flipped back on."""
	from feed_dealer.feed_dealer.doctype.data_processing_consent import data_processing_consent as consent

	customer = _customer("T2")
	doc = frappe.get_doc(
		{
			"doctype": "Data Processing Consent",
			"customer": customer,
			"consent_type": consent.MARKETING_SCOPE,
			"granted_via": "T2 fixture",
			"withdrawn": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	blocked, detail = False, ""
	try:
		doc.reload()
		doc.withdrawn = 0
		doc.save(ignore_permissions=True)
	except Exception as exc:  # noqa: BLE001
		blocked, detail = True, f"{type(exc).__name__}: {str(exc)[:120]}"
	if not blocked:
		raise AssertionError("a withdrawn consent was revived - the audit trail is rewritable")
	if "không thể bật lại" not in detail:
		raise AssertionError(f"refused for the WRONG reason (weak test): {detail}")
	return f"revive attempt refused: {detail}"


def check_debt_slip_fills_and_prints():
	"""T3: slip rows/total come from open debts; print format renders; unsigned confirm refused."""
	from feed_dealer.feed_dealer.doctype.debt_confirmation_slip import debt_confirmation_slip as slip_mod

	customer = _customer("T3")
	b1, b2 = _batch("T3a", customer), _batch("T3b", customer)
	inv1 = _invoice([{"qty": 10, "rate": 100_000, "batch": b1}], customer=customer)
	inv2 = _invoice([{"qty": 4, "rate": 50_000, "batch": b2}], customer=customer)
	expected = 1_000_000 + 200_000
	for inv in (inv1, inv2):
		if len(_debts(inv.name)) != 1:
			raise AssertionError(f"fixture broken: no single debt for {inv.name}")

	result = slip_mod.build_slip(customer)
	doc = frappe.get_doc("Debt Confirmation Slip", result["name"])
	if len(doc.debt_details) != 2:
		raise AssertionError(f"expected 2 debt rows on the slip, got {len(doc.debt_details)}")
	if flt(doc.total_confirmed_debt) != expected:
		raise AssertionError(f"total should be {expected:,}, got {doc.total_confirmed_debt}")

	html = frappe.get_print(
		"Debt Confirmation Slip", doc.name, print_format=slip_mod.PRINT_FORMAT
	)
	if customer not in html:
		raise AssertionError("the rendered slip does not name the customer")
	if "1,200,000" not in html:
		raise AssertionError("the rendered slip does not show the confirmed total")

	# Confirmed-without-signature must be refused (the signature IS the evidence).
	blocked, detail = False, ""
	try:
		doc.reload()
		doc.confirmed_by_customer = 1
		doc.save(ignore_permissions=True)
	except Exception as exc:  # noqa: BLE001
		blocked, detail = True, f"{type(exc).__name__}: {str(exc)[:110]}"
	if not blocked:
		raise AssertionError("'confirmed' without a signature photo was accepted")
	if "chữ ký" not in detail:
		raise AssertionError(f"refused for the WRONG reason (weak test): {detail}")
	return (
		f"{doc.name}: {len(doc.debt_details)} rows, total {doc.total_confirmed_debt:,.0f}, printed via "
		f"'{slip_mod.PRINT_FORMAT}' (customer+total present); unsigned confirm refused"
	)


def check_batch_split_conserves_debt():
	"""T4: split one batch into two by quantity - debt conservation must be exact."""
	inv, batch, debt_name = _batched_invoice("T4", qty=10, rate=100_000)
	before = _debt(debt_name)
	if flt(before.outstanding_amount) != 1_000_000:
		raise AssertionError(f"fixture broken: {before}")
	customer = frappe.db.get_value("Batch Debt", debt_name, "customer")
	left, right = _batch("T4left", customer), _batch("T4right", customer)
	# The debug/manual re-run path: clear a previous split of this batch.
	for name in frappe.get_all("Batch Debt", filters={"batch": ["in", [left, right]]}, pluck="name"):
		doc = frappe.get_doc("Batch Debt", name)
		if doc.docstatus == 1:
			doc.flags.ignore_links = True
			doc.cancel()
		frappe.delete_doc("Batch Debt", name, force=True, ignore_permissions=True)

	op = _operation("Tách lứa", [(batch, 10)], [(left, 6), (right, 4)])
	left_total, right_total = _batch_open_debt(left), _batch_open_debt(right)
	if left_total + right_total != flt(before.allocated_amount):
		raise AssertionError(
			f"split lost money: sources {flt(before.allocated_amount):,.0f} vs targets "
			f"{left_total + right_total:,.0f}"
		)
	if (left_total, right_total) != (600_000, 400_000):
		raise AssertionError(f"6/4 split of 1,000,000 should be 600,000/400,000, got {left_total}/{right_total}")
	source = frappe.db.get_value("Batch Debt", debt_name, ["docstatus", "status"], as_dict=True)
	if source.docstatus != 2:
		raise AssertionError(f"the source slice must be released (cancelled), got {source}")
	if not frappe.db.get_value("Feed Batch", batch, "has_been_split") or frappe.db.get_value(
		"Feed Batch", batch, "split_operation"
	) != op.name:
		raise AssertionError("the source batch was not marked as split")
	for target in (left, right):
		if frappe.db.get_value("Feed Batch", target, "parent_batch") != batch:
			raise AssertionError(f"target {target} does not point back at its parent batch")
	return (
		f"{op.name}: {debt_name} 1,000,000 -> {left_total:,.0f} + {right_total:,.0f} (source cancelled, "
		f"targets linked to parent)"
	)


def check_batch_split_refuses_touched_debt():
	"""T5: a debt with money on it cannot be split - settle first."""
	inv, batch, debt_name = _batched_invoice("T5", qty=10, rate=100_000)
	_payment(inv.name, 200_000)
	paid = _debt(debt_name)
	if flt(paid.outstanding_amount) != 800_000:
		raise AssertionError(f"fixture broken: the payment did not land: {paid}")
	customer = frappe.db.get_value("Batch Debt", debt_name, "customer")
	left, right = _batch("T5left", customer), _batch("T5right", customer)

	blocked, detail = False, ""
	try:
		_operation("Tách lứa", [(batch, 10)], [(left, 5), (right, 5)])
	except Exception as exc:  # noqa: BLE001
		blocked, detail = True, f"{type(exc).__name__}: {str(exc)[:150]}"
	if not blocked:
		raise AssertionError("a part-paid debt was split - the split cannot be honest about paid money")
	if "đã có thanh toán" not in detail:
		raise AssertionError(f"refused for the WRONG reason (weak test): {detail}")
	if flt(_debt(debt_name).outstanding_amount) != 800_000:
		raise AssertionError("the refused split must not have moved the debt")
	return f"split of a part-paid debt refused ({detail}); debt untouched"


def check_livestock_offset_double_entry():
	"""T6: Purchase Invoice + JE (Dr AP / Cr AR) net the debt by the invoice amount."""
	from feed_dealer.events.livestock_offset import offset_livestock_sale

	inv, batch, debt_name = _batched_invoice("T6", qty=10, rate=100_000)
	supplier = _supplier(f"{PREFIX} Supplier T6")
	pi = _purchase_invoice(supplier, 700_000)
	row_name = _livestock_row(batch, pi.name, 700_000)

	result = offset_livestock_sale(batch, row_name)
	je = frappe.get_doc("Journal Entry", result["journal_entry"])
	if je.docstatus != 1:
		raise AssertionError(f"the offset Journal Entry must be submitted: {je.name}")
	debits = sum(flt(row.debit_in_account_currency) for row in je.accounts)
	credits = sum(flt(row.credit_in_account_currency) for row in je.accounts)
	if round(debits, 2) != round(credits, 2) or round(debits, 2) != 700_000:
		raise AssertionError(f"the JE does not balance at 700,000: debit={debits}, credit={credits}")
	attributed = [row for row in _je_lines(je.name) if row.batch_debt]
	if len(attributed) != 1 or attributed[0].batch_debt != debt_name:
		raise AssertionError(f"the offset was not attributed to {debt_name}: {attributed}")
	debt = _debt(debt_name)
	if flt(debt.offset_amount) != 700_000 or flt(debt.outstanding_amount) != 300_000:
		raise AssertionError(f"offset_amount/outstanding wrong after offset: {debt}")
	if frappe.db.get_value("Livestock Sale", row_name, "journal_entry") != je.name:
		raise AssertionError("the livestock row does not link its journal entry")
	return (
		f"{row_name}: PI {pi.name} + JE {je.name} (balanced at 700,000) -> {debt_name} offset 700,000, "
		f"outstanding 1,000,000 -> 300,000"
	)


def check_livestock_offset_capped_and_reversible():
	"""T7: never offset more than is owed; cancelling the JE reopens the debt."""
	from feed_dealer.events.livestock_offset import offset_livestock_sale

	inv, batch, debt_name = _batched_invoice("T7", qty=10, rate=100_000)
	supplier = _supplier(f"{PREFIX} Supplier T7")
	pi = _purchase_invoice(supplier, 1_500_000)
	row_name = _livestock_row(batch, pi.name, 1_500_000)

	result = offset_livestock_sale(batch, row_name)
	if flt(result["netted"]) != 1_000_000 or flt(result["left_payable"]) != 500_000:
		raise AssertionError(f"the offset must be capped at the 1,000,000 debt: {result}")
	debt = _debt(debt_name)
	if flt(debt.offset_amount) != 1_000_000 or flt(debt.outstanding_amount) != 0:
		raise AssertionError(f"debt should be fully netted: {debt}")

	je = frappe.get_doc("Journal Entry", result["journal_entry"])
	je.flags.ignore_links = True
	je.cancel()
	frappe.db.commit()
	after = _debt(debt_name)
	if flt(after.offset_amount) != 0 or flt(after.outstanding_amount) != 1_000_000:
		raise AssertionError(f"cancelling the JE must reopen the debt: {after}")
	return (
		f"PI 1,500,000 vs debt 1,000,000 -> netted 1,000,000 (500,000 stays payable); cancelling "
		f"{je.name} reopened the debt to 1,000,000"
	)


CHECKS = (
	("T1  consent lifecycle", check_consent_lifecycle),
	("T2  consent append-only", check_consent_cannot_be_revived),
	("T3  debt slip + print", check_debt_slip_fills_and_prints),
	("T4  split conserves debt", check_batch_split_conserves_debt),
	("T5  split refuses part-paid", check_batch_split_refuses_touched_debt),
	("T6  livestock offset (JE)", check_livestock_offset_double_entry),
	("T7  offset capped + reversible", check_livestock_offset_capped_and_reversible),
)


def collect():
	report = Report()
	for name, fn in CHECKS:
		report.check(name, fn)
	return report


def run():
	"""Entry point for `bench execute`. Raises when any check fails."""
	cleanup()
	frappe.db.commit()
	report = collect()
	text, failed = report.render()
	print(text)
	print(json.dumps({"site": frappe.local.site, "failed": failed}))
	frappe.db.commit()
	if failed:
		frappe.throw(f"P1F acceptance FAILED: {failed}", title="P1F ACCEPTANCE FAILED")
	print("P1F ACCEPTANCE: ALL PASS")
	return {"failed": failed, "passed": len(report.rows) - len(failed)}


def debug():
	"""Print every failing check's real traceback (bench execute masks errors)."""
	cleanup()
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
	"""Remove P1F fixtures in dependency order (docs that hold links first)."""
	removed = []
	customers = _customers()

	batches = frappe.get_all(
		"Feed Batch", filters={"notes": ["like", f"{PREFIX}%"]}, pluck="name"
	)
	# Journal Entries created by the offset API hang off the livestock rows.
	jes = set()
	for batch in batches:
		for row in frappe.get_all(
			"Livestock Sale",
			filters={"parent": batch, "parenttype": "Feed Batch"},
			pluck="journal_entry",
		):
			if row:
				jes.add(row)
	for je_name in jes:
		try:
			doc = frappe.get_doc("Journal Entry", je_name)
			if doc.docstatus == 1:
				doc.flags.ignore_links = True
				doc.cancel()
			frappe.delete_doc("Journal Entry", je_name, force=True, ignore_permissions=True)
			removed.append(f"Journal Entry {je_name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Journal Entry {je_name} FAILED: {exc}")

	for name in frappe.get_all("Batch Operation", pluck="name"):
		doc = frappe.get_doc("Batch Operation", name)
		in_scope = any(
			row.batch in batches for row in list(doc.source_batches) + list(doc.target_batches)
		)
		if not in_scope:
			continue
		try:
			if doc.docstatus == 1:
				doc.flags.ignore_links = True
				doc.cancel()
			frappe.delete_doc("Batch Operation", name, force=True, ignore_permissions=True)
			removed.append(f"Batch Operation {name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Batch Operation {name} FAILED: {exc}")

	for name in frappe.get_all(
		"Purchase Invoice",
		filters={"supplier": ["like", f"{PREFIX}%"]},
		pluck="name",
	):
		try:
			doc = frappe.get_doc("Purchase Invoice", name)
			if doc.docstatus == 1:
				doc.flags.ignore_links = True
				doc.cancel()
			frappe.delete_doc("Purchase Invoice", name, force=True, ignore_permissions=True)
			removed.append(f"Purchase Invoice {name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Purchase Invoice {name} FAILED: {exc}")

	for name in frappe.get_all(
		"Debt Confirmation Slip", filters={"customer": ["in", customers]}, pluck="name"
	):
		try:
			doc = frappe.get_doc("Debt Confirmation Slip", name)
			if doc.docstatus == 1:
				doc.flags.ignore_links = True
				doc.cancel()
			frappe.delete_doc("Debt Confirmation Slip", name, force=True, ignore_permissions=True)
			removed.append(f"Debt Confirmation Slip {name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Debt Confirmation Slip {name} FAILED: {exc}")

	for name in frappe.get_all(
		"Data Processing Consent", filters={"customer": ["in", customers]}, pluck="name"
	):
		frappe.delete_doc("Data Processing Consent", name, force=True, ignore_permissions=True)
		removed.append(f"Data Processing Consent {name}")

	for row in frappe.get_all(
		"Payment Entry",
		filters={"party_type": "Customer", "party": ["in", customers]},
		fields=["name", "docstatus"],
	):
		try:
			if row.docstatus == 1:
				frappe.get_doc("Payment Entry", row.name).cancel()
			frappe.delete_doc("Payment Entry", row.name, force=True, ignore_permissions=True)
			removed.append(f"Payment Entry {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Payment Entry {row.name} FAILED: {exc}")

	for row in frappe.get_all(
		"Batch Debt", filters={"customer": ["in", customers]}, fields=["name", "docstatus"]
	):
		try:
			if row.docstatus == 1:
				frappe.get_doc("Batch Debt", row.name).cancel()
			frappe.delete_doc("Batch Debt", row.name, force=True, ignore_permissions=True)
			removed.append(f"Batch Debt {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Batch Debt {row.name} FAILED: {exc}")

	for row in frappe.get_all(
		"Sales Invoice", filters={"customer": ["in", customers]}, fields=["name", "docstatus"]
	):
		try:
			if row.docstatus == 1:
				frappe.get_doc("Sales Invoice", row.name).cancel()
			frappe.delete_doc("Sales Invoice", row.name, force=True, ignore_permissions=True)
			removed.append(f"Sales Invoice {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Sales Invoice {row.name} FAILED: {exc}")

	for name in batches:
		frappe.delete_doc("Feed Batch", name, force=True, ignore_permissions=True)
		removed.append(f"Feed Batch {name}")
	for name in customers:
		frappe.delete_doc("Customer", name, force=True, ignore_permissions=True)
		removed.append(f"Customer {name}")
	for name in frappe.get_all(
		"Supplier", filters={"supplier_name": ["like", f"{PREFIX}%"]}, pluck="name"
	):
		frappe.delete_doc("Supplier", name, force=True, ignore_permissions=True)
		removed.append(f"Supplier {name}")
	frappe.db.commit()
	print(f"[feed_dealer] P1F cleanup removed {len(removed)} fixture(s):")
	for item in removed:
		print(f"  - {item}")
	return removed
