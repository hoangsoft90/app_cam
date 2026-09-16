"""P1C acceptance: Sales Order credit limit (draft + submitted + atomic re-check).

Run:      bench --site <site> execute feed_dealer.setup.p1c_acceptance.run
Debug:    bench --site <site> execute feed_dealer.setup.p1c_acceptance.debug
Cleanup:  bench --site <site> execute feed_dealer.setup.p1c_acceptance.cleanup

Every check drives REAL Sales Orders / Sales Invoices / Payment Entries, because
the rule under test is a hard money limit and a mocked one would prove nothing:

  T1  draft orders hold credit      -> a third draft past the limit is refused
                                       (this is the "N drafts" bypass of v2.0)
  T2  an invoice appears while a draft waits -> the draft's submit is refused,
                                       even though NOTHING was invoiced when the
                                       draft was created
  T3  the submit re-check is atomic -> an order that entered the DB through a
                                       path which skipped the draft gate (import /
                                       offline sync / race) is still refused at
                                       submit, and it stays a draft (the write
                                       really was blocked, not undone afterwards)
  T4  no Credit Score = no credit   -> blocked, and the message says what to do
  T5  paying a debt frees credit    -> the order that was refused now passes
  T6  the override is manager-only and audited (reason, by, at)
  T7  score -> tier boundaries are deterministic
  T8  the limit comes from history (avg order value x tier %), not a constant
  T9  one shared rule: the whitelisted API and the hook reach the same verdict,
      including for a non-privileged role

Two frappe details the suite had to respect (each found by a failing check, not
by guessing): `flags.ignore_validate` cannot be used to sneak a document into the
database - it short-circuits `run_before_save_methods` (so ERPNext's own
`validate` never fills price list/UOM fields and `_validate_mandatory` then
refuses the insert), and it would also suppress `before_submit` on that same
document. T3 therefore reaches the same state the honest way: the limit changes
while the drafts wait.
"""

import json
import traceback

import frappe
from frappe.utils import add_days, flt, nowdate

from feed_dealer.credit_limit import (
	check_order_credit,
	credit_position,
	get_credit_position,
	outstanding_debt,
	validate_credit_limit,
)
from feed_dealer.setup.p1a_acceptance import _company, _debts, _invoice, _item
from feed_dealer.setup.p1b_acceptance import _payment

PREFIX = "P1C-ACCEPT"
MANAGER_EMAIL = "p1c-acceptance-manager@example.com"
STAFF_EMAIL = "p1c-acceptance-staff@example.com"
FARMER_EMAIL = "p1c-acceptance-farmer@example.com"
FARMER_ROLE = "Feed Farmer"
MANAGER_ROLE = "Feed Dealer Manager"
STAFF_ROLE = "Feed Dealer Staff"


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
		lines.append(f"TOTAL: {len(self.rows)}   PASS: {len(self.rows) - len(failed)}   FAIL: {len(failed)}")
		return "\n".join(lines), failed


# ------------------------------------------------------------------- fixtures
def _customers():
	return frappe.get_all("Customer", filters={"customer_name": ["like", f"{PREFIX}%"]}, pluck="name")


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


def _credit_score(customer):
	"""The customer's Credit Score document (creating it in memory if absent)."""
	name = frappe.db.get_value("Credit Score", {"customer": customer}, "name")
	doc = frappe.get_doc("Credit Score", name) if name else frappe.new_doc("Credit Score")
	if not name:
		doc.customer = customer
	return doc


def _set_limit(customer, limit, reason=f"{PREFIX} fixture limit"):
	"""Give a customer an approved limit the way the Desk does: a manager override.

	`manual_limit` through `manual_override` is the only way to hand a test
	customer a fixed limit, because a customer with no invoice history has
	limit_by_score = 0 by design (see the Credit Score module docstring).
	"""
	doc = _credit_score(customer)
	doc.manual_override = 1
	doc.manual_limit = flt(limit)
	doc.override_reason = reason
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return doc


def _seed_history(customer, tag):
	"""Two submitted-and-paid 10,000,000 invoices: a real repayment history."""
	for index in (1, 2):
		batch = frappe.db.get_value(
			"Feed Batch", {"customer": customer, "notes": f"{PREFIX} {tag}-{index}"}, "name"
		)
		if not batch:
			batch = (
				frappe.get_doc(
					{
						"doctype": "Feed Batch",
						"customer": customer,
						"animal_type": "Lợn",
						"start_date": nowdate(),
						"notes": f"{PREFIX} {tag}-{index}",
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		invoice = _invoice(
			[{"qty": 1, "rate": 10_000_000, "batch": batch}],
			customer=customer,
			due_days=15,
		)
		debts = _debts(invoice.name)
		if len(debts) != 1:
			raise AssertionError(f"fixture broken: expected 1 debt for {invoice.name}, got {debts}")
		_payment(invoice.name, 10_000_000)
	frappe.db.commit()


def _new_invoice(customer, tag, amount, due_days=15):
	"""One submitted invoice -> one Batch Debt of `amount` (what the credit consumes)."""
	batch = frappe.db.get_value(
		"Feed Batch", {"customer": customer, "notes": f"{PREFIX} {tag}"}, "name"
	)
	if not batch:
		batch = (
			frappe.get_doc(
				{
					"doctype": "Feed Batch",
					"customer": customer,
					"animal_type": "Lợn",
					"start_date": nowdate(),
					"notes": f"{PREFIX} {tag}",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	invoice = _invoice([{"qty": 1, "rate": amount, "batch": batch}], customer=customer, due_days=due_days)
	debts = _debts(invoice.name)
	if len(debts) != 1:
		raise AssertionError(f"fixture broken: expected 1 debt for {invoice.name}, got {debts}")
	frappe.db.commit()
	return invoice


def _order(customer, amount, submit=False):
	"""A real Sales Order for `amount` (1 line, qty 1, tax-free feed item)."""
	doc = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"company": _company(),
			"customer": customer,
			"currency": "VND",
			"transaction_date": nowdate(),
			"delivery_date": add_days(nowdate(), 7),
			"items": [
				{
					"item_code": _item(),
					"qty": 1,
					"rate": amount,
					"delivery_date": add_days(nowdate(), 7),
				}
			],
		}
	)
	doc.insert(ignore_permissions=True)
	if flt(doc.grand_total) != flt(amount):
		raise AssertionError(f"fixture broken: grand_total {doc.grand_total} != requested {amount}")
	if submit:
		doc.submit()
	frappe.db.commit()
	return doc


def _reject(fn, *, expect=("hạn mức",)):
	"""Run `fn`, require a rejection whose message mentions one of `expect`.

	`expect=None` accepts any rejection (used where the refusal may legitimately
	come from the permission layer instead of the credit gate).
	"""
	try:
		fn()
	except Exception as exc:  # noqa: BLE001 - the message is the evidence
		message = str(exc)
		if expect is not None and not any(token in message for token in expect):
			raise AssertionError(
				f"rejected, but not by the credit gate (expected {expect}): {message[:200]}"
			) from exc
		return message
	raise AssertionError("expected the credit gate to reject this, but it succeeded")


def _user(email, role):
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": f"{PREFIX} {role}",
				"user_type": "System User",
				"send_welcome_email": 0,
				"roles": [{"role": role}],
			}
		)
		user.insert(ignore_permissions=True)
		frappe.db.commit()
	return email


# ---------------------------------------------------------------------- tests
def check_draft_orders_hold_credit():
	"""T1: drafts consume the limit, so the third draft is refused."""
	customer = _customer("T1")
	_set_limit(customer, 50_000_000)
	first = _order(customer, 20_000_000)
	second = _order(customer, 20_000_000)
	message = _reject(lambda: _order(customer, 20_000_000))
	if "Đơn nháp" not in message:
		raise AssertionError(f"the message should show the draft term: {message[:200]}")
	if frappe.db.count("Sales Order", {"customer": customer}) != 2:
		raise AssertionError("a refused draft must not be stored")
	return (
		f"{first.name} 20,000,000 + {second.name} 20,000,000 held; third 20,000,000 vs "
		f"50,000,000 limit refused: {message.split('<br>')[-1]}"
	)


def check_invoice_between_create_and_submit():
	"""T2: credit consumed after the draft was created is still honoured at submit."""
	customer = _customer("T2")
	_set_limit(customer, 50_000_000)
	order = _order(customer, 30_000_000)
	if flt(outstanding_debt(customer)) != 0:
		raise AssertionError("fixture broken: this customer should start with no debt")
	# The dealer sells directly on invoice (P1A flow) while the draft waits.
	_new_invoice(customer, "T2-direct", 30_000_000)
	if flt(outstanding_debt(customer)) != 30_000_000:
		raise AssertionError(f"fixture broken: debt should be 30,000,000, got {outstanding_debt(customer)}")
	message = _reject(lambda: order.submit())
	position = get_credit_position(customer)
	if flt(position["outstanding_debt"]) != 30_000_000:
		raise AssertionError(f"the gate should see the new debt: {position}")
	return (
		f"draft {order.name} 30,000,000 was fine at creation; after a 30,000,000 invoice landed "
		f"its submit was refused (nợ 30,000,000 + đơn 30,000,000 > 50,000,000): "
		f"{message.split('<br>')[-1]}"
	)


def check_submit_recheck_is_atomic():
	"""T3: the submit path re-reads the limit and the committed total itself.

	Once drafts count towards the limit (T1), a submit can only be the FIRST gate
	when something changed after the draft was created - here a manager lowers the
	limit. That is exactly the "another request changed this customer's credit"
	situation the atomic re-check (row lock + re-read) exists for, and it also
	shows the submit path does NOT count drafts: the first order was still allowed.
	"""
	customer = _customer("T3")
	_set_limit(customer, 50_000_000)
	first = _order(customer, 20_000_000)
	second = _order(customer, 20_000_000)
	_set_limit(customer, 30_000_000)  # the limit drops while both drafts wait
	first.submit()  # 20,000,000 <= 30,000,000 -> allowed (drafts are not counted here)
	frappe.db.commit()
	message = _reject(lambda: second.submit())
	stored = frappe.db.get_value("Sales Order", second.name, "docstatus")
	if stored != 0:
		raise AssertionError(
			f"the refusal must happen BEFORE the write (before_submit), docstatus={stored}"
		)
	# The submit-time view of the same numbers (the whitelisted API is the UI view,
	# which counts drafts: it has no check_draft argument on purpose).
	position = credit_position(customer, check_draft=False)
	if flt(position["submitted_uninvoiced_orders"]) != 20_000_000:
		raise AssertionError(f"the submitted order should be the blocking term: {position}")
	return (
		f"limit lowered to 30,000,000 while both drafts waited: {first.name} 20,000,000 submitted, "
		f"{second.name} refused and stayed docstatus=0 ({message.split('<br>')[-1]}); "
		f"submitted-uninvoiced {flt(position['submitted_uninvoiced_orders']):,.0f}"
	)


def check_customer_without_credit_score_is_blocked():
	"""T4: no Credit Score document means no credit, and the message says so."""
	customer = _customer("T4")
	message = _reject(lambda: _order(customer, 1_000_000), expect=("Credit Score",))
	_set_limit(customer, 5_000_000)
	allowed = _order(customer, 1_000_000)
	return (
		f"before: {message[:110]}... | after the limit was granted: {allowed.name} 1,000,000 accepted"
	)


def check_payment_frees_credit():
	"""T5: settling the debt reopens the limit (acceptance #2)."""
	customer = _customer("T5")
	_set_limit(customer, 50_000_000)
	invoice = _new_invoice(customer, "T5", 40_000_000)
	first_message = _reject(lambda: _order(customer, 30_000_000))
	_payment(invoice.name, 40_000_000)
	frappe.db.commit()
	if flt(outstanding_debt(customer)) != 0:
		raise AssertionError(f"the payment should clear the debt, still {outstanding_debt(customer)}")
	order = _order(customer, 30_000_000)
	order.submit()
	frappe.db.commit()
	position = get_credit_position(customer)
	return (
		f"with the 40,000,000 debt: draft refused ({first_message.split('<br>')[-1]}); after paying "
		f"it: {order.name} 30,000,000 accepted (khả dụng {position['available']:,.0f})"
	)


def check_override_is_manager_only_and_audited():
	"""T6: manager-only override, with reason + stamp; calculated limit still 0."""
	customer = _customer("T6")
	doc = _credit_score(customer)
	doc.save(ignore_permissions=True)  # no history, no override
	stored = frappe.db.get_value(
		"Credit Score", customer, ["limit_by_score", "credit_limit", "score", "tier"], as_dict=True
	)
	if flt(stored.limit_by_score) != 0 or flt(stored.credit_limit) != 0:
		raise AssertionError(f"fixture broken: expected a calculated limit of 0, got {stored}")
	zero_message = _reject(lambda: _order(customer, 1_000_000))

	staff, manager = _user(STAFF_EMAIL, STAFF_ROLE), _user(MANAGER_EMAIL, MANAGER_ROLE)
	original_user = frappe.session.user
	try:
		# (a) the controller-level gate, tested directly: this role may not override.
		frappe.set_user(staff)
		probe = frappe.get_doc("Credit Score", customer)
		probe.manual_override, probe.manual_limit, probe.override_reason = 1, 5_000_000, "thử"
		gate_message = _reject(lambda: probe._validate_override(), expect=("Quản lý",))
		# (b) the end-to-end path: nothing may be persisted by this role (the
		# refusal may come from the DocType permissions before our gate runs).
		_reject(lambda: probe.save(), expect=None)
	finally:
		frappe.set_user(original_user)
	if frappe.db.get_value("Credit Score", customer, "manual_override"):
		raise AssertionError("a staff save persisted the override - the gate is not enforced")

	try:
		frappe.set_user(manager)
		manager_doc = frappe.get_doc("Credit Score", customer)
		# Missing reason and missing limit are refused even for a manager.
		manager_doc.manual_override, manager_doc.manual_limit = 1, 5_000_000
		no_reason = _reject(lambda: manager_doc.save(), expect=("lý do",))
		# A REJECTED save still bumps the instance's `modified` (frappe stamps it
		# before validate runs) while the database row keeps its old value, so the
		# next save on this same instance fails with TimestampMismatchError about a
		# row nobody touched. Reload - what the Desk would do.
		manager_doc.reload()
		manager_doc.manual_override, manager_doc.manual_limit = 1, 0
		manager_doc.override_reason = f"{PREFIX} thiếu hạn mức"
		no_limit = _reject(lambda: manager_doc.save(), expect=("Hạn mức ghi đè",))
		manager_doc.reload()
		manager_doc.manual_override, manager_doc.manual_limit = 1, 5_000_000
		manager_doc.override_reason = f"{PREFIX} đợt khuyến mãi"
		manager_doc.save()
	finally:
		frappe.set_user(original_user)

	audited = frappe.db.get_value(
		"Credit Score",
		customer,
		["credit_limit", "manual_limit", "override_by", "override_date", "override_reason"],
		as_dict=True,
	)
	if flt(audited.credit_limit) != 5_000_000 or flt(audited.manual_limit) != 5_000_000:
		raise AssertionError(f"the override must become the approved limit: {audited}")
	if audited.override_by != manager or not audited.override_date:
		raise AssertionError(f"override must be stamped with the manager and a time: {audited}")
	under = _order(customer, 4_000_000)
	over = _reject(lambda: _order(customer, 6_000_000))
	return (
		f"calculated 0 -> refused ({zero_message.split('<br>')[-1]}); staff: {gate_message[:40]}...; "
		f"manager missing-reason refused ('{no_reason.split('(')[-1][:20]}...'), missing-limit refused "
		f"('{no_limit[:30]}...'); override 5,000,000 by {audited.override_by} at "
		f"{audited.override_date}: {under.name} 4,000,000 ok, 6,000,000 refused "
		f"({over.split('<br>')[-1]})"
	)


def check_tier_boundaries_are_deterministic():
	"""T7: the frozen score -> tier table, at every boundary."""
	from feed_dealer.feed_dealer.doctype.credit_score.credit_score import tier_for_score

	expected = {0: "Đồng", 39: "Đồng", 40: "Bạc", 59: "Bạc", 60: "Vàng", 79: "Vàng", 80: "Kim Cương", 100: "Kim Cương"}
	wrong = {score: (tier_for_score(score), tier) for score, tier in expected.items() if tier_for_score(score) != tier}
	if wrong:
		raise AssertionError(f"tier mapping wrong (got, expected): {wrong}")
	return " ".join(f"{score}->{tier}" for score, tier in sorted(expected.items()))


def check_limit_comes_from_history():
	"""T8: avg order value x tier % (no override) drives the approved limit."""
	from feed_dealer.feed_dealer.doctype.credit_score.credit_score import recalculate

	customer = _customer("T8")
	_seed_history(customer, "T8")
	doc = recalculate(customer)
	frappe.db.commit()
	if doc.tier != "Vàng" or flt(doc.score) != 60:
		raise AssertionError(f"expected score 60 / Vàng after 2 on-time payments, got {doc.score}/{doc.tier}")
	if flt(doc.limit_by_score) != 8_000_000:
		raise AssertionError(
			f"avg order value 10,000,000 x 80% (Vàng) should be 8,000,000, got {doc.limit_by_score}"
		)
	if flt(doc.credit_limit) != 8_000_000:
		raise AssertionError(f"the calculated limit must equal limit_by_score: {doc.credit_limit}")
	allowed = _order(customer, 7_000_000)
	message = _reject(lambda: _order(customer, 9_000_000))
	return (
		f"score 60 / {doc.tier} / limit_by_score {flt(doc.limit_by_score):,.0f} (avg 10,000,000 x 80%): "
		f"{allowed.name} 7,000,000 ok; 9,000,000 refused ({message.split('<br>')[-1]})"
	)


def check_one_shared_rule_for_hook_and_api():
	"""T9: the hook and the whitelisted API reach the same verdict, for any role."""
	customer = _customer("T9")
	_set_limit(customer, 30_000_000)
	hook_message = _reject(lambda: _order(customer, 31_000_000))
	position = get_credit_position(customer)
	if flt(position["approved_limit"]) != 30_000_000 or flt(position["available"]) != 30_000_000:
		raise AssertionError(f"a fresh customer's position should be the full limit: {position}")

	staff = _user(STAFF_EMAIL, STAFF_ROLE)
	original_user = frappe.session.user
	try:
		frappe.set_user(staff)
		api_message = _reject(lambda: check_order_credit(customer, 31_000_000))
		staff_position = get_credit_position(customer)
	finally:
		frappe.set_user(original_user)

	if api_message != hook_message:
		raise AssertionError(
			f"hook and API disagree.\nhook: {hook_message}\napi:  {api_message}"
		)
	if flt(staff_position["approved_limit"]) != flt(position["approved_limit"]):
		raise AssertionError("the API must report the same limit to every role")
	# And the hook really is the same function object, not a copy of the rule.
	from feed_dealer.events import sales_order as order_hook

	if order_hook.validate_credit_limit is not validate_credit_limit:
		raise AssertionError("the Sales Order hook does not delegate to the shared gate")
	return (
		f"hook and API ({staff}) produced the identical rejection for 31,000,000 against a "
		f"30,000,000 limit; position available {flt(staff_position['available']):,.0f}"
	)


def check_api_refuses_foreign_customer():
	"""T10: a whitelisted API is open to any logged-in user, so it must not hand
	someone else's credit position to a role that may not read it (found in
	self-review: the first version returned every customer's limit to anybody)."""
	other = _customer("T10")
	_set_limit(other, 5_000_000)
	farmer = _user(FARMER_EMAIL, FARMER_ROLE)
	original_user = frappe.session.user
	try:
		frappe.set_user(farmer)
		message = _reject(lambda: get_credit_position(other), expect=("quyền",))
		api_message = _reject(lambda: check_order_credit(other, 1_000_000), expect=("quyền",))
	finally:
		frappe.set_user(original_user)
	# ...and the same call works for a role that MAY read it.
	allowed = get_credit_position(other)
	if flt(allowed["approved_limit"]) != 5_000_000:
		raise AssertionError(f"a role with read access must get the position: {allowed}")
	return f"{farmer}: {message[:70]}... | {api_message[:40]}... | manager read ok ({allowed['available']:,.0f})"


CHECKS = (
	("T1  draft orders hold credit", check_draft_orders_hold_credit),
	("T2  invoice before submit", check_invoice_between_create_and_submit),
	("T3  atomic submit re-check", check_submit_recheck_is_atomic),
	("T4  no Credit Score = blocked", check_customer_without_credit_score_is_blocked),
	("T5  payment frees credit", check_payment_frees_credit),
	("T6  override manager-only + audit", check_override_is_manager_only_and_audited),
	("T7  tier boundaries", check_tier_boundaries_are_deterministic),
	("T8  limit from history", check_limit_comes_from_history),
	("T9  one rule for hook + API", check_one_shared_rule_for_hook_and_api),
	("T10 API refuses foreign customer", check_api_refuses_foreign_customer),
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
		frappe.throw(f"P1C acceptance FAILED: {failed}", title="P1C ACCEPTANCE FAILED")
	print("P1C ACCEPTANCE: ALL PASS")
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
	"""Remove P1C fixtures: orders, then payments (they reverse allocations), then
	allocations, debts, invoices, batches, Credit Score documents and customers.

	Order matters: a submitted Sales Order must be cancelled before deletion, a
	payment must be cancelled before the invoice it settled (P1A's guard refuses
	otherwise), and the Credit Score / Customer rows last (both are linked).
	"""
	removed = []
	customers = _customers()

	for row in frappe.get_all(
		"Sales Order", filters={"customer": ["in", customers or [""]]}, fields=["name", "docstatus"]
	):
		try:
			doc = frappe.get_doc("Sales Order", row.name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Sales Order", row.name, force=True, ignore_permissions=True)
			removed.append(f"Sales Order {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Sales Order {row.name} FAILED: {exc}")

	for row in frappe.get_all(
		"Payment Entry", filters={"party": ["in", customers or [""]]}, fields=["name", "docstatus"]
	):
		try:
			doc = frappe.get_doc("Payment Entry", row.name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Payment Entry", row.name, force=True, ignore_permissions=True)
			removed.append(f"Payment Entry {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Payment Entry {row.name} FAILED: {exc}")

	debts = frappe.get_all(
		"Batch Debt", filters={"customer": ["in", customers or [""]]}, fields=["name", "docstatus"]
	)
	debt_names = [row.name for row in debts]
	for row in frappe.get_all(
		"Payment Allocation",
		filters={"batch_debt": ["in", debt_names or [""]]},
		fields=["name", "docstatus"],
	):
		try:
			doc = frappe.get_doc("Payment Allocation", row.name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Payment Allocation", row.name, force=True, ignore_permissions=True)
			removed.append(f"Payment Allocation {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Payment Allocation {row.name} FAILED: {exc}")

	for row in debts:
		try:
			doc = frappe.get_doc("Batch Debt", row.name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Batch Debt", row.name, force=True, ignore_permissions=True)
			removed.append(f"Batch Debt {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Batch Debt {row.name} FAILED: {exc}")

	for row in frappe.get_all(
		"Sales Invoice", filters={"customer": ["in", customers or [""]]}, fields=["name", "docstatus"]
	):
		try:
			doc = frappe.get_doc("Sales Invoice", row.name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Sales Invoice", row.name, force=True, ignore_permissions=True)
			removed.append(f"Sales Invoice {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Sales Invoice {row.name} FAILED: {exc}")

	for name in frappe.get_all("Feed Batch", filters={"notes": ["like", f"{PREFIX}%"]}, pluck="name"):
		frappe.delete_doc("Feed Batch", name, force=True, ignore_permissions=True)
		removed.append(f"Feed Batch {name}")

	for customer in customers:
		for name in frappe.get_all("Credit Score", filters={"customer": customer}, pluck="name"):
			frappe.delete_doc("Credit Score", name, force=True, ignore_permissions=True)
			removed.append(f"Credit Score {name}")
	for customer in customers:
		frappe.delete_doc("Customer", customer, force=True, ignore_permissions=True)
		removed.append(f"Customer {customer}")

	for email in (MANAGER_EMAIL, STAFF_EMAIL, FARMER_EMAIL):
		if frappe.db.exists("User", email):
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
			removed.append(f"User {email}")

	frappe.db.commit()
	print(f"[feed_dealer] P1C cleanup removed {len(removed)} fixture(s):")
	for item in removed:
		print(f"  - {item}")
	return removed
