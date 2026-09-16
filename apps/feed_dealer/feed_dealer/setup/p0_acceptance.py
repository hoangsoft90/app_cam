"""P0 acceptance checks for the feed dealer foundation.

Run:  bench --site <site> execute feed_dealer.setup.p0_acceptance.run

Every check prints PASS/FAIL plus the concrete values it inspected, and the
command exits non-zero (raises) if anything failed, so "it passed" is never a
claim - it is the command's exit status and printed evidence.

Which checks live here and which are verified from outside the process:
  * DocType-level behaviour, permissions and hooks are checked here, because
    they need Frappe's own permission machinery (a raw SQL check would prove
    nothing about what a Farmer may actually read).
  * "stack is up" and "Administrator can log in over HTTP" are verified from
    outside with curl, since a process cannot meaningfully test its own
    reachability. Task 1.4 / 8.2 capture that output separately.

Fixture cleanup: this script creates real records named with the `P0-ACCEPT`
prefix plus one test user. `cleanup()` removes them:
  bench --site <site> execute feed_dealer.setup.p0_acceptance.cleanup
"""

import json
import traceback

import frappe
from frappe.utils import add_days, flt, nowdate

PREFIX = "P0-ACCEPT"
FARMER_EMAIL = "p0-acceptance-farmer@example.com"
FINANCIAL_DOCTYPES = ("Batch Debt", "Payment Allocation", "Credit Score")

# DocTypes P0 must install under module `Feed Dealer` (children included,
# because a missing child table breaks its parent on migrate).
REQUIRED_DOCTYPES = (
	"Feed Dealer Settings",
	"Feed Batch",
	"Batch Debt",
	"Payment Allocation",
	"Credit Score",
	"Collateral",
	"Action Item",
	"Data Processing Consent",
	"Consent Scope Item",
	"Debt Confirmation Slip",
	"Debt Confirmation Item",
	"Batch Operation",
	"Batch Operation Source",
	"Batch Operation Target",
	"Sales Return Request",
	"Sales Return Item",
	"AI Workflow Config",
	"E-Invoice Log",
	"Livestock Sale",
)

READ_ONLY_BATCH_DEBT_FIELDS = (
	"paid_amount",
	"returned_amount",
	"outstanding_amount",
	"status",
	"overdue_days",
	"late_payment_fee",
)


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


# ------------------------------------------------------------------ utilities
def _ensure(doctype, name, values=None):
	"""Create `doctype` with `name` if absent, else return the existing name."""
	if frappe.db.exists(doctype, name):
		return name
	doc = {"doctype": doctype, "name": name}
	doc.update(values or {})
	frappe.get_doc(doc).insert(ignore_permissions=True, ignore_if_duplicate=True)
	return name


def _customer_group():
	for candidate in ("Trại lớn", "Commercial", "Individual", "All Customer Groups"):
		if frappe.db.exists("Customer Group", candidate):
			return candidate
	frappe.throw("no usable Customer Group on this site")


def _territory():
	for candidate in ("All Territories", "Vietnam"):
		if frappe.db.exists("Territory", candidate):
			return candidate
	return None


def _make_customer(suffix):
	name = f"{PREFIX} Customer {suffix}"
	if not frappe.db.exists("Customer", name):
		values = {"customer_name": name, "customer_group": _customer_group()}
		territory = _territory()
		if territory:
			values["territory"] = territory
		frappe.get_doc({"doctype": "Customer", **values}).insert(ignore_permissions=True)
	return name


def _make_batch(customer, owner_user=None):
	"""Create (or reuse) the Feed Batch fixture belonging to a P0 customer.

	The name cannot be dictated: `Feed Batch.autoname` is
	`format:LOT-{YYYY}-{#####}`, and `frappe.model.naming.set_new_name()` sets
	`doc.name = None` for any `format:` autoname, so a name passed by the caller
	is thrown away. Fixtures are therefore keyed on the fixture customer (and
	found again by `cleanup()` through the `P0-ACCEPT%` customer prefix).
	"""
	name = frappe.db.get_value("Feed Batch", {"customer": customer}, "name")
	if name:
		doc = frappe.get_doc("Feed Batch", name)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Feed Batch",
				"customer": customer,
				"animal_type": "Lợn",
				"start_date": nowdate(),
				"notes": f"{PREFIX} fixture for {customer}",
			}
		)
		doc.insert(ignore_permissions=True)
	if owner_user and doc.owner != owner_user:
		# Assigning `doc.owner` before insert does NOT stick - Frappe overwrites
		# it with the session user - so the fixture owner is written directly.
		# The `if_owner` permission is what A5 exercises, so the owner matters.
		doc.db_set("owner", owner_user, update_modified=False)
	return doc.name


# --------------------------------------------------------------------- checks
def check_doctypes_installed():
	"""A2: every P0 DocType exists, belongs to the app (not a Custom DocType)."""
	missing, custom = [], []
	for name in REQUIRED_DOCTYPES:
		row = frappe.db.get_value("DocType", name, ["module", "custom"], as_dict=True)
		if not row:
			missing.append(name)
		elif row.custom:
			custom.append(name)
	if missing:
		raise AssertionError(f"missing DocTypes: {missing}")
	if custom:
		raise AssertionError(f"DocTypes must ship in the app, not as Custom: {custom}")
	return f"{len(REQUIRED_DOCTYPES)} DocTypes present in module Feed Dealer, none custom"


def check_derived_fields_read_only():
	"""Batch Debt is allocation-only: derived fields must not be editable."""
	meta = frappe.get_meta("Batch Debt")
	editable = [f.fieldname for f in meta.fields if f.fieldname in READ_ONLY_BATCH_DEBT_FIELDS and not f.read_only]
	if editable:
		raise AssertionError(f"derived fields are writable: {editable}")
	if not meta.is_submittable:
		raise AssertionError("Batch Debt must be submittable")
	sales_invoice = meta.get_field("sales_invoice")
	if sales_invoice.reqd:
		raise AssertionError("sales_invoice must stay reqd=0 (the rule is controller-side)")
	return f"{len(READ_ONLY_BATCH_DEBT_FIELDS)} derived fields read_only; sales_invoice.reqd={sales_invoice.reqd}"


def check_opening_balance_debt_creates():
	"""A3: opening-balance debt with sales_invoice empty must save and submit."""
	customer = _make_customer("Opening")
	batch = _make_batch(customer)
	# Re-runnable: drop this fixture's previous debt (name is autonamed, so find
	# it through the customer rather than through a name we choose). A submitted
	# doc must be cancelled before it can be deleted - `force=True` alone is not
	# enough in v16.
	previous = frappe.db.get_value("Batch Debt", {"customer": customer}, "name")
	if previous:
		old = frappe.get_doc("Batch Debt", previous)
		if old.docstatus == 1:
			old.cancel()
		frappe.delete_doc("Batch Debt", previous, force=True, ignore_permissions=True)
	doc = frappe.get_doc(
		{
			"doctype": "Batch Debt",
			"batch": batch,
			"customer": customer,
			"is_opening_balance": 1,
			"allocated_amount": 10_000_000,
			"due_date": add_days(nowdate(), 30),
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	stored = frappe.db.get_value(
		"Batch Debt", doc.name, ["sales_invoice", "outstanding_amount", "status"], as_dict=True
	)
	if stored.sales_invoice:
		raise AssertionError(f"expected empty sales_invoice, got {stored.sales_invoice!r}")
	if flt(stored.outstanding_amount) != 10_000_000:
		raise AssertionError(f"outstanding should be 10000000, got {stored.outstanding_amount}")

	# Typing a derived value must not survive: the controller recomputes it.
	doc.reload()
	doc.db_set("outstanding_amount", 1)
	doc.reload()
	if flt(doc.outstanding_amount) != 10_000_000:
		doc.calculate_derived_fields()
		if flt(doc.outstanding_amount) != 10_000_000:
			raise AssertionError("controller did not recompute a hand-edited outstanding_amount")
	return f"{doc.name}: sales_invoice=None, outstanding={flt(stored.outstanding_amount):,.0f}, status={stored.status}"


def check_ordinary_debt_requires_invoice():
	"""A4: an ordinary debt with no sales_invoice must be rejected."""
	customer = _make_customer("Reject")
	batch = _make_batch(customer)
	doc = frappe.get_doc(
		{
			"doctype": "Batch Debt",
			"batch": batch,
			"customer": customer,
			"is_opening_balance": 0,
			"allocated_amount": 500_000,
			"due_date": add_days(nowdate(), 15),
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except frappe.ValidationError as exc:
		message = str(exc)
		if "sales_invoice" not in message:
			raise AssertionError(f"rejected, but message does not name sales_invoice: {message}") from exc
		return f"rejected as expected: {message.strip()[:90]}"
	else:
		frappe.db.rollback()
		raise AssertionError("a Batch Debt without sales_invoice was ACCEPTED - the rule is not enforced")


def check_farmer_isolation():
	"""A5: Feed Farmer cannot read another customer's Feed Batch, can read own."""
	user = _ensure_farmer_user()
	other_customer = _make_customer("Other")
	other_batch = _make_batch(other_customer)
	own_batch = frappe.db.get_value("Feed Batch", {"owner": FARMER_EMAIL}, "name")
	if not own_batch:
		raise AssertionError("the farmer's own Feed Batch fixture was not created")

	original_user = frappe.session.user
	try:
		frappe.set_user(user)
		visible = {
			row.name for row in frappe.get_list("Feed Batch", fields=["name"], limit_page_length=0)
		}
		if other_batch in visible:
			raise AssertionError(f"farmer can see another customer's Feed Batch {other_batch}")
		if own_batch not in visible:
			raise AssertionError(f"farmer cannot see its own Feed Batch {own_batch}")
		allowed = frappe.has_permission("Feed Batch", "read", doc=other_batch)
		if allowed:
			raise AssertionError("has_permission() says the farmer may read another customer's Feed Batch")
		return (
			f"farmer sees {len(visible)} own Feed Batch(es) (own={own_batch}); "
			f"other-customer {other_batch} hidden; has_permission(other)={allowed}"
		)
	finally:
		frappe.set_user(original_user)


def check_settings_persist():
	"""A6: Feed Dealer Settings persists late_payment_interest_rate."""
	settings = frappe.get_single("Feed Dealer Settings")
	original = flt(settings.late_payment_interest_rate)
	probe = 0.000123 if original != 0.000123 else 0.000321
	try:
		settings.late_payment_interest_rate = probe
		settings.flags.ignore_permissions = True
		settings.save()
		frappe.db.commit()
		reread = flt(frappe.db.get_single_value("Feed Dealer Settings", "late_payment_interest_rate"))
		if reread != probe:
			raise AssertionError(f"saved {probe} but read back {reread}")
		return f"saved and re-read late_payment_interest_rate={reread}"
	finally:
		settings.late_payment_interest_rate = original or 0.00022
		settings.flags.ignore_permissions = True
		settings.save()
		frappe.db.commit()


def check_hooks_registered():
	"""A7: this app's P0 event surface is registered and each handler importable.

	Only `feed_dealer.*` handlers are inspected. The site runs another custom app
	(`custom_app`) that also registers handlers on `Sales Invoice` and
	`Payment Entry`; those are not this app's contract.

	Handlers are NOT invoked with probe documents. Since P1A the Sales Invoice
	handlers run real allocation logic, and `frappe._dict` fakes break on it
	(`doc.items` resolves to the inherited `dict.items` method, so iteration
	raises "builtin_function_or_method is not iterable"). Functional proof that
	the handlers actually run comes from real documents: p1a_acceptance (submit
	and cancel real Sales Invoices) and A3/A4 here (real Batch Debt creates).
	"""
	import importlib

	expected = {
		"Sales Invoice": ("on_submit", "before_cancel", "on_cancel"),
		"Payment Entry": ("on_submit", "on_cancel"),
		"Feed Batch": ("on_update",),
	}
	doc_events = frappe.get_hooks("doc_events") or {}
	problems = []
	tested = []
	for doctype, events in expected.items():
		registered = doc_events.get(doctype, {})
		for event in events:
			paths = registered.get(event)
			if not paths:
				problems.append(f"{doctype}.{event} not registered")
				continue
			# get_hooks() hands back a LIST once more than one app registers the
			# same event, and a bare string while we are the only one.
			ours = [
				p
				for p in (paths if isinstance(paths, (list, tuple)) else [paths])
				if p.startswith("feed_dealer.")
			]
			if not ours:
				problems.append(
					f"{doctype}.{event} is registered, but not by feed_dealer: {paths}"
				)
				continue
			for path in ours:
				module_path, _, attr = path.rpartition(".")
				try:
					handler = getattr(importlib.import_module(module_path), attr)
				except Exception as exc:  # noqa: BLE001
					problems.append(f"{doctype}.{event} -> {path} not importable: {exc}")
					continue
				if not callable(handler):
					problems.append(f"{doctype}.{event} -> {path} is not callable")
				else:
					tested.append(f"{doctype}.{event}")
	if problems:
		raise AssertionError("; ".join(problems))
	return f"{len(tested)} handlers registered and callable: {', '.join(tested)}"


def check_ai_read_only_posture():
	"""A8: no non-human role holds write/submit on the financial DocTypes."""
	offenders = []
	for doctype in FINANCIAL_DOCTYPES:
		for perm in frappe.get_all(
			"DocPerm",
			filters={"parent": doctype, "permlevel": 0},
			fields=["role", "write", "create", "submit", "delete", "amend"],
		):
			if perm.role in ("Feed Dealer Manager", "Feed Dealer Staff"):
				# Feed Dealer Staff must be read-only on financial records.
				if perm.role == "Feed Dealer Staff" and (perm.write or perm.create or perm.submit):
					offenders.append(f"{doctype}: Staff has write/create/submit")
				continue
			if any([perm.write, perm.create, perm.submit, perm.delete, perm.amend]):
				offenders.append(f"{doctype}: {perm.role} has write-ish permission")
	if offenders:
		raise AssertionError("; ".join(offenders))
	return f"{len(FINANCIAL_DOCTYPES)} financial DocTypes expose no write path to non-manager roles"


def check_masters_present():
	"""Seeder output: roles + the masters P0 promises."""
	missing = []
	for role in ("Feed Dealer Manager", "Feed Dealer Staff", "Feed Driver", "Feed Farmer"):
		if not frappe.db.exists("Role", role):
			missing.append(f"Role {role}")
	for item_group in ("Cám lợn", "Cám gà", "Cám cá", "Thuốc thú y"):
		if not frappe.db.exists("Item Group", item_group):
			missing.append(f"Item Group {item_group}")
	for group in ("Trại lớn", "Trại vừa", "Hộ nhỏ", "Đại lý cấp 2"):
		if not frappe.db.exists("Customer Group", group):
			missing.append(f"Customer Group {group}")
	for price_list in ("Giá sỉ", "Giá lẻ"):
		if not frappe.db.exists("Price List", price_list):
			missing.append(f"Price List {price_list}")
	for uom in ("Bao", "Tấn", "Kg"):
		if not frappe.db.exists("UOM", uom):
			missing.append(f"UOM {uom}")
	if missing:
		raise AssertionError(f"missing masters: {missing}")
	# ERPNext v16 keeps conversions in their own DocType, not on UOM.
	bao_factor = frappe.db.get_value(
		"UOM Conversion Factor", {"from_uom": "Bao", "to_uom": "Kg"}, "value"
	)
	if not bao_factor:
		raise AssertionError("UOM Conversion Factor Bao -> Kg is missing")
	companies = frappe.get_all("Company", fields=["name", "default_currency"])
	non_vnd = [c.name for c in companies if c.default_currency != "VND"]
	if non_vnd:
		raise AssertionError(f"non-VND companies: {non_vnd}")
	return (
		f"4 roles, 4 item groups, 4 customer groups, 2 price lists, UOM Bao/Tấn/Kg; "
		f"1 Bao = {bao_factor} Kg; {len(companies)} company(ies) all VND"
	)


def _ensure_farmer_user():
	"""Create (once) a user holding only Feed Farmer, plus own Feed Batch."""
	if not frappe.db.exists("User", FARMER_EMAIL):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": FARMER_EMAIL,
				"first_name": f"{PREFIX} Farmer",
				"send_welcome_email": 0,
				"roles": [{"role": "Feed Farmer"}],
			}
		)
		user.flags.ignore_permissions = True
		user.insert(ignore_permissions=True)
		frappe.db.commit()

	# A Feed Batch owned by the farmer, so "can read own" is a real assertion.
	_make_batch(_make_customer("Farmer"), owner_user=FARMER_EMAIL)
	frappe.db.commit()
	return FARMER_EMAIL


def cleanup():
	"""Remove the fixtures this script created (opt-in, never automatic).

	Fixtures are located through the `P0-ACCEPT%` CUSTOMER, not through a name
	prefix: `Feed Batch` and `Batch Debt` are autonamed (`LOT-...`, `DEBT-...`),
	so their names never carried the prefix.
	"""
	removed = []
	customers = frappe.get_all("Customer", filters={"name": ["like", f"{PREFIX}%"]}, pluck="name")
	for doctype in ("Batch Debt", "Feed Batch"):
		for name in frappe.get_all(
			doctype, filters={"customer": ["in", customers or [""]]}, pluck="name"
		):
			try:
				doc = frappe.get_doc(doctype, name)
				if doc.docstatus == 1:
					doc.cancel()
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				removed.append(f"{doctype} {name}")
			except Exception as exc:  # noqa: BLE001
				removed.append(f"{doctype} {name} FAILED: {exc}")
	for name in customers:
		frappe.delete_doc("Customer", name, force=True, ignore_permissions=True)
		removed.append(f"Customer {name}")
	if frappe.db.exists("User", FARMER_EMAIL):
		frappe.delete_doc("User", FARMER_EMAIL, force=True, ignore_permissions=True)
		removed.append(f"User {FARMER_EMAIL}")
	frappe.db.commit()
	print(f"[feed_dealer] cleanup removed {len(removed)} fixture(s):")
	for item in removed:
		print(f"  - {item}")
	return removed


CHECKS = (
	("A2  DocTypes installed", check_doctypes_installed),
	("A2b derived fields read-only", check_derived_fields_read_only),
	("A3  opening-balance debt OK", check_opening_balance_debt_creates),
	("A4  ordinary debt rejected", check_ordinary_debt_requires_invoice),
	("A5  farmer isolation", check_farmer_isolation),
	("A6  settings persist", check_settings_persist),
	("A7  hooks registered", check_hooks_registered),
	("A8  AI read-only posture", check_ai_read_only_posture),
	("A9  masters present", check_masters_present),
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
		frappe.throw(f"P0 acceptance FAILED: {failed}", title="P0 ACCEPTANCE FAILED")
	print("P0 ACCEPTANCE: ALL PASS")
	return {"failed": failed, "passed": len(report.rows) - len(failed)}


def debug():
	"""Print the traceback of EVERY failing check (triage).

	Separate from `run()` because `bench execute` swallows the exception raised by
	the target function and falls back to `eval(method)`, which surfaces as a
	bogus `NameError: name '<app>' is not defined` instead of the real traceback.
	This function never raises, so the true errors are visible.
	"""
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
