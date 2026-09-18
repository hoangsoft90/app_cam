"""Delivery Confirmation (P2 / Mốc 3) - proof of delivery from the driver app.

Why this is its OWN DocType instead of custom fields on ERPNext's Delivery Note:
the proof (photos, signature, GPS, OTP, the "chờ chủ duyệt" flag and the owner
decision trail) is a business record with its own lifecycle and audit history;
putting that state on a core stock document would tie our workflow to ERPNext's
submit/cancel semantics. The app has no Delivery Note pipeline today, so this
record is the single source of truth for "what the driver proved".

Server-authoritative rules (the client is NOT trusted for these):

* `customer` is copied from the Sales Order, `driver` from the session user -
  a client-sent value is overwritten.
* `pending_owner_approval` / `status` / `otp_verified_at` are COMPUTED here from
  `confirmation_method`, never taken from the payload (the app sends them as a
  hint at most).
* Required evidence per method:
    - OTP       -> otp_code non-empty (the farmer read it out)
    - Signature -> signature_image attached
    - Photo     -> no_otp_reason non-empty AND >= 1 proof photo
  "Photo Only" is the addendum-B9 escape hatch: the driver is never blocked for
  a missing OTP, but the delivery is only PROVISIONAL until the owner approves.
* GPS: mandatory for "Photo Only" (the provisional path leans on evidence),
  optional-but-recorded for OTP/Signature so a device without a location fix
  cannot stop a normal delivery.
* One live confirmation per Sales Order (a rejected one may be re-done).

Known limits (documented, not hidden): nothing attests that the GPS coordinates
or the photo timestamp are genuine - the phone is the only witness. OTP delivery
by SMS and Delivery Note submission are still open owner decisions.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

METHOD_OTP = "OTP"
METHOD_SIGNATURE = "Signature"
METHOD_PHOTO = "Photo Only — Needs Approval"

STATUS_DONE = "Giao thành công"
STATUS_PROVISIONAL = "Giao thành công tạm"
STATUS_REJECTED = "Bị từ chối"


class DeliveryConfirmation(Document):
	def validate(self):
		self._from_sales_order()
		self._driver_is_session_user()
		self._validate_evidence()
		self._compute_state()
		self._one_live_confirmation_per_order()

	# ----------------------------------------------------------------- inputs
	def _from_sales_order(self):
		"""Customer is server truth: the client must not be able to name it."""
		if not self.sales_order:
			frappe.throw(_("Phải chọn đơn hàng (Sales Order)."))
		order = frappe.db.get_value(
			"Sales Order", self.sales_order, ["customer", "docstatus"], as_dict=True
		)
		if not order:
			frappe.throw(_("Đơn hàng {0} không tồn tại.").format(self.sales_order))
		if order.docstatus != 1:
			frappe.throw(
				_("Đơn hàng {0} chưa được duyệt (còn nháp) - không thể xác nhận giao hàng.").format(
					self.sales_order
				)
			)
		self.customer = order.customer
		if not self.delivery_date:
			self.delivery_date = frappe.db.get_value("Sales Order", self.sales_order, "delivery_date")

	def _driver_is_session_user(self):
		"""A driver cannot file a confirmation on someone else's behalf.

		Only stamped on CREATE: the owner's approve/reject also saves this doc, and
		unconditionally rewriting `driver` there would credit the Manager with a
		delivery the driver actually made (review catch, 2026-09-18).
		"""
		if self.is_new() or not self.driver:
			self.driver = frappe.session.user

	# -------------------------------------------------------------- evidence
	def _validate_evidence(self):
		method = (self.confirmation_method or "").strip()
		if method not in (METHOD_OTP, METHOD_SIGNATURE, METHOD_PHOTO):
			frappe.throw(_("Cách xác nhận không hợp lệ: {0}").format(self.confirmation_method))

		if method == METHOD_OTP:
			if not (self.otp_code or "").strip():
				frappe.throw(_("Xác nhận bằng OTP cần nhập mã OTP khách đọc."))
			return

		if method == METHOD_SIGNATURE:
			if not (self.signature_image or "").strip():
				frappe.throw(_("Xác nhận bằng chữ ký cần có ảnh chữ ký."))
			return

		# Photo Only — Needs Approval (the B9 provisional path)
		if not (self.no_otp_reason or "").strip():
			frappe.throw(
				_("Xác nhận bằng ảnh (không OTP) BẮT BUỘC ghi lý do - ví dụ: khách không nghe máy.")
			)
		if not self.proof_photos:
			frappe.throw(_("Xác nhận bằng ảnh cần ít nhất 1 ảnh bằng chứng."))
		# frappe Float fields default to 0 (not None) on a new doc, so 0 means
		# "no fix captured" here. Valid Vietnamese coordinates are never 0/0.
		if not flt(self.gps_latitude) and not flt(self.gps_longitude):
			frappe.throw(_("Xác nhận bằng ảnh cần toạ độ GPS (bật định vị rồi thử lại)."))

	# ----------------------------------------------------------------- state
	def _compute_state(self):
		"""`pending_owner_approval`/`status` are COMPUTED - never client-set.

		Once the owner has decided (`approved_at` set), the decision WINS: without
		this guard the owner's approve/reject save would re-run the method rules and
		silently undo itself (review catch, 2026-09-18).
		"""
		if self.approved_at:
			return
		method = (self.confirmation_method or "").strip()
		if method == METHOD_OTP:
			self.pending_owner_approval = 0
			self.status = STATUS_DONE
			if not self.otp_verified_at:
				self.otp_verified_at = now_datetime()
			return
		# Signature and Photo Only both count as "giao thành công tạm": the owner
		# must confirm within the day (addendum B9).
		self.pending_owner_approval = 1
		self.status = STATUS_PROVISIONAL
		self.otp_verified_at = None

	def _one_live_confirmation_per_order(self):
		existing = frappe.db.get_value(
			"Delivery Confirmation",
			{
				"sales_order": self.sales_order,
				"status": ["!=", STATUS_REJECTED],
				"name": ["!=", self.name or ""],
			},
			["name"],
			as_dict=True,
		)
		if existing:
			frappe.throw(
				_("Đơn hàng {0} đã có xác nhận giao hàng {1}.").format(
					self.sales_order, existing.name
				)
			)

	# --------------------------------------------------------- owner decision
	def apply_owner_decision(self, approve, reason=None, user=None):
		"""Called by the whitelisted API after the caller's role was checked."""
		user = user or frappe.session.user
		if approve:
			self.pending_owner_approval = 0
			self.status = STATUS_DONE
			self.reject_reason = None
		else:
			if not (reason or "").strip():
				frappe.throw(_("Từ chối xác nhận giao hàng phải ghi lý do."))
			self.pending_owner_approval = 0
			self.status = STATUS_REJECTED
			self.reject_reason = reason.strip()
		self.approved_by = user
		self.approved_at = now_datetime()
		self.save()
		return self
