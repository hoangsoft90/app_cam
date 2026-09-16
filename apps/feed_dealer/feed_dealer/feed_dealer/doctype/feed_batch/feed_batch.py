import frappe
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate

# Ngày nuôi tối thiểu theo loại vật nuôi (dùng để suy ra expected_end_date).
# Đây là rule nghiệp vụ mặc định, có thể chuyển thành cấu hình khi cần.
DEFAULT_CYCLE_DAYS = {"Lợn": 150, "Gà": 60, "Cá": 120, "Khác": 90}


class FeedBatch(Document):
	"""Lứa nuôi. Named Feed Batch to avoid ERPNext's own `Batch` DocType."""

	def validate(self):
		self.validate_dates()
		self.set_expected_end_date()
		self.recalculate_debt()

	def validate_dates(self):
		if self.start_date and self.actual_end_date:
			if getdate(self.actual_end_date) < getdate(self.start_date):
				frappe.throw("Ngày kết thúc thực tế không được trước ngày bắt đầu lứa.")
		if self.parent_batch and self.parent_batch == self.name:
			frappe.throw("Lứa gốc không thể là chính nó.")

	def set_expected_end_date(self):
		if self.expected_end_date or not self.start_date:
			return
		cycle = DEFAULT_CYCLE_DAYS.get(self.animal_type, DEFAULT_CYCLE_DAYS["Khác"])
		self.expected_end_date = add_days(self.start_date, cycle)

	def recalculate_debt(self):
		"""Read the debt back from the allocation layer (never store it twice)."""
		if self.is_new():
			self.total_debt = 0
			return
		# Dict syntax is mandatory in v16: a string field containing a SQL
		# function is rejected with "SQL functions are not allowed as strings
		# in SELECT".
		rows = frappe.db.get_all(
			"Batch Debt",
			filters={"batch": self.name, "docstatus": 1},
			fields=[{"SUM": "outstanding_amount", "as": "outstanding"}],
		)
		self.total_debt = flt(rows[0].outstanding) if rows else 0.0
