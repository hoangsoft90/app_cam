"""Declarative custom fields owned by this app.

`custom_field.json` (module folder) declares the Custom Fields this app needs on
core DocTypes. `sync()` runs in `after_migrate` so a plain `bench migrate` is
all it takes to (re)create them — no fixtures, no Customize Form state, and the
definition is versioned in git like everything else.

Rules:
  * create-if-absent / update-in-place, keyed on (dt, fieldname);
  * fields belonging to other apps or to humans are never touched;
  * the function is idempotent: a second migrate must change nothing.
"""

import json
import os

import frappe

SPEC_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "feed_dealer", "custom_field.json"
)


def _load_specs():
    with open(SPEC_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def sync(dry_run=False):
    """Sync every field declared in custom_field.json. Returns a change report."""
    changes = []
    for spec in _load_specs():
        dt, fieldname = spec["dt"], spec["fieldname"]
        name = frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": fieldname}, "name")
        if name:
            existing = frappe.get_doc("Custom Field", name)
            diff = {k: v for k, v in spec.items() if existing.get(k) != v}
            if not diff:
                continue
            if not dry_run:
                for k, v in diff.items():
                    existing.set(k, v)
                existing.save(ignore_permissions=True)
            changes.append(f"updated {dt}.{fieldname}: {sorted(diff)}")
        else:
            if not dry_run:
                frappe.get_doc({"doctype": "Custom Field", **spec}).insert(
                    ignore_permissions=True
                )
            changes.append(f"created {dt}.{fieldname}")
    return changes


def run():
    """bench execute feed_dealer.setup.custom_fields.run"""
    changes = sync()
    frappe.db.commit()
    if not changes:
        print("[feed_dealer] custom fields already in sync")
        return []
    print(f"[feed_dealer] custom fields synced - {len(changes)} change(s):")
    for change in changes:
        print(f"  - {change}")
    return changes
