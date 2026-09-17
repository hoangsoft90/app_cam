"""Hạn mức tín dụng — Script Report over the P1C credit gate itself.

Every number is read from `feed_dealer.credit_limit.credit_position()`, the one
implementation of the rule the Sales Order gate enforces. A SQL re-implementation
would drift: the gate also counts orders that are submitted but not yet invoiced
and drafts that are holding limit, so a debt-only query would show the owner more
available credit than the gate will actually allow.

Read-only: `credit_position` takes no locks and writes nothing, so opening the
report can never move a customer's limit.
"""

import frappe
from frappe import _
from frappe.utils import flt

from feed_dealer.credit_limit import credit_position


def execute(filters=None):
	columns = _columns()
	rows = []
	for customer in _customers(filters):
		position = credit_position(customer)
		limit = flt(position["approved_limit"])
		credit = frappe.db.get_value("Credit Score", {"customer": customer}, ["tier", "score"], as_dict=True)
		rows.append(
			frappe._dict(
				{
					"customer": customer,
					"has_credit_score": 1 if position["has_credit_score"] else 0,
					"tier": (credit.tier if credit else None),
					"score": (credit.score if credit else None),
					"approved_limit": limit,
					"outstanding_debt": flt(position["outstanding_debt"]),
					"submitted_uninvoiced_orders": flt(position["submitted_uninvoiced_orders"]),
					"draft_orders": flt(position["draft_orders"]),
					"committed": flt(position["committed"]),
					"available": flt(position["available"]),
					"used_percent": round(flt(position["committed"]) / limit * 100, 1) if limit else None,
				}
			)
		)
	rows.sort(key=lambda row: row["available"])
	return columns, rows


def _customers(filters):
	filters = filters or {}
	if filters.get("customer"):
		return [filters["customer"]]
	return frappe.get_all("Credit Score", pluck="customer", order_by="customer")


def _columns():
	return [
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link", "options": "Customer", "width": 200},
		{"fieldname": "has_credit_score", "label": _("Has Credit Score"), "fieldtype": "Check", "width": 80},
		{"fieldname": "tier", "label": _("Tier"), "fieldtype": "Data", "width": 110},
		{"fieldname": "score", "label": _("Score"), "fieldtype": "Int", "width": 80},
		{"fieldname": "approved_limit", "label": _("Approved limit"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "outstanding_debt", "label": _("Batch debt outstanding"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "submitted_uninvoiced_orders", "label": _("Submitted, not invoiced"), "fieldtype": "Currency", "width": 160},
		{"fieldname": "draft_orders", "label": _("Draft orders holding limit"), "fieldtype": "Currency", "width": 150},
		{"fieldname": "committed", "label": _("Committed"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "available", "label": _("Available"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "used_percent", "label": _("Used %"), "fieldtype": "Percent", "width": 90},
	]
