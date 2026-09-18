"""Baseline master data and roles for the feed dealer system.

Design rules (P0 constraints):
  1. **Create-if-absent, resolve by NAME.** The target site is a live ERPNext
     with real companies, warehouses, item groups and customers. The seeder must
     never rename, re-parent or overwrite something a human configured, so every
     step is "if it does not exist, create it" and nothing else. Running it twice
     must produce no changes and no error.
  2. **Never hard-code a Company or Warehouse name.** The default company and
     warehouse live in `Feed Dealer Settings`, and are filled in from Frappe's
     global defaults when this seeder first runs.
  3. **Do not create a Company.** The site already has companies; adding one
     would fork the chart of accounts. Instead the seeder *verifies* that a
     company with the VND currency exists.
"""

import frappe

MODULE = "Feed Dealer"

# (uom_name, uom_category). `category` is a v16 field and is optional on UOM,
# but a conversion may only reference UOMs that ERPNext can group, so the ones
# this seeder creates are given the category its conversions use.
UOMS = (
	("Bao", "Mass"),
	("Tấn", "Mass"),
	("Kg", "Mass"),
)

# ERPNext v16 moved UOM conversion OFF the UOM DocType: `UOM` has no
# `conversion_factor` / `reference_uom` fields any more, and conversions live in
# the `UOM Conversion Factor` DocType, which additionally requires `category`
# (Link -> UOM Category, reqd). Writing to the old field fails with
# `Unknown column 'conversion_factor' in 'SELECT'`, and omitting `category`
# fails with `MandatoryError: [UOM Conversion Factor, ...]: category`.
#
# Feed here is measured by weight, so both conversions sit in `Mass` and are
# expressed the ERPNext way: `1 from_uom = value to_uom`.
UOM_CONVERSIONS = (
	("Bao", "Kg", 25.0, "Mass"),
	("Tấn", "Kg", 1000.0, "Mass"),
)

# Feed groups nest under an existing parent when that parent already exists,
# otherwise they fall back to the root group.
ITEM_GROUPS = (
	("Cám lợn", "Cám chăn nuôi"),
	("Cám gà", "Cám chăn nuôi"),
	("Cám cá", "Cám chăn nuôi"),
	("Thuốc thú y", None),
)
ITEM_GROUP_FALLBACK_PARENT = "All Item Groups"

CUSTOMER_GROUPS = ("Trại lớn", "Trại vừa", "Hộ nhỏ", "Đại lý cấp 2")
CUSTOMER_GROUP_PARENT = "All Customer Groups"

PRICE_LISTS = ("Giá sỉ", "Giá lẻ")

ROLES = (
	"Feed Dealer Manager",
	"Feed Dealer Staff",
	"Feed Driver",
	"Feed Farmer",
)


def _settings():
	return frappe.get_doc("Feed Dealer Settings")


def resolve_default_company():
	"""Return the company to record in settings, or None when ambiguous.

	Prefers what is already configured, then Frappe's global default, then the
	only company on the site. With several companies and no default it returns
	None and leaves the setting for a human to fill in - guessing would put the
	wrong company into invoices.
	"""
	configured = frappe.db.get_single_value("Feed Dealer Settings", "default_company")
	if configured and frappe.db.exists("Company", configured):
		return configured

	for resolver in (
		lambda: frappe.defaults.get_global_default("company"),
		lambda: frappe.defaults.get_user_default("Company"),
	):
		candidate = resolver()
		if candidate and frappe.db.exists("Company", candidate):
			return candidate

	companies = frappe.get_all("Company", pluck="name", order_by="creation")
	return companies[0] if len(companies) == 1 else None


def ensure_uoms(dry_run=False):
	"""Create the feed UOMs (v16 schema: no conversion factor on UOM).

	`Bao` and `Kg` already exist on the target site with `must_be_whole_number`
	set by a human, so an existing UOM is left exactly as it is.
	"""
	created = []
	for name, category in UOMS:
		if frappe.db.exists("UOM", name):
			continue
		if not dry_run:
			frappe.get_doc(
				{
					"doctype": "UOM",
					"uom_name": name,
					"category": category,
					"must_be_whole_number": 0,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		created.append(f"UOM {name} (category {category})")
	return created


def ensure_uom_conversions(dry_run=False):
	"""1 Bao = 25 Kg, 1 Tấn = 1000 Kg (v16 `UOM Conversion Factor`)."""
	created = []
	for from_uom, to_uom, value, category in UOM_CONVERSIONS:
		if not frappe.db.exists("UOM", from_uom) or not frappe.db.exists("UOM", to_uom):
			continue
		existing = frappe.db.get_value(
			"UOM Conversion Factor", {"from_uom": from_uom, "to_uom": to_uom}, "value"
		)
		if existing is not None:
			continue
		if not dry_run:
			frappe.get_doc(
				{
					"doctype": "UOM Conversion Factor",
					"category": category,
					"from_uom": from_uom,
					"to_uom": to_uom,
					"value": value,
				}
			).insert(ignore_permissions=True)
		created.append(f"UOM Conversion Factor {from_uom} -> {to_uom} = {value} ({category})")
	return created


def ensure_item_groups(dry_run=False):
	created = []
	for name, parent in ITEM_GROUPS:
		if frappe.db.exists("Item Group", name):
			continue
		parent = parent if parent and frappe.db.exists("Item Group", parent) else ITEM_GROUP_FALLBACK_PARENT
		if not dry_run:
			frappe.get_doc(
				{
					"doctype": "Item Group",
					"item_group_name": name,
					"parent_item_group": parent,
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
		created.append(f"Item Group {name} (parent {parent})")
	return created


def ensure_customer_groups(dry_run=False):
	created = []
	for name in CUSTOMER_GROUPS:
		if frappe.db.exists("Customer Group", name):
			continue
		if not dry_run:
			frappe.get_doc(
				{
					"doctype": "Customer Group",
					"customer_group_name": name,
					"parent_customer_group": CUSTOMER_GROUP_PARENT,
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
		created.append(f"Customer Group {name}")
	return created


def ensure_price_lists(dry_run=False):
	created = []
	for name in PRICE_LISTS:
		if frappe.db.exists("Price List", name):
			continue
		if not dry_run:
			frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": name,
					"selling": 1,
					"buying": 0,
					"enabled": 1,
					"currency": "VND",
				}
			).insert(ignore_permissions=True)
		created.append(f"Price List {name}")
	return created


def ensure_roles(dry_run=False):
	"""Create the four feed dealer roles.

	`Feed Farmer` gets desk access off: farmers only ever reach their own records
	through the Zalo Mini App / mobile client, never the Desk.
	"""
	created = []
	for name in ROLES:
		if frappe.db.exists("Role", name):
			continue
		if not dry_run:
			frappe.get_doc({"doctype": "Role", "role_name": name, "desk_access": 1}).insert(
				ignore_permissions=True
			)
		created.append(f"Role {name}")
	return created


def ensure_settings_defaults(dry_run=False):
	"""Fill default_company / default_warehouse once, from global defaults."""
	changed = []
	settings = _settings()
	company = resolve_default_company()

	if not settings.default_company and company:
		if not dry_run:
			settings.default_company = company
		changed.append(f"Feed Dealer Settings.default_company = {company}")

	if company:
		warehouses = frappe.get_all(
			"Warehouse",
			filters={"company": company, "is_group": 0, "disabled": 0},
			pluck="name",
			order_by="creation",
		)
		# Prefer a feed warehouse when the company has one, else the first.
		feed = [w for w in warehouses if "cám" in w.lower() or "cam" in w.lower()]
		chosen = (feed or warehouses or [None])[0]

		if not settings.default_warehouse and chosen:
			if not dry_run:
				settings.default_warehouse = chosen
			changed.append(f"Feed Dealer Settings.default_warehouse = {chosen}")

		# DRIFT CHECK (measured 2026-09-18): the field is filled ONCE, so a value
		# stamped while another company was the default - or set by hand - is never
		# re-validated. A warehouse of the WRONG company does not fail here; it fails
		# much later, as `Warehouse Stores - S does not belong to company Minh Phát
		# Cám & VLXD` on the first Delivery Note, i.e. nothing can ship at all.
		elif settings.default_warehouse and chosen and not dry_run:
			owner = frappe.db.get_value("Warehouse", settings.default_warehouse, "company")
			if owner != company:
				old = settings.default_warehouse
				settings.default_warehouse = chosen
				changed.append(
					f"Feed Dealer Settings.default_warehouse: {old} (công ty {owner}) "
					f"-> {chosen} (công ty {company}) - kho cũ không thuộc công ty đang dùng"
				)
			# No "OK" line: this list is the list of things CHANGED. A caller that
			# prints it as a change report must not see a row for work that never
			# happened (the drift check still ran - silence means "already correct").

	if changed and not dry_run:
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)
	return changed


def verify_company_currency():
	"""Every company must be VND; return the list for evidence."""
	return frappe.get_all("Company", fields=["name", "default_currency"], order_by="creation")


def seed_all(dry_run=False):
	"""Run every step. Returns a report dict; safe to run repeatedly."""
	report = {
		"roles": ensure_roles(dry_run),
		"uoms": ensure_uoms(dry_run),
		"uom_conversions": ensure_uom_conversions(dry_run),
		"item_groups": ensure_item_groups(dry_run),
		"customer_groups": ensure_customer_groups(dry_run),
		"price_lists": ensure_price_lists(dry_run),
		"settings": ensure_settings_defaults(dry_run),
	}
	return report


def run():
	"""bench execute feed_dealer.setup.masters.run"""
	report = seed_all()
	total = sum(len(v) for v in report.values())
	print(f"[feed_dealer] master seed complete - {total} record(s) created/updated")
	for section, items in report.items():
		if items:
			print(f"  {section}:")
			for item in items:
				print(f"    - {item}")
	if not total:
		print("  (nothing to do - all masters already present)")
	companies = verify_company_currency()
	print(f"[feed_dealer] companies: {[(c.name, c.default_currency) for c in companies]}")
	non_vnd = [c.name for c in companies if c.default_currency != "VND"]
	if non_vnd:
		print(f"  WARNING - non-VND companies found: {non_vnd}")
	return report
