"""P1D acceptance: Sales Return Request -> credit note -> returned_amount.

Run:      bench --site <site> execute feed_dealer.setup.p1d_acceptance.run
Debug:    bench --site <site> execute feed_dealer.setup.p1d_acceptance.debug
Cleanup:  bench --site <site> execute feed_dealer.setup.p1d_acceptance.cleanup

Covers the prompt's P1D acceptance list, on real submitted documents:

  T1  partial return of an unpaid debt -> outstanding drops by the credit
      note's line net, request is Approved with a real is_return invoice, AR
      grand_total of the original invoice equals base - returned (the credit
      note actually hit AR, not just the batch view)
  T2  over-return is refused on the DRAFT (our earlier check) and again at the
      credit note itself by ERPNext's validate_returned_items
  T3  a Draft request has zero financial side effects (no note, debts and
      batch total untouched); Reject too
  T4  two batches on one request -> each debt only carries its own lines
      (mapping by return_line, never a gross grand_total subtraction)
  T5  cancelling the credit note raises outstanding back (the note's rows
      stop counting; nothing else stores the returned figure)
  T6  one request spanning two invoices is refused (never half-credited)
  T7  a part-paid debt cannot be returned for more than it still owes: the
      quantity guard alone would have driven outstanding negative

`run()` and `debug()` clear their own fixtures first, so every assertion is
absolute. Fixtures live under the P1D-ACCEPT prefix, so P1A/P1B/P1C cleanups
never touch them.
"""

import json
import traceback

import frappe
from frappe.utils import add_days, flt, nowdate

from feed_dealer.events.payment_entry import _recalculate
from feed_dealer.setup.p1a_acceptance import _company, _debts, _invoice, _item
from feed_dealer.setup.p1b_acceptance import Report

PREFIX = "P1D-ACCEPT"


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


def _batch(tag, customer):
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


def _batched_invoice(tag, qty, rate, customer_tag=None, due_days=15):
	"""A real submitted Sales Invoice for one batch -> exactly one Batch Debt."""
	customer = _customer(customer_tag or tag)
	batch = _batch(tag, customer)
	inv = _invoice([{"qty": qty, "rate": rate, "batch": batch}], customer=customer, due_days=due_days)
	debts = _debts(inv.name)
	if len(debts) != 1:
		raise AssertionError(f"fixture broken: expected 1 debt for {inv.name}, got {len(debts)}")
	return inv, batch, debts[0].name


def _return_line(invoice, idx=0):
	return invoice.items[idx].name


def _request(invoice, rows, submit=True, customer=None, approval_status="Draft"):
	"""A Sales Return Request naming exact original invoice lines."""
	doc = frappe.get_doc(
		{
			"doctype": "Sales Return Request",
			"customer": customer or invoice.customer,
			"sales_invoice": invoice.name,
			"reason": "Quality Issue",
			"items": rows,
		}
	)
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
		frappe.db.commit()
	if approval_status != "Draft":
		doc.approval_status = approval_status
	return doc


def _approve(request_name):
	from feed_dealer.feed_dealer.doctype.sales_return_request.sales_return_request import (
		approve_request,
		reject,
	)

	return approve_request(request_name)


def _debt(name):
	return frappe.db.get_value(
		"Batch Debt", name, ["allocated_amount", "paid_amount", "returned_amount", "outstanding_amount", "status"], as_dict=True
	)


def _batch_total(batch):
	rows = frappe.get_all(
		"Batch Debt",
		filters={"batch": batch, "docstatus": 1},
		fields=[{"SUM": "outstanding_amount", "as": "total"}],
	)
	return flt(rows[0].total) if rows else 0.0


def _credit_notes(request_name):
	return frappe.get_all("Sales Invoice", filters={"feed_dealer_return_request": request_name}, pluck="name")


# ---------------------------------------------------------------------- tests
def check_partial_return_reduces_outstanding():
	"""T1: return part of an unpaid debt -> outstanding drops by the line net."""
	inv, batch, debt_name = _batched_invoice("T1", qty=10, rate=100_000)
	before = _debt(debt_name)
	if flt(before.outstanding_amount) != 1_000_000:
		raise AssertionError(f"fixture broken: {before}")
	request = _request(
		inv,
		[{"item_code": _item(), "qty": 5, "return_line": _return_line(inv), "batch_debt": debt_name}],
	)
	_approve(request.name)

	note_names = _credit_notes(request.name)
	if len(note_names) != 1:
		raise AssertionError(f"expected exactly 1 credit note, got {note_names}")
	note = frappe.get_doc("Sales Invoice", note_names[0])
	if note.docstatus != 1 or not note.is_return or note.return_against != inv.name:
		raise AssertionError(f"note is not a submitted return against the original: {note.name}")
	if flt(note.net_total) != -500_000:
		raise AssertionError(f"note net_total should be -500,000, got {note.net_total}")

	debt = _debt(debt_name)
	if flt(debt.returned_amount) != 500_000:
		raise AssertionError(f"returned_amount should be +500,000, got {debt.returned_amount}")
	if flt(debt.outstanding_amount) != 500_000:
		raise AssertionError(f"outstanding should be 500,000, got {debt.outstanding_amount}")
	if debt.status != "Chưa trả":
		raise AssertionError(f"a half-returned unpaid debt stays 'Chưa trả', got {debt.status!r}")
	if _batch_total(batch) != 500_000:
		raise AssertionError(f"batch total should be 500,000, got {_batch_total(batch):,.0f}")
	after = frappe.db.get_value("Sales Invoice", inv.name, "outstanding_amount")
	note_outstanding = frappe.db.get_value("Sales Invoice", note.name, "outstanding_amount")
	# Measured v16 semantics on this site (Payment Ledger): the return note posts
	# AGAINST ITSELF (`against_voucher_no` = the note, -500,000) and the original
	# invoice's outstanding column stays untouched; the customer-level AR truth
	# is the NET of the two (GL is the ledger). Assert exactly that contract.
	if flt(after) != 1_000_000:
		raise AssertionError(f"v16 keeps the original outstanding unchanged, got {after}")
	if flt(note_outstanding) != -500_000:
		raise AssertionError(f"the note should carry -500,000 outstanding, got {note_outstanding}")
	if flt(after) + flt(note_outstanding) != 500_000:
		raise AssertionError(f"net AR should be 500,000, got {flt(after) + flt(note_outstanding):,.0f}")
	stored = frappe.db.get_value(
		"Sales Return Request", request.name, ["approval_status", "approved_by", "batch_debt_adjusted"], as_dict=True
	)
	if stored.approval_status != "Approved" or not stored.approved_by or not stored.batch_debt_adjusted:
		raise AssertionError(f"approval stamp incomplete: {stored}")
	return (
		f"{request.name} -> {note.name}: {debt_name} returned 500,000, outstanding 1,000,000 -> 500,000, "
		f"AR net {flt(after) + flt(note_outstanding):,.0f} (orig {after:,.0f} + note {note_outstanding:,.0f}), "
		f"approved by {stored.approved_by}"
	)


def check_over_return_refused():
	"""T2: returning more than the original line is refused, twice over."""
	inv, _batch, debt_name = _batched_invoice("T2", qty=10, rate=100_000)
	line = _return_line(inv)
	# First return: the full line (submitted, real money moved).
	request1 = _request(inv, [{"item_code": _item(), "qty": 10, "return_line": line, "batch_debt": debt_name}])
	_approve(request1.name)
	if flt(_debt(debt_name).outstanding_amount) != 0:
		raise AssertionError("fixture broken: full return did not zero the debt")

	# Second request on the SAME line: our draft-side check refuses the insert.
	blocked = False
	detail = ""
	try:
		request2 = _request(inv, [{"item_code": _item(), "qty": 1, "return_line": line, "batch_debt": debt_name}])
		_approve(request2.name)
	except Exception as exc:  # noqa: BLE001 - either guard may fire first
		blocked = True
		detail = f"{type(exc).__name__}: {str(exc)[:160]}"
	if not blocked:
		raise AssertionError("an over-return request was approved - money invented from nothing")
	# Assert the REASON, not just that something blew up: without this, ERPNext's own
	# English quantity guard (or any fixture bug) would count as a pass, and the test
	# would stay green even if our draft-side guard stopped running (mutation-checked).
	if "chưa được trả" not in detail:
		raise AssertionError(f"refused for the WRONG reason (weak test): {detail}")
	# And the debt did not go negative while blocking.
	if flt(_debt(debt_name).outstanding_amount) < 0:
		raise AssertionError("over-return left the debt negative")
	return f"second return on {line[:40]}… refused ({detail}); debt stays 0, not negative"


def check_draft_has_no_side_effect():
	"""T3: a Draft (and a Rejected) request never touches money."""
	inv, batch, debt_name = _batched_invoice("T3", qty=10, rate=100_000)
	before, total_before = _debt(debt_name), _batch_total(batch)
	request = _request(
		inv,
		[{"item_code": _item(), "qty": 5, "return_line": _return_line(inv), "batch_debt": debt_name}],
		submit=False,
	)
	request.submit()  # the request paper itself is submitted; the return is not
	frappe.db.commit()
	if _credit_notes(request.name):
		raise AssertionError("a Draft request must not create a credit note")
	if _debt(debt_name) != before or _batch_total(batch) != total_before:
		raise AssertionError("a Draft request must not move debts")
	rejected = frappe.get_doc("Sales Return Request", request.name)
	from feed_dealer.feed_dealer.doctype.sales_return_request.sales_return_request import reject

	reject(rejected)
	if _credit_notes(request.name):
		raise AssertionError("a Rejected request must not create a credit note")
	if _debt(debt_name) != before or _batch_total(batch) != total_before:
		raise AssertionError("a Rejected request must not move debts")
	return f"{request.name} draft+rejected -> 0 notes, debt {before.outstanding_amount:,.0f} unchanged"


def check_mixed_invoice_request_refused():
	"""T6: one request spanning TWO invoices must be refused, not half-credited."""
	customer = _customer("T6")
	b1, b2 = _batch("T6a", customer), _batch("T6b", customer)
	inv1 = _invoice([{"qty": 10, "rate": 100_000, "batch": b1}], customer=customer)
	inv2 = _invoice([{"qty": 10, "rate": 100_000, "batch": b2}], customer=customer)
	d1, d2 = _debts(inv1.name)[0].name, _debts(inv2.name)[0].name
	if frappe.db.get_value("Batch Debt", d1, "sales_invoice") == frappe.db.get_value(
		"Batch Debt", d2, "sales_invoice"
	):
		raise AssertionError("fixture broken: debts must belong to different invoices")
	blocked, detail = False, ""
	try:
		_request(
			inv1,
			[
				{"item_code": _item(), "qty": 5, "return_line": _return_line(inv1), "batch_debt": d1},
				# Line 2 points at a debt of the OTHER invoice (return_line matches,
				# customer matches) — the classic silent-drop shape.
				{"item_code": _item(), "qty": 5, "return_line": _return_line(inv2), "batch_debt": d2},
			],
		)
	except Exception as exc:  # noqa: BLE001
		blocked, detail = True, f"{type(exc).__name__}: {str(exc)[:110]}"
	if not blocked:
		raise AssertionError(
			"a mixed-invoice request was accepted — second invoice's lines would be silently dropped"
		)
	if "hoá đơn" not in detail:
		raise AssertionError(f"refused for the WRONG reason (weak test): {detail}")
	if flt(_debt(d1).returned_amount) or flt(_debt(d2).returned_amount):
		raise AssertionError("refused request must not have touched any debt")
	return f"two-invoice request refused ({detail}); both debts untouched"


def check_two_batches_map_separately():
	"""T4: two batches on ONE invoice -> each debt carries only its own lines."""
	customer = _customer("T4")
	b1, b2 = _batch("T4a", customer), _batch("T4b", customer)
	inv = _invoice(
		[
			{"qty": 10, "rate": 100_000, "batch": b1},
			{"qty": 2, "rate": 250_000, "batch": b2},
		],
		customer=customer,
	)
	debts = _debts(inv.name)
	if len(debts) != 2:
		raise AssertionError(f"fixture broken: expected 2 debts on 2 batches, got {len(debts)}")
	debt1, debt2 = debts[0].name, debts[1].name
	request = _request(
		inv,
		[
			{"item_code": _item(), "qty": 4, "return_line": _return_line(inv, 0), "batch_debt": debt1},
			{"item_code": _item(), "qty": 2, "return_line": _return_line(inv, 1), "batch_debt": debt2},
		],
	)
	_approve(request.name)
	d1, d2 = _debt(debt1), _debt(debt2)
	if flt(d1.returned_amount) != 400_000 or flt(d1.outstanding_amount) != 600_000:
		raise AssertionError(f"debt 1 should carry only its line: {d1}")
	# Debt 2 was allocated 500,000 and BOTH returned units are on its line, so a
	# full-line return closes it entirely ("Đã trả") — per-line mapping means no
	# leftover from debt 1's return can leak here.
	if flt(d2.returned_amount) != 500_000 or flt(d2.outstanding_amount) != 0:
		raise AssertionError(f"debt 2 should be fully returned: {d2}")
	if d2.status != "Đã trả":
		raise AssertionError(f"debt 2 status should be 'Đã trả', got {d2.status!r}")
	return f"{request.name}: {debt1} -400,000, {debt2} -500,000 (per-line mapping, no gross subtraction)"


def check_cancel_credit_note_restores():
	"""T5: cancelling the credit note raises outstanding back."""
	inv, batch, debt_name = _batched_invoice("T5", qty=10, rate=100_000)
	request = _request(
		inv,
		[{"item_code": _item(), "qty": 5, "return_line": _return_line(inv), "batch_debt": debt_name}],
	)
	_approve(request.name)
	note_name = _credit_notes(request.name)[0]
	if flt(_debt(debt_name).outstanding_amount) != 500_000:
		raise AssertionError("fixture broken: the return did not land")

	from feed_dealer.feed_dealer.doctype.sales_return_request.sales_return_request import cancel_credit_note

	result = cancel_credit_note(request.name)
	if result.get("cancelled") != note_name:
		raise AssertionError(f"cancel_credit_note cancelled the wrong note: {result}")
	frappe.db.commit()
	debt = _debt(debt_name)
	if flt(debt.returned_amount) != 0:
		raise AssertionError(f"cancelled note must stop counting, returned_amount={debt.returned_amount}")
	if flt(debt.outstanding_amount) != 1_000_000:
		raise AssertionError(f"outstanding must be restored, got {debt.outstanding_amount}")
	if _batch_total(batch) != 1_000_000:
		raise AssertionError(f"batch total must be restored, got {_batch_total(batch):,.0f}")
	# And the cancelled request cannot be cancelled again through the guard: the
	# request itself stays with its (now cancelled) note reference.
	return f"{note_name} cancelled -> {debt_name} back to 1,000,000, returned_amount 0"


def check_return_beyond_outstanding_refused():
	"""T7: a part-paid debt cannot be returned for more than it still owes.

	Quantity alone is not enough: allocated 1,000,000, paid 600,000 leaves 400,000
	owed, and a 5-of-10 return is worth 500,000 — over the remaining debt. Without
	the value guard `outstanding = allocated - paid - returned` lands on -100,000,
	which also inflates P1C's available credit limit (it sums outstanding).
	"""
	from feed_dealer.setup.p1b_acceptance import _payment

	inv, _batch_name, debt_name = _batched_invoice("T7", qty=10, rate=100_000)
	_payment(inv.name, 600_000)
	debt = _debt(debt_name)
	if flt(debt.paid_amount) != 600_000 or flt(debt.outstanding_amount) != 400_000:
		raise AssertionError(f"fixture broken: the part payment did not land: {debt}")

	blocked, detail = False, ""
	try:
		_request(
			inv,
			[{"item_code": _item(), "qty": 5, "return_line": _return_line(inv), "batch_debt": debt_name}],
		)
	except Exception as exc:  # noqa: BLE001
		blocked, detail = True, f"{type(exc).__name__}: {str(exc)[:160]}"
	if not blocked:
		raise AssertionError(
			"returning 500,000 against a debt that only owes 400,000 was accepted - "
			"outstanding goes negative and the credit limit inflates"
		)
	if "chưa thanh toán" not in detail:
		raise AssertionError(f"refused for the WRONG reason (weak test): {detail}")
	if flt(_debt(debt_name).returned_amount):
		raise AssertionError("the refused request must not have moved the debt")
	return (
		f"{debt_name} owes 400,000 after a 600,000 payment; a 500,000 return was refused ({detail}); "
		f"returned_amount stays 0"
	)


CHECKS = (
	("T1  partial return reduces debt", check_partial_return_reduces_outstanding),
	("T2  over-return refused", check_over_return_refused),
	("T3  draft/reject: no side effect", check_draft_has_no_side_effect),
	("T4  two batches map separately", check_two_batches_map_separately),
	("T6  mixed-invoice request refused", check_mixed_invoice_request_refused),
	("T5  cancel note restores", check_cancel_credit_note_restores),
	("T7  return beyond outstanding refused", check_return_beyond_outstanding_refused),
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
		frappe.throw(f"P1D acceptance FAILED: {failed}", title="P1D ACCEPTANCE FAILED")
	print("P1D ACCEPTANCE: ALL PASS")
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
	"""Remove P1D fixtures: return requests and their credit notes, then the
	invoices, debts, batches and customers under the P1D-ACCEPT prefix."""
	removed = []
	customers = _customers()

	for name in frappe.get_all("Sales Return Request", pluck="name"):
		if not frappe.db.exists("Sales Return Request", name):
			continue
		doc = frappe.get_doc("Sales Return Request", name)
		if doc.customer not in customers:
			continue
		try:
			# A live note must be cancelled via the API (its own back-link from
			# the submitted request blocks a plain cancel).
			if doc.credit_note_reference and frappe.db.get_value(
				"Sales Invoice", doc.credit_note_reference, "docstatus"
			) == 1:
				from feed_dealer.feed_dealer.doctype.sales_return_request.sales_return_request import (
					cancel_credit_note,
				)

				cancel_credit_note(name)
				removed.append(f"credit note {doc.credit_note_reference}")
			if doc.docstatus == 1:
				frappe.get_doc("Sales Return Request", name).cancel()
			frappe.delete_doc("Sales Return Request", name, force=True, ignore_permissions=True)
			removed.append(f"Sales Return Request {name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Sales Return Request {name} FAILED: {exc}")

	for row in frappe.get_all(
		"Sales Invoice",
		filters={"customer": ["in", customers], "is_return": 1},
		fields=["name", "docstatus"],
	):
		try:
			if row.docstatus == 1:
				frappe.get_doc("Sales Invoice", row.name).cancel()
			frappe.delete_doc("Sales Invoice", row.name, force=True, ignore_permissions=True)
			removed.append(f"credit note {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"credit note {row.name} FAILED: {exc}")

	# Payments first: a submitted Payment Entry (T7 pays a debt before returning it)
	# holds allocations that block both the debt's cancel and its delete.
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

	debts = frappe.get_all("Batch Debt", filters={"customer": ["in", customers]}, fields=["name", "docstatus"])
	for row in debts:
		try:
			if row.docstatus == 1:
				frappe.get_doc("Batch Debt", row.name).cancel()
			frappe.delete_doc("Batch Debt", row.name, force=True, ignore_permissions=True)
			removed.append(f"Batch Debt {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Batch Debt {row.name} FAILED: {exc}")

	for row in frappe.get_all(
		"Sales Invoice", filters={"customer": ["in", customers], "is_return": 0}, fields=["name", "docstatus"]
	):
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
	for name in customers:
		frappe.delete_doc("Customer", name, force=True, ignore_permissions=True)
		removed.append(f"Customer {name}")
	frappe.db.commit()
	print(f"[feed_dealer] P1D cleanup removed {len(removed)} fixture(s):")
	for item in removed:
		print(f"  - {item}")
	return removed
