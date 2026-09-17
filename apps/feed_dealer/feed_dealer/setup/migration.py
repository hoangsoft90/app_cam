"""P0.5 legacy data migration: opening balances WITHOUT fake invoices.

Contract (.plan/phases/phase_00_5_migration.md + prompt_P0_5_migration.md):

* Legacy debt from the paper ledger has NO Sales Invoice behind it, and creating a
  backdated one violates NĐ 123/2020. The opening entry is therefore:
      Journal Entry   Dr Accounts Receivable (party = Customer) / Cr opening account
      Batch Debt      is_opening_balance=1, sales_invoice=None, opening_journal_entry=<JE>
* AR opening comes ONLY from these Journal Entries. `reconcile()` proves
  SUM(opening Batch Debt.outstanding) == SUM(JE AR debits) with a 0 VND tolerance
  (integer VND sums of the same columns — a non-zero diff is a defect, not noise).
* The Batch Debt controller deliberately does NOT require `opening_journal_entry`
  (enforcing it at DocType level would block the very import it exists to support —
  see batch_debt.py); the rule is enforced HERE at the migration boundary: every
  imported debt carries its JE, and p05 T3 asserts it.
* The JE lines carry NO `batch_debt`: that custom field is P1F's offset attribution
  (`_offset_sum` computes credit - debit over it), so a DEBIT line carrying it would
  book the opening amount as a negative offset and silently raise the debt. The
  debt -> JE link lives on `Batch Debt.opening_journal_entry` instead.

One JE per balance ROW (i.e. per debt), not one per customer: the plan allows
"1 JE per customer HOẶC nhiều JE", and per-debt makes each row individually
traceable (the JE remark names the customer, the old batch code and the as-of
date) and makes idempotency a per-row property.

Idempotency: a balance row whose (customer, batch, allocated_amount) opening debt
already exists (any docstatus) is SKIPPED — re-running the same file adds nothing
(p05 T4). Clearing a REAL import means cancelling money documents: it is never
automatic; see scripts/migration/README.md and get the owner's explicit go.

No whitelisted/REST API on purpose: this module writes money documents, so it runs
from the bench (Manager on the host), never from an arbitrary logged-in session.

Templates: scripts/migration/customers.csv, opening_batches.csv (optional),
opening_balances.csv in the repo; `import_from_files(dirpath)` reads them from the
bench host filesystem.
"""

import csv
import os

import frappe
from frappe.utils import flt, getdate, now, nowdate

from feed_dealer.events.sales_invoice import _refresh_batch_total

MARK = "P0.5-migration"
JE_REMARK = "Opening balance migration Phase 0.5"
STATUS_OPTIONS = ("Đang nuôi", "Đã xuất bán", "Kết thúc", "Tạm dừng")
ANIMAL_TYPES = ("Lợn", "Gà", "Cá", "Khác")


# ------------------------------------------------------------------ resolution
def _company(company=None):
	name = company or frappe.db.get_single_value("Feed Dealer Settings", "default_company")
	if name and frappe.db.exists("Company", name):
		return name
	companies = frappe.get_all("Company", pluck="name", order_by="creation")
	if not companies:
		frappe.throw("Chưa có Company nào trên site.")
	return companies[0]


def _resolve_customer_group(name):
	"""The row's group (must be a leaf), else a seeded/leaf fallback — never invent."""
	if name:
		row = frappe.db.get_value("Customer Group", name, ["name", "is_group"], as_dict=True)
		if not row:
			frappe.throw(f"Customer Group '{name}' không tồn tại trên site.")
		if row.is_group:
			frappe.throw(
				f"Customer Group '{name}' là nhóm cha — chọn một nhóm lá (vd: Trại lớn).",
				title="Nhóm khách không hợp lệ",
			)
		return row.name
	for candidate in ("Trại lớn", "Trại vừa", "Hộ nhỏ", "Đại lý cấp 2"):
		if frappe.db.exists("Customer Group", candidate):
			return candidate
	leaf = frappe.get_all("Customer Group", filters={"is_group": 0}, pluck="name", order_by="name", limit=1)
	if leaf:
		return leaf[0]
	frappe.throw("Không có Customer Group nào trên site để gán cho khách nhập vào.")


def _opening_equity_account(company, override=None):
	"""The credit side of the opening JE, resolved — never hard-coded.

	Order: explicit parameter > the standard ERPNext "Temporary" account
	(created for opening balances in every standard chart) > a leaf Equity
	account, but ONLY when exactly one exists. With several equity accounts the
	classification is an accounting decision, not ours: fail closed and list the
	candidates so the owner can pass `equity_account` explicitly.
	"""
	if override:
		row = frappe.db.get_value("Account", override, ["name", "company"], as_dict=True)
		if not row or row.company != company:
			frappe.throw(
				f"Tài khoản '{override}' không tồn tại hoặc không thuộc công ty {company}.",
				title="Sai tài khoản đầu kỳ",
			)
		return row.name
	temporary = frappe.get_all(
		"Account", filters={"company": company, "account_type": "Temporary", "is_group": 0},
		pluck="name", order_by="name", limit=1,
	)
	if temporary:
		return temporary[0]
	equity = frappe.get_all(
		"Account", filters={"company": company, "root_type": "Equity", "is_group": 0},
		pluck="name", order_by="name",
	)
	if len(equity) == 1:
		return equity[0]
	frappe.throw(
		f"Không xác định được tài khoản đối ứng đầu kỳ cho công ty {company}. "
		f"(Không có tài khoản account_type='Temporary'; Equity lá tìm thấy: {equity or 'không có'}.) "
		f"Truyền equity_account='...' vào lệnh import để chỉ định rõ.",
		title="Thiếu tài khoản đầu kỳ",
	)


def _receivable_account(company):
	account = frappe.get_cached_value("Company", company, "default_receivable_account")
	if not account:
		frappe.throw(
			f"Công ty {company} chưa cấu hình 'Tài khoản phải thu mặc định' (default_receivable_account).",
			title="Thiếu tài khoản phải thu",
		)
	return account


# ------------------------------------------------------------------ validation
def _clean(raw):
	return {(k or "").strip(): (v or "").strip() for k, v in dict(raw).items() if k is not None}


def _fail(problems, what):
	if problems:
		frappe.throw(
			f"Dữ liệu {what} chưa hợp lệ — không import gì cả:\n- " + "\n- ".join(problems),
			title="P0.5 validate",
		)


def _parse_date(raw, index, field, problems):
	"""Parse one optional date column; an unparseable value is a row problem."""
	if not raw:
		return None
	try:
		parsed = getdate(raw)
	except Exception:  # noqa: BLE001 - frappe raises on garbage input
		parsed = None
	if not parsed:
		problems.append(f"dòng {index}: {field} '{raw}' không parse được (YYYY-MM-DD)")
	return parsed


def _customer_by_phone(phone):
	return frappe.db.get_value("Customer", {"mobile_no": phone}, "name")


def validate_customers(rows):
	"""Header row excluded by DictReader; CSV line numbers start at 2."""
	problems, cleaned, seen = [], [], {}
	for index, raw in enumerate(rows, start=2):
		row = _clean(raw)
		name, phone = row.get("customer_name"), row.get("phone")
		if not name:
			problems.append(f"dòng {index}: thiếu customer_name")
			continue
		if not phone:
			problems.append(f"dòng {index} ({name}): thiếu phone")
			continue
		if phone in seen:
			problems.append(f"dòng {index}: phone {phone} đã xuất hiện ở dòng {seen[phone]} — phone phải unique")
			continue
		seen[phone] = index
		cleaned.append(
			{
				"customer_name": name,
				"phone": phone,
				"customer_group": row.get("customer_group"),
				"address": row.get("address"),
				"tax_id": row.get("tax_id"),
				"notes": row.get("notes"),
				"old_code": row.get("old_code"),
				"_row": index,
			}
		)
	_fail(problems, "customers")
	return cleaned


def validate_opening_batches(rows):
	problems, cleaned = [], []
	for index, raw in enumerate(rows, start=2):
		row = _clean(raw)
		phone, old_code = row.get("customer_phone"), row.get("old_batch_code")
		if not phone or not old_code:
			problems.append(f"dòng {index}: thiếu customer_phone hoặc old_batch_code")
			continue
		if not _customer_by_phone(phone):
			problems.append(f"dòng {index}: chưa có khách với SĐT {phone} — import customers trước")
			continue
		animal = row.get("animal_type") or "Khác"
		if animal not in ANIMAL_TYPES:
			problems.append(f"dòng {index}: animal_type '{animal}' phải thuộc {ANIMAL_TYPES}")
			continue
		status = row.get("status")
		if status and status not in STATUS_OPTIONS:
			problems.append(f"dòng {index}: status '{status}' phải thuộc {STATUS_OPTIONS}")
			continue
		start = _parse_date(row.get("start_date"), index, "start_date", problems)
		if row.get("start_date") and start is None:
			continue
		cleaned.append(
			{
				"phone": phone,
				"old_code": old_code,
				"animal_type": animal,
				"quantity": int(flt(row.get("quantity") or 0)),
				"start_date": start,
				"status": status,
				"notes": row.get("notes"),
				"_row": index,
			}
		)
	_fail(problems, "opening_batches")
	return cleaned


def validate_opening_balances(rows):
	"""phone must exist, amount > 0, as_of_date parseable and NOT in the future.

	A future as-of is almost always a dd/mm-vs-mm/dd typing error; flagging it
	here beats booking an opening debt with a due date that has not happened yet.
	An exact duplicate row (same phone + batch + amount) is also a problem: the
	idempotency skip would silently collapse what the ledger may intend as two
	debts, so the owner has to disambiguate the file instead.
	"""
	problems, cleaned, seen = [], [], set()
	for index, raw in enumerate(rows, start=2):
		row = _clean(raw)
		phone = row.get("customer_phone")
		amount = flt(row.get("outstanding_amount"))
		if not phone:
			problems.append(f"dòng {index}: thiếu customer_phone")
			continue
		if amount <= 0:
			problems.append(f"dòng {index} ({phone}): outstanding_amount phải > 0, nhận {row.get('outstanding_amount')!r}")
			continue
		if not _customer_by_phone(phone):
			problems.append(f"dòng {index}: chưa có khách với SĐT {phone} — import customers trước")
			continue
		as_of = _parse_date(row.get("as_of_date"), index, "as_of_date", problems)
		if row.get("as_of_date") and as_of is None:
			continue
		if as_of and as_of > getdate(nowdate()):
			problems.append(f"dòng {index}: as_of_date {as_of} ở tương lai — kiểm tra lại ngày của sổ cũ")
			continue
		key = (phone, row.get("batch_old_code") or "", amount)
		if key in seen:
			problems.append(
				f"dòng {index}: trùng đúng dòng khác (SĐT {phone}, batch {row.get('batch_old_code') or '-'}, "
				f"{amount:,.0f}) — gộp hoặc phân biệt bằng as_of_date/batch nếu thật sự là 2 khoản"
			)
			continue
		seen.add(key)
		cleaned.append(
			{
				"phone": phone,
				"batch_old_code": row.get("batch_old_code"),
				"amount": amount,
				"as_of": as_of,
				"notes": row.get("notes"),
				"_row": index,
			}
		)
	_fail(problems, "opening_balances")
	return cleaned


# ------------------------------------------------------------------- importing
def _ensure_customer(row, company):
	"""Create-or-reuse by phone, then by name — with loud conflicts, never a merge."""
	holder = frappe.db.get_value(
		"Customer", {"mobile_no": row["phone"]}, ["name", "customer_name"], as_dict=True
	)
	if holder and holder.customer_name != row["customer_name"]:
		frappe.throw(
			f"dòng {row['_row']}: SĐT {row['phone']} đã thuộc khách {holder.name} "
			f"({holder.customer_name}) — phone phải unique, không thể gán cho {row['customer_name']}.",
			title="Trùng SĐT",
		)
	if holder:
		return holder.name, "reused"
	existing = frappe.db.get_value("Customer", row["customer_name"], ["name", "mobile_no"], as_dict=True)
	if existing:
		if existing.mobile_no and existing.mobile_no != row["phone"]:
			frappe.throw(
				f"dòng {row['_row']}: khách '{row['customer_name']}' đã tồn tại với SĐT "
				f"{existing.mobile_no}, khác SĐT {row['phone']} trong file — trùng tên nhưng khác người.",
				title="Trùng tên khách",
			)
		if not existing.mobile_no:
			frappe.db.set_value("Customer", existing.name, "mobile_no", row["phone"], update_modified=False)
		return existing.name, "reused"

	values = {
		"doctype": "Customer",
		"customer_name": row["customer_name"],
		"customer_group": _resolve_customer_group(row.get("customer_group")),
		"mobile_no": row["phone"],
	}
	if row.get("tax_id"):
		values["tax_id"] = row["tax_id"]
	if frappe.get_meta("Customer").has_field("customer_details"):
		details = ""
		if row.get("old_code"):
			# The old ledger code travels with the customer for the audit trail.
			details = f"{MARK} old_code: {row['old_code']}"
		if row.get("notes"):
			details = (details + f" | {row['notes']}").strip(" |")
		if details:
			values["customer_details"] = details
	elif row.get("old_code") or row.get("notes"):
		print(
			f"[P0.5] Customer trên site này không có field 'customer_details' — "
			f"old_code/notes của '{row['customer_name']}' chỉ còn trong file nguồn."
		)
	doc = frappe.get_doc(values)
	doc.insert(ignore_permissions=True)
	if row.get("address"):
		_ensure_address(doc.name, row["address"], company)
	return doc.name, "created"


def _ensure_address(customer, address_text, company):
	existing = frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": "Customer", "link_name": customer, "parenttype": "Address"},
		pluck="parent",
		limit=1,
	)
	if existing:
		return existing[0]
	country = frappe.db.get_value("Company", company, "country") or frappe.db.get_default("country")
	if not country:
		frappe.throw("Không xác định được quốc gia (Company.country trống) để tạo địa chỉ.")
	doc = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": customer,
			"address_type": "Billing",
			# The template has one free-text address column and no city; ERPNext
			# requires a city, so the placeholder is documented in the README.
			"address_line1": address_text[:140],
			"city": "N/A",
			"country": country,
			"links": [{"link_doctype": "Customer", "link_name": customer}],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def import_customers(rows, company=None):
	"""Import the Customers template. Idempotent: existing phone/name is reused."""
	company = _company(company)
	cleaned = validate_customers(rows)
	created, reused = [], []
	for row in cleaned:
		name, state = _ensure_customer(row, company)
		(created if state == "created" else reused).append(name)
	frappe.db.commit()
	print(
		f"[P0.5 {now()} by {frappe.session.user}] customers: "
		f"+{len(created)} created, {len(reused)} reused (file rows {len(cleaned)})"
	)
	return {"created": created, "reused": reused}


def _opening_batch(customer, old_code=None, animal_type=None, quantity=0, start_date=None, status=None, extra_notes=""):
	"""Find-or-create the Feed Batch a balance attaches to (Batch Debt requires one).

	Old ledger batches are keyed by the machine-readable notes prefix
	`{MARK} batch {old_code}` for that customer; balances WITHOUT a batch go to
	one per-customer synthetic batch (`{MARK} opening {customer}`) — the plan's
	"OPENING-{customer}" shape under Feed Batch's own autoname.
	"""
	base = f"{MARK} batch {old_code}" if old_code else f"{MARK} opening {customer}"
	existing = frappe.get_all(
		"Feed Batch", filters={"customer": customer, "notes": ["like", f"{base}%"]}, pluck="name", limit=1
	)
	if existing:
		return existing[0], False
	doc = frappe.get_doc(
		{
			"doctype": "Feed Batch",
			"customer": customer,
			"animal_type": animal_type or "Khác",
			"quantity": int(quantity or 0),
			"start_date": start_date or nowdate(),
			"status": status or "Đang nuôi",
			"notes": base + (f" — {extra_notes}" if extra_notes else ""),
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name, True


def import_opening_batches(rows, company=None):
	company = _company(company)
	cleaned = validate_opening_batches(rows)
	created, reused = [], []
	for row in cleaned:
		customer = _customer_by_phone(row["phone"])
		name, is_new = _opening_batch(
			customer,
			old_code=row["old_code"],
			animal_type=row["animal_type"],
			quantity=row["quantity"],
			start_date=row["start_date"],
			status=row["status"],
			extra_notes=row["notes"],
		)
		(created if is_new else reused).append(name)
	frappe.db.commit()
	print(
		f"[P0.5 {now()} by {frappe.session.user}] opening batches: "
		f"+{len(created)} created, {len(reused)} reused"
	)
	return {"created": created, "reused": reused}


def _opening_je(customer, amount, company, equity_account, old_code, as_of):
	"""Dr AR (party=Customer) / Cr opening account — balanced by construction.

	NOT tagged with `batch_debt` (see the module docstring: that field is P1F's
	offset attribution and a debit line carrying it would book a negative offset).
	"""
	receivable = _receivable_account(company)
	amount = flt(amount, 2)
	detail = f"khách {customer}" + (f", lứa cũ {old_code}" if old_code else "")
	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": company,
			"posting_date": nowdate(),
			"user_remark": f"{JE_REMARK} — {detail}, as_of {as_of or nowdate()}",
			"accounts": [
				{
					"account": receivable,
					"party_type": "Customer",
					"party": customer,
					"debit_in_account_currency": amount,
					"user_remark": f"AR opening {customer}",
				},
				{
					"account": equity_account,
					"credit_in_account_currency": amount,
					"user_remark": "Opening balance equity (P0.5)",
				},
			],
		}
	)
	je.insert(ignore_permissions=True)
	je.submit()
	return je.name


def _adopt_or_create_je(customer, amount, company, equity_account, old_code, as_of):
	"""JE for one opening debt — adopting an orphan from an interrupted run first.

	Crash window: the run died after a JE was submitted but before its debt was
	submitted. Re-creating the JE would double the customer's AR opening (reconcile
	would catch it red, but only after the damage). Better to ADOPT the orphan: a
	submitted, unlinked JE with the marker remark, the same party and the same
	debit amount. Oldest first keeps adoption deterministic.
	"""
	linked = {
		row.opening_journal_entry
		for row in frappe.get_all(
			"Batch Debt", filters={"is_opening_balance": 1}, fields=["opening_journal_entry"]
		)
		if row.opening_journal_entry
	}
	candidates = frappe.get_all(
		"Journal Entry",
		filters={
			"docstatus": 1,
			"user_remark": ["like", f"{JE_REMARK}%"],
			"name": ["not in", sorted(linked) or [""]],
		},
		pluck="name",
		order_by="creation asc",
	)
	for name in candidates:
		rows = frappe.get_all(
			"Journal Entry Account",
			filters={"parent": name, "docstatus": 1, "party_type": "Customer", "party": customer},
			fields=["debit_in_account_currency"],
		)
		if not rows or flt(rows[0].debit_in_account_currency) != flt(amount):
			continue
		# Identity match, not just money match: two rows of the same customer can
		# carry the same amount (two old batches owed the same sum is REAL), and
		# a party+amount-only adoption may swap their JEs — money lands correctly
		# but each audit remark ("lứa cũ LOT-...") would name the wrong batch.
		# The remark is deterministic from the file (never from the clock), so a
		# needle on `lứa cũ {old_code}` is a safe row identity.
		if old_code:
			remark = frappe.db.get_value("Journal Entry", name, "user_remark") or ""
			if f"lứa cũ {old_code}" not in remark:
				continue  # that orphan belongs to a sibling row; its own pass will claim it
			return name
		# Row without an old batch code: no distinguishing field exists, so any
		# same-(customer, amount) orphan is symmetric — adopt the oldest.
		return name
	# No adoptable orphan: book a NEW JE. This is the only path that should ever
	# increase the AR opening beyond what earlier runs booked. An orphan left
	# unclaimed here belongs to a sibling row (its remark names that row's old
	# batch) and will be adopted when that row's turn comes; if it never is, the
	# reconcile (diff != 0) flags the site red for an operator cleanup — visible
	# failure, never a silent audit swap.
	return _opening_je(customer, amount, company, equity_account, old_code, as_of)


def import_opening_balances(rows, equity_account=None, company=None):
	"""Import the Opening Balances template: one JE + one submitted Batch Debt per row.

	The equity account is resolved BEFORE the first write so a misconfigured
	account fails the whole import instead of half-filling the ledger.
	"""
	company = _company(company)
	cleaned = validate_opening_balances(rows)
	equity = _opening_equity_account(company, equity_account)
	created, skipped = [], []
	for row in cleaned:
		customer = _customer_by_phone(row["phone"])
		batch, _new_batch = _opening_batch(customer, old_code=row["batch_old_code"])
		existing = frappe.db.get_value(
			"Batch Debt",
			{
				"customer": customer,
				"is_opening_balance": 1,
				"batch": batch,
				"allocated_amount": row["amount"],
				"docstatus": ["<", 2],
			},
			["name", "docstatus", "opening_journal_entry"],
			as_dict=True,
		)
		if existing and existing.docstatus == 1:
			# Idempotent: the same (customer, batch, amount) row is already booked.
			skipped.append(f"{existing.name} (JE {existing.opening_journal_entry})")
			continue
		if not existing:
			# Draft debt FIRST, JE second: the debt row exists on disk before any
			# money document, so an interrupted run RESUMES (draft -> adopt/create
			# JE -> submit) instead of leaving an orphan JE behind.
			debt = frappe.get_doc(
				{
					"doctype": "Batch Debt",
					"batch": batch,
					"customer": customer,
					"is_opening_balance": 1,
					"allocated_amount": row["amount"],
					# Plan: due_date = as_of_date (or today) — a past as_of derives
					# status "Quá hạn" through the controller, never typed in.
					"due_date": row["as_of"] or nowdate(),
					"payment_terms": "Nợ đầu kỳ (P0.5)"
					+ (f" — {row['notes']}" if row.get("notes") else ""),
				}
			)
			debt.insert(ignore_permissions=True)
			frappe.db.commit()
			name = debt.name
		else:
			name = existing.name
		if existing and existing.opening_journal_entry:
			# Interrupted AFTER the JE was linked (crashed before submit): finish it.
			je = existing.opening_journal_entry
		else:
			je = _adopt_or_create_je(
				customer, row["amount"], company, equity, row["batch_old_code"], row["as_of"]
			)
			frappe.db.set_value("Batch Debt", name, "opening_journal_entry", je, update_modified=False)
		debt = frappe.get_doc("Batch Debt", name)
		debt.submit()
		_refresh_batch_total(batch)
		# Money layer must not half-exist (same rule as P1A/P1F).
		frappe.db.commit()
		created.append(f"{debt.name} <- {je} = {row['amount']:,.0f}")
	print(
		f"[P0.5 {now()} by {frappe.session.user}] opening balances: "
		f"+{len(created)} debts/JEs, {len(skipped)} skipped, equity={equity}"
	)
	for line in created:
		print(f"  + {line}")
	return {"created": created, "skipped": skipped, "equity_account": equity}


# ----------------------------------------------------------------- reconciling
def reconcile(customers=None, prefix=None):
	"""AR opening (JE debits) == SUM(opening Batch Debt outstanding), tolerance 0 VND.

	Run this BEFORE any payment lands on an opening debt: `outstanding_amount` is
	the import-time figure only while the debts are untouched (P1B allocations
	would lower it and the identity would legitimately stop holding).
	"""
	filters = {"is_opening_balance": 1, "docstatus": 1}
	if customers:
		filters["customer"] = ["in", customers]
	elif prefix:
		filters["customer"] = ["like", f"{prefix}%"]
	debts = frappe.get_all(
		"Batch Debt",
		filters=filters,
		fields=["name", "customer", "batch", "outstanding_amount", "opening_journal_entry"],
	)
	if not debts:
		frappe.throw("Không có nợ đầu kỳ nào trong phạm vi — không có gì để đối chiếu.")
	unlinked = sorted(debt.name for debt in debts if not debt.opening_journal_entry)
	if unlinked:
		# Name the owner of the unlinked debt: A3 of p0_acceptance creates an
		# opening-balance debt WITHOUT a JE on purpose (P0 contract), and those
		# rows used to fail every migration reconcile even when scoped by prefix.
		detail = frappe.get_all(
			"Batch Debt",
			filters={"name": ["in", unlinked]},
			fields=["name", "customer", "allocated_amount"],
		)
		lines = ", ".join(f"{row.name} ({row.customer}, {flt(row.allocated_amount):,.0f})" for row in detail[:5])
		frappe.throw(
			f"Các nợ đầu kỳ không gắn bút toán: {lines}. Nếu đây là fixture P0 (không thuộc đợt "
			f"import này) thì thu hẹp phạm vi đối chiếu bằng `customers`/`prefix` của khách import; "
			f"nếu đây là nợ của đợt import thì đợt import KHÔNG đạt chuẩn P0.5.",
			title="Nợ đầu kỳ thiếu bút toán",
		)
	scope = sorted({debt.customer for debt in debts})
	je_names = sorted({debt.opening_journal_entry for debt in debts})
	ar_rows = frappe.get_all(
		"Journal Entry Account",
		filters={
			"parent": ["in", je_names],
			"docstatus": 1,
			"party_type": "Customer",
			"party": ["in", scope],
		},
		fields=["party", "debit_in_account_currency"],
	)
	ar_open, bd_open = {}, {}
	for row in ar_rows:
		ar_open[row.party] = ar_open.get(row.party, 0.0) + flt(row.debit_in_account_currency)
	for debt in debts:
		bd_open[debt.customer] = bd_open.get(debt.customer, 0.0) + flt(debt.outstanding_amount)
	per_customer = sorted(set(ar_open) | set(bd_open))
	diff = sum(ar_open.values()) - sum(bd_open.values())
	if flt(diff) != 0:
		wrong = [
			f"{name}: AR {flt(ar_open.get(name, 0.0)):,.0f} vs BD {flt(bd_open.get(name, 0.0)):,.0f}"
			for name in per_customer
			if flt(ar_open.get(name, 0.0)) != flt(bd_open.get(name, 0.0))
		]
		frappe.throw(
			f"Đối chiếu nợ đầu kỳ LỆCH {flt(diff):,.0f}đ — chi tiết: {'; '.join(wrong[:10])}. "
			f"Đây là lệch tiền thật (số nguyên VND), không phải sai số.",
			title="P0.5 RECONCILE FAILED",
		)
	return {
		"AR_open": flt(sum(ar_open.values())),
		"BD_open": flt(sum(bd_open.values())),
		"diff": 0,
		"customers": len(scope),
		"debts": len(debts),
		"journal_entries": len(je_names),
		"per_customer": {name: flt(bd_open.get(name, 0.0)) for name in per_customer},
	}


def signoff_report(prefix=None):
	"""Print the per-customer opening table the owner signs (plan deliverable 8)."""
	result = reconcile(prefix=prefix)
	lines = [
		f"{'Customer'.ljust(40)}  {'Outstanding (VND)'.rjust(18)}",
		"-" * 60,
	]
	for name, amount in sorted(result["per_customer"].items()):
		lines.append(f"{name[:40].ljust(40)}  {amount:,.0f}".rjust(58))
	lines.append("-" * 60)
	lines.append(f"TOTAL AR_open == BD_open == {result['AR_open']:,.0f} (diff 0)")
	text = "\n".join(lines)
	print(text)
	print(
		"\nOwner sign-off: chép bảng trên vào scripts/migration/OWNER_SIGNOFF.md, "
		"điền ngày/người/import nguồn rồi lưu file evidence."
	)
	return text


# ----------------------------------------------------------------- file loader
def load_csv(path):
	"""Read a template CSV (utf-8 BOM-tolerant, Excel-friendly) into dicts."""
	with open(path, encoding="utf-8-sig", newline="") as handle:
		rows = [_clean(row) for row in csv.DictReader(handle)]
	return [row for row in rows if any(row.values())]


def import_from_files(dirpath, equity_account=None, company=None):
	"""Import the three template files from `dirpath` on the bench host.

	bench --site <site> execute feed_dealer.setup.migration.import_from_files \\
	    kwargs='{"dirpath": "/path/to/migration_data", "equity_account": "..."}'
	"""
	summary = {"customers": import_customers(load_csv(os.path.join(dirpath, "customers.csv")), company=company)}
	batches_path = os.path.join(dirpath, "opening_batches.csv")
	if os.path.exists(batches_path):
		summary["opening_batches"] = import_opening_batches(load_csv(batches_path), company=company)
	summary["opening_balances"] = import_opening_balances(
		load_csv(os.path.join(dirpath, "opening_balances.csv")),
		equity_account=equity_account,
		company=company,
	)
	# Scope the reconciliation to the customers this import just touched. A
	# site-wide scope would trip over OTHER opening debts (e.g. p0_acceptance's
	# A3 fixture is an opening-balance debt created WITHOUT a JE on purpose), and
	# the operator's sign-off covers the imported scope, not the whole ledger.
	phones = [row.get("customer_phone") for row in load_csv(os.path.join(dirpath, "opening_balances.csv"))]
	phones += [row.get("phone") for row in load_csv(os.path.join(dirpath, "customers.csv"))]
	scope = sorted({name for name in (_customer_by_phone(p) for p in phones if p) if name})
	summary["reconcile"] = reconcile(customers=scope)
	return summary
