"""Batch Operation (P1F): split / merge / reallocate a feed batch's debt.

Money rule that makes this safe to automate: an operation may only move debts that
have NOT been touched yet (no payment, return or offset). Splitting a part-paid
debt would mean splitting money columns that are DERIVED from allocations -- there
is no honest way to do that, so the operation REFUSES and tells the operator to
settle first. What it does guarantee, and what `p1f_acceptance` checks, is
conservation: the sum of the target batches' open debt equals the sources'.

Sources are released (cancelled, not deleted) so the audit trail keeps both sides
of the move, and the new slices carry `payment_terms` naming the operation.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt, nowdate

RATIO_FIELD = {"Theo số con": "quantity", "Theo trọng lượng": "weight"}


def _touched(debt_name):
	"""Has real money moved on this debt? Then it must not be split or merged."""
	row = frappe.db.get_value(
		"Batch Debt", debt_name, ["paid_amount", "returned_amount", "offset_amount"], as_dict=True
	)
	return bool(flt(row.paid_amount) or flt(row.returned_amount) or flt(row.offset_amount))


def _open_debts(batch):
	return frappe.get_all(
		"Batch Debt",
		filters={"batch": batch, "docstatus": 1, "outstanding_amount": [">", 0]},
		fields=[
			"name",
			"batch",
			"customer",
			"sales_invoice",
			"item_tax_template",
			"allocated_amount",
			"due_date",
		],
		order_by="due_date asc, name asc",
	)


def _copy_debt(debt, target_batch, amount, operation):
	"""A new slice on `target_batch` carrying the source's invoice attribution."""
	new = frappe.get_doc(
		{
			"doctype": "Batch Debt",
			"batch": target_batch,
			"customer": debt.customer,
			"sales_invoice": debt.sales_invoice or None,
			"item_tax_template": debt.item_tax_template or None,
			"is_opening_balance": 1 if not debt.sales_invoice else 0,
			"allocated_amount": flt(amount, 2),
			"due_date": debt.due_date,
			"payment_terms": f"Chuyển từ {debt.name} bởi {operation}",
		}
	)
	new.insert(ignore_permissions=True)
	new.submit()
	return new.name


def _release(debt_name):
	"""Cancel the source slice: it is now represented by its target slice(s)."""
	doc = frappe.get_doc("Batch Debt", debt_name)
	doc.flags.ignore_links = True
	doc.cancel()


def _shares(debts, targets, basis):
	"""Per-(debt, target) amounts: `basis` is a ratio field or 'manual' weights."""
	if basis == "manual":
		weights = [flt(row.allocated_debt) for row in targets]
	else:
		weights = [flt(getattr(row, basis)) for row in targets]
	total = sum(weights)
	if total <= 0:
		frappe.throw(
			"Không chia được nợ: tổng trọng số của các lứa đích phải lớn hơn 0 "
			f"(phương pháp: {basis})."
		)
	plan = []
	for debt in debts:
		remaining = flt(debt.allocated_amount)
		for idx, row in enumerate(targets):
			if idx == len(targets) - 1:
				amount = flt(remaining, 2)  # last target absorbs the rounding
			else:
				amount = flt(flt(debt.allocated_amount) * weights[idx] / total, 2)
				remaining -= amount
			plan.append((debt, row.batch, amount))
	return plan


class BatchOperation(Document):
	def validate(self):
		if not self.operation_type:
			frappe.throw("Phải chọn loại thao tác (tách lứa/gộp lứa/phân bổ).")
		self.operation_date = self.operation_date or nowdate()
		if not self.source_batches:
			frappe.throw("Phải có ít nhất một lứa nguồn.")
		if not self.target_batches:
			frappe.throw("Phải có ít nhất một lứa đích.")
		sources = [row.batch for row in self.source_batches]
		targets = [row.batch for row in self.target_batches]
		if len(set(sources)) != len(sources):
			frappe.throw("Lứa nguồn bị lặp — mỗi lứa chỉ liệt kê một lần.")
		if len(set(targets)) != len(targets):
			frappe.throw("Lứa đích bị lặp — mỗi lứa chỉ liệt kê một lần.")
		if set(sources) & set(targets):
			frappe.throw("Một lứa không thể vừa là nguồn vừa là đích.")
		for row in self.source_batches:
			if not row.batch:
				frappe.throw(f"Dòng nguồn {row.idx}: thiếu lứa nguồn.")
		for row in self.target_batches:
			if not row.batch:
				frappe.throw(f"Dòng đích {row.idx}: thiếu lứa đích.")
		if self.operation_type == "Tách lứa" and len(self.source_batches) != 1:
			frappe.throw("Tách lứa hiện hỗ trợ đúng MỘT lứa nguồn.")
		if self.operation_type != "Tách lứa" and len(self.target_batches) != 1:
			frappe.throw(f"{self.operation_type} cần đúng MỘT lứa đích.")
		self._stage_target_allocation()

	def _stage_target_allocation(self):
		"""Show the operator the split before submitting (manual amounts are respected)."""
		if self.debt_allocation_method != "Thủ công":
			field = RATIO_FIELD.get(self.debt_allocation_method) or "quantity"
			total_weight = sum(flt(getattr(row, field)) for row in self.target_batches)
			if total_weight <= 0:
				frappe.throw(
					f"Không chia được nợ theo '{self.debt_allocation_method}': tổng {field} của "
					f"các lứa đích phải lớn hơn 0."
				)
			total_debt = flt(self._source_open_debt(), 2)
			remaining = total_debt
			for idx, row in enumerate(self.target_batches):
				if idx == len(self.target_batches) - 1:
					row.allocated_debt = flt(remaining, 2)
				else:
					row.allocated_debt = flt(
						total_debt * flt(getattr(row, field)) / total_weight, 2
					)
					remaining -= row.allocated_debt

	def _source_open_debt(self):
		total = 0.0
		for row in self.source_batches:
			for debt in _open_debts(row.batch):
				total += flt(debt.allocated_amount)
		return total

	def on_submit(self):
		"""Move the debt. Refuses any slice that already has money on it."""
		for row in self.source_batches:
			for debt in _open_debts(row.batch):
				if _touched(debt.name):
					frappe.throw(
						f"Không thể {self.operation_type} lứa {row.batch}: khoản nợ {debt.name} đã có "
						f"thanh toán / trả hàng / cấn trừ. Tất toán khoản nợ đó trước rồi thao tác lại.",
						title="Nợ đã có phát sinh",
					)

		basis = "manual" if self.debt_allocation_method == "Thủ công" else (
			RATIO_FIELD.get(self.debt_allocation_method) or "quantity"
		)
		created = []
		if self.operation_type == "Tách lứa":
			source = self.source_batches[0].batch
			debts = _open_debts(source)
			for debt, target_batch, amount in _shares(debts, self.target_batches, basis):
				if amount <= 0:
					continue
				created.append(_copy_debt(debt, target_batch, amount, self.name))
			for debt in debts:
				_release(debt.name)
		else:
			target_batch = self.target_batches[0].batch
			for row in self.source_batches:
				for debt in _open_debts(row.batch):
					created.append(_copy_debt(debt, target_batch, debt.allocated_amount, self.name))
					_release(debt.name)

		source_names = {row.batch for row in self.source_batches}
		# `has_been_split` means exactly that: only a split sets it. A merge/allocate still records the
		# operation on the sources (audit), but flagging them as "split" would be wrong.
		for name in source_names:
			values = {"split_operation": self.name}
			if self.operation_type == "Tách lứa":
				values["has_been_split"] = 1
			frappe.db.set_value("Feed Batch", name, values, update_modified=False)
		parent = self.source_batches[0].batch if self.operation_type == "Tách lứa" else None
		for row in self.target_batches:
			values = {"split_operation": self.name}
			if parent:
				values["parent_batch"] = parent
			frappe.db.set_value(
				"Feed Batch", row.batch, values, update_modified=False
			)
		frappe.db.commit()
		return {"created": created, "sources_released": sorted(source_names)}
