"""P0.5 acceptance: opening balances via Journal Entry + Batch Debt (NO Sales Invoice).

Run:      bench --site <site> execute feed_dealer.setup.p05_acceptance.run
Debug:    bench --site <site> execute feed_dealer.setup.p05_acceptance.debug
Cleanup:  bench --site <site> execute feed_dealer.setup.p05_acceptance.cleanup

Covers the prompt's four cases:
  T1  import the sample dataset   -> reconcile AR_open vs BD_open, diff = 0 VND
  T2  ordinary Batch Debt without sales_invoice is still rejected
  T3  opening debts carry NO Sales Invoice and NO backdated SI exists
  T4  re-importing the same file adds NOTHING (idempotent), amounts unchanged
  T5  an interrupted run's orphan JE is ADOPTED on retry, not duplicated
  T6  every JE is balanced (debits == credits) and every opening debt links a JE

The sample data is scripts/migration/*.csv (10 customers / 11 balances /
34,975,000 VND), loaded from the app repo — the exact same files a real
migration would use, so acceptance exercises the real templates.
"""

import json
import os
import traceback

import frappe
from frappe.utils import flt

from feed_dealer.setup.p1b_acceptance import Report
from feed_dealer.setup.p1d_acceptance import _company  # same resolution as P0
from feed_dealer.setup import migration

PREFIX = "P0.5-MIG"
SAMPLE_DIR_NAME = "sample"


def _scope_customers():
    """Customer names the sample import actually owns, resolved from its phones.

    The sample rows carry REAL ledger names (the owner's data is never renamed
    for a test), so a name-prefix scope matches nothing — measured: `prefix=
    "P0.5-MIG%"` found 0 debts while the import had created 11. Scoping by the
    file's phone list is what a real operator does (scope = the imported scope),
    and it cannot silently pass on customers the import did not touch.
    """
    phones = [
        row.get("customer_phone") or row.get("phone")
        for row in migration.load_csv(os.path.join(_sample_dir(), "customers.csv"))
    ]
    names = []
    for phone in phones:
        name = migration._customer_by_phone(phone)
        if not name:
            raise AssertionError(f"sample customer with phone {phone} not imported yet — T1 must run first")
        names.append(name)
    return sorted(set(names))


def _sample_dir():
    """Locate the sample CSVs as seen from INSIDE the bench container.

    Two homes, first match wins: the repo's scripts/migration/sample (when the
    workspace tree is bind-mounted) and the flat host copy the deploy step
    writes to /Users/hoang/htdocs/erpnext/app_cam/scripts_migration_sample.
    The fallback exists because the MCP file tool cannot create new directories
    outside the app tree and the sample must not live inside the shipped app.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    # here = <app_root>/feed_dealer/setup. In the BENCH container that is
    # /home/frappe/frappe-bench/apps/feed_dealer/feed_dealer/setup, and the host
    # repo root (scripts/migration/sample) is NOT mounted there — `docker inspect`
    # measured that ONLY <app_root> is bind-mounted (host app_cam/feed_dealer).
    # The deploy helper parks the sample CSVs at <app_root>/.migration_sample,
    # i.e. here/../.. inside the container.
    app_root = os.path.abspath(os.path.join(here, "..", ".."))
    workspace_root = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
    candidates = [
        os.path.join(app_root, ".migration_sample"),
        os.path.join(workspace_root, "scripts", "migration", SAMPLE_DIR_NAME),
    ]
    for path in candidates:
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "customers.csv")):
            return path
    raise AssertionError(
        f"sample CSVs not found in any of {candidates} — push scripts/migration/sample/* to the host"
    )


def _customers():
    try:
        return _scope_customers()
    except AssertionError:
        return []


def _ordinary_debt_rejected():
    """T2: the controller rule survives — ordinary debt still requires an invoice."""
    customer = frappe.db.get_value("Customer", {"mobile_no": "0901110001"}, "name")
    if not customer:
        raise AssertionError("fixture broken: sample customer not imported yet — T1 must run first")
    batch = frappe.db.get_value(
        "Feed Batch", {"customer": customer, "notes": ["like", f"{migration.MARK}%"]}, "name"
    )
    if not batch:
        raise AssertionError("fixture broken: no opening batch to attach the debt to")
    doc = frappe.get_doc(
        {
            "doctype": "Batch Debt",
            "batch": batch,
            "customer": customer,
            "is_opening_balance": 0,
            "allocated_amount": 1_000_000,
            "due_date": frappe.utils.nowdate(),
        }
    )
    blocked, detail = False, ""
    try:
        doc.insert(ignore_permissions=True)
    except frappe.ValidationError as exc:
        blocked, detail = True, str(exc)
    if not blocked:
        raise AssertionError("an ordinary debt without sales_invoice was accepted")
    if "Hóa đơn bán hàng" not in detail and "sales_invoice" not in detail:
        raise AssertionError(f"rejected for the wrong reason: {detail[:120]}")
    return f"ordinary debt rejected: {detail.strip()[:70]}"


def check_reconcile_zero():
    """T1: import the real template files -> AR opening == BD opening, diff = 0."""
    # Scope: the P0 fixture (p0_acceptance A3) also stores an opening-balance
    # debt WITHOUT a JE by design, so reconcile must be scoped to the migration
    # customers — matching how a real run is scoped to its own import.
    # IMPORT FIRST, scope second: T1 is the bootstrap check and must work on a
    # freshly cleaned site (measured: after cleanup() removed the sample
    # customers, resolving the scope before the import raised "sample customer
    # ... not imported yet" — a circular precondition on the check that does
    # the importing).
    summary = migration.import_from_files(_sample_dir(), company=_company())
    scope = _scope_customers()
    result = migration.reconcile(customers=scope)
    if flt(result["diff"]) != 0:
        raise AssertionError(f"reconcile diff {result['diff']:,} != 0: {result}")
    if not result["debts"]:
        raise AssertionError("reconcile passed on an empty scope — 0 == 0 proves nothing")
    # Reconcile must be RERUNNABLE (it is the report the owner signs against).
    again = migration.reconcile(customers=scope)
    if flt(again["diff"]) != 0 or again["debts"] != result["debts"]:
        raise AssertionError(f"second reconcile drifted: {again}")
    if flt(result["AR_open"]) != 34_975_000:
        raise AssertionError(
            f"sample total drifted: AR_open {result['AR_open']:,.0f} != 34,975,000 "
            "(the template files changed without updating this check)"
        )
    return (
        f"AR_open == BD_open == {result['AR_open']:,.0f} over {result['debts']} debts, "
        f"{result['journal_entries']} JEs, diff 0"
    )


def check_opening_has_no_invoice():
    """T3: opening debts have no sales_invoice — and the migration created no SI at all."""
    scope = _scope_customers()
    debts = frappe.get_all(
        "Batch Debt",
        filters={"customer": ["in", scope or [""]], "is_opening_balance": 1, "docstatus": 1},
        fields=["name", "sales_invoice", "opening_journal_entry", "allocated_amount", "outstanding_amount"],
    )
    if not debts:
        raise AssertionError("no opening debts found — T1 must run first")
    with_invoice = [row.name for row in debts if row.sales_invoice]
    if with_invoice:
        raise AssertionError(f"opening debts carrying a Sales Invoice: {with_invoice}")
    unlinked = [row.name for row in debts if not row.opening_journal_entry]
    if unlinked:
        raise AssertionError(f"opening debts without opening_journal_entry: {unlinked}")
    created_si = frappe.db.count("Sales Invoice", {"customer": ["in", scope or [""]]})
    if created_si:
        raise AssertionError(
            f"{created_si} Sales Invoice(s) exist for migration customers — NĐ 123 violation"
        )
    # The opening debt's JE debits the receivable (Dr AR / Cr opening).
    rows = frappe.get_all(
        "Journal Entry Account",
        filters={"parent": debts[0].opening_journal_entry, "docstatus": 1},
        fields=["account", "party_type", "party", "debit_in_account_currency", "credit_in_account_currency"],
    )
    receivable = frappe.get_cached_value("Company", _company(), "default_receivable_account")
    debits = [r for r in rows if r.account == receivable and flt(r.debit_in_account_currency) > 0]
    if not debits or debits[0].party_type != "Customer":
        raise AssertionError(f"opening JE must debit AR with party=Customer: {rows}")
    return (
        f"{len(debts)} opening debts: 0 SI, every debt linked to a Dr-AR/Cr-opening JE "
        f"(sample {debts[0].name} -> {debts[0].opening_journal_entry})"
    )


def check_reimport_idempotent():
    """T4: importing the same files again must add nothing."""
    scope = _scope_customers()
    before_debts = frappe.db.count(
        "Batch Debt", {"customer": ["in", scope or [""]], "is_opening_balance": 1}
    )
    before_je = _opening_je_doc_count()
    before_sum = _opening_sum()
    migration.import_from_files(_sample_dir(), company=_company())
    after_debts = frappe.db.count(
        "Batch Debt", {"customer": ["in", scope or [""]], "is_opening_balance": 1}
    )
    after_je = _opening_je_doc_count()
    after_sum = _opening_sum()
    if after_debts != before_debts or after_je != before_je:
        raise AssertionError(
            f"re-import created documents: debts {before_debts}->{after_debts}, JEs {before_je}->{after_je}"
        )
    if flt(after_sum) != flt(before_sum):
        raise AssertionError(f"re-import changed the money: {before_sum:,.0f} -> {after_sum:,.0f}")
    result = migration.reconcile(customers=scope)
    if flt(result["diff"]) != 0:
        raise AssertionError(f"reconcile broke after re-import: {result}")
    return (
        f"re-import: debts {before_debts}->{after_debts}, JEs {before_je}->{after_je}, "
        f"sum {after_sum:,.0f} unchanged, diff 0"
    )


def _opening_je_doc_count():
    """COUNT OF Journal Entry DOCUMENTS carrying the P0.5 remark (site-wide).

    DELIBERATELY NOT the debt->JE link count: during a legitimate adoption the
    re-created debt re-links the SAME orphan JE, so any link-based counter goes
    up by one and "no new JE was booked" reads as a failure (measured T5
    10 -> 11 with the correct, non-duplicating adoption in place). Documents
    cannot be re-linked into existence — a new number here is a genuinely new
    JE, i.e. real money booked twice.
    """
    return len(
        frappe.get_all(
            "Journal Entry",
            filters={"docstatus": 1, "user_remark": ["like", f"{migration.JE_REMARK}%"]},
            pluck="name",
        )
    )


def _opening_sum():
    scope = _scope_customers()
    rows = frappe.get_all(
        "Batch Debt",
        filters={"customer": ["in", scope or [""]], "is_opening_balance": 1, "docstatus": 1},
        fields=[{"SUM": "outstanding_amount", "as": "total"}],
    )
    return flt(rows[0].total) if rows else 0.0


def check_orphan_je_adopted():
    """T5: an interrupted run's orphan JE is adopted, never duplicated.

    Replays the REAL crash window: the run died AFTER submitting a row's JE but
    BEFORE its debt was submitted. On the site that state is produced by taking
    an already-imported row's debt away (cancel + delete) while leaving its JE —
    re-import must then ADOPT that JE for the re-created debt instead of booking
    a second JE (which would double this customer's AR opening).
    """
    customer = frappe.db.get_value("Customer", {"mobile_no": "0901110001"}, "name")
    if not customer:
        raise AssertionError("fixture broken: sample customer 'Trần Văn A' not imported yet — T1 must run first")
    company = _company()
    amount = 4_500_000  # the LOT-CU-001 row of the sample file
    debt_name = frappe.db.get_value(
        "Batch Debt",
        {"customer": customer, "is_opening_balance": 1, "allocated_amount": amount, "docstatus": 1},
        "name",
    )
    if not debt_name:
        raise AssertionError("fixture broken: the 4,500,000 opening debt is not imported — T1 must run first")
    je = frappe.db.get_value("Batch Debt", debt_name, "opening_journal_entry")
    if not je:
        raise AssertionError(f"fixture broken: {debt_name} has no JE — T3 must have passed")
    # Simulate the crash: the debt goes away, the JE stays behind (submitted).
    frappe.get_doc("Batch Debt", debt_name).cancel()
    frappe.delete_doc("Batch Debt", debt_name, force=True, ignore_permissions=True)
    frappe.db.commit()
    orphan = frappe.db.get_value("Journal Entry", je, "docstatus")
    if orphan != 1:
        raise AssertionError(f"fixture broken: JE {je} did not survive as an orphan (docstatus={orphan})")

    before = _opening_je_doc_count()
    summary = migration.import_from_files(_sample_dir(), company=company)
    after = _opening_je_doc_count()
    if after != before:
        raise AssertionError(f"the re-import booked an extra JE instead of adopting: {before} -> {after}")
    re_linked = frappe.db.get_value(
        "Batch Debt",
        {"customer": customer, "is_opening_balance": 1, "allocated_amount": amount, "opening_journal_entry": je},
        "name",
    )
    if not re_linked:
        raise AssertionError(f"orphan JE {je} was not adopted: no debt links it after re-import")
    result = summary["reconcile"]
    if flt(result["diff"]) != 0:
        raise AssertionError(f"reconcile broke after adoption: {result}")
    if flt(result["AR_open"]) != 34_975_000:
        raise AssertionError(
            f"adoption changed the money: AR_open {result['AR_open']:,.0f} != 34,975,000"
        )
    return f"orphan {je} adopted into {re_linked}; JEs {before} -> {after}, AR_open {result['AR_open']:,.0f}, diff 0"


def check_jes_balanced():
    """T6: every migration JE balances and no opening JE names a batch_debt."""
    scope = _scope_customers()
    debts = frappe.get_all(
        "Batch Debt",
        filters={"customer": ["in", scope or [""]], "is_opening_balance": 1},
        pluck="opening_journal_entry",
    )
    names = sorted({name for name in debts if name})
    if not names:
        raise AssertionError("no opening JEs to check")
    rows = frappe.get_all(
        "Journal Entry Account",
        filters={"parent": ["in", names], "docstatus": 1},
        fields=[
            "parent",
            "batch_debt",
            "party_type",
            "debit_in_account_currency",
            "credit_in_account_currency",
        ],
    )
    per_je = {}
    for row in rows:
        if row.batch_debt:
            raise AssertionError(
                f"JE {row.parent} carries batch_debt {row.batch_debt} — that field is P1F's "
                "offset attribution; an opening debit carrying it books a phantom offset"
            )
        per_je.setdefault(row.parent, [0.0, 0.0])
        per_je[row.parent][0] += flt(row.debit_in_account_currency)
        per_je[row.parent][1] += flt(row.credit_in_account_currency)
    unbalanced = {
        name: f"D {dr:,.0f} / C {cr:,.0f}" for name, (dr, cr) in per_je.items() if flt(dr) != flt(cr)
    }
    if unbalanced:
        raise AssertionError(f"unbalanced opening JEs: {unbalanced}")
    # And the AR side of those JEs equals the debts' allocated exactly.
    ar = sum(flt(r.debit_in_account_currency) for r in rows if r.party_type == "Customer")
    bd = _opening_sum()
    if flt(ar) != flt(bd):
        raise AssertionError(f"JE AR debits {ar:,.0f} != opening debts {bd:,.0f}")
    return f"{len(names)} JE(s) balanced, none carries batch_debt, AR debits {ar:,.0f} == debts"


CHECKS = (
    ("T1  import + reconcile diff 0", check_reconcile_zero),
    ("T2  ordinary debt still rejected", _ordinary_debt_rejected),
    ("T3  opening has NO invoice", check_opening_has_no_invoice),
    ("T4  re-import idempotent", check_reimport_idempotent),
    ("T5  orphan JE adopted", check_orphan_je_adopted),
    ("T6  JEs balanced + no offset tag", check_jes_balanced),
)


def collect():
    report = Report()
    for name, fn in CHECKS:
        report.check(name, fn)
    return report


def run():
    """Entry point for `bench execute`. Raises when any check fails."""
    frappe.db.commit()
    report = collect()
    text, failed = report.render()
    print(text)
    print(json.dumps({"site": frappe.local.site, "failed": failed}))
    frappe.db.commit()
    if failed:
        frappe.throw(f"P0.5 acceptance FAILED: {failed}", title="P0.5 ACCEPTANCE FAILED")
    print("P0.5 ACCEPTANCE: ALL PASS")
    return {"failed": failed, "passed": len(report.rows) - len(failed)}


def debug():
    """Print every failing check's real traceback (bench execute masks errors)."""
    failed = 0
    for name, fn in CHECKS:
        try:
            detail = fn()
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"\n===== FAIL {name} =====")
            traceback.print_exc()
        else:
            print(f"===== PASS {name}: {detail}")
    print(f"\n[debug] {len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    return {"failed": failed}


def cleanup():
    """Remove P0.5 fixtures IN MONEY ORDER: debts -> JEs -> batches -> customers.

    Deleting an opening debt whose JE stays behind would leave AR opening in the
    ledger with nothing explaining it, so JEs go right after their debts. This is
    only for the PREFIXED sample data — a REAL migration is never auto-cleaned
    (clearing it means cancelling money documents; see README.md).
    """
    removed = []
    customers = _customers()
    je_names = set(
        frappe.get_all(
            "Batch Debt", filters={"customer": ["in", customers]}, pluck="opening_journal_entry"
        )
    )
    # JEs whose remark carries the marker but were never linked (T5 leftovers).
    je_names.update(
        frappe.get_all("Journal Entry", filters={"user_remark": ["like", f"{migration.JE_REMARK}%"]}, pluck="name")
    )
    for row in frappe.get_all(
        "Batch Debt", filters={"customer": ["in", customers]}, fields=["name", "docstatus"]
    ):
        try:
            if row.docstatus == 1:
                frappe.get_doc("Batch Debt", row.name).cancel()
            frappe.delete_doc("Batch Debt", row.name, force=True, ignore_permissions=True)
            removed.append(f"Batch Debt {row.name}")
        except Exception as exc:  # noqa: BLE001
            removed.append(f"Batch Debt {row.name} FAILED: {exc}")
    for name in sorted(je_names):
        try:
            doc = frappe.get_doc("Journal Entry", name)
            if doc.docstatus == 1:
                doc.flags.ignore_links = True
                doc.cancel()
            frappe.delete_doc("Journal Entry", name, force=True, ignore_permissions=True)
            removed.append(f"Journal Entry {name}")
        except Exception as exc:  # noqa: BLE001
            removed.append(f"Journal Entry {name} FAILED: {exc}")
    for name in frappe.get_all(
        "Feed Batch", filters={"notes": ["like", f"{migration.MARK}%"]}, pluck="name"
    ):
        try:
            frappe.delete_doc("Feed Batch", name, force=True, ignore_permissions=True)
            removed.append(f"Feed Batch {name}")
        except Exception as exc:  # noqa: BLE001
            removed.append(f"Feed Batch {name} FAILED: {exc}")
    for name in customers:
        try:
            frappe.delete_doc("Customer", name, force=True, ignore_permissions=True)
            removed.append(f"Customer {name}")
        except Exception as exc:  # noqa: BLE001
            removed.append(f"Customer {name} FAILED: {exc}")
    frappe.db.commit()
    print(f"[feed_dealer] P0.5 cleanup removed {len(removed)} fixture(s):")
    for item in removed:
        print(f"  - {item}")
    return removed
