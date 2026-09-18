"""Mobile API (P2) - endpoints the Cám Việt internal app calls.

Everything here is `@frappe.whitelist()`: reachable by ANY logged-in user, so
each function checks the caller's role itself (lesson #23: whitelist is not
authorisation).

Endpoints
---------
confirm_delivery(payload)            driver files a proof of delivery (idempotent)
driver_deliveries(limit)             today's orders + their confirmation state
pending_delivery_approvals(limit)    provisional deliveries waiting for the owner
approve_delivery(name)               owner accepts a provisional delivery
reject_delivery(name, reason)        owner rejects it (reason mandatory)

Idempotency: the client generates ONE `idempotency_key` per delivery and reuses
it on every retry (Mốc 4 offline queue will rely on this). A duplicate key
returns the existing record instead of creating a second one - and the DB unique
index is the backstop if two calls race.
"""

import base64
import binascii
import json

import frappe
from frappe import _

# Single source of truth for the status string: the controller owns the lifecycle,
# so api.py imports it instead of re-typing the literal (a typo here would silently
# mis-read a rejected confirmation as live).
from feed_dealer.feed_dealer.doctype.delivery_confirmation.delivery_confirmation import (
	STATUS_REJECTED,
)

MANAGER_ROLES = ("Feed Dealer Manager", "System Manager")
DELIVERY_ROLES = ("Driver", "Feed Dealer Staff", "Feed Dealer Manager", "System Manager")

MAX_PHOTOS = 6
# Client compresses before sending (image_picker maxWidth/quality); these are the
# server-side backstop, not the primary defence. The TOTAL cap matters as much as
# the per-image one: 6 x 3 MB would be a ~24 MB request over a farm-network link.
MAX_IMAGE_BYTES = 1536 * 1024
MAX_TOTAL_BYTES = 6 * 1024 * 1024


# --------------------------------------------------------------------- guards
def _roles():
	return set(frappe.get_roles(frappe.session.user))


def _require_login():
	if frappe.session.user in (None, "", "Guest"):
		frappe.throw(_("Cần đăng nhập."), frappe.PermissionError)


def _require_delivery_role():
	_require_login()
	if not (_roles() & set(DELIVERY_ROLES)):
		frappe.throw(
			_("Tài khoản của bạn không có quyền xác nhận giao hàng."), frappe.PermissionError
		)


def _require_manager():
	_require_login()
	if not (_roles() & set(MANAGER_ROLES)):
		frappe.throw(
			_("Chỉ chủ đại lý (Manager) được duyệt xác nhận giao hàng."), frappe.PermissionError
		)


# --------------------------------------------------------------------- helpers
def _as_payload(payload):
	if isinstance(payload, str):
		try:
			payload = json.loads(payload)
		except ValueError as exc:
			frappe.throw(_("Payload không phải JSON hợp lệ: {0}").format(exc))
	if not isinstance(payload, dict):
		frappe.throw(_("Payload phải là một object JSON."))
	return payload


def _photo_texts(photos):
	"""Validate the client's photo list as a list of NON-EMPTY base64 strings.

	This is raw input from an untrusted app: a dict, a number or a nested object
	used to reach `len()` / `.strip()` and come out as an unhandled 500 traceback
	instead of a message the driver can read. Refuse the shape up front (review
	catch, 2026-09-18) - nothing below has to defend itself after this.
	"""
	if not isinstance(photos, list):
		frappe.throw(_("`photos` phải là danh sách ảnh base64."))
	out = []
	for index, raw in enumerate(photos, start=1):
		if not isinstance(raw, str) or not raw.strip():
			frappe.throw(
				_("Ảnh bằng chứng {0} phải là chuỗi base64 (nhận được {1}).").format(
					index, type(raw).__name__
				)
			)
		out.append(raw)
	return out


def _normalise_image(raw, label):
	"""Validate an image payload and return it as CLEAN base64 text.

	Accepts raw base64 or a `data:image/...;base64,` data URI. The decoded bytes
	are only used to validate and to measure the size - they are NOT handed to
	frappe: `File.save_file(content=bytes)` coerces the bytes to `str` and then
	crashes in `strip_exif_data` (`Image.open(io.BytesIO(str))`), measured on the
	site 2026-09-18. Pass the base64 string + `decode=True` instead.
	"""
	if not raw:
		return None
	text = raw.strip()
	if text.startswith("data:"):
		# NEVER unpack into `_` here: it would shadow frappe's `_()` translation
		# function for the WHOLE function body, and every frappe.throw(_(...))
		# below then dies with "cannot access local variable '_'" (measured
		# 2026-09-18 via T13a - the messages only fail on the error path, so the
		# happy path kept passing while every refusal was a 500).
		_prefix, _comma, text = text.partition(",")
	try:
		# validate=True: a corrupt payload must FAIL loudly instead of decoding to
		# silently-truncated bytes (base64 discards invalid characters otherwise).
		content = base64.b64decode(text, validate=True)
	except (binascii.Error, ValueError) as exc:
		frappe.throw(_("{0}: ảnh không phải base64 hợp lệ ({1}).").format(label, exc))
	if not content:
		frappe.throw(_("{0}: ảnh rỗng.").format(label))
	if len(content) > MAX_IMAGE_BYTES:
		# NOT `//`: integer division printed the 1.5 MB cap as "1 MB", so a 1.4 MB
		# image was told to shrink below a limit it already respected.
		frappe.throw(
			_("{0}: ảnh {1:.1f} MB vượt giới hạn {2:.1f} MB - cần nén lại phía app.").format(
				label, len(content) / 1024 / 1024, MAX_IMAGE_BYTES / 1024 / 1024
			)
		)
	return text


def _normalise_total(data, photos):
	"""Refuse an oversized BATCH before decoding anything, so a slow link is not
	wasted on a payload the server would reject at the end anyway.

	Callers MUST pass `_photo_texts()` output: this only measures.
	"""
	signature = data.get("signature_png")
	payloads = [*photos, signature if isinstance(signature, str) else ""]
	total = sum(len(p) for p in payloads)
	# base64 is ~4/3 of the decoded size; compare like for like.
	decoded = total * 3 // 4
	if decoded > MAX_TOTAL_BYTES:
		frappe.throw(
			_("Tổng ảnh {0:.1f} MB vượt giới hạn {1:.0f} MB - cần nén lại phía app.").format(
				decoded / 1024 / 1024, MAX_TOTAL_BYTES / 1024 / 1024
			)
		)


def _attach(doc, filename, base64_text):
	"""Save a private file attached to `doc` and return its URL.

	Plain `File` document instead of `frappe.utils.file_manager.save_file`: the
	wrapper produced a File whose content made `strip_exif_data` fail with "a
	bytes-like object is required, not 'str'" for image/jpeg names. Both variants
	were measured on the site 2026-09-18 - this one decodes correctly (a 70-byte
	PNG landed as bytes, and the .jpg variant went through EXIF stripping).

	`decode` is an ATTRIBUTE, not a File field (Frappe v16 has no such field): it
	tilts `get_content()` into base64-decoding the payload we hand over.
	"""
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
			"is_private": 1,
			"content": base64_text,
			"decode": True,
		}
	).insert(ignore_permissions=True)
	return file_doc.file_url


def _summary(doc):
	return {
		"name": doc.name,
		"sales_order": doc.sales_order,
		"customer": doc.customer,
		"confirmation_method": doc.confirmation_method,
		"status": doc.status,
		"pending_owner_approval": int(doc.pending_owner_approval or 0),
		"driver": doc.driver,
		"delivery_note": doc.delivery_note,
	}


# ------------------------------------------------------------------- driver API
@frappe.whitelist(methods=["POST"])
def confirm_delivery(payload=None):
	"""Driver confirms a delivery. Idempotent on `idempotency_key`."""
	_require_delivery_role()
	data = _as_payload(payload)

	sales_order = (data.get("sales_order") or "").strip()
	key = (data.get("idempotency_key") or "").strip()
	if not sales_order:
		frappe.throw(_("Thiếu sales_order."))
	if not key:
		frappe.throw(_("Thiếu idempotency_key (app phải sinh khoá này một lần cho mỗi lần giao)."))

	# Serialise everything that follows for this order. Without the row lock two
	# phones (or a double tap with different keys) can both pass the "one live
	# confirmation" check and file two deliveries for one order - the same class
	# of race P1B hit with concurrent payment entries.
	if not frappe.db.get_value("Sales Order", sales_order, "name", for_update=True):
		frappe.throw(_("Đơn hàng {0} không tồn tại.").format(sales_order))

	existing = frappe.db.get_value(
		"Delivery Confirmation", {"idempotency_key": key}, "name"
	)
	if existing:
		# Retry / offline queue replay: hand back the record already stored.
		doc = frappe.get_doc("Delivery Confirmation", existing)
		out = _summary(doc)
		out["idempotent"] = True
		return out

	photos = _photo_texts(data.get("photos") or [])
	if len(photos) > MAX_PHOTOS:
		frappe.throw(_("Tối đa {0} ảnh bằng chứng mỗi lần giao.").format(MAX_PHOTOS))
	_normalise_total(data, photos)

	doc = frappe.get_doc(
		{
			"doctype": "Delivery Confirmation",
			"sales_order": sales_order,
			"confirmation_method": data.get("confirmation_method"),
			"otp_code": data.get("otp_code"),
			"no_otp_reason": data.get("no_otp_reason"),
			"notes": data.get("notes"),
			"idempotency_key": key,
			"gps_latitude": data.get("gps_latitude"),
			"gps_longitude": data.get("gps_longitude"),
			"gps_timestamp": data.get("gps_timestamp"),
		}
	)
	# Two-step on purpose: frappe needs a doc NAME before a file can be attached
	# to it, but validation ("Photo Only needs a photo") must judge the FINAL
	# state. So the first insert skips validation and the closing save() below
	# enforces every rule - measured on the site 2026-09-18: validating the
	# half-built record refused the photo path before its photo existed.
	# `customer`, `driver`, `status`, `pending_owner_approval` are computed in the
	# controller's validate() - the payload cannot set them.
	# SAVEPOINT, not a full rollback: a refusal must undo OUR work only. A bare
	# `frappe.db.rollback()` also discards anything the caller did earlier in the
	# same transaction - measured 2026-09-18: it silently reverted a config value a
	# test had just set, which made a later "kho không đủ hàng" check pass while
	# pretending to test the guard.
	frappe.db.savepoint("feed_dealer_confirm_delivery")
	doc.flags.ignore_validate = True
	doc.insert()

	try:
		signature = _normalise_image(data.get("signature_png"), _("Chữ ký"))
		if signature:
			doc.signature_image = _attach(doc, f"{doc.name}-signature.png", signature)

		for index, raw in enumerate(photos, start=1):
			content = _normalise_image(raw, _("Ảnh bằng chứng {0}").format(index))
			if not content:
				continue
			doc.append(
				"proof_photos",
				{"image": _attach(doc, f"{doc.name}-photo-{index}.jpg", content)},
			)

		# Validation runs HERE - and the flag must be RESET first: `ignore_validate`
		# sticks to the document instance, so leaving it True silently skipped every
		# rule for the whole request (measured by the acceptance suite: T1-T6, T8,
		# T11 all failed with "nothing was refused"). A rule break therefore leaves
		# nothing behind: files removed, then rollback.
		# Driver holds `create` but not `write` (a driver must not edit someone
		# else's record over REST) - this save only stores the URLs of the files
		# WE just attached to the record this same caller created.
		doc.flags.ignore_validate = False
		doc.save(ignore_permissions=True)

		# Stock document for a FINAL delivery, created inside the same transaction:
		# if the warehouse cannot cover the order this raises, the block below
		# removes the proof files and rolls back, so the driver is told the truth
		# (nothing is left claiming "giao thành công" without a Delivery Note).
		# Signature/Photo Only are provisional and wait for the owner's approval.
		doc.ensure_delivery_note()
	except Exception:
		for file_name in frappe.get_all(
			"File",
			filters={"attached_to_doctype": doc.doctype, "attached_to_name": doc.name},
			pluck="name",
		):
			try:
				frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)
			except Exception:  # noqa: BLE001 - cleanup is best effort; the rollback is the guarantee
				pass
		frappe.db.rollback(save_point="feed_dealer_confirm_delivery")
		raise

	out = _summary(doc)
	out["idempotent"] = False
	return out


@frappe.whitelist()
def driver_deliveries(limit=50):
	"""Submitted orders the driver may deliver, with the confirmation state.

	OPEN QUESTION (flagged for the owner, not decided by the agent): there is no
	per-driver assignment field yet, so EVERY Driver account sees EVERY
	outstanding order. Fine for a single-dealer pilot, but it is a data-exposure
	boundary that needs an owner decision before more drivers exist.
	"""
	_require_delivery_role()
	limit = min(int(limit or 50), 200)

	orders = frappe.get_all(
		"Sales Order",
		filters={"docstatus": 1, "status": ["not in", ["Closed", "Cancelled"]]},
		fields=["name", "customer", "customer_name", "grand_total", "delivery_date", "status"],
		order_by="delivery_date asc, name asc",
		limit_page_length=limit,
	)

	confirmations = frappe.get_all(
		"Delivery Confirmation",
		filters={"sales_order": ["in", [o.name for o in orders]] or [""]},
		fields=["name", "sales_order", "status", "pending_owner_approval", "confirmation_method"],
		limit_page_length=0,
	)
	# A rejected confirmation may be followed by a re-filed one for the SAME order
	# (`_one_live_confirmation_per_order` allows exactly that). A plain dict
	# comprehension kept whichever row came back LAST, so the driver could be shown
	# the dead rejected record and re-deliver an order that is already confirmed.
	# Live wins; the rejected one is only the fallback for the app's "re-file" hint.
	by_order = {}
	for row in confirmations:
		previous = by_order.get(row.sales_order)
		if previous is None or (previous.status == STATUS_REJECTED and row.status != STATUS_REJECTED):
			by_order[row.sales_order] = row

	rows = []
	for order in orders:
		row = dict(order)
		confirmation = by_order.get(order.name)
		row["confirmation"] = confirmation.name if confirmation else None
		row["confirmation_status"] = confirmation.status if confirmation else None
		row["pending_owner_approval"] = int(confirmation.pending_owner_approval or 0) if confirmation else 0
		rows.append(row)
	return rows


# -------------------------------------------------------------------- owner API
@frappe.whitelist()
def pending_delivery_approvals(limit=50):
	"""Provisional deliveries ('giao thành công tạm') waiting for the owner."""
	_require_manager()
	limit = min(int(limit or 50), 200)
	rows = frappe.get_all(
		"Delivery Confirmation",
		filters={"pending_owner_approval": 1},
		fields=[
			"name",
			"sales_order",
			"customer",
			"driver",
			"confirmation_method",
			"no_otp_reason",
			"status",
			"delivery_date",
			"gps_latitude",
			"gps_longitude",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=limit,
	)
	for row in rows:
		# `pluck` already returns plain URLs - do NOT reach for `.image` on them
		# (measured: AttributeError: 'str' object has no attribute 'image').
		row["proof_photos"] = frappe.get_all(
			"Delivery Proof Photo",
			filters={"parent": row.name},
			pluck="image",
			limit_page_length=0,
		)
	return rows


@frappe.whitelist(methods=["POST"])
def approve_delivery(name=None):
	"""Owner accepts a provisional (Photo Only) delivery.

	Only a PROVISIONAL record can be approved: a rejected one must be re-filed by
	the driver (otherwise an approval would silently resurrect a delivery the
	owner already disputed), and an already-final one is returned as-is so a
	double tap is harmless.
	"""
	_require_manager()
	if not name:
		frappe.throw(_("Thiếu tên xác nhận giao hàng."))
	doc = frappe.get_doc("Delivery Confirmation", name)
	if doc.status == "Bị từ chối":
		frappe.throw(
			_("Xác nhận {0} đã bị từ chối - tài xế phải xác nhận lại, không duyệt lại được.").format(name)
		)
	if not doc.pending_owner_approval:
		return _summary(doc)  # already final: nothing to do, no state change
	return _summary(doc.apply_owner_decision(approve=True))


@frappe.whitelist(methods=["POST"])
def reject_delivery(name=None, reason=None):
	"""Owner rejects a provisional delivery (reason mandatory)."""
	_require_manager()
	if not name:
		frappe.throw(_("Thiếu tên xác nhận giao hàng."))
	doc = frappe.get_doc("Delivery Confirmation", name)
	if doc.status == STATUS_REJECTED:
		# A retry (double tap / dropped link) must not raise at the owner: the
		# delivery is already rejected, which is what they wanted. Mirrors
		# approve_delivery's no-op behaviour.
		return _summary(doc)
	if not doc.pending_owner_approval:
		frappe.throw(
			_("Chỉ từ chối được xác nhận đang chờ duyệt (trạng thái hiện tại: {0}).").format(doc.status)
		)
	return _summary(doc.apply_owner_decision(approve=False, reason=reason))
