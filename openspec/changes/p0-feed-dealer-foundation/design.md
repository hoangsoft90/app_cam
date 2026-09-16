# Design — P0 Foundation

## Context

See `proposal.md` — Why. Constraints that shape the approach:

- The authoritative data model lives in `.plan/plan_final_hardening.md` §4.2–4.14 plus the v2.1/v2.2/v2.3 patches. P0 must not invent fields, only translate them into DocType metadata.
- `Batch Debt` must be an **allocation layer**: ERPNext Accounts Receivable stays the truth for money; the feed-dealer layer only maps slices of invoices to batches.
- `.plan/overview.md` states the Web Admin is the existing ERPNext Desk plus this custom app — so P0 is an app + data-model change, not a new UI.
- **The runtime already exists and is not ours.** ERPNext runs on the user's Mac mini; this machine reaches it through an ngrok HTTPS endpoint (REST, token auth) and an MCP bridge that can `docker exec`/`bench` on that host. The target is the site as it is: ERPNext v16, with other apps and real company/UOM/warehouse data already in it.
- The plan was written for ERPNext v15. The site is v16, and the differences are behavioural, not cosmetic — they are recorded as decisions below because each one cost a real failure to discover.

## Goals / Non-Goals

**Goals**

- Land `feed_dealer` on the target site as a versioned app whose DocTypes, roles and masters are all reproduced from the repository by `bench install-app` + `bench migrate`.
- Leave the target site's existing apps, companies, UOMs, warehouses, item groups and customers untouched.
- Make every P0 acceptance criterion machine-verifiable by a single command whose output is the evidence.

**Non-Goals**

- No payment allocation, credit-limit, rebate, e-invoice or cascade logic — controllers carry validation plus deliberate no-op stubs.
- No mobile/Zalo client, no n8n/AI runtime, no data import.
- No custom Desk UI beyond what DocType metadata yields for free.
- No management of the ERPNext runtime itself (no Compose file, no image build, no site bootstrap) — that stack is the user's, not this repository's.

## Decisions

### D1 — Target the existing site; the repository ships only the app

P0 deploys to the ERPNext v16 site that is already running on the user's Mac. The repository contains `apps/feed_dealer` and nothing that builds a runtime.

**Why not a repo-managed Compose stack:** an earlier draft of this change did exactly that (a local `docker-compose.yml` + custom image). It was removed at the user's direction once it was clear the real environment already existed behind ngrok: keeping it would have meant maintaining a second, divergent ERPNext (v15 against a v16 target) whose site, data and version drift from production.

**Trade-off:** deployment is a manual sequence against a live host (`push source → install-app → migrate`) and is not reproducible by `git clone && up`. Accepted: the target is a single pilot site, and the acceptance script is what proves the result.

### D2 — The app reaches the bench as a bind-mounted custom app

`/Users/hoang/htdocs/erpnext/app_cam/feed_dealer` on the Mac is bind-mounted into the bench container at `/home/frappe/frappe-bench/apps/feed_dealer`, which is the same pattern the site already uses for its other custom apps.

**Why not bake it into an image:** the bench is already running with its own app set and volume layout; a bind mount needs no rebuild, keeps `git` as the source of truth, and matches how the neighbouring apps are deployed.

**Trade-off:** the source of truth is on two machines (this repo and the Mac copy), and only the bench side is exercised at runtime. The sync helper verifies file-by-file equality after every push and fails loudly on a mismatch or on a file that exists remotely but not locally.

### D3 — DocTypes as app files, not Customize Form

Each DocType is a directory under the module folder containing `<name>.json` (metadata), `<name>.py` (controller) and `__init__.py`.

**Why:** app-file DocTypes are versioned with code, replayed by `bench migrate`, and diffable in review. A DocType created through the API or Customize Form lives only in the target database, would be invisible to `git`, and — critically — **cannot carry a Python controller**, which is where this app's validation lives.

Every DocType ships a controller, child tables included, because `bench migrate` imports `<app>.<module>.doctype.<scrubbed>.<scrubbed>` for every DocType, and a missing module aborts the migration of its parent.

### D4 — The lứa nuôi DocType is named `Feed Batch`, not `Batch`

The plan called it `Batch`. A DocType name is global across all installed apps, so shipping `Batch` overwrote ERPNext's stock `Batch` metadata: its `module` flipped from `Stock` to `Feed Dealer`, and stock code then failed with `'Batch' object has no attribute 'reference_doctype'`.

**Resolution:** name the DocType `Feed Batch` (folder `feed_batch`, class `FeedBatch`), and keep the plan's user-facing batch code as the document name via `autoname: format:LOT-{YYYY}-{#####}` — later phases and the mobile client expect "Lứa: LOT-2026-0012", and that is the name, not the DocType.

**Verification:** every shipped DocType name was compared against the DocTypes shipped by `frappe` and `erpnext` in the bench source; `Feed Batch` was the only collision found, and it is now removed. After the fix and a migrate, `Batch` again reports module `Stock`.

**Residue, needs a human decision:** the collision added 12 columns to ERPNext's `tabBatch` (`customer`, `animal_type`, `start_date`, …). Frappe restores the *metadata* from the owning app but does not drop the orphan columns, so they remain as unused columns on a live table. Dropping them is schema DDL on a live site and is therefore left to the user.

### D5 — Derived fields enforced by metadata *and* controller

`read_only = 1` in JSON stops the Desk UI from offering the field; it does not stop programmatic writes. The controller therefore recomputes every derived field in `before_save` from `Payment Allocation` rows and `allocated_amount`, overwriting anything supplied externally.

**Why not a database trigger:** triggers live outside the app, are engine-specific, and are invisible to reviewers reading the DocType. Keeping the rule in the controller keeps one place to read.

**P0 scope:** the recomputation is implemented (it is cheap and proves the SoT claim in an automated test), but the *allocation* logic that would feed real `Payment Allocation` rows is P1B.

### D6 — Opening-balance rule expressed twice, deliberately

`sales_invoice` stays `reqd = 0` with `mandatory_depends_on: "eval:!doc.is_opening_balance"` for the Desk experience, and the controller asserts the same rule in `validate()`.

**Why both:** `mandatory_depends_on` is evaluated client-side; any API/import path bypasses it. The controller check is the enforcement, the metadata is the UX. The acceptance script asserts both directions (opening balance with empty invoice → saves; ordinary debt with empty invoice → rejected naming the field).

### D7 — Masters seeded create-if-absent, resolved by name, invoked as a patch

`feed_dealer/setup/masters.py` exposes `seed_all()`, invoked from a patch so it runs on `migrate`.

**Why not fixtures:** fixtures overwrite operator edits on every migrate; a guarded patch creates-if-absent and leaves human edits alone. On the target site this matters concretely — it already had `Bao` and `Kg`, three companies and its own warehouses, so the seeder must add only what is missing and never rename or re-parent anything.

**Company/Warehouse are settings, not constants.** `Feed Dealer Settings.default_company` / `default_warehouse` are filled from Frappe's global defaults on first run and then read from there; no business code hard-codes a company name. The seeder *verifies* a VND company exists rather than creating one, because adding a company would fork the chart of accounts.

### D8 — v16 master-data model: conversions live in their own DocType

ERPNext v16 removed `conversion_factor` / `reference_uom` from `UOM` and moved conversions into `UOM Conversion Factor` (`category` **required**, plus `from_uom`, `to_uom`, `value`). Writing the v15 field fails with `Unknown column 'conversion_factor'`, and omitting `category` fails with `MandatoryError: [UOM Conversion Factor, …]: category`.

The seeder therefore creates `UOM` records with a `category` and writes the factors as `UOM Conversion Factor` rows (`1 Bao = 25 Kg`, `1 Tấn = 1000 Kg`, category `Mass`). Existing UOMs are left exactly as found, including the `must_be_whole_number` flag a human set on `Bao`.

### D9 — Permissions declared in DocType JSON with an explicit owner rule for farmers

Feed Farmer gets `read` with `if_owner = 1`; no feed-dealer DocType grants write/submit to a role intended for automated or non-human access, preserving the read-only AI posture for P3.

**Why `if_owner`:** it is native Frappe behaviour (compare `owner` to the session user) and needs no bespoke row-level security. The acceptance check asserts the negative case, since "farmer sees nothing" would also pass on a buggy empty list.

**Note for fixture-writing:** assigning `doc.owner` before `insert()` does **not** stick — Frappe overwrites it with the session user. A record must be given to another user with `db_set("owner", …)` after insert. This is what the isolation check does, and getting it wrong is invisible: the assertion "farmer sees its own record" is the only thing that catches it.

### D10 — Acceptance evidence as a single idempotent command

`feed_dealer/setup/p0_acceptance.py` runs nine named checks, prints a PASS/FAIL table, and raises when anything failed, so "it passed" is the command's exit status rather than a claim.

**Why not the Frappe test runner alone:** the criteria include site-level facts (HTTPS reachable, Administrator authenticates) that are not unittest-shaped. Those are captured outside the process; the DocType/permission/hook assertions live in the script. Hooks and permissions are asserted *in* the process on purpose — a raw SQL check would prove nothing about what a Farmer may actually read.

**Two operational details the script is built around:**

- `bench execute` swallows the exception raised by the target function and falls back to `eval(method)`, which surfaces as a misleading `NameError: name 'feed_dealer' is not defined`. A failing run therefore looks like a name error, not like the real traceback. `p0_acceptance.debug()` exists to print every failure's real traceback and never raises.
- Fixtures must be re-runnable. `Feed Batch` and `Batch Debt` are autonamed (`LOT-…`, `DEBT-…`) and `frappe.model.naming.set_new_name()` sets `doc.name = None` for any `format:` autoname, so a name supplied by the caller is discarded. Fixtures are therefore keyed on their fixture customer and found again by `cleanup()` through the `P0-ACCEPT%` prefix, and a submitted fixture is cancelled before deletion (`force=True` alone no longer permits deleting a submitted doc).

### D11 — One declarative generator for the DocType JSON, committed output

`.agent/gen_feed_dealer.py` holds the 19 DocType specs (fields, permissions, autoname) and writes the JSON plus controller skeletons. Its output is ordinary Frappe source committed under `apps/feed_dealer`; the generator is bootstrap tooling and never ships inside the app.

**Why:** 19 DocTypes × 3 files of near-identical boilerplate is where typos hide and where one missing `read_only` silently breaks the allocation-only invariant. One declarative spec keeps them consistent.

**Trade-off:** regenerating rewrites the controllers for the two DocTypes with real logic (`Feed Batch`, `Batch Debt`), so those controllers are edited *in the generator*. This is a real sharp edge — hand-editing the generated file loses the change on the next run. It is stated in both the generator docstring and the app README.

### D12 — Payment Allocation is a document, not a Payment Entry child table

P1B records each FIFO slice as a standalone submitted `Payment Allocation` (`format:ALLOC-{YYYY}-{#####}`) linking back to its `Payment Entry`.

**Why not the child table P0 sketched:** `Document._submit()` sets `docstatus = 1` and calls `save()`, and `doc_events.on_submit` fires from `run_post_save_methods()` — *after* the parent row is written. Child rows appended inside the hook therefore do not persist without a nested re-save of an ERPNext document, and when the payment is cancelled the rows stay behind with no lifecycle of their own. A standalone document gives each slice a docstatus, so cancelling the payment cancels its slices and the debts reopen — the same shape P1A already uses for `Sales Invoice → Batch Debt`.

**Verification:** `Batch Debt._get_paid_from_allocations()` sums submitted allocations regardless of parentage, so the controller was correct either way; the decision is about who owns the slice's lifecycle.

### D13 — The FIFO allocation key is (invoice, batch, tax template)

The `Sales Invoice.on_submit` idempotency check uses the same triple it groups by. Keying on `(sales_invoice, batch)` alone matched the *first* tax group of a multi-tax invoice and silently dropped the rest: the T6 fixture (one batch, KCT feed line + VAT line, 1,000,000 each) produced a single 1,000,000 debt, so a million VND of debt never existed. `item_tax_template` is therefore stored on the debt.

**Why the fixture needs two items:** ERPNext rewrites a line's `item_tax_template` to a template valid for the item's own Item/Item Group tax rules (`TaxesAndTotals.validate_item_tax_template`), so "two templates on one item" is not reachable through the real UI. The fixture uses a feed item (group rule → KCT) plus an item in a group without tax rules.

**Draft handling:** a group whose previous attempt died between insert and submit leaves a draft `Batch Debt`; the hook submits that draft instead of skipping the group forever, and skips only a group that already has a *submitted* debt.

### D14 — Payment allocation writes derived debt columns with `db.set_value`

The allocation layer recomputes a debt through the controller's `calculate_derived_fields()` and persists the result with `frappe.db.set_value(..., update_modified=False)` rather than `doc.save()`.

**Why:** a Batch Debt is a submitted document and its money columns are read-only, and `save()` did not persist the recomputed columns when called from inside another document's submit — the row kept its old `paid_amount`/`outstanding_amount`/`status` (measured: `_recalculate` computed 400,000/600,000 in memory, the row still read 0/1,000,000 after the save returned). These columns are server-owned and derived, and this is the same "derived column written by its owner" pattern P1A already uses for `Feed Batch.total_debt`.

The formula still lives in exactly one place — the controller — so the write mechanism cannot drift from the value shown on the form.

**Open gaps (deliberately not closed in P1B, needs a decision):**

- ERPNext's *Unreconcile Payment* tool de-reconciles an invoice by posting a new Journal Entry while the Payment Entry stays submitted; nothing fires in this module, so the allocations would keep claiming the debt is paid. Reversing it belongs with the P1C payment-ledger work.
- Two Payment Entries submitted **concurrently** for the same customer both read the same debt outage and can each allocate up to it, over-crediting the debt. The plan's FIFO pseudocode has the same shape and a real fix needs row locking (`select … for update`) plus a concurrency test; recorded here rather than silently accepted.

### D15 — The credit gate is one shared function, and a customer without a Credit Score is blocked

`feed_dealer.credit_limit.validate_credit_limit()` implements `plan_final_v2.2_mustfix.md`
MUST-3 verbatim:

```
committed = SUM(Batch Debt.outstanding_amount)   docstatus=1, status != "Đã trả"
          + SUM(Sales Order.grand_total)         docstatus=1, not Completed/Stopped, per_billed < 100
          + SUM(Sales Order.grand_total)         docstatus=0            [drafts]
reject when order_value > approved_limit - committed
```

Decisions taken here (each was a real fork, recorded instead of guessed):

- **MUST-3 supersedes the P1C prompt's acceptance example.** The prompt asks for "3 draft SO of
  20 million against a 50 million limit → the third submit is refused", which is the *pre*-v2.2
  behaviour: with drafts counted at save time the third draft is refused when it is CREATED
  (v2.2's whole point — without it, N drafts each pass and the customer ends up N times over the
  limit). Both behaviours are now covered by tests: T1 (the third draft is refused) and T2/T3
  (the submit-time re-check still refuses an order whose committed total grew after creation).
- **Drafts are not counted at submit** (`check_draft=False`, `for_submit=True`): the order being
  submitted is about to become a submitted order, so counting it as a draft as well would block
  a customer at exactly their limit.
- **A customer with no `Credit Score` document has a limit of 0 and is blocked.** The alternative
  (fail open) makes the gate bypassable by simply not creating the document. The thrown message
  says which document to create, and the manager override is the sanctioned path.
- **`limit_by_score` = the tier percentage × the customer's average submitted invoice total.** The
  plan calls this "avg batch value" but never defines it; the user chose invoice-history on
  2026-09-16. A customer with no history therefore has a calculated limit of 0 — the honest answer
  for an unknown customer, and the reason the override exists (T6/T8).
- **The submitted-order term counts the order's full `grand_total` while `per_billed < 100`,** so a
  partially invoiced order is counted twice (here and through the Batch Debt its invoice created).
  The spec prescribes it and the direction is safe: it over-reserves, never under-reserves.
- **The whitelisted API is permission-checked.** `get_credit_position` / `check_order_credit` are
  `@frappe.whitelist()`, i.e. open to any logged-in user; the first version returned any
  customer's limit to anybody (caught in self-review), and now checks read access to that
  customer's `Credit Score` and fails closed (T10).
- **Known weakness, not papered over:** `Batch Debt` zeroes `overdue_days` when a debt is settled
  (the P1B contract), so the score's "on-time / late" counters see the customer's live standing
  rather than their whole history. Persisting the worst overdue streak at settle time is the fix
  and needs a field, so it is deferred here rather than approximated silently.

### D16 — Closing the two P1B gaps: unreconcile and concurrent allocation

- **Unreconcile.** ERPNext v16's `Unreconcile Payment` does not cancel the Payment Entry; its
  `on_submit` calls `unlink_ref_doc_from_payment_entries()` (delinks the payment from the invoice,
  marks Payment Ledger Entries delinked) and `update_voucher_outstanding()`. Nothing fires on our
  `Payment Entry` hooks, so the allocation layer would keep claiming the debt was paid while AR
  says the invoice is open again. `feed_dealer.events.unreconcile_payment.on_submit` now cancels
  the allocations whose Batch Debt belongs to an invoice in the unreconcile's `allocations` list
  and recomputes those debts — the same end state a Payment Entry cancel produces (T8). Left
  genuinely unhandled: *cancelling* an `Unreconcile Payment` re-links nothing in v16 (the DocType
  has no `on_cancel`), so this hook does not re-create allocations either; re-reconciling in AR
  must be followed by running the allocation again.
- **Concurrency.** `_open_debts(..., for_update=True)` locks the debt rows the FIFO allocation is
  about to consume. Without it two payments submitted at the same moment both read the same
  `outstanding_amount` and can hand the same money out twice; a locking read sees the latest
  committed row, so the second caller waits and then finds the debt settled. Row order is fixed
  (`due_date asc, creation asc`) so two allocations cannot deadlock. Proven from a second DB
  connection (its `FOR UPDATE` must time out) *and* by a spy that watches a real submit request
  the lock (T9) — the first version of that test only called the helper itself and stayed green
  even with the lock switched off, which the mutation run caught.

## Risks / Trade-offs

- [Deploying to a live site that other people and apps are using] → Every master step is create-if-absent and name-resolved; nothing is renamed, re-parented or deleted; the only writes are new DocTypes, new roles/masters and acceptance fixtures named `P0-ACCEPT…`, which `cleanup()` removes.
- [A DocType name collision silently corrupts another app — this already happened once] → The collision check is now part of the shipped DocType contract (`platform-runtime`), and the incident is recorded in D4 rather than only in commit history.
- [Orphan columns left on `tabBatch` by the collision] → Accepted for now and left as a user decision, because dropping columns is irreversible DDL on a live site.
- [The bridge to the Mac is a single transient path (ngrok + Tailscale, IPv6-only host name)] → Acceptance evidence is captured as command output rather than as a live assertion, and long-running commands write their log to a file on the target so the result survives an MCP call timing out. When the path is down, work continues against the REST endpoint, which is independent.
- [Target is v16 while the plan was written for v15] → The four v16 differences found so far (UOM conversion model, aggregate field syntax, submitted-doc deletion, hook value shape) are each recorded with the failing message that revealed them, so the next version bump has a checklist to re-verify rather than a surprise.
- [`bench new-app` scaffolds a module folder nested as `feed_dealer/feed_dealer/feed_dealer/`] → Keep the generated layout (fighting it breaks Frappe's module-path conventions) and record the path in the README so reviewers do not mistake it for a mistake.
- [The generator owns the two non-trivial controllers] → Documented in the generator and README; the acceptance suite fails loudly if a controller is regenerated into a state that breaks behaviour. P1A's `on_submit` fix had already been lost once this way, so the P1A/P1B work was applied to the generator first and `--check` is run before every push.
- [Concurrent payments can over-credit a batch debt, and ERPNext's Unreconcile Payment bypasses our reversal] → Both were P1B's open gaps; D16 records the fixes (row lock on the debt rows, plus a hook on `Unreconcile Payment`) and the acceptance that proves each one. The remaining half — cancelling an Unreconcile Payment re-links nothing in v16 — is stated there rather than hidden.
- [The credit limit counts a partially invoiced order twice] → Prescribed by v2.2 MUST-3 and deliberately kept: over-reserving is the safe direction for a hard limit. If it starts blocking real orders in the field, the fix is to subtract the billed part (`per_billed`) and re-test T2/T3.
- [The credit score's on-time/late counters only see live debts] → Documented in D15 with the reason (settled debts zero `overdue_days`) and the fix (persist the worst overdue streak at settle time). The score, tier and limits stay deterministic in the meantime.
- [A customer with no Credit Score cannot buy on credit at all] → Accepted deliberately (D15): the gate fails closed, the message names the missing document, and a manager override is the sanctioned path. If the rollout needs a softer start, the switch belongs in `Feed Dealer Settings`, not in a silent default.

## Migration Plan

1. Push `apps/feed_dealer` to the Mac path the bench bind-mounts, verifying file-for-file equality.
2. `bench --site frontend install-app feed_dealer` (once), then `bench --site frontend migrate` so the DocTypes sync and the master-seed patch runs.
3. Run `bench --site frontend execute feed_dealer.setup.p0_acceptance.run` and capture the table; all checks must read PASS.
4. Re-run `migrate` and the acceptance script to prove idempotency.
5. Rollback: the app is additive. `bench --site frontend uninstall-app feed_dealer` removes its DocTypes and its records; the generated files are re-creatable from the generator, and the target site's pre-existing data was never touched. Once P0.5 has imported real debt, rollback becomes restore-from-backup and is no longer covered by this plan.
