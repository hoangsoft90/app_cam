"""Consent gate (P1F): warn when a customer has no in-force data-processing consent.

Policy (design.md D21): WARN, do not block. Customer creation happens from imports,
APIs and the Desk, and hardening it into a hard stop would break the very migration
paths the project needs; the enforceable gate is `marketing_allowed(customer)`, which
Phase 3+ must call before sending anything promotional.

The warning is emitted on `validate`, so it shows while the record is being saved —
non-fatal, and never on a read.
"""

import frappe


def on_customer_validate(doc, method=None):
	if doc.get("disabled"):
		return
	if not doc.name:
		return
	# Imported lazily: keeping doctype-controller imports out of module scope is the
	# rule for the controllers themselves; this function only needs the helper.
	from feed_dealer.feed_dealer.doctype.data_processing_consent import data_processing_consent as consent

	if consent.has_active_consent(doc.name):
		return
	frappe.msgprint(
		(
			f"Khách hàng {doc.name} chưa có 'Phiếu đồng ý xử lý dữ liệu' còn hiệu lực. Hệ thống vẫn "
			f"lưu (policy: cảnh báo, không chặn), nhưng KHÔNG được gửi tin quảng cáo cho khách này "
			f"cho tới khi có đồng ý."
		),
		title="Thiếu đồng ý xử lý dữ liệu",
		indicator="orange",
		alert=True,
	)
