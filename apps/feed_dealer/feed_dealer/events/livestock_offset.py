"""Livestock offset (P1F): net a farmer's livestock purchase against their batch debt.

Accounting is plain double-entry, nothing about AR is edited outside ERPNext's ledger:

    Dr  Accounts Payable    (party = the supplier we bought the animals from)
    Cr  Accounts Receivable (party = the customer, one credit line per debt slice)

The Purchase Invoice raises the payable; the Journal Entry nets it against the
receivable, so the customer's AR drops by the netted amount and Batch Debt mirrors
that through `offset_amount` (see `_offset_sum` in the Batch Debt controller).

Attribution: one CREDIT line per batch debt slice, oldest due date first (the same
FIFO rule as P1B), each carrying the custom field `batch_debt`. The payable side is
one line for the netted total, so debits == credits by construction — the JE passes
ERPNext's own balance validation rather than our say-so.

Capped at what is actually owed: if the purchase is worth more than the batch's open
debt, only the owed part is netted and the remainder stays payable in cash. Offsetting
more would invent a negative debt (and would inflate P1C's available credit limit,
which sums `outstanding_amount`).
"""

import frappe
from frappe.utils import flt, nowdate

CUSTOMER_PARTY = "Customer"


def _company_account(company, field, label):
	account = frappe.get_cached_value("Company", company, field)
	if not account:
		frappe.throw(
			f"Công ty {company} chưa cấu hình '{label}' nên không lập được bút toán cấn trừ.",
			title="Thiếu tài khoản kế toán",
		)
	return account


def _open_debts(batch, customer):
	"""Open debt of ONE batch (a batch holds several slices when it has several invoices)."""
	return frappe.get_all(
		"Batch Debt",
		filters={
			"batch": batch,
			"customer": customer,
			"docstatus": 1,
			"outstanding_amount": [">", 0],
		},
		fields=["name", "outstanding_amount"],
		order_by="due_date asc, name asc",
	)


@frappe.whitelist()
def offset_livestock_sale(feed_batch, row_name):
	"""Create + submit the offset Journal Entry for one Livestock Sale row (API)."""
	batch = frappe.get_doc("Feed Batch", feed_batch)
	batch.check_permission("write")
	row = next((r for r in batch.livestock_sales if r.name == row_name), None)
	if row is None:
		frappe.throw(f"Không tìm thấy dòng bán vật nuôi {row_name} trên lứa {feed_batch}.")
	if not row.offset_to_debt:
		frappe.throw("Dòng này không bật 'Cấn trừ vào nợ lứa'.")
	if row.journal_entry:
		frappe.throw(f"Dòng này đã có bút toán cấn trừ {row.journal_entry}.")
	if not row.purchase_invoice:
		frappe.throw("Dòng này thiếu 'Hoá đơn mua vào' nên không có gì để cấn trừ.")

	invoice = frappe.get_doc("Purchase Invoice", row.purchase_invoice)
	if invoice.docstatus != 1:
		frappe.throw(f"Hoá đơn mua vào {invoice.name} chưa submit.")
	if not invoice.company:
		frappe.throw(f"Hoá đơn mua vào {invoice.name} thiếu công ty.")
	# IDENTITY GUARD (review finding): the Journal Entry below debits the SUPPLIER's payable and
	# credits the CUSTOMER's receivable. If those are two different parties, the entry cancels one
	# person's payable with another person's receivable — the farmer never gets paid AND an unrelated
	# supplier's debt disappears. Offsetting is only meaningful when both sides are the same subject
	# (the usual case: the farmer is registered as Supplier with the same name), so refuse anything
	# else and let accounting settle it explicitly.
	if invoice.supplier != batch.customer:
		frappe.throw(
			f"Nhà cung cấp trên hoá đơn mua ({invoice.supplier}) không phải khách hàng của lứa "
			f"({batch.customer}). Cấn trừ chỉ hợp lệ khi cùng một chủ thể — tạo Supplier trùng tên với "
			f"Customer (hoặc để kế toán xử lý riêng), không cấn trừ nợ của người khác.",
			title="Sai chủ thể cấn trừ",
		)

	debts = _open_debts(batch.name, batch.customer)
	total_open = flt(sum(flt(debt.outstanding_amount) for debt in debts))
	nettable = min(flt(invoice.grand_total), total_open)
	if nettable <= 0:
		frappe.throw(
			f"Không còn nợ nào trên lứa {batch.name} để cấn trừ (hoá đơn mua {invoice.name}).",
			title="Không có nợ để cấn trừ",
		)

	accounts = [
		{
			"account": _company_account(invoice.company, "default_payable_account", "Tài khoản phải trả"),
			"party_type": "Supplier",
			"party": invoice.supplier,
			"debit_in_account_currency": flt(nettable, 2),
			"user_remark": f"Cấn trừ hoá đơn mua {invoice.name}",
		}
	]
	remaining = flt(nettable, 2)
	# One credit line per debt slice: this is the attribution `_offset_sum` reads.
	for debt in debts:
		if remaining <= 0:
			break
		slice_amount = flt(min(remaining, flt(debt.outstanding_amount)), 2)
		if slice_amount <= 0:
			continue
		accounts.append(
			{
				"account": _company_account(
					invoice.company, "default_receivable_account", "Tài khoản phải thu"
				),
				"party_type": CUSTOMER_PARTY,
				"party": batch.customer,
				"credit_in_account_currency": slice_amount,
				"batch_debt": debt.name,
				"user_remark": f"Cấn trừ nợ {debt.name} (lứa {batch.name})",
			}
		)
		remaining -= slice_amount

	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": invoice.company,
			"posting_date": nowdate(),
			"user_remark": (
				f"Cấn trừ bán vật nuôi (lứa {batch.name}) vào nợ — hoá đơn mua {invoice.name}, "
				f"dòng {row_name}"
			),
			"accounts": accounts,
		}
	)
	je.insert(ignore_permissions=True)
	je.submit()
	frappe.db.set_value("Livestock Sale", row_name, "journal_entry", je.name, update_modified=False)
	frappe.db.commit()
	return {
		"journal_entry": je.name,
		"netted": flt(nettable, 2),
		"invoice_total": flt(invoice.grand_total, 2),
		"left_payable": flt(flt(invoice.grand_total) - nettable, 2),
		"debts": sorted(debt.name for debt in debts),
	}
