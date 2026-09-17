"""Install the app's Jinja print formats (P1F).

A patch rather than a fixture file so it is idempotent and obviously one-off: each
format is upserted by name, and a format the USER created (custom_format=1 but owned
elsewhere) is never touched — we only own the ones we ship under these exact names.

`Phiếu xác nhận nợ (feed_dealer)` renders a Debt Confirmation Slip: the row-by-row
allocation view the customer signs, plus the confirmed total.
"""

import frappe

DEBT_SLIP_FORMAT = "Phiếu xác nhận nợ (feed_dealer)"

DEBT_SLIP_HTML = """<div style="font-family: sans-serif; font-size: 12px">
  <h3 style="margin: 0 0 8px">PHIẾU XÁC NHẬN NỢ</h3>
  <p style="margin: 0 0 4px">Khách hàng: <b>{{ doc.customer }}</b></p>
  <p style="margin: 0 0 4px">Ngày xác nhận: {{ doc.confirmation_date }}</p>
  <p style="margin: 0 0 8px">Kỳ đối chiếu: {{ doc.period_from or "…" }} → {{ doc.period_to or "…" }}</p>
  <table border="1" cellspacing="0" cellpadding="4" style="border-collapse: collapse">
    <tr>
      <th align="left">Lứa nuôi</th>
      <th align="left">Khoản nợ</th>
      <th align="left">Hạn thanh toán</th>
      <th align="right">Đã trả</th>
      <th align="right">Còn nợ</th>
      <th align="right">Quá hạn (ngày)</th>
    </tr>
    {% for row in doc.debt_details %}
    <tr>
      <td>{{ row.batch }}</td>
      <td>{{ row.batch_debt }}</td>
      <td>{{ row.due_date or "" }}</td>
      <td align="right">{{ "{:,.0f}".format(row.paid_amount or 0) }}</td>
      <td align="right">{{ "{:,.0f}".format(row.outstanding_amount or 0) }}</td>
      <td align="right">{{ row.overdue_days or 0 }}</td>
    </tr>
    {% endfor %}
  </table>
  <p style="margin-top: 8px">Tổng nợ xác nhận: <b>{{ "{:,.0f}".format(doc.total_confirmed_debt or 0) }}</b></p>
  <p>Khách hàng xác nhận số nợ trên là đúng: ____________________</p>
  {% if doc.qr_code %}<p>QR: {{ doc.qr_code }}</p>{% endif %}
</div>"""

FORMATS = {
	DEBT_SLIP_FORMAT: {"doc_type": "Debt Confirmation Slip", "html": DEBT_SLIP_HTML},
}


def execute():
	installed = []
	for name, spec in FORMATS.items():
		if frappe.db.exists("Print Format", name):
			doc = frappe.get_doc("Print Format", name)
			if doc.get("custom_format") and doc.get("module"):
				# Owned by another app/module: leave it alone.
				continue
		else:
			doc = frappe.new_doc("Print Format")
			doc.name = name
		doc.doc_type = spec["doc_type"]
		doc.print_format_type = "Jinja"
		doc.custom_format = 1
		doc.standard = "No"
		doc.html = spec["html"]
		doc.flags.ignore_permissions = True
		doc.save()
		installed.append(name)
	print(f"[install_print_formats] installed/updated: {installed or 'nothing'}")
