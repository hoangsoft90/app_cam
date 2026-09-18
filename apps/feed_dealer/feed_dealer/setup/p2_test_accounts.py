"""P2 test roles + accounts — fixtures for driving the mobile app, NOT real users.

Run:      bench --site <site> execute feed_dealer.setup.p2_test_accounts.run
Cleanup:  bench --site <site> execute feed_dealer.setup.p2_test_accounts.cleanup

WHY
---
The P2 checklist needs one account per role (Owner / Staff / Driver) to prove the
multi-role switcher and the driver flow end to end. Until now the site only had
the throwaway users the acceptance suites make (`p0-acceptance-*`,
`p1c-acceptance-*`), and role `Driver` did not exist at all.

NOT REAL USERS, ON PURPOSE
--------------------------
* Emails end in `@example.com` and are prefixed `p2-test-`, so a real account can
  never collide with them and `cleanup()` can find them by prefix.
* **No password is written in this file.** Each run generates a random password
  and prints it once, so the credential is not a secret sitting in git. Treat the
  printed password as throwaway test data and rotate it when real accounts are
  made at go-live.
* Driver is a `Website User` (no Desk seat): a System User seat costs money and
  the driver uses the mobile app over REST, not Desk. Real accounts + the
  password policy stay in the go-live checklist.
"""

import json
import secrets
import string

import frappe

PREFIX = "p2-test"

ACCOUNTS = (
    {"email": f"{PREFIX}-owner@example.com", "first_name": "P2 Owner",
     "roles": ["Feed Dealer Manager"], "user_type": "System User"},
    {"email": f"{PREFIX}-staff@example.com", "first_name": "P2 Staff",
     "roles": ["Feed Dealer Staff"], "user_type": "System User"},
    {"email": f"{PREFIX}-driver@example.com", "first_name": "P2 Driver",
     "roles": ["Driver"], "user_type": "Website User"},
)

# `role_name` (NOT `role`): `role` is the field name on the User-role CHILD table,
# while the Role DocType itself is named by `role_name`. Using `role` here makes
# frappe generate the name from an unset field and insert blows up in naming.
# desk_access=0: the driver role must NOT open Desk even if the account is later
# promoted to System User.
ROLES = ({"role_name": "Driver", "desk_access": 0},)


def _password():
    """A random throwaway password, printed and never stored in the repo."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(14))


def run():
    created, passwords = [], {}
    for spec in ROLES:
        if not frappe.db.exists("Role", spec["role_name"]):
            frappe.get_doc({"doctype": "Role", **spec}).insert(ignore_permissions=True)
            created.append(f"Role {spec['role_name']}")

    for spec in ACCOUNTS:
        if frappe.db.exists("User", spec["email"]):
            # Idempotent: keep the account, refresh its roles so a role added to
            # this list later still lands on an existing test account.
            user = frappe.get_doc("User", spec["email"])
            have = {row.role for row in user.roles}
            missing = [role for role in spec["roles"] if role not in have]
            if missing:
                for role in missing:
                    user.append("roles", {"role": role})
                user.save(ignore_permissions=True)
            created.append(f"User {spec['email']} (existing, roles refreshed: {missing or 'none missing'})")
            continue

        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": spec["email"],
                "first_name": spec["first_name"],
                "send_welcome_email": 0,
                "user_type": spec["user_type"],
                "roles": [{"role": role} for role in spec["roles"]],
            }
        )
        password = _password()
        user.new_password = password
        user.insert(ignore_permissions=True)
        passwords[spec["email"]] = password
        created.append(f"User {spec['email']} ({spec['user_type']})")

    frappe.db.commit()
    result = {
        "created": created,
        "passwords": passwords,
        "note": "throwaway test credentials - printed once, not stored in the repo",
    }
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return result


def cleanup():
    """Remove ONLY the p2-test-* accounts (never a real user)."""
    removed = []
    for spec in ACCOUNTS:
        if frappe.db.exists("User", spec["email"]):
            frappe.delete_doc("User", spec["email"], force=True, ignore_permissions=True)
            removed.append(f"User {spec['email']}")
    frappe.db.commit()
    print(json.dumps({"removed": removed}, indent=1, ensure_ascii=False))
    return removed
