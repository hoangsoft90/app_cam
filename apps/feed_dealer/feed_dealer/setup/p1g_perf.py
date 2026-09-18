"""P1G performance smoke: measure the app on a LARGE dataset.

Run:      bench --site <site> execute feed_dealer.setup.p1g_perf.measure 1000
Describe: bench --site <site> execute feed_dealer.setup.p1g_perf.describe
Cleanup:  bench --site <site> execute feed_dealer.setup.p1g_perf.cleanup

WHY THIS EXISTS
---------------
`EXIT_GATE_PHASE1.md` §6 (Performance) was "NOT ASSESSABLE" because the only
evidence was the 60-transaction P1G dataset. A 60-row smoke test cannot tell you
whether a `Sales Invoice.on_submit` that recomputes a customer's credit position
over their whole history stays usable once that history is thousands of invoices
instead of a handful.

This module reuses the P1G generator verbatim (same `build_dataset`, same random
chain SO -> SI -> Batch Debt -> Payment Entry -> Sales Return -> offset) but runs
it under its OWN prefix and its OWN Site Default keys, so:

  * the measurement dataset does not mix with the P1G integrity dataset, and
  * the P1G dataset stays exactly as verifiable as before.

It reports raw numbers only (seconds and milliseconds). It does NOT assert a
threshold: the plan asks for a measurement, not an optimization, and inventing a
pass mark here would be an agent signing off on a gate it does not own.

WHAT IS MEASURED (the three things the plan names)
--------------------------------------------------
  (a) ONE `Sales Invoice.on_submit` on a database that already holds the large
      dataset -- the cost the accountant pays on every bill.
  (b) the AR-vs-Batch-Debt integrity suite over the whole dataset (9 checks).
  (c) each of the 7 Desk reports, through the same runner the Desk uses.

DO NOT run this on a production site. It creates thousands of documents and then
`cleanup()` deletes them; the deletion is a lot of irreversible writes.
"""

import json
import time

import frappe

from feed_dealer.setup import p1g_integrity as p1g

PERF_PREFIX = "P1G-PERF"
PERF_CUSTOMER_COUNT = 25
PERF_CREDIT_LIMIT = 5_000_000_000  # far above anything the random dataset spends
PERF_MARKER_BUILT = "feed_dealer_p1g_perf_built"
PERF_MARKER_SHAPE = "feed_dealer_p1g_perf_dataset"
DEFAULT_COUNT = 2000


def _activate():
    """Point the P1G generator/checks at the PERF prefix and keys.

    `p1g_integrity` resolves PREFIX/CUSTOMER_COUNT/MARKER_* and the checks read
    `_customers()` off the module, so reassigning the module globals swaps the
    whole dataset scope without copying the generator.

    The swap is NOT restored. Run each entry point in its own `bench execute`
    process: calling `p1g_integrity.run()` in the same process afterwards would
    silently scope to the PERF prefix (and find no dataset).
    """
    p1g.PREFIX = PERF_PREFIX
    p1g.CUSTOMER_COUNT = PERF_CUSTOMER_COUNT
    p1g.CREDIT_LIMIT = PERF_CREDIT_LIMIT
    p1g.MARKER_BUILT = PERF_MARKER_BUILT
    p1g.MARKER_SHAPE = PERF_MARKER_SHAPE


def _customers():
    return frappe.get_all("Customer", filters={"customer_name": ["like", f"{PERF_PREFIX}%"]}, pluck="name")


def build(count=DEFAULT_COUNT, seed=p1g.SEED):
    """Build the PERF dataset from scratch and record its exact shape."""
    _activate()
    # A build that dies halfway leaves documents behind (customers, batches and
    # each committed Sales Order survive) with NO marker, and `build_dataset`
    # appends to whatever is already there. Rebuilding on top of that produces a
    # dataset whose exact shape does not match the recorded one - measured: one
    # extra Sales Order from a run that died on `QueueOverloaded` raised by
    # ANOTHER app's on_update hook, which C9 then (correctly) flagged as a
    # mismatch. `ensure_dataset` guards this for P1G; mirror it here rather than
    # relying on the caller to have cleaned first.
    if _customers() and not frappe.db.get_default(PERF_MARKER_BUILT):
        print("[perf] partial PERF dataset detected (no build marker) — cleaning before rebuild")
        cleanup()
    started = time.time()
    shape = p1g.build_dataset(count=count, seed=seed)
    frappe.db.set_default(PERF_MARKER_SHAPE, json.dumps(shape, ensure_ascii=False))
    frappe.db.set_default(PERF_MARKER_BUILT, "1")
    frappe.db.commit()
    elapsed = time.time() - started
    result = {
        "prefix": PERF_PREFIX,
        "requested_transactions": count,
        "built": shape,
        "build_seconds": round(elapsed, 2),
        "per_transaction_ms": round(elapsed * 1000 / max(count, 1), 1),
    }
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return result


def measure_invoice_submit():
    """(a) time ONE fresh Sales Invoice insert+submit on the big database.

    Uses the real generator helpers, so the measured code path is the same one
    the dataset itself exercises: insert -> hooks -> Batch Debt for the batched
    line. Timing includes the credit-position recompute the hook chain triggers.
    """
    _activate()
    customers = _customers()
    if not customers:
        raise AssertionError("no P1G-PERF dataset on this site — run `build` first")
    customer = customers[0]
    batch = p1g._batch("PERF-SUBMIT-B1", customer)
    lines = [{"qty": 10, "rate": 50_000, "batch": batch}]
    started = time.time()
    inv = p1g._invoice(lines, customer=customer, due_days=15)
    elapsed = time.time() - started
    result = {
        "invoice": inv.name,
        "customer": customer,
        "seconds": round(elapsed, 3),
        "ms": round(elapsed * 1000),
    }
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return result


def measure_integrity():
    """(b) time the AR-vs-Batch-Debt integrity suite over the whole dataset."""
    _activate()
    started = time.time()
    report = p1g.collect()
    text, failed = report.render()
    elapsed = time.time() - started
    print(text)
    result = {"seconds": round(elapsed, 3), "checks": len(report.rows), "failed": failed}
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return result


def measure_reports():
    """(c) time each of the 7 Desk reports through the Desk's own runner."""
    from feed_dealer.setup.p1g_reports_check import REPORTS, _run_report

    timings = {}
    for name in REPORTS:
        started = time.time()
        columns, rows = _run_report(name)
        timings[name] = {
            "seconds": round(time.time() - started, 3),
            "rows": len(rows),
            "columns": len(columns),
        }
    result = {
        "reports": timings,
        "slowest": max(timings, key=lambda key: timings[key]["seconds"]) if timings else None,
    }
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return result


def measure(count=DEFAULT_COUNT, reuse=True):
    """Build (unless already built) and run all three measurements in order."""
    _activate()
    built = json.loads(frappe.db.get_default(PERF_MARKER_SHAPE) or "{}")
    if not (reuse and built):
        build(count=count)
    elif built.get("transactions", 0) < count:
        # A smaller dataset than the one requested would not measure what was
        # asked for; rebuild rather than quietly report the old size.
        print(f"[perf] existing dataset has {built.get('transactions')} transaction(s) < {count} — rebuilding")
        cleanup()
        build(count=count)

    # Integrity FIRST, while the dataset still matches its recorded shape: the
    # single-invoice probe below adds a document, which would (correctly) fail
    # C9's exact-count check.
    integrity = measure_integrity()
    reports = measure_reports()
    submit = measure_invoice_submit()

    # Re-read the marker AFTER the build/reuse step. The `built` read at the top
    # is the PRE-run value and would report the previous dataset's size (measured
    # bug: a 2.000-transaction build whose summary claimed transactions=100, the
    # leftover from the earlier pilot).
    built = json.loads(frappe.db.get_default(PERF_MARKER_SHAPE) or "{}")
    summary = {
        "requested_transactions": count,
        "dataset": built,
        "integrity": integrity,
        "reports": reports,
        "single_invoice_submit": submit,
    }
    print("=== P1G PERF SUMMARY ===")
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return summary


def describe():
    _activate()
    return p1g.describe()


def cleanup():
    """Remove ONLY the PERF dataset; the P1G integrity dataset is left alone.

    `_activate()` first: `p1g.cleanup()` scopes by the module PREFIX and clears
    the module MARKER_* keys, so with the PERF values in place it touches the
    PERF customers/keys and leaves the integrity dataset's keys intact.
    """
    _activate()
    # Deleting a few thousand documents in ONE transaction trips frappe's guard
    # (MAX_WRITES_PER_TRANSACTION = 200k writes, and the whole action is rolled
    # back with TooManyWritesError). Measured on the 2.000-transaction dataset.
    # Auto-commit is the escape hatch frappe provides for exactly this: a bulk
    # delete has no meaningful "all or nothing", so flushing as we go is right.
    frappe.db.auto_commit_on_many_writes = True
    removed = p1g.cleanup()
    print(f"[perf] cleanup removed {len(removed)} fixture(s)")
    return removed
