"""P1B acceptance: Payment Entry -> Payment Allocation (FIFO).

Run:      bench --site <site> execute feed_dealer.setup.p1b_acceptance.run
Debug:    bench --site <site> execute feed_dealer.setup.p1b_acceptance.debug
Cleanup:  bench --site <site> execute feed_dealer.setup.p1b_acceptance.cleanup

Covers the prompt's P1B acceptance list, on real submitted documents:

  T1  partial payment          -> status "Một phần", outstanding reduced, and the
                                  allocation snapshot (previous_paid / after) is right
  T2  full payment             -> status "Đã trả", outstanding 0, batch total 0
  T3  FIFO order               -> the older due_date is paid first, the newer gets
                                  only the remainder
  T4  cancel the payment       -> allocations cancelled, outstanding restored
  T5  overdue + late fee       -> overdue_days/status from due_date, fee follows
                                  Feed Dealer Settings (proven by changing the rate)
  T6  re-fired hook / SoT      -> a retry never pays a debt twice, and the batch
                                  view can never exceed Payment Entry.paid_amount
  T7  refund does not allocate -> a `Pay` entry to a Customer (ERPNext allows it
                                  by API) leaves the debts alone
  T8  Unreconcile Payment      -> ERPNext's own tool delinks the payment WITHOUT
                                  cancelling it; our allocations are reversed and
                                  the debt reopens (P1C hardening of a P1B gap)
  T9  the FIFO read locks      -> proven from a second DB connection: while an
                                  allocation holds the debt rows, another
                                  session's FOR UPDATE on them times out

`run()` and `debug()` both clear their own fixtures first, so every assertion is
absolute instead of relative to whatever a previous run left behind. Fixtures
live under the P1B-ACCEPT prefix on a dedicated customer, so `cleanup()` never
touches P1A's (or real) data.
"""

import json
import traceback

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from frappe.utils import flt, nowdate

from feed_dealer.events.payment_entry import _open_debts, _recalculate, on_submit
from feed_dealer.setup.p1a_acceptance import _company, _debts, _invoice

PREFIX = "P1B-ACCEPT"

# One customer PER CHECK. FIFO runs across every open debt of the customer, so
# two checks sharing a customer would pay each other's debts: a payment in T2
# would settle T1's leftover first, which is correct behaviour and useless as a
# test fixture.


class Report:
	def __init__(self):
		self.rows = []

	def check(self, name, fn):
		try:
			detail = fn()
			self.rows.append((name, True, detail or "ok"))
		except Exception as exc:  # noqa: BLE001 - one failing check must not hide the rest
			self.rows.append((name, False, f"{type(exc).__name__}: {exc}"))
		return self.rows[-1][1]

	def render(self):
		width = max(len(name) for name, _ok, _d in self.rows)
		lines = ["", f"{'CHECK'.ljust(width)}  RESULT  DETAIL", "-" * (width + 44)]
		for name, ok, detail in self.rows:
			lines.append(f"{name.ljust(width)}  {'PASS  ' if ok else 'FAIL  '}  {detail}")
		failed = [name for name, ok, _ in self.rows if not ok]
		lines.append("-" * (width + 44))
		lines.append(
			f"TOTAL: {len(self.rows)}   PASS: {len(self.rows) - len(failed)}   FAIL: {len(failed)}"
		)
		return "\n".join(lines), failed


# ------------------------------------------------------------------- fixtures
def _customers():
	return frappe.get_all(
		"Customer", filters={"customer_name": ["like", f"{PREFIX}%"]}, pluck="name"
	)


def _customer(tag):
	name = f"{PREFIX} {tag}"
	if not frappe.db.exists("Customer", name):
		group = frappe.db.get_value("Customer Group", "Trại lớn") or frappe.db.get_value(
			"Customer Group", "All Customer Groups"
		)
		frappe.get_doc(
			{"doctype": "Customer", "customer_name": name, "customer_group": group}
		).insert(ignore_permissions=True)
	return name


def _batch(tag, customer):
	"""One dedicated Feed Batch per tag, so runs are repeatable.

	`notes` carries the P1B prefix so this suite's own cleanup finds its batches
	(and P1A's cleanup, which keys on the P1A prefix, leaves them alone).
	"""
	notes = f"{PREFIX} {tag}"
	existing = frappe.db.get_value("Feed Batch", {"customer": customer, "notes": notes}, "name")
	if existing:
		return existing
	doc = frappe.get_doc(
		{
			"doctype": "Feed Batch",
			"customer": customer,
			"animal_type": "Lợn",
			"start_date": nowdate(),
			"notes": notes,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _batched_invoice(tag, qty, rate, due_days=15, customer_tag=None):
	"""A real submitted Sales Invoice for one batch -> one Batch Debt."""
	customer = _customer(customer_tag or tag)
	batch = _batch(tag, customer)
	inv = _invoice(
		[{"qty": qty, "rate": rate, "batch": batch}],
		customer=customer,
		due_days=due_days,
		# An already-overdue invoice must also be posted in the past.
		posting_days=due_days - 10 if due_days < 0 else 0,
	)
	debts = _debts(inv.name)
	if len(debts) != 1:
		raise AssertionError(f"fixture broken: expected 1 debt for {inv.name}, got {debts}")
	return inv, batch, debts[0].name


def _payment(invoice_name, amount):
	"""A real submitted Payment Entry (Receive, Customer) against that invoice.

	`get_payment_entry` is ERPNext's own factory: it picks the company's bank/cash
	account and the customer's receivable account, so the fixture cannot invent
	an account combination that the real UI would never produce.
	"""
	pe = frappe.get_doc(get_payment_entry("Sales Invoice", invoice_name))
	amount = flt(amount)
	pe.paid_amount = amount
	pe.received_amount = amount
	# A receipt into a Bank account needs a bank transaction reference
	# (PaymentEntry.validate_transaction_reference); the real UI asks for it too.
	pe.reference_no = f"{PREFIX} bank transfer"
	pe.reference_date = pe.posting_date or nowdate()
	if pe.references:
		# Cap the invoice-level reference at what that invoice still owes: the
		# remainder stays unallocated, which is precisely the lump-sum payment
		# FIFO is here to slice across batches.
		pe.references[0].allocated_amount = min(amount, flt(pe.references[0].outstanding_amount))
	pe.insert(ignore_permissions=True)
	pe.submit()
	frappe.db.commit()
	return pe


def _allocations(payment_name):
	return frappe.get_all(
		"Payment Allocation",
		filters={"payment_entry": payment_name},
		fields=[
			"name",
			"docstatus",
			"batch_debt",
			"paid_amount",
			"previous_paid",
			"outstanding_after",
		],
		order_by="creation",
	)


def _debt(name):
	return frappe.db.get_value(
		"Batch Debt",
		name,
		["paid_amount", "outstanding_amount", "status", "overdue_days", "late_payment_fee"],
		as_dict=True,
	)


def _batch_total(batch):
	rows = frappe.get_all(
		"Batch Debt",
		filters={"batch": batch, "docstatus": 1},
		fields=[{"SUM": "outstanding_amount", "as": "total"}],
	)
	return flt(rows[0].total) if rows else 0.0


# ---------------------------------------------------------------------- tests
def check_partial_payment():
	"""T1: paying part of a debt leaves status "Một phần" with the right numbers."""
	inv, batch, debt_name = _batched_invoice("T1", qty=10, rate=100_000)
	pe = _payment(inv.name, 400_000)
	allocs = _allocations(pe.name)
	if len(allocs) != 1:
		raise AssertionError(f"expected 1 allocation, got {allocs}")
	alloc = allocs[0]
	if alloc.docstatus != 1:
		raise AssertionError(f"allocation must be submitted, docstatus={alloc.docstatus}")
	if flt(alloc.paid_amount) != 400_000 or flt(alloc.outstanding_after) != 600_000:
		raise AssertionError(f"allocation snapshot wrong: {alloc}")
	debt = _debt(debt_name)
	if flt(debt.paid_amount) != 400_000 or flt(debt.outstanding_amount) != 600_000:
		raise AssertionError(f"debt not updated: {debt}")
	if debt.status != "Một phần":
		raise AssertionError(f"status should be 'Một phần', got {debt.status!r}")
	if _batch_total(batch) != 600_000:
		raise AssertionError(f"batch total_debt should be 600,000, got {_batch_total(batch):,.0f}")
	return f"{pe.name} 400,000 -> {debt_name}: paid 400,000, outstanding 600,000, 'Một phần'"


def check_full_payment():
	"""T2: paying the whole debt closes it (status "Đã trả", batch total 0)."""
	inv, batch, debt_name = _batched_invoice("T2", qty=10, rate=100_000)
	pe = _payment(inv.name, 1_000_000)
	debt = _debt(debt_name)
	if flt(debt.outstanding_amount) != 0:
		raise AssertionError(f"outstanding should be 0, got {debt.outstanding_amount}")
	if debt.status != "Đã trả":
		raise AssertionError(f"status should be 'Đã trả', got {debt.status!r}")
	if debt.overdue_days != 0:
		raise AssertionError(f"a settled debt has no overdue days, got {debt.overdue_days}")
	if _batch_total(batch) != 0:
		raise AssertionError(f"batch total_debt should be 0, got {_batch_total(batch):,.0f}")
	alloc = _allocations(pe.name)[0]
	if flt(alloc.outstanding_after) != 0:
		raise AssertionError(f"allocation should record zero outstanding after, got {alloc}")
	return f"{pe.name} 1,000,000 -> {debt_name}: 'Đã trả', batch total 0"


def check_fifo_order():
	"""T3: two debts -> the oldest due_date is paid first, newer gets the rest."""
	older_inv, _older_batch, older_debt = _batched_invoice(
		"T3-old", qty=10, rate=100_000, due_days=5, customer_tag="T3"
	)
	_newer_inv, _newer_batch, newer_debt = _batched_invoice(
		"T3-new", qty=10, rate=100_000, due_days=20, customer_tag="T3"
	)
	# One payment against the newest invoice on purpose: FIFO is a property of the
	# customer's debt list, not of which invoice the payment names.
	pe = _payment(_newer_inv.name, 1_200_000)
	by_debt = {row.batch_debt: flt(row.paid_amount) for row in _allocations(pe.name)}
	if by_debt.get(older_debt) != 1_000_000:
		raise AssertionError(f"the older debt must be settled first, got {by_debt}")
	if by_debt.get(newer_debt) != 200_000:
		raise AssertionError(f"the newer debt must take only the remainder, got {by_debt}")
	older, newer = _debt(older_debt), _debt(newer_debt)
	if older.status != "Đã trả" or flt(older.outstanding_amount) != 0:
		raise AssertionError(f"older debt should be closed: {older}")
	if newer.status != "Một phần" or flt(newer.outstanding_amount) != 800_000:
		raise AssertionError(f"newer debt should be partly paid: {newer}")
	# The older debt belongs to a different invoice than the one the payment
	# names; `older_inv` only exists here to keep the fixture explicit.
	return (
		f"{pe.name} 1,200,000 -> {older_debt} 1,000,000 (older, due 5d) then "
		f"{newer_debt} 200,000 (newer, due 20d)"
	)


def check_cancel_payment_restores():
	"""T4: cancelling the Payment Entry reopens the debt it settled."""
	inv, batch, debt_name = _batched_invoice("T4", qty=10, rate=100_000)
	pe = _payment(inv.name, 500_000)
	if flt(_debt(debt_name).outstanding_amount) != 500_000:
		raise AssertionError("fixture broken: partial payment did not land")
	pe.cancel()
	frappe.db.commit()
	allocs = _allocations(pe.name)
	if any(row.docstatus != 2 for row in allocs):
		raise AssertionError(f"allocations must be cancelled with the payment: {allocs}")
	debt = _debt(debt_name)
	if flt(debt.paid_amount) != 0 or flt(debt.outstanding_amount) != 1_000_000:
		raise AssertionError(f"debt must be restored after PE cancel: {debt}")
	if debt.status != "Chưa trả":
		raise AssertionError(f"status should be back to 'Chưa trả', got {debt.status!r}")
	if _batch_total(batch) != 1_000_000:
		raise AssertionError(f"batch total_debt should be restored, got {_batch_total(batch):,.0f}")
	# Cancel must be reversible in the other direction too: paying again works.
	pe2 = _payment(inv.name, 1_000_000)
	if _debt(debt_name).status != "Đã trả":
		raise AssertionError("re-paying after a cancel should settle the debt again")
	return f"{pe.name} cancelled -> {debt_name} back to 1,000,000; re-paid by {pe2.name}"


def check_overdue_and_fee_from_settings():
	"""T5: overdue days/status, and the late fee follows Feed Dealer Settings."""
	_inv, _batch_name, debt_name = _batched_invoice("T5", qty=10, rate=100_000, due_days=-10)
	debt = _debt(debt_name)
	if debt.overdue_days != 10:
		raise AssertionError(f"overdue_days should be 10, got {debt.overdue_days}")
	if debt.status != "Quá hạn":
		raise AssertionError(f"status should be 'Quá hạn', got {debt.status!r}")

	settings = frappe.get_doc("Feed Dealer Settings")
	original = flt(settings.late_payment_interest_rate)
	probe = 0.001
	try:
		frappe.db.		set_single_value("Feed Dealer Settings", "late_payment_interest_rate", probe)
		# 10 x 100,000 = 1,000,000, of which 400,000 is paid here, so the fee must
		# be charged on the 600,000 still owed -- i.e. outstanding is computed
		# before overdue before status, not from the original amount.
		inv_name = frappe.db.get_value("Batch Debt", debt_name, "sales_invoice")
		pe = _payment(inv_name, 400_000)
		debt = _debt(debt_name)
		expected = 600_000 * probe * 10
		if flt(debt.late_payment_fee) != flt(expected):
			raise AssertionError(
				f"fee must use the Settings rate: expected {expected:,.0f}, "
				f"got {flt(debt.late_payment_fee):,.0f}"
			)
		if debt.status != "Quá hạn" or debt.overdue_days != 10:
			raise AssertionError(f"a partly paid overdue debt stays 'Quá hạn': {debt}")
	finally:
		frappe.db.set_single_value(
			"Feed Dealer Settings", "late_payment_interest_rate", original or 0.00022
		)
	# And with the site's own rate restored the fee is the smaller one again.
	_recalculate(debt_name)
	restored = flt(_debt(debt_name).late_payment_fee)
	natural = 600_000 * (original or 0.00022) * 10
	if flt(restored) != flt(natural):
		raise AssertionError(f"fee after restoring the rate: expected {natural:,.0f}, got {restored:,.0f}")
	return (
		f"{debt_name}: 10 days overdue, fee {flt(restored):,.0f} at the site rate "
		f"and {flt(600_000 * probe * 10):,.0f} at the probe rate {probe} ({pe.name})"
	)


def check_refund_does_not_allocate():
	"""T7: money going back to the customer must never settle a batch debt.

	`payment_type = "Pay"` with `party_type = "Customer"` is a refund. ERPNext
	filters the party-type dropdown per payment type in the Desk UI only, so an
	integration/import (or the P2 mobile API) can post one; measured on this site,
	it submits and used to allocate its amount against the customer's oldest open
debt. The hook now refuses anything that is not a receipt.
	"""
	inv, _batch_name, debt_name = _batched_invoice("T7", qty=5, rate=200_000)
	pe = frappe.get_doc(get_payment_entry("Sales Invoice", inv.name))
	pe.payment_type = "Pay"
	pe.paid_from, pe.paid_to = pe.paid_to, pe.paid_from
	pe.reference_no = f"{PREFIX} refund"
	pe.reference_date = pe.posting_date or nowdate()
	pe.insert(ignore_permissions=True)
	pe.submit()
	frappe.db.commit()

	allocs = _allocations(pe.name)
	if allocs:
		raise AssertionError(f"a refund must not create allocations, got {allocs}")
	debt = _debt(debt_name)
	if flt(debt.paid_amount) != 0 or flt(debt.outstanding_amount) != 1_000_000:
		raise AssertionError(f"a refund must leave the debt untouched: {debt}")
	return f"{pe.name} (Pay/Customer refund) -> 0 allocations, {debt_name} still owes 1,000,000"


def check_no_double_allocation():
	"""T6: a re-fired hook allocates nothing again, and SoT holds."""
	inv, _batch_name, debt_name = _batched_invoice("T6", qty=10, rate=100_000)
	pe = _payment(inv.name, 1_000_000)
	before = _allocations(pe.name)
	if len(before) != 1:
		raise AssertionError(f"expected 1 allocation after submit, got {before}")

	# Re-fire the hook exactly as a retry would (frappe runs doc_events again if
	# the submit is replayed). Nothing may be handed out twice.
	reload_pe = frappe.get_doc("Payment Entry", pe.name)
	result = on_submit(reload_pe, None)
	after = _allocations(pe.name)
	if len(after) != len(before):
		raise AssertionError(f"re-fired hook duplicated allocations: {before} -> {after}")
	if flt(_debt(debt_name).paid_amount) != 1_000_000:
		raise AssertionError(f"debt paid_amount drifted: {_debt(debt_name)}")

	# SoT invariant: the batch view can never claim more money than the payment
	# actually brought in.
	allocated = sum(flt(row.paid_amount) for row in after if row.docstatus == 1)
	if allocated > flt(reload_pe.paid_amount):
		raise AssertionError(
			f"allocated {allocated:,.0f} exceeds paid_amount {flt(reload_pe.paid_amount):,.0f}"
		)
	return f"{pe.name} re-fired -> still {len(after)} allocation, allocated {allocated:,.0f} <= paid 1,000,000 ({result})"


def check_unreconcile_payment_reverses_allocations():
	"""T8: ERPNext's Unreconcile Payment must reopen the debt it had settled.

	`Unreconcile Payment` does not cancel the Payment Entry (verified in the
	installed source), so P1B's `on_cancel` never fires in that flow. Without
	feed_dealer.events.unreconcile_payment the debt would keep saying "Đã trả"
	while AR considers the invoice outstanding again.
	"""
	inv, batch, debt_name = _batched_invoice("T8", qty=10, rate=100_000)
	pe = _payment(inv.name, 1_000_000)
	if _debt(debt_name).status != "Đã trả":
		raise AssertionError("fixture broken: the payment did not settle the debt")

	unreconcile = frappe.get_doc(
		{
			"doctype": "Unreconcile Payment",
			"company": _company(),
			"voucher_type": "Payment Entry",
			"voucher_no": pe.name,
		}
	)
	unreconcile.add_references()  # ERPNext's own helper fills `allocations`
	if not unreconcile.allocations:
		raise AssertionError("fixture broken: ERPNext found nothing to unreconcile")
	unreconcile.insert(ignore_permissions=True)
	unreconcile.submit()
	frappe.db.commit()

	allocs = _allocations(pe.name)
	if any(row.docstatus != 2 for row in allocs):
		raise AssertionError(f"our allocations must be cancelled: {allocs}")
	debt = _debt(debt_name)
	if flt(debt.paid_amount) != 0 or flt(debt.outstanding_amount) != 1_000_000:
		raise AssertionError(f"the debt must be open again: {debt}")
	if debt.status != "Chưa trả":
		raise AssertionError(f"status should be back to 'Chưa trả', got {debt.status!r}")
	if _batch_total(batch) != 1_000_000:
		raise AssertionError(f"batch total should be restored, got {_batch_total(batch):,.0f}")
	if frappe.db.get_value("Payment Entry", pe.name, "docstatus") != 1:
		raise AssertionError("Unreconcile Payment must NOT have cancelled the payment entry")
	return (
		f"{unreconcile.name}: {pe.name} delinked from {inv.name} -> {debt_name} back to 1,000,000 "
		f"('Chưa trả'), payment still submitted"
	)


def check_allocation_locks_debt_rows():
	"""T9: the FIFO read locks the debt rows it is about to allocate against.

	Two payments submitted at the same moment would otherwise both read the same
	`outstanding_amount` and hand the same money out twice.

	Two proofs, because either alone can pass for the wrong reason (a mutation run
	showed that a test which only calls `_open_debts(for_update=True)` itself stays
	green even when the real submit path stops asking for the lock):

	1. the PRODUCTION path asks for it - a spy on `_open_debts` watches a real
	   Payment Entry submit;
	2. the lock is real - with it held here, a second DB connection's
	   `SELECT ... FOR UPDATE` on the same rows fails after one second (the timeout
	   is set per session, so the check stays fast).
	"""
	from feed_dealer.events import payment_entry as payment_entry_module

	customer = _customer("T9")
	inv, _batch, _debt = _batched_invoice("T9", qty=10, rate=100_000, customer_tag="T9")

	seen = {}
	original = payment_entry_module._open_debts

	def spy(customer, for_update=False):
		seen["for_update"] = for_update
		return original(customer, for_update=for_update)

	payment_entry_module._open_debts = spy
	try:
		_payment(inv.name, 1_000_000)
	finally:
		payment_entry_module._open_debts = original
	if not seen.get("for_update"):
		raise AssertionError(
			f"the real submit path did not request the row lock (for_update={seen.get('for_update')!r})"
		)

	# A second, still-open debt of the same customer to hold the lock on.
	_fresh_inv, _fresh_batch, debt_name = _batched_invoice(
		"T9b", qty=10, rate=100_000, customer_tag="T9"
	)
	conn = _second_connection()
	if conn is None:
		raise AssertionError("could not open a second DB connection, lock unproven")
	blocked, error = False, ""
	try:
		cur = conn.cursor()
		cur.execute("SET SESSION innodb_lock_wait_timeout=1")
		locked = _open_debts(customer, for_update=True)
		if not locked:
			raise AssertionError("fixture broken: no open debt to lock")
		try:
			cur.execute(
				"SELECT name FROM `tabBatch Debt` WHERE customer=%s AND docstatus=1 "
				"AND outstanding_amount>0 FOR UPDATE",
				(customer,),
			)
		except Exception as exc:  # noqa: BLE001 - the timeout IS the evidence
			blocked, error = True, str(exc)[:90]
	finally:
		try:
			conn.rollback()
			conn.close()
		finally:
			frappe.db.commit()  # release the lock for the rest of the suite
	if not blocked:
		raise AssertionError(
			f"the second session locked the rows too - the FIFO read is NOT locking. "
			f"Held rows: {[row['name'] for row in locked]}"
		)
	return (
		f"on_submit asked for the lock (for_update={seen['for_update']}); {debt_name} then locked by "
		f"_open_debts(for_update=True) and a second session's FOR UPDATE failed: {error}"
	)


def _second_connection():
	"""A raw DB connection to the same site DB, to observe row locks from outside."""
	try:
		import pymysql
	except ImportError:  # pragma: no cover - frappe ships pymysql for MariaDB
		print("[feed_dealer] pymysql is not importable")
		return None
	conf = frappe.conf
	params = {
		"host": conf.get("db_host") or "127.0.0.1",
		"port": int(conf.get("db_port") or 3306),
		"user": conf.get("db_user") or conf.get("root_login") or "root",
		"password": conf.get("db_password") or conf.get("root_password") or "",
		"database": conf.db_name,
		"connect_timeout": 5,
	}
	try:
		return pymysql.connect(**params)
	except Exception as exc:  # noqa: BLE001
		print(f"[feed_dealer] second connection failed: {type(exc).__name__}: {exc}")
		print(f"[feed_dealer] conf keys available: db_host/db_port/db_name/db_user? {sorted(k for k in frappe.conf if k.startswith('db') or k.startswith('root'))}")
		return None


CHECKS = (
	("T1  partial payment", check_partial_payment),
	("T2  full payment", check_full_payment),
	("T3  FIFO order", check_fifo_order),
	("T4  cancel payment restores", check_cancel_payment_restores),
	("T5  overdue + fee from Settings", check_overdue_and_fee_from_settings),
	("T6  no double allocation", check_no_double_allocation),
	("T7  refund does not allocate", check_refund_does_not_allocate),
	("T8  unreconcile reverses", check_unreconcile_payment_reverses_allocations),
	("T9  FIFO read locks rows", check_allocation_locks_debt_rows),
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
		frappe.throw(f"P1B acceptance FAILED: {failed}", title="P1B ACCEPTANCE FAILED")
	print("P1B ACCEPTANCE: ALL PASS")
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
	"""Remove P1B fixtures: payments (which reverse their allocations), then the
	allocation layer, invoices, batches, items and the dedicated customer.

	Payments are cancelled BEFORE invoices: cancelling an invoice whose debts
	hold payments is refused (P1A's guard), and the debts only release their
	payments once the Payment Entry is cancelled.
	"""
	removed = []
	customers = _customers()
	# Unreconcile Payment (T8) references the payment; drop it before the payment.
	for row in frappe.get_all(
		"Unreconcile Payment",
		filters={"voucher_no": ["in", frappe.get_all(
			"Payment Entry", filters={"party": ["in", customers or [""]]}, pluck="name"
		) or [""]]},
		fields=["name", "docstatus"],
	):
		try:
			doc = frappe.get_doc("Unreconcile Payment", row.name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Unreconcile Payment", row.name, force=True, ignore_permissions=True)
			removed.append(f"Unreconcile Payment {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Unreconcile Payment {row.name} FAILED: {exc}")

	for row in frappe.get_all(
		"Payment Entry", filters={"party": ["in", customers]}, fields=["name", "docstatus"]
	):
		try:
			if row.docstatus == 1:
				frappe.get_doc("Payment Entry", row.name).cancel()
			frappe.delete_doc("Payment Entry", row.name, force=True, ignore_permissions=True)
			removed.append(f"Payment Entry {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Payment Entry {row.name} FAILED: {exc}")

	debts = frappe.get_all(
		"Batch Debt", filters={"customer": ["in", customers]}, fields=["name", "docstatus"]
	)
	debt_names = [row.name for row in debts]
	for row in frappe.get_all(
		"Payment Allocation",
		filters={"batch_debt": ["in", debt_names]},
		fields=["name", "docstatus"],
	):
		try:
			if row.docstatus == 1:
				frappe.get_doc("Payment Allocation", row.name).cancel()
			frappe.delete_doc("Payment Allocation", row.name, force=True, ignore_permissions=True)
			removed.append(f"Payment Allocation {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Payment Allocation {row.name} FAILED: {exc}")

	for row in debts:
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

	for name in frappe.get_all(
		"Feed Batch", filters={"notes": ["like", f"{PREFIX}%"]}, pluck="name"
	):
		frappe.delete_doc("Feed Batch", name, force=True, ignore_permissions=True)
		removed.append(f"Feed Batch {name}")
	for name in customers:
		frappe.delete_doc("Customer", name, force=True, ignore_permissions=True)
		removed.append(f"Customer {name}")
	frappe.db.commit()
	print(f"[feed_dealer] P1B cleanup removed {len(removed)} fixture(s):")
	for item in removed:
		print(f"  - {item}")
	return removed


# `_item`/`_company` stay in the P1A suite: both suites invoice the same fixture
# item, and `_invoice`/`_debts` already carry those defaults.
