"""P2 Mốc 3 acceptance: Delivery Confirmation (proof of delivery, driver app).

Run:      bench --site <site> execute feed_dealer.setup.p2_delivery_acceptance.run
Debug:    bench --site <site> execute feed_dealer.setup.p2_delivery_acceptance.debug
Cleanup:  bench --site <site> execute feed_dealer.setup.p2_delivery_acceptance.cleanup

Every check goes through the SAME door the mobile app uses
(`feed_dealer.api.confirm_delivery` / `approve_delivery` / `reject_delivery`),
under the real role of the caller - so a permission or validation hole shows up
here instead of on a driver's phone.

  T1  OTP confirmation -> status "Giao thành công", no owner approval needed,
      otp_verified_at stamped, customer/driver taken from the server
  T2  a client that FORCES customer / driver / status / pending_owner_approval
      cannot change them: the server recomputes every one of those fields
  T3  Photo Only without `no_otp_reason` is refused (the B9 rule)
  T4  Photo Only with a reason but no photo is refused
  T5  Photo Only without a GPS fix is refused
  T6  Photo Only with reason + photo + GPS -> "Giao thành công tạm",
      pending_owner_approval=1, the photo really attached (File row exists)
  T7  idempotency: the same key twice -> ONE record, second call in_idempotent
  T8  a second, different-key confirmation for the same order is refused
  T9  owner decision: Driver calling approve -> PermissionError; Manager approve
      clears the flag; reject without a reason is refused; reject with a reason
      stores it
  T10 GPS coordinates are recorded on the OTP path too (recorded, not required)
  T11 a DRAFT order cannot be confirmed (nothing delivered before approval)

Fixtures: customer + submitted Sales Order under the P2-DELIVERY-ACCEPT prefix,
plus two acceptance-only accounts (Driver, Manager). `run()`/`debug()` clean up
after themselves, so every assertion is absolute.
"""

import base64
import json
import traceback

import frappe

from feed_dealer.api import (
	approve_delivery,
	confirm_delivery,
	pending_delivery_approvals,
	reject_delivery,
)
from feed_dealer.setup.p1b_acceptance import Report
from feed_dealer.setup.p1c_acceptance import _order, _set_limit

PREFIX = "P2-DELIVERY-ACCEPT"
DRIVER_EMAIL = "p2-delivery-accept-driver@example.com"
MANAGER_EMAIL = "p2-delivery-accept-manager@example.com"

# 1x1 PNG - enough to prove the file really lands in the File table.
PNG_1PX = (
	"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/"
	"q842iQAAAABJRU5ErkJggg=="
)


# ------------------------------------------------------------------- fixtures
class _As:
	"""Run the body as another user, always restoring the session user."""

	def __init__(self, user):
		self.user = user

	def __enter__(self):
		self.previous = frappe.session.user
		frappe.set_user(self.user)
		return self

	def __exit__(self, *_exc):
		frappe.set_user(self.previous)
		return False


def _users():
	"""Acceptance-only accounts (idempotent; never deletes real users)."""
	for email, role, first_name in (
		(DRIVER_EMAIL, "Driver", "P2 Delivery Driver"),
		(MANAGER_EMAIL, "Feed Dealer Manager", "P2 Delivery Manager"),
	):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first_name,
					"send_welcome_email": 0,
					"user_type": "Website User",
					"roles": [{"role": role}],
				}
			).insert(ignore_permissions=True)
		elif not frappe.db.exists("Has Role", {"parent": email, "role": role}):
			user = frappe.get_doc("User", email)
			user.append("roles", {"role": role})
			user.save(ignore_permissions=True)
	frappe.db.commit()
	return DRIVER_EMAIL, MANAGER_EMAIL


def _customers():
	return frappe.get_all(
		"Customer", filters={"customer_name": ["like", f"{PREFIX}%"]}, pluck="name"
	)


def _customer(tag):
	name = f"{PREFIX} {tag}"
	if not frappe.db.exists("Customer", name):
		group = frappe.db.get_value("Customer Group", "Trại lớn") or frappe.db.get_value(
			"Customer Group", "All Customer Groups"
		)
		frappe.get_doc(
			{"doctype": "Customer", "customer_name": name, "customer_group": group}
		).insert(ignore_permissions=True)
	# The credit gate runs on SO submit; give every acceptance customer headroom.
	_set_limit(name, 20_000_000, reason=f"{PREFIX} fixture limit")
	frappe.db.commit()
	return name


def _payload(order, key, method, **extra):
	body = {
		"sales_order": order.name,
		"confirmation_method": method,
		"idempotency_key": key,
	}
	body.update(extra)
	return json.dumps(body)


def _confirmation_of(order_name):
	name = frappe.db.get_value("Delivery Confirmation", {"sales_order": order_name}, "name")
	return frappe.get_doc("Delivery Confirmation", name) if name else None


def _reject(fn, expect, order=None):
	"""Require a refusal that mentions `expect` AND leaves nothing persisted."""
	order_name = order.name if order is not None else None
	try:
		fn()
	except Exception as exc:  # noqa: BLE001 - the message IS the evidence
		message = str(exc)
		if expect not in message:
			raise AssertionError(f"rejected, but not for the expected reason ({expect}): {message[:250]}")
		if order_name:
			left = frappe.db.count("Delivery Confirmation", {"sales_order": order_name})
			if left:
				raise AssertionError(f"a refused confirmation left {left} row(s) behind")
		return message
	raise AssertionError(f"expected a rejection mentioning {expect!r}, nothing was refused")


def _key(tag):
	return f"{PREFIX}-{tag}-{frappe.generate_hash(length=8)}"


# --------------------------------------------------------------------- checks
def _t1_otp(report):
	order = _order(_customer("OTP"), 100_000, submit=True)
	key = _key("otp")
	with _As(DRIVER_EMAIL):
		result = confirm_delivery(_payload(order, key, "OTP", otp_code="123456"))

	def check():
		if result.get("idempotent"):
			raise AssertionError("first call must not be reported as a replay")
		doc = frappe.get_doc("Delivery Confirmation", result["name"])
		if doc.status != "Giao thành công" or doc.pending_owner_approval:
			raise AssertionError(f"OTP path must be final: status={doc.status} pending={doc.pending_owner_approval}")
		if not doc.otp_verified_at:
			raise AssertionError("otp_verified_at was not stamped")
		if doc.customer != order.customer:
			raise AssertionError(f"customer must come from the Sales Order: {doc.customer}")
		if doc.driver != DRIVER_EMAIL:
			raise AssertionError(f"driver must be the session user: {doc.driver}")
		return f"{doc.name} status={doc.status} driver={doc.driver}"

	report.check("T1 OTP confirm -> final, server-owned customer/driver", check)


def _t2_client_cannot_force(report):
	order = _order(_customer("FORCE"), 100_000, submit=True)
	key = _key("force")
	with _As(DRIVER_EMAIL):
		result = confirm_delivery(
			_payload(
				order,
				key,
				"Photo Only — Needs Approval",
				no_otp_reason="khách không nghe máy",
				gps_latitude=10.123456,
				gps_longitude=106.654321,
				photos=[PNG_1PX],
				# Everything below is a lie the server must ignore.
				customer="HACK-CUSTOMER",
				driver="Administrator",
				status="Giao thành công",
				pending_owner_approval=0,
			)
		)

	def check():
		doc = frappe.get_doc("Delivery Confirmation", result["name"])
		if doc.customer != order.customer:
			raise AssertionError(f"client forced customer: {doc.customer}")
		if doc.driver != DRIVER_EMAIL:
			raise AssertionError(f"client forced driver: {doc.driver}")
		if doc.status != "Giao thành công tạm" or not doc.pending_owner_approval:
			raise AssertionError(
				f"client forced status/pending: {doc.status} / {doc.pending_owner_approval}"
			)
		return "customer, driver, status and pending flag all recomputed server-side"

	report.check("T2 client-forced fields are ignored", check)


def _t3_photo_without_reason(report):
	order = _order(_customer("REASON"), 100_000, submit=True)
	key = _key("noreason")

	def attempt():
		with _As(DRIVER_EMAIL):
			confirm_delivery(
				_payload(
					order,
					key,
					"Photo Only — Needs Approval",
					no_otp_reason="   ",
					gps_latitude=10.1,
					gps_longitude=106.1,
					photos=[PNG_1PX],
				)
			)

	report.check(
		"T3 Photo Only without reason -> refused",
		lambda: _reject(attempt, "BẮT BUỘC ghi lý do", order),
	)


def _t4_photo_without_photo(report):
	order = _order(_customer("NOPHOTO"), 100_000, submit=True)

	def attempt():
		with _As(DRIVER_EMAIL):
			confirm_delivery(
				_payload(
					order,
					_key("nophoto"),
					"Photo Only — Needs Approval",
					no_otp_reason="khách không nghe máy",
					gps_latitude=10.1,
					gps_longitude=106.1,
				)
			)

	report.check("T4 Photo Only without a photo -> refused", lambda: _reject(attempt, "ảnh bằng chứng", order))


def _t5_photo_without_gps(report):
	order = _order(_customer("NOGPS"), 100_000, submit=True)

	def attempt():
		with _As(DRIVER_EMAIL):
			confirm_delivery(
				_payload(
					order,
					_key("nogps"),
					"Photo Only — Needs Approval",
					no_otp_reason="khách không nghe máy",
					photos=[PNG_1PX],
				)
			)

	report.check("T5 Photo Only without GPS -> refused", lambda: _reject(attempt, "toạ độ GPS", order))


def _t6_photo_happy_path(report):
	order = _order(_customer("PHOTO"), 100_000, submit=True)
	with _As(DRIVER_EMAIL):
		result = confirm_delivery(
			_payload(
				order,
				_key("photo"),
				"Photo Only — Needs Approval",
				no_otp_reason="khách không nghe máy",
				gps_latitude=10.776889,
				gps_longitude=106.700806,
				photos=[PNG_1PX],
			)
		)

	def check():
		doc = frappe.get_doc("Delivery Confirmation", result["name"])
		if doc.status != "Giao thành công tạm" or not doc.pending_owner_approval:
			raise AssertionError(f"provisional state wrong: {doc.status} / {doc.pending_owner_approval}")
		if doc.no_otp_reason != "khách không nghe máy":
			raise AssertionError(f"reason not stored: {doc.no_otp_reason!r}")
		photos = frappe.get_all("Delivery Proof Photo", filters={"parent": doc.name}, pluck="image")
		if len(photos) != 1:
			raise AssertionError(f"expected 1 attached photo row, got {len(photos)}")
		if not frappe.db.exists("File", {"file_url": photos[0]}):
			raise AssertionError(f"no File row for {photos[0]}")
		return f"{doc.name} pending={doc.pending_owner_approval} photo={photos[0]}"

	report.check("T6 Photo Only + reason + photo + GPS -> provisional, photo attached", check)


def _t7_idempotent(report):
	order = _order(_customer("IDEM"), 100_000, submit=True)
	key = _key("idem")
	payload = _payload(order, key, "OTP", otp_code="999111")
	with _As(DRIVER_EMAIL):
		first = confirm_delivery(payload)
		second = confirm_delivery(payload)

	def check():
		if first["name"] != second["name"]:
			raise AssertionError(f"replay created a second record: {first['name']} vs {second['name']}")
		if not second.get("idempotent"):
			raise AssertionError("the replay was not reported as idempotent")
		count = frappe.db.count("Delivery Confirmation", {"idempotency_key": key})
		if count != 1:
			raise AssertionError(f"expected 1 record for the key, found {count}")
		return f"{first['name']} (same record, replay flagged)"

	report.check("T7 same key twice -> ONE record", check)


def _t8_second_confirmation_refused(report):
	order = _order(_customer("DUPE"), 100_000, submit=True)
	with _As(DRIVER_EMAIL):
		confirm_delivery(_payload(order, _key("dupe-a"), "OTP", otp_code="111222"))

	def attempt():
		with _As(DRIVER_EMAIL):
			confirm_delivery(_payload(order, _key("dupe-b"), "OTP", otp_code="333444"))

	report.check(
		"T8 second confirmation for the same order -> refused",
		lambda: _reject(attempt, "đã có xác nhận giao hàng"),
	)


def _t9_owner_decision(report):
	order = _order(_customer("APPROVE"), 100_000, submit=True)
	with _As(DRIVER_EMAIL):
		created = confirm_delivery(
			_payload(
				order,
				_key("approve"),
				"Signature",
				signature_png=PNG_1PX,
				no_otp_reason="khách ký tay",
				gps_latitude=10.5,
				gps_longitude=106.5,
			)
		)

	def driver_cannot_approve():
		with _As(DRIVER_EMAIL):
			approve_delivery(created["name"])

	def manager_approves():
		with _As(MANAGER_EMAIL):
			approve_delivery(created["name"])
		doc = frappe.get_doc("Delivery Confirmation", created["name"])
		if doc.pending_owner_approval or doc.status != "Giao thành công":
			raise AssertionError(f"approve did not clear the flag: {doc.status} / {doc.pending_owner_approval}")
		if doc.approved_by != MANAGER_EMAIL or not doc.approved_at:
			raise AssertionError(f"approver not recorded: {doc.approved_by} / {doc.approved_at}")
		return f"approved_by={doc.approved_by}"

	def reject_without_reason():
		with _As(MANAGER_EMAIL):
			reject_delivery(created["name"], reason="  ")

	def reject_with_reason():
		with _As(MANAGER_EMAIL):
			reject_delivery(created["name"], reason="ảnh không thấy hàng")
		doc = frappe.get_doc("Delivery Confirmation", created["name"])
		if doc.status != "Bị từ chối" or not doc.reject_reason or doc.pending_owner_approval:
			raise AssertionError(f"reject not stored: {doc.status} / {doc.reject_reason!r}")
		return f"status={doc.status} reason={doc.reject_reason!r}"

	def pending_list_visible():
		# A fresh provisional delivery must show up in the owner's queue.
		other = _order(_customer("QUEUE"), 100_000, submit=True)
		with _As(DRIVER_EMAIL):
			confirm_delivery(
				_payload(
					other,
					_key("queue"),
					"Photo Only — Needs Approval",
					no_otp_reason="khách không nghe máy",
					gps_latitude=10.2,
					gps_longitude=106.2,
					photos=[PNG_1PX],
				)
			)
		with _As(MANAGER_EMAIL):
			rows = pending_delivery_approvals()
		names = [row["name"] for row in rows]
		if created["name"] in names:
			raise AssertionError("a rejected delivery is still in the pending queue")
		if not any(row["sales_order"] == other.name for row in rows):
			raise AssertionError("the provisional delivery is missing from the owner queue")
		return f"queue has {len(rows)} provisional row(s)"

	report.check(
		"T9a Driver cannot approve (permission)",
		lambda: _reject(driver_cannot_approve, "Chỉ chủ đại lý"),
	)
	report.check("T9b Manager approve clears pending + records approver", manager_approves)
	report.check(
		"T9c reject without a reason -> refused",
		lambda: _reject(reject_without_reason, "phải ghi lý do"),
	)
	report.check("T9d reject with a reason stores it", reject_with_reason)
	report.check("T9e owner queue shows provisional deliveries", pending_list_visible)


def _t10_gps_recorded_on_otp(report):
	order = _order(_customer("GPSOK"), 100_000, submit=True)
	with _As(DRIVER_EMAIL):
		result = confirm_delivery(
			_payload(
				order,
				_key("gpsok"),
				"OTP",
				otp_code="555666",
				gps_latitude=9.176,
				gps_longitude=105.15,
			)
		)

	def check():
		doc = frappe.get_doc("Delivery Confirmation", result["name"])
		if round(doc.gps_latitude, 3) != 9.176 or round(doc.gps_longitude, 3) != 105.15:
			raise AssertionError(f"GPS not stored: {doc.gps_latitude}/{doc.gps_longitude}")
		return f"gps={doc.gps_latitude},{doc.gps_longitude}"

	report.check("T10 GPS recorded on the OTP path", check)


def _t11_draft_order_refused(report):
	order = _order(_customer("DRAFT"), 100_000, submit=False)

	def attempt():
		with _As(DRIVER_EMAIL):
			confirm_delivery(_payload(order, _key("draft"), "OTP", otp_code="111111"))

	report.check(
		"T11 draft order cannot be confirmed",
		lambda: _reject(attempt, "chưa được duyệt", order),
	)


CHECKS = (
	("T1", _t1_otp),
	("T2", _t2_client_cannot_force),
	("T3", _t3_photo_without_reason),
	("T4", _t4_photo_without_photo),
	("T5", _t5_photo_without_gps),
	("T6", _t6_photo_happy_path),
	("T7", _t7_idempotent),
	("T8", _t8_second_confirmation_refused),
	("T9", _t9_owner_decision),
	("T10", _t10_gps_recorded_on_otp),
	("T11", _t11_draft_order_refused),
)


# ---------------------------------------------------------------------- runner
def run():
	_users()
	cleanup()
	report = Report()
	for _name, check in CHECKS:
		check(report)
	text, failed = report.render()
	print(text)
	if failed:
		print(f"\nP2 DELIVERY ACCEPTANCE: FAIL ({len(failed)})")
		return {"failed": failed}
	print("\nP2 DELIVERY ACCEPTANCE: ALL PASS")
	return {"failed": []}


def debug():
	"""Run every check independently and print the full traceback of failures."""
	failed = 0
	for name, check in CHECKS:
		report = Report()
		try:
			check(report)
			rows = report.render()[0]
			print(rows)
			if report.rows and not report.rows[0][1]:
				raise AssertionError(report.rows[0][2])
			print(f"===== PASS {name}")
		except Exception:  # noqa: BLE001 - debug mode prints everything
			failed += 1
			print(f"\n===== FAIL {name} =====")
			traceback.print_exc()
	print(f"\n[debug] {len(CHECKS) - failed}/{len(CHECKS)} checks passed")
	return {"failed": failed}


def cleanup():
	"""Remove P2 delivery fixtures: confirmations (+ files), orders, customers."""
	removed = []
	customers = _customers()
	orders = frappe.get_all(
		"Sales Order", filters={"customer": ["in", customers or [""]]}, pluck="name"
	)
	confirmations = frappe.get_all(
		"Delivery Confirmation", filters={"sales_order": ["in", orders or [""]]}, pluck="name"
	)

	for name in confirmations:
		for file_name in frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Delivery Confirmation", "attached_to_name": name},
			pluck="name",
		):
			try:
				frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)
			except Exception as exc:  # noqa: BLE001
				removed.append(f"File {file_name} FAILED: {exc}")
		try:
			frappe.delete_doc("Delivery Confirmation", name, force=True, ignore_permissions=True)
			removed.append(f"Delivery Confirmation {name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Delivery Confirmation {name} FAILED: {exc}")

	for row in frappe.get_all(
		"Sales Order", filters={"customer": ["in", customers or [""]]}, fields=["name", "docstatus"]
	):
		try:
			doc = frappe.get_doc("Sales Order", row.name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Sales Order", row.name, force=True, ignore_permissions=True)
			removed.append(f"Sales Order {row.name}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Sales Order {row.name} FAILED: {exc}")

	for customer in customers:
		for name in frappe.get_all("Credit Score", filters={"customer": customer}, pluck="name"):
			try:
				frappe.delete_doc("Credit Score", name, force=True, ignore_permissions=True)
			except Exception as exc:  # noqa: BLE001
				removed.append(f"Credit Score {name} FAILED: {exc}")
		try:
			frappe.delete_doc("Customer", customer, force=True, ignore_permissions=True)
			removed.append(f"Customer {customer}")
		except Exception as exc:  # noqa: BLE001
			removed.append(f"Customer {customer} FAILED: {exc}")

	frappe.db.commit()
	print(f"[feed_dealer] cleaned {len(removed)} fixture row(s)")
	return removed
