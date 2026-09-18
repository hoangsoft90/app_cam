"""P1G integrity: ERPNext's AR versus the Batch Debt allocation view.

Run:      bench --site <site> execute feed_dealer.setup.p1g_integrity.run
Debug:    bench --site <site> execute feed_dealer.setup.p1g_integrity.debug
Cleanup:  bench --site <site> execute feed_dealer.setup.p1g_integrity.cleanup
Dataset:  bench --site <site> execute feed_dealer.setup.p1g_integrity.describe

WHAT THIS IS FOR
----------------
`Batch Debt` is a *view* of the customer's Accounts Receivable, split per batch.
ERPNext owns the accounts; this app only mirrors a slice of them. So the view is
only trustworthy if it can be reconciled back to the ledger — that is the whole
point of this script, and it is the measurement the plan asks for before Phase 1
can be declared done (`prompt_P1G_reports_integrity.md` acceptance 1).

It builds a RANDOM (seeded, therefore reproducible) dataset of real documents —
Sales Order -> Sales Invoice (several batches, several tax treatments in one
invoice) -> Payment Entry (some carrying `references`, most deliberately empty)
-> Sales Return -> livestock offset — and then checks, against the database, a
set of identities that must hold EXACTLY.

THE ALGEBRA (why each identity is what it is)
---------------------------------------------
Scope = the P1G dataset's own customers. Over that scope:

  TG = SUM(Sales Invoice.grand_total)          all submitted invoices, credit notes negative
  TE = SUM(Sales Invoice.outstanding_amount)   ERPNext's own receivable figure
  TT = SUM(net_amount of lines this app tracks)  a line counts as tracked when it carries
       `custom_batch` (the debt was created from it) or `batch_debt` (a return note line
       attributed to a debt) — i.e. exactly the lines the Batch Debt view claims to mirror
  U  = TG - TT                                 the part of AR this app deliberately does
                                               NOT mirror: VAT, and lines with no batch
                                               (accessories, loose medicine)
  ER = TG - TE                                 what ERPNext applied at INVOICE level
                                               (references / advance reconciliation)
  PA = SUM(Payment Allocation.paid_amount)     what THIS app's FIFO applied to debts
  OF = SUM(Batch Debt.offset_amount)           what a livestock Journal Entry netted
  RT = SUM(Batch Debt.returned_amount)         what credit notes took back
  BD = SUM(Batch Debt.outstanding_amount)      the view under test

Identity 1 (the view is internally exact):

  BD = TT - PA - OF

  Note what is NOT here: the return amount. A credit note reduces TT (its tracked
  lines are negative) and reduces the debt by the same amount, so it cancels out.
  If this identity fails, the view is not mirroring the ledger — that is the bug.

Identity 2 (the view is a slice of AR, decomposed into named buckets):

  BD - TE = (-U) + ER + (-PA) + (-OF)

  Each bucket is a *reason* the two numbers differ, not a fudge factor:
    -U   AR that this app never mirrored, by design
    ER   money ERPNext applied to invoices but our FIFO did not (or vice versa)
    -PA  money our FIFO applied but ERPNext did not apply to those invoices
    -OF  AR credited by an offset Journal Entry; `Sales Invoice.outstanding_amount`
         is a per-invoice column and is not rewritten by a JE, so it stays high
  `ER - PA` is the number that answers the FIFO-vs-`references` question: when the
  accountant leaves `references` empty (design.md D19) ERPNext applies 0 to the
  invoices and this gap is exactly -PA. It is reported, never silenced.

TOLERANCE: 0 VND, on purpose.
  Every amount here is an integer VND sum of the same columns — no FX conversion,
  no percentage rounding, no pro-rating. A non-zero difference is therefore never
  float noise; it is either a missing term in the identity or a real defect. The
  tolerance is NOT relaxed to make a run pass: a deviation is reported with the
  offending documents, and the fix is a code/design decision, not a wider threshold.

Negative-outstanding debts and `status` are checked too (a debt cannot owe less
than nothing, and the stored status must match the recomputed one), plus the
per-batch `total_debt` roll-up and a "the dataset really contains what it claims"
check so a degenerate sample cannot pass vacuously.
"""

import json
import os
import random
import traceback

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from frappe.utils import add_days, flt, nowdate

from feed_dealer.setup.p1a_acceptance import _company, _debts, _invoice, _item, _vat_item
from feed_dealer.setup.p1b_acceptance import Report
from feed_dealer.setup.p1c_acceptance import _set_limit
from feed_dealer.setup.p1d_acceptance import _approve, _request, _return_line
from feed_dealer.setup.p1f_acceptance import _livestock_row, _purchase_invoice, _supplier

# The dataset is identified by its prefix. The two Site Default keys below are
# module variables (not literals) so a SECOND dataset can be built alongside
# this one without clobbering it -- `p1g_perf.py` swaps prefix+keys to measure
# on a few thousand invoices while the P1G integrity dataset stays verifiable.
# Defaults are exactly the historical names, so existing runs are unaffected.
PREFIX = "P1G-INTEGRITY"
MARKER_BUILT = "feed_dealer_p1g_dataset_built"
MARKER_SHAPE = "feed_dealer_p1g_dataset"
SEED = 20260917
TRANSACTIONS = 60  # the prompt asks for 50-100; 60 keeps a full run under a minute
CUSTOMER_COUNT = 6

KCT_TAX_TEMPLATE = "KCT Cám chăn nuôi - MP"
VAT_TAX_TEMPLATE = "Vietnam Tax - MP"

# Credit the SO leg may commit per customer. Only a Sales Order is gated by P1C;
# an invoice is not. The limit is deliberately far above anything the random
# dataset spends, so the credit gate never becomes the reason a transaction
# fails (if it did, the dataset would silently shrink and the identity checks
# would get weaker, not red).
CREDIT_LIMIT = 500_000_000


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


def _batch(tag, customer):
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


def _receipt(customer, invoice, amount, with_references):
	"""A submitted customer receipt, with or without ERPNext's `references`.

	`get_payment_entry` is still used to pick the company's real bank/cash account
	and the customer's receivable account, so the fixture cannot invent an account
	combination the Desk would never produce. When `with_references` is false the
	reference table is CLEARED before insert — that is the operating assumption of
	design.md D19 (the accountant leaves it empty and lets the batch FIFO decide),
	and most of the dataset's receipts are created that way so the assumption is
	exercised by real documents instead of being asserted in a comment.

	Returns None when there is no invoice left to build the receipt from (the
	template invoice is the only source of the company's account pair), so the
	caller can skip that transaction instead of inventing an account combination.
	"""
	if invoice is None or flt(invoice.outstanding_amount) <= 0:
		rows = frappe.get_all(
			"Sales Invoice",
			filters={"customer": customer, "docstatus": 1, "outstanding_amount": [">", 0]},
			pluck="name",
			limit=1,
		)
		if not rows:
			return None
		invoice = frappe.get_doc("Sales Invoice", rows[0])
	pe = frappe.get_doc(get_payment_entry("Sales Invoice", invoice.name))
	amount = flt(amount)
	pe.paid_amount = amount
	pe.received_amount = amount
	pe.reference_no = f"{PREFIX} bank transfer"
	pe.reference_date = pe.posting_date or nowdate()
	if with_references and pe.references:
		pe.references[0].allocated_amount = min(amount, flt(pe.references[0].outstanding_amount))
	else:
		pe.references = []
	pe.insert(ignore_permissions=True)
	pe.submit()
	frappe.db.commit()
	return pe


def _order_invoice(customer, lines, due_days=15):
	"""SO -> SI: the chain the plan describes, with the batch set on the invoice lines.

	A Sales Order line has no batch field (ours lives on the Sales Invoice Item), so
	the batch is attached when the invoice is raised from the order — the same order
	of operations the Desk flow has.
	"""
	order = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"company": _company(),
			"customer": customer,
			"currency": "VND",
			"transaction_date": nowdate(),
			"delivery_date": add_days(nowdate(), 7),
			"items": [
				{
					"item_code": line.get("item") or _item(),
					"qty": line["qty"],
					"rate": line["rate"],
					"delivery_date": add_days(nowdate(), 7),
				}
				for line in lines
			],
		}
	)
	order.insert(ignore_permissions=True)
	order.submit()
	frappe.db.commit()

	inv = frappe.get_doc(make_sales_invoice(order.name))
	# `zip` truncates silently: if the mapper ever returns a different number of
	# rows, some lines would keep an empty batch and quietly produce fewer debts,
	# weakening the dataset without failing anything.
	if len(inv.items) != len(lines):
		raise AssertionError(
			f"mapper produced {len(inv.items)} invoice line(s) for {len(lines)} order line(s)"
		)
	inv.due_date = add_days(nowdate(), due_days)
	for line, mapped in zip(lines, inv.items):
		mapped.custom_batch = line.get("batch")
	inv.insert(ignore_permissions=True)
	inv.submit()
	frappe.db.commit()
	return inv


def _open_debt_total(customer):
	rows = frappe.get_all(
		"Batch Debt",
		filters={"customer": customer, "docstatus": 1, "outstanding_amount": [">", 0]},
		fields=[{"SUM": "outstanding_amount", "as": "total"}],
	)
	return flt(rows[0].total) if rows else 0.0


# -------------------------------------------------------------------- dataset
def _random_lines(rng, batches):
	"""1-3 lines, mixed batches, and (sometimes) two tax treatments in one invoice."""
	lines = []
	for _ in range(rng.randint(1, 3)):
		line = {
			"qty": rng.randint(1, 20),
			"rate": rng.choice([20_000, 35_000, 50_000, 75_000, 100_000, 150_000, 250_000, 500_000]),
		}
		if rng.random() < 0.9:
			line["batch"] = rng.choice(batches)
		# A VAT item keeps the template it is given whereas the feed item picks up
		# the site's KCT rule, so mixing them puts two tax treatments -- and a VAT
		# share in grand_total -- inside one invoice.
		if rng.random() < 0.25:
			line["item"] = _vat_item()
			line["tax"] = VAT_TAX_TEMPLATE
		lines.append(line)
	return lines


def build_dataset(count=TRANSACTIONS, seed=SEED):
	"""Create the random-but-real dataset. Returns the shape counters it produced."""
	rng = random.Random(seed)
	customers = [_customer(f"C{number}") for number in range(1, CUSTOMER_COUNT + 1)]
	batches = {}
	for cust_index, customer in enumerate(customers, start=1):
		# A real dealer's first SO must pass the P1C gate, which reads the approved
		# limit; a customer with no history has limit 0 by design. Give each dataset
		# customer a manager-approved limit once.
		_set_limit(customer, CREDIT_LIMIT, reason=f"{PREFIX} dataset limit")
		batches[customer] = [
			_batch(f"C{cust_index}-B{number}", customer) for number in range(1, 4)
		]

	shape = {
		"transactions": 0,
		"invoices": 0,
		"orders": 0,
		"multi_batch_invoices": 0,
		"two_tax_invoices": 0,
		"unbatched_lines": 0,
		"receipts": 0,
		"receipts_with_references": 0,
		"receipts_empty_references": 0,
		"advance_receipts": 0,
		"returns": 0,
		"offsets": 0,
	}
	for tx_index in range(count):
		customer = rng.choice(customers)
		lines = _random_lines(rng, batches[customer])
		# every 5th transaction goes through a Sales Order (the documented chain)
		if tx_index % 5 == 0:
			inv = _order_invoice(customer, lines)
			shape["orders"] += 1
		else:
			inv = _invoice(lines, customer=customer, due_days=rng.choice([7, 15, 30]))
			# `_batch` is resolved per line above; `_invoice` needs them already set
		shape["transactions"] += 1
		shape["invoices"] += 1
		shape["unbatched_lines"] += sum(1 for line in lines if not line.get("batch"))
		debts = _debts(inv.name)
		if len({debt.batch for debt in debts}) > 1:
			shape["multi_batch_invoices"] += 1
		if len({debt.item_tax_template for debt in debts}) > 1:
			shape["two_tax_invoices"] += 1

		# Receipt: mostly FIFO (references empty), a minority invoice-level.
		open_total = _open_debt_total(customer)
		roll = rng.random()
		amount, with_references, is_advance = None, False, False
		if open_total > 0 and roll < 0.65:
			fraction = rng.choice([1.0, 1.0, 0.4, 0.6, 0.8])
			amount = round(open_total * fraction / 1000) * 1000
			with_references = rng.random() < 0.3
		elif roll >= 0.93:
			# An advance: pays more than is owed, so part of it cannot be allocated.
			amount = round((open_total + 1_000_000) / 1000) * 1000
			is_advance = True
		if amount and _receipt(customer, inv, amount, with_references):
			shape["receipts"] += 1
			shape["receipts_with_references" if with_references else "receipts_empty_references"] += 1
			shape["advance_receipts"] += 1 if is_advance else 0

		# Return: a partial credit note attributed to the debt of a batched line.
		if rng.random() < 0.25:
			# Index by POSITION, not by `inv.items.index(row)`: Document equality is
			# field-based, so two lines with the same item/rate compare equal and the
			# index can point at the wrong sibling (measured: `_request` then refused
			# the row with "không map được về dòng yêu cầu trả hàng").
			candidates = []
			for index, (row, line) in enumerate(zip(inv.items, lines)):
				if not line.get("batch") or flt(row.net_amount) <= 0:
					continue
				debt = frappe.db.get_value(
					"Batch Debt", {"sales_invoice": inv.name, "batch": line["batch"]}, "name"
				)
				if not debt:
					# No debt for that pair means the invoice never created one (e.g. an
					# accessory line): skip before reading its columns, not after.
					continue
				# The return must clear BOTH guards: qty <= the original line's qty and
				# amount <= what that debt still owes (P1D value guard). A receipt or an
				# offset may already have drained it, so the max qty is derived from the
				# debt's live outstanding rather than assumed from the line.
				outstanding = flt(frappe.db.get_value("Batch Debt", debt, "outstanding_amount"))
				rate = flt(row.rate)
				max_qty = min(int(line["qty"]), int(outstanding // rate) if rate else 0)
				if max_qty >= 1:
					candidates.append((index, row, line, debt, max_qty))
			if candidates:
				index, row, line, debt, max_qty = rng.choice(candidates)
				request = _request(
					inv,
					[
						{
							"item_code": row.item_code,
							"qty": rng.randint(1, max_qty),
							"return_line": _return_line(inv, index),
							"batch_debt": debt,
						}
					],
					customer=customer,
				)
				_approve(request.name)
				shape["returns"] += 1

		# Livestock offset: the farmer sells animals back and it nets against a batch debt.
		if rng.random() < 0.12:
			open_debts = frappe.get_all(
				"Batch Debt",
				filters={"customer": customer, "docstatus": 1, "outstanding_amount": [">", 0]},
				fields=["name", "batch", "outstanding_amount"],
			)
			if open_debts:
				target = rng.choice(open_debts)
				supplier = _supplier(customer)
				amount = round((min(flt(target.outstanding_amount), 800_000)) / 1000) * 1000
				if amount > 0:
					pi = _purchase_invoice(supplier, amount)
					row_name = _livestock_row(target.batch, pi.name, amount)
					from feed_dealer.events.livestock_offset import offset_livestock_sale

					offset_livestock_sale(target.batch, row_name)
					shape["offsets"] += 1
		frappe.db.commit()
	return shape


# ------------------------------------------------------------------ aggregates
def _facts():
	"""Every number the identities need, read fresh from the database."""
	customers = _customers()
	if not customers:
		raise AssertionError("no P1G dataset on this site - run `p1g_integrity.run` first")
	if not frappe.db.get_default(MARKER_BUILT):
		# Guards against the vacuous pass: with no documents every identity is
		# "0 == 0" and the suite would report green on an empty database.
		raise AssertionError(
			"the P1G dataset build never completed (marker missing) - identities would be 0 == 0"
		)

	invoices = frappe.db.sql(
		"""
		select si.name, si.grand_total, si.outstanding_amount, si.is_return,
		       sum(case when ifnull(sii.custom_batch, '') != '' or ifnull(sii.batch_debt, '') != ''
		                then sii.net_amount else 0 end) as tracked
		from `tabSales Invoice` si
		left join `tabSales Invoice Item` sii
		       on sii.parent = si.name and sii.parenttype = 'Sales Invoice'
		where si.docstatus = 1 and si.customer in %(customers)s
		group by si.name
		""",
		{"customers": customers},
		as_dict=True,
	)
	debts = frappe.get_all(
		"Batch Debt",
		filters={"customer": ["in", customers]},
		fields=[
			"name",
			"batch",
			"docstatus",
			"status",
			"outstanding_amount",
			"allocated_amount",
			"paid_amount",
			"returned_amount",
			"offset_amount",
		],
	)
	active = [row for row in debts if row.docstatus == 1]
	payments = frappe.get_all(
		"Payment Entry",
		filters={"party_type": "Customer", "party": ["in", customers], "docstatus": 1},
		fields=["name", "paid_amount"],
	)
	references = frappe.get_all(
		"Payment Entry Reference",
		filters={"parenttype": "Payment Entry", "parent": ["in", [row.name for row in payments]]},
		fields=["parent", "allocated_amount"],
	)
	allocations = frappe.get_all(
		"Payment Allocation",
		filters={"batch_debt": ["in", [row.name for row in debts]]},
		fields=["name", "payment_entry", "batch_debt", "docstatus", "paid_amount"],
	)
	facts = {
		"customers": customers,
		"invoices": invoices,
		"debts": debts,
		"active_debts": active,
		"payments": payments,
		"references": references,
		"allocations": allocations,
		"TG": sum(flt(row.grand_total) for row in invoices),
		"TE": sum(flt(row.outstanding_amount) for row in invoices),
		"TT": sum(flt(row.tracked) for row in invoices),
		"TT_notes": sum(flt(row.tracked) for row in invoices if row.is_return),
		"BD": sum(flt(row.outstanding_amount) for row in active),
		"PA": sum(flt(row.paid_amount) for row in allocations if row.docstatus == 1),
		"OF": sum(flt(row.offset_amount) for row in active),
		"RT": sum(flt(row.returned_amount) for row in active),
		"REF": sum(flt(row.allocated_amount) for row in references),
		"RECEIPTS": sum(flt(row.paid_amount) for row in payments),
	}
	facts["U"] = facts["TG"] - facts["TT"]
	facts["ER"] = facts["TG"] - facts["TE"]
	return facts


def _money(value):
	return f"{flt(value):,.0f}"


# ---------------------------------------------------------------------- checks
def check_view_matches_ledger():
	"""C1: BD = TT - PA - OF exactly (see the module docstring for the algebra)."""
	f = _facts()
	diff = flt(f["BD"]) - (flt(f["TT"]) - flt(f["PA"]) - flt(f["OF"]))
	if flt(diff) != 0:
		raise AssertionError(
			f"identity 1 broken: BD {_money(f['BD'])} != TT {_money(f['TT'])} - PA {_money(f['PA'])}"
			f" - OF {_money(f['OF'])} (diff {_money(diff)}); "
			f"active debts={len(f['active_debts'])}, invoices={len(f['invoices'])}"
		)
	return (
		f"BD {_money(f['BD'])} == TT {_money(f['TT'])} - PA {_money(f['PA'])} - OF {_money(f['OF'])}"
		f" (diff {_money(diff)})"
	)


def check_return_notes_net_to_returned():
	"""C2: the tracked net of credit notes equals -SUM(returned_amount) exactly."""
	f = _facts()
	diff = flt(f["TT_notes"]) + flt(f["RT"])
	if flt(diff) != 0:
		raise AssertionError(
			f"credit-note tracked net {_money(f['TT_notes'])} != -returned {_money(f['RT'])}"
			f" (diff {_money(diff)})"
		)
	return f"credit notes tracked {_money(f['TT_notes'])} == -returned {_money(-flt(f['RT']))}"


def check_batch_total_rollup():
	"""C3: Feed Batch.total_debt equals the sum of its active debts' outstanding."""
	f = _facts()
	rows = frappe.get_all(
		"Feed Batch", filters={"notes": ["like", f"{PREFIX}%"]}, fields=["name", "total_debt"]
	)
	bad = []
	for row in rows:
		expected = sum(
			flt(debt.outstanding_amount)
			for debt in f["active_debts"]
			if debt.batch == row.name
		)
		if flt(row.total_debt) != flt(expected):
			bad.append(f"{row.name}: stored {_money(row.total_debt)} != {_money(expected)}")
	if bad:
		raise AssertionError("total_debt roll-up wrong: " + "; ".join(bad))
	return f"{len(rows)} Feed Batch(es) match the sum of their active debts"


def check_no_negative_and_status():
	"""C4: an active debt never owes less than nothing, and its stored status is the derived one."""
	f = _facts()
	negative = [row.name for row in f["active_debts"] if flt(row.outstanding_amount) < 0]
	if negative:
		raise AssertionError(f"active debts with negative outstanding: {negative[:5]}")
	bad = []
	for row in f["active_debts"]:
		outstanding = flt(row.outstanding_amount)
		stored = row.status
		# "Quá hạn" is date-dependent, so only the money-derived part is compared:
		# a settled debt must say so, and a part-paid one must not say "Chưa trả".
		if outstanding > 0 and stored == "Đã trả":
			bad.append(f"{row.name}: outstanding {_money(outstanding)} but status 'Đã trả'")
		if outstanding <= 0 and stored != "Đã trả":
			bad.append(f"{row.name}: no outstanding but status {stored!r}")
		if outstanding > 0 and flt(row.paid_amount) > 0 and stored == "Chưa trả":
			bad.append(f"{row.name}: part-paid {_money(row.paid_amount)} but status 'Chưa trả'")
	if bad:
		raise AssertionError("status/outstanding disagree: " + "; ".join(bad[:5]))
	return f"{len(f['active_debts'])} active debts: none negative, every status agrees with the money"


def check_allocations_within_receipts():
	"""C5: our FIFO never hands out more than the receipt actually received."""
	f = _facts()
	per_payment = {}
	for row in f["allocations"]:
		if row.docstatus != 1:
			continue
		per_payment[row.payment_entry] = per_payment.get(row.payment_entry, 0.0) + flt(row.paid_amount)
	paid = {row.name: flt(row.paid_amount) for row in f["payments"]}
	bad = [
		f"{name}: allocated {_money(amount)} > paid {_money(paid.get(name, 0))}"
		for name, amount in per_payment.items()
		if flt(amount) > flt(paid.get(name, 0)) + 0.001
	]
	if bad:
		raise AssertionError("over-allocated receipts: " + "; ".join(bad[:5]))
	slack = sum(flt(paid.get(name, 0)) - flt(amount) for name, amount in per_payment.items())
	return (
		f"{len(per_payment)}/${len(f['payments'])} receipt(s) allocated, "
		f"max allocation <= paid_amount (unallocated total {_money(slack)})"
	)


def check_erp_reconciliation_matches_references():
	"""C6: what ERPNext applied at invoice level equals what `references` asked for."""
	f = _facts()
	diff = flt(f["ER"]) - flt(f["REF"])
	if flt(diff) != 0:
		raise AssertionError(
			f"ERPNext reconciled {_money(f['ER'])} but references total {_money(f['REF'])}"
			f" (diff {_money(diff)})"
		)
	return f"ERPNext invoice-level applied {_money(f['ER'])} == references {_money(f['REF'])}"


def check_ar_vs_batch_debt_buckets():
	"""C7: BD - TE decomposes exactly into the four named buckets (the SoT number)."""
	f = _facts()
	diff = flt(f["BD"]) - flt(f["TE"])
	buckets = {
		"unbatched AR (VAT + lines with no batch)": -flt(f["U"]),
		"ERPNext invoice-level reconciliation": flt(f["ER"]),
		"our FIFO allocation": -flt(f["PA"]),
		"livestock offset (JE, not on the invoice row)": -flt(f["OF"]),
	}
	residual = flt(diff) - sum(buckets.values())
	if flt(residual) != 0:
		raise AssertionError(
			f"BD - TE = {_money(diff)} but the buckets sum to {_money(sum(buckets.values()))}"
			f" (residual {_money(residual)}) - an unexplained term exists"
		)
	return (
		f"AR (SUM SI.outstanding) {_money(f['TE'])}, Batch Debt {_money(f['BD'])}, "
		f"diff {_money(diff)} == buckets "
		+ " + ".join(f"{_money(value)} [{name}]" for name, value in buckets.items())
	)


def check_fifo_vs_references():
	"""C8: size the FIFO-vs-`references` gap and prove it comes from the receipts.

	`ER - PA` is positive when ERPNext applied more to invoices than our FIFO
	handed to debts, negative in the D19 case (references left empty: ERPNext
	applies nothing, our FIFO applies the money). Either way the number must equal
	the sum over receipts of (that receipt's reference amount - its FIFO slices),
	so the gap is explained receipt by receipt rather than netted away.
	"""
	f = _facts()
	per_receipt = {}
	for row in f["references"]:
		per_receipt[row.parent] = per_receipt.get(row.parent, 0.0) + flt(row.allocated_amount)
	for row in f["allocations"]:
		if row.docstatus == 1:
			per_receipt[row.payment_entry] = per_receipt.get(row.payment_entry, 0.0) - flt(row.paid_amount)
	gap = flt(f["ER"]) - flt(f["PA"])
	residual = flt(gap) - sum(per_receipt.values())
	if flt(residual) != 0:
		raise AssertionError(
			f"the FIFO/references gap {_money(gap)} != per-receipt detail"
			f" {_money(sum(per_receipt.values()))} (residual {_money(residual)})"
		)
	with_refs = sum(1 for name in per_receipt if name in {row.parent for row in f["references"]})
	return (
		f"gap (ERPNext applied {_money(f['ER'])} - FIFO allocated {_money(f['PA'])}) = {_money(gap)}; "
		f"{with_refs} receipt(s) carry references, the rest rely on the batch FIFO"
	)


def check_dataset_shape():
	"""C9: the random dataset must actually contain every case the checks claim.

	A dataset that quietly produced no returns or no referenced receipt would make
	C2/C6/C8 pass for the wrong reason. `describe()` persists the counters; this
	check re-reads the documents so the claim is about the data, not about a dict.
	"""
	f = _facts()
	customers = f["customers"]
	debt_names = [row.name for row in f["debts"]]
	if len(customers) < CUSTOMER_COUNT:
		raise AssertionError(f"expected {CUSTOMER_COUNT} dataset customers, found {len(customers)}")
	invoice_count = frappe.db.count("Sales Invoice", {"customer": ["in", customers], "docstatus": 1})
	note_count = frappe.db.count(
		"Sales Invoice", {"customer": ["in", customers], "docstatus": 1, "is_return": 1}
	)
	order_count = frappe.db.count("Sales Order", {"customer": ["in", customers], "docstatus": 1})
	payment_count = len(f["payments"])
	payments_with_refs = {row.parent for row in f["references"]}
	offset_count = len(
		frappe.get_all("Journal Entry Account", filters={"batch_debt": ["in", debt_names]}, pluck="parent")
	)
	# A dataset that quietly produced no return, no referenced receipt or no offset
	# would make C2/C6/C8 pass for the wrong reason, so each case must be present.
	missing = []
	if invoice_count < 40:
		missing.append(f"only {invoice_count} submitted invoices")
	if note_count < 5:
		missing.append(f"only {note_count} credit notes (returns)")
	if order_count < 5:
		missing.append(f"only {order_count} submitted Sales Orders")
	if payment_count < 10:
		missing.append(f"only {payment_count} receipts")
	if len(payments_with_refs) < 1:
		missing.append("no receipt carries `references`")
	if payment_count - len(payments_with_refs) < 1:
		missing.append("no receipt has empty `references`")
	if offset_count < 1:
		missing.append("no livestock offset Journal Entry")
	if flt(f["RT"]) <= 0:
		missing.append("no returned_amount on any debt")
	if missing:
		raise AssertionError("dataset too degenerate to prove anything: " + "; ".join(missing))

	# EXACT counts against what the builder recorded, not just "enough rows":
	# leftovers from a crashed build used to sit in the dataset unnoticed (one
	# extra submitted Sales Order), and a green run then rested on documents the
	# shape counters never claimed. Any extra or missing document is a red check.
	shape = json.loads(frappe.db.get_default(MARKER_SHAPE) or "{}")
	expected = {
		"invoices": shape.get("transactions", 0) + shape.get("returns", 0),
		"sales_orders": shape.get("orders", 0),
		"receipts": shape.get("receipts", 0),
		"credit_notes": shape.get("returns", 0),
	}
	actual = {
		"invoices": invoice_count,
		"sales_orders": order_count,
		"receipts": payment_count,
		"credit_notes": note_count,
	}
	if expected != actual:
		raise AssertionError(
			f"the dataset on the site is not the dataset the builder recorded: expected {expected}, "
			f"found {actual} - leftovers from a crashed/partial build, or documents created outside "
			f"the builder. Run `p1g_integrity.cleanup` and rebuild before trusting the numbers."
		)
	reported = frappe.db.get_default(MARKER_SHAPE) or "{}"
	return (
		f"{invoice_count} invoice(s) ({note_count} credit note), {order_count} SO->SI, "
		f"{payment_count} receipt(s) ({len(payments_with_refs)} with references), {offset_count} offset JE(s); "
		f"dataset={reported}"
	)


CHECKS = (
	("C1  view == ledger identity", check_view_matches_ledger),
	("C2  credit notes == returned_amount", check_return_notes_net_to_returned),
	("C3  Feed Batch total_debt roll-up", check_batch_total_rollup),
	("C4  no negative / status agrees", check_no_negative_and_status),
	("C5  allocations <= receipts", check_allocations_within_receipts),
	("C6  ERPNext applied == references", check_erp_reconciliation_matches_references),
	("C7  AR vs Batch Debt buckets", check_ar_vs_batch_debt_buckets),
	("C8  FIFO vs references gap", check_fifo_vs_references),
	("C9  dataset shape has teeth", check_dataset_shape),
)


def collect():
	report = Report()
	for name, fn in CHECKS:
		report.check(name, fn)
	return report


def describe():
	"""Print the SoT numbers without asserting anything (for the RESULT report)."""
	f = _facts()
	summary = {
		"AR_sum_si_outstanding": flt(f["TE"]),
		"Batch_Debt_outstanding": flt(f["BD"]),
		"diff": flt(f["BD"]) - flt(f["TE"]),
		"batched_net_TT": flt(f["TT"]),
		"untracked_U": flt(f["U"]),
		"fifo_allocated_PA": flt(f["PA"]),
		"erp_applied_ER": flt(f["ER"]),
		"offset_OF": flt(f["OF"]),
		"returned_RT": flt(f["RT"]),
		"receipts_total": flt(f["RECEIPTS"]),
		"tolerance": 0,
		"tolerance_reason": "integer VND sums of the same columns: no FX, no percentage rounding, no pro-rating",
	}
	print(json.dumps(summary, indent=1, ensure_ascii=False))
	return summary


def ensure_dataset(count=TRANSACTIONS):
	"""Build the dataset when the site has none (or when a rebuild is forced).

	Every entry point goes through here: `debug()` used to skip it, so a site
	without the dataset reported nine red checks that all meant "no fixtures"
	rather than "the identity is broken" — a useless signal.
	"""
	# `_customers()` is NOT enough on its own: a build that died halfway leaves the
	# customer records behind (they are committed by `_set_limit`), so "customers
	# exist" would look like "the dataset exists" while zero invoices were ever
	# made, and every identity check would pass against an empty database. The
	# marker is written only after a build that ran to completion.
	#
	# A PARTIAL build (customers present, marker missing) is cleaned before the
	# rebuild, and this is not cosmetic: a retry on top of the leftovers of a
	# crashed build produced a dataset with one EXTRA submitted Sales Order that
	# the shape counters never recorded, because the crash happened between the
	# order's submit and the invoice raised from it. The run still went green — the
	# identities do not involve a stray order — which is exactly why the counters
	# now have to be exact (check_dataset_shape) and the rebuild has to start clean.
	marker = frappe.db.get_default(MARKER_BUILT)
	force = os.environ.get("FEED_DEALER_P1G_REBUILD") == "1"
	if not marker and not force and _customers():
		print("[feed_dealer] partial P1G dataset detected (no build marker) - cleaning before rebuild")
		cleanup()
	if force or not marker:
		shape = build_dataset(count=count)
		frappe.db.set_default(MARKER_SHAPE, json.dumps(shape, ensure_ascii=False))
		frappe.db.set_default(MARKER_BUILT, "1")
		frappe.db.commit()
		return shape
	return None


def run(count=TRANSACTIONS):
	"""Build the dataset (if needed) and run every identity check."""
	ensure_dataset(count=count)
	frappe.db.commit()
	report = collect()
	text, failed = report.render()
	print(text)
	print(json.dumps(describe(), ensure_ascii=False))
	print(json.dumps({"site": frappe.local.site, "failed": failed}))
	frappe.db.commit()
	if failed:
		frappe.throw(f"P1G integrity FAILED: {failed}", title="P1G INTEGRITY FAILED")
	print("P1G INTEGRITY: ALL PASS")
	return {"failed": failed, "passed": len(report.rows) - len(failed)}


def debug(count=TRANSACTIONS):
	"""Print every failing check's real traceback (bench execute masks errors)."""
	ensure_dataset(count=count)
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
	"""Remove the P1G dataset in dependency order (documents that hold links first)."""
	removed = []
	customers = _customers()
	batches = frappe.get_all("Feed Batch", filters={"notes": ["like", f"{PREFIX}%"]}, pluck="name")

	jes = set()
	for batch in batches:
		for name in frappe.get_all(
			"Livestock Sale", filters={"parent": batch, "parenttype": "Feed Batch"}, pluck="journal_entry"
		):
			if name:
				jes.add(name)
	for debt in frappe.get_all("Batch Debt", filters={"customer": ["in", customers]}, pluck="name"):
		jes.update(frappe.get_all("Journal Entry Account", filters={"batch_debt": debt}, pluck="parent"))
	for name in jes:
		try:
			doc = frappe.get_doc("Journal Entry", name)
			if doc.docstatus == 1:
				doc.flags.ignore_links = True
				doc.cancel()
			frappe.delete_doc("Journal Entry", name, force=True, ignore_permissions=True)
			removed.append(f"Journal Entry {name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Journal Entry {name} FAILED: {exc}")

	# Return requests and their credit notes must go through the API: the submitted
	# request holds a back-link that blocks a plain cancel of the note.
	for name in frappe.get_all(
		"Sales Return Request", filters={"customer": ["in", customers]}, pluck="name"
	):
		try:
			doc = frappe.get_doc("Sales Return Request", name)
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

	for name in frappe.get_all("Purchase Invoice", filters={"supplier": ["in", customers]}, pluck="name"):
		try:
			doc = frappe.get_doc("Purchase Invoice", name)
			if doc.docstatus == 1:
				doc.flags.ignore_links = True
				doc.cancel()
			frappe.delete_doc("Purchase Invoice", name, force=True, ignore_permissions=True)
			removed.append(f"Purchase Invoice {name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Purchase Invoice {name} FAILED: {exc}")

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

	for row in frappe.get_all(
		"Sales Order", filters={"customer": ["in", customers]}, fields=["name", "docstatus"]
	):
		try:
			if row.docstatus == 1:
				frappe.get_doc("Sales Order", row.name).cancel()
			frappe.delete_doc("Sales Order", row.name, force=True, ignore_permissions=True)
			removed.append(f"Sales Order {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Sales Order {row.name} FAILED: {exc}")

	for name in batches:
		try:
			frappe.delete_doc("Feed Batch", name, force=True, ignore_permissions=True)
			removed.append(f"Feed Batch {name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Feed Batch {name} FAILED: {exc}")
	for name in customers:
		try:
			frappe.delete_doc("Customer", name, force=True, ignore_permissions=True)
			removed.append(f"Customer {name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Customer {name} FAILED: {exc}")
	frappe.db.set_default(MARKER_SHAPE, "")
	frappe.db.set_default(MARKER_BUILT, "")
	frappe.db.commit()
	print(f"[feed_dealer] P1G cleanup removed {len(removed)} fixture(s):")
	for item in removed:
		print(f"  - {item}")
	return removed
