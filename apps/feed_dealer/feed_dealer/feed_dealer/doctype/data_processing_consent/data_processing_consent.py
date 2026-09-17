"""Data Processing Consent (P1F): consent once, withdraw forever.

Policy (design.md D21): the app WARNS, it does not block. Blocking Customer
creation or ordering would break the import/API paths that have no consent yet
(and the P0-P1D suites create customers directly), so the gate is a message plus
a queryable API the later phases read:

  * `has_active_consent(customer)`  -- is there an unwithdrawn consent
  * `marketing_allowed(customer)`   -- the SMS/Zalo scope specifically; Phase 3+
    must call this before sending anything promotional.

A consent record is append-only in spirit: withdrawing sets `withdrawn` +
`withdrawn_date`, and the record cannot be flipped back -- a new record IS the
audit trail. `has_active_consent` therefore reads the LATEST record for the
customer (and the per-scope row inside it), never "any record ever granted".
"""

import frappe
from frappe.model.document import Document
from frappe.utils import now

MARKETING_SCOPE = "Thông báo SMS/Zalo"
DEFAULT_CONSENT_TEXT = (
	"Khách hàng đồng ý cho chúng tôi thu thập và xử lý dữ liệu phục vụ quản lý "
	"công nợ và chăm sóc khách hàng."
)


def has_active_consent(customer, scope=None):
	"""True when the customer's LATEST consent (for `scope`) is still in force."""
	if not customer:
		return False
	filters = {"customer": customer}
	if scope:
		parents = frappe.get_all(
			"Consent Scope Item",
			filters={"parenttype": "Data Processing Consent", "scope": scope},
			pluck="parent",
		)
		if not parents:
			return False
		filters["name"] = ["in", set(parents)]
	latest = frappe.get_all(
		"Data Processing Consent",
		filters=filters,
		fields=["name", "withdrawn"],
		order_by="creation desc",
		limit=1,
	)
	if not latest or latest[0].withdrawn:
		return False
	if scope and frappe.get_all(
		"Consent Scope Item",
		filters={"parent": latest[0].name, "scope": scope, "granted": 0},
		limit=1,
	):
		return False
	return True


def marketing_allowed(customer):
	"""The gate Phase 3+ (Zalo/SMS) must read: withdrawn or missing scope = no sending."""
	return has_active_consent(customer, scope=MARKETING_SCOPE)


class DataProcessingConsent(Document):
	def validate(self):
		if not self.customer:
			frappe.throw("Phải chọn khách hàng cho phiếu đồng ý dữ liệu.")
		if not self.consent_type:
			frappe.throw("Phải chọn loại đồng ý dữ liệu.")
		if not self.consent_scope:
			# The consent type is the coarse scope; the table records the fine-grained
			# list (a customer may grant debt management and refuse marketing).
			self.append("consent_scope", {"scope": self.consent_type, "granted": 1})
		self.consent_date = self.consent_date or now()
		self.consent_text = self.consent_text or DEFAULT_CONSENT_TEXT
		if self.withdrawn:
			self.withdrawn_date = self.withdrawn_date or now()
		else:
			if not self.is_new() and frappe.db.get_value(
				"Data Processing Consent", self.name, "withdrawn"
			):
				frappe.throw(
					"Phiếu đồng ý này đã bị rút và không thể bật lại — tạo phiếu mới để lưu vết.",
					title="Không thể bật lại đồng ý",
				)
			self.withdrawn_date = None


@frappe.whitelist()
def withdraw(customer, consent_type="Quản lý nợ", reason=None):
	"""Withdraw consent by APPENDING a withdrawn record (never editing history)."""
	doc = frappe.get_doc(
		{
			"doctype": "Data Processing Consent",
			"customer": customer,
			"consent_type": consent_type,
			"granted_via": "API rút đồng ý",
			"consent_text": reason or "Khách hàng rút đồng ý xử lý dữ liệu.",
			"withdrawn": 1,
			"consent_scope": [{"scope": consent_type, "granted": 0}],
		}
	)
	doc.insert()  # insert() enforces create permission for the calling user
	return {"name": doc.name, "marketing_allowed": marketing_allowed(customer)}
