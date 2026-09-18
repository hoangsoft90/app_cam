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
* The Delivery Note is created + submitted by THIS record, never by the app:
  immediately when the driver's confirmation is FINAL (OTP/Signature), or when
  the owner approves a provisional (Photo Only) one. Rejecting never creates
  stock movement. If the warehouse cannot cover the order the whole action
  fails - a record must never claim "giao thành công" with no stock document.

Known limits (documented, not hidden): nothing attests that the GPS coordinates
or the photo timestamp are genuine - the phone is the only witness. OTP delivery
by SMS and Delivery Note submission are still open owner decisions.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

# `make_delivery_note` lives with the Sales Order controller in ERPNext v16; the
# mapping (pending qty per line, taxes, warehouse) is core behaviour we must not
# re-implement.

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

	# ------------------------------------------------------------- stock side
	def build_delivery_note(self):
		"""Create + SUBMIT the Delivery Note for this order and return its name.

		Uses ERPNext's own `make_delivery_note` so pending quantities, taxes and the
		SO link come from core (never re-mapped by hand), then stamps the pilot's
		`default_warehouse` from Feed Dealer Settings - decided in P0, so no new
		warehouse is invented here.

		Raises when the warehouse cannot cover the order: the caller's transaction
		must fail as a whole rather than leave a "delivered" claim with no stock
		document behind it.
		"""
		from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

		# The mapping needs a caller that may READ the order and CREATE a stock
		# document. A Driver may do NEITHER - measured on the site 2026-09-18: the
		# Driver role is All/Driver/Guest only, `has_permission("Delivery Note",
		# "create")` is False, and `frappe.flags.ignore_permissions` is NOT consulted
		# by has_permission, so flags cannot help. Filing the stock document is the
		# SERVER's job, so the build runs as the server; the record keeps the driver
		# as `owner` for audit.
		filed_by = frappe.session.user
		frappe.set_user("Administrator")
		try:
			dn = make_delivery_note(self.sales_order)
		except Exception as exc:  # noqa: BLE001 - surface the core reason in Vietnamese
			frappe.throw(
				_("Không tạo được phiếu xuất kho cho đơn {0}: {1}").format(self.sales_order, exc)
			)
		finally:
			frappe.set_user(filed_by)
		# Audit trail: the person who filed the delivery, not the server account.
		dn.owner = filed_by
		dn.modified_by = filed_by

		warehouse = frappe.db.get_single_value("Feed Dealer Settings", "default_warehouse")
		if warehouse:
			# `set_warehouse` is the doctype's own field: it re-stamps every line that
			# has no warehouse of its own, which is what a SO line with no Item Default
			# looks like. Without it ERPNext refuses the stock lines on validate.
			dn.set_warehouse = warehouse
		dn.flags.ignore_permissions = True
		frappe.db.savepoint("feed_dealer_delivery_note")
		try:
			dn.insert()
			dn.submit()
		except Exception as exc:  # noqa: BLE001
			# A refused submit can already have written stock ledger entries before it
			# hit the guard - measured: the bin moved by the refused quantity when the
			# CALLER caught the error, i.e. outside frappe's request-level rollback. A
			# savepoint makes a refusal leave nothing behind, whatever the caller does
			# with the exception.
			frappe.db.rollback(save_point="feed_dealer_delivery_note")
			frappe.throw(
				_("Không xuất được kho cho đơn {0} (kiểm tra tồn kho): {1}").format(
					self.sales_order, exc
				)
			)
		return dn.name

	def ensure_delivery_note(self):
		"""Idempotent: return the linked Delivery Note, creating it for FINAL records.

		Returns None for a provisional or rejected record (nothing may leave the
		warehouse before the owner accepts a provisional delivery).
		"""
		if self.delivery_note:
			return self.delivery_note
		if self.status != STATUS_DONE:
			return None
		name = self.build_delivery_note()
		# db_set, not save(): re-running validate() here is exactly what used to
		# silently undo an owner decision (see _compute_state's approved_at guard).
		self.db_set("delivery_note", name)
		self.delivery_note = name
		return name

	# --------------------------------------------------------- owner decision
	def apply_owner_decision(self, approve, reason=None, user=None):
		"""Called by the whitelisted API after the caller's role was checked."""
		user = user or frappe.session.user
		if approve:
			# Stock document FIRST: if the warehouse cannot cover it, this raises and
			# the approval never lands, so the driver's record stays provisional
			# instead of claiming a delivery we could not actually ship.
			self.delivery_note = self.delivery_note or self.build_delivery_note()
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
