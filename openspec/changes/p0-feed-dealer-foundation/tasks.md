# Tasks — P0 Foundation

Every task names its evidence; a task is only `[x]` when that evidence exists (command output
captured, not a prose claim). `[–]` marks a task that was dropped as superseded, with the
reason recorded so the decision is not silently lost.

## 1. Runtime target

- [–] 1.1 Build a local `docker-compose.yml` + custom ERPNext v15 image. **Dropped:** the real
  runtime already exists (the user's ERPNext v16 site behind ngrok), and a second stack would
  pin v15 against a v16 target. The Compose/Dockerfile files were removed from the repo at the
  user's direction.
- [x] 1.2 Confirm the target site, version and access paths.
  *Evidence:* `api/method/frappe.auth.get_logged_user` → `{"message": "Administrator"}` over the
  ngrok HTTPS endpoint with token auth; `frappe.utils.change_log.get_versions` →
  `frappe 16.17.2`, `erpnext 16.16.0`, plus coexisting `camvlxd` and `custom_app`; the bench
  container bind-mounts `/Users/hoang/htdocs/erpnext/app_cam/feed_dealer` to
  `/home/frappe/frappe-bench/apps/feed_dealer`.
- [x] 1.3 Deploy the app source to the path the bench mounts.
  *Evidence:* push helper reports `local files: 83  remote files: 83` and `OK — matches local
  tree`; the remote tree is listed back and diffed against the local one.
- [x] 1.4 Prove `bench migrate` is clean **and idempotent** on the target site, capturing both runs.
  *Evidence:* `/tmp/mig1.log` and `/tmp/mig2.log` inside the bench container, each ending
  `=== EXIT 0 ===` after the full sync ("Updating installed applications… Executing `after_migrate`
  hooks… Queued rebuilding of search index"); runs were serialised because concurrent migrate fails
  on the `bench_migrate` file lock with `LockTimeoutError`.

## 2. App — `feed_dealer` skeleton

- [x] 2.1 Generate the app scaffold with `bench new-app feed_dealer` (not hand-invented) and commit it
  under `apps/feed_dealer`.
  *Evidence:* `apps/feed_dealer/pyproject.toml`, `feed_dealer/feed_dealer/hooks.py` and
  `modules.txt` (module `Feed Dealer`) exist and are the generated ones.
- [x] 2.2 Document the nested module path and the generator/regen sharp edge.
  *Evidence:* `apps/feed_dealer/README.md` documents `feed_dealer/feed_dealer/feed_dealer/`, the
  deploy steps, and that `Feed Batch`/`Batch Debt` controllers are generated from
  `.agent/gen_feed_dealer.py`; the seed patch id is listed in `feed_dealer/patches.txt`.
- [x] 2.3 Install the app on the site.
  *Evidence:* `get_versions` reports `feed_dealer: 0.0.1`.

## 3. Masters — seed baseline data

- [x] 3.1 Implement `feed_dealer/setup/masters.py` creating UOMs `Bao`/`Tấn`/`Kg`, Item Groups
  `Cám lợn`/`Cám gà`/`Cám cá`/`Thuốc thú y`, Customer Groups `Trại lớn`/`Trại vừa`/`Hộ nhỏ`/
  `Đại lý cấp 2`, Price Lists `Giá sỉ`/`Giá lẻ`, resolving every record by name and never
  overwriting an existing one. No Company is created — an existing VND company is verified and
  recorded in `Feed Dealer Settings`.
  *Evidence:* seeder output lists exactly the created records and
  `Feed Dealer Settings.default_company = Minh Phát Cám & VLXD`; companies reported as
  `[('SANLOAN','VND'), ('Minh Phát Cám & VLXD','VND'), ('DEMO POC CO','VND')]`.
- [x] 3.2 Seed the UOM conversions using the v16 model (`UOM Conversion Factor` with `category`).
  *Evidence:* `UOM Conversion Factor Bao -> Kg = 25.0 (Mass)`,
  `UOM Conversion Factor Tấn -> Kg = 1000.0 (Mass)`.
- [x] 3.3 Register the seed as a patch and verify it is idempotent.
  *Evidence:* the second seeder run created only what was missing (roles list empty) and raised no
  duplicate-entry error.

## 4. Roles

- [x] 4.1 Create `Feed Dealer Manager`, `Feed Dealer Staff`, `Feed Driver`, `Feed Farmer`.
  *Evidence:* acceptance check A9 — `4 roles, 4 item groups, 4 customer groups, 2 price lists`.
- [x] 4.2 Verify no feed-dealer DocType grants write/submit to a non-human role.
  *Evidence:* acceptance check A8 — `3 financial DocTypes expose no write path to non-manager roles`.

## 5. DocTypes

- [x] 5.1–5.14 Define all 19 P0 DocTypes with JSON metadata, a controller for every one (child tables
  included) and permission presets. `Feed Batch` is named to avoid ERPNext's own `Batch`, and keeps
  the plan's `LOT-{YYYY}-{#####}` batch code as its autoname.
  *Evidence:* every DocType's class name was checked against Frappe's own rule — 19/19 OK, e.g.
  `Feed Batch -> class FeedBatch`, `AI Workflow Config -> class AIWorkflowConfig`.
- [x] 5.15 Run `bench migrate` and verify all DocTypes appear in module `Feed Dealer` with no error.
  *Evidence:* 19 DocTypes in module `Feed Dealer`, `custom = 0` (queried over REST after migrate).
- [x] 5.16 Repair the `Batch` name collision and prove ERPNext's DocType is restored.
  *Evidence:* the stale `doctype/batch/` module was deleted from the Mac, and `Batch` now reports
  `module: Stock` with its own fields (`batch_id`, `item`, `batch_qty`, `stock_uom`, …) and
  `autoname: field:batch_id`; a name-collision sweep against `frappe`/`erpnext` source found no
  remaining collision.
- [ ] 5.17 Decide what to do about the 12 orphan columns left on ERPNext's `tabBatch`
  (`customer`, `animal_type`, `start_date`, `expected_end_date`, `quantity`, `start_weight`,
  `current_weight`, `status`, `split_operation`, `has_been_split`, `total_debt`, `notes`).
  Frappe restored the metadata but does not drop columns. Dropping them is irreversible DDL on a
  live site, so it is left to the user to approve.

## 6. hooks.py

- [x] 6.1 Register `doc_events` for `Sales Invoice` (`on_submit`, `on_cancel`), `Payment Entry`
  (`on_submit`, `on_cancel`) and `Feed Batch` (`on_update`) pointing at importable stub functions.
  *Evidence:* acceptance check A7 — `5 handlers registered and callable: Sales Invoice.on_submit,
  Sales Invoice.on_cancel, Payment Entry.on_submit, Payment Entry.on_cancel, Feed Batch.on_update`.

## 7. Permissions

- [x] 7.1 Manager full, Staff read-only on financial DocTypes, Driver read, Farmer `if_owner` read.
  *Evidence:* acceptance check A8.
- [x] 7.2 Verify with a Farmer-only user that another customer's `Feed Batch` is unreadable and the
  user's own is readable.
  *Evidence:* acceptance check A5 — `farmer sees 1 own Feed Batch(es) (own=LOT-2026-00638);
  other-customer LOT-2026-00639 hidden; has_permission(other)=False`.

## 8. Acceptance & handoff

- [x] 8.1 Implement `feed_dealer/setup/p0_acceptance.py` with the acceptance checks plus a `debug()`
  entry point that prints every failing check's real traceback (needed because `bench execute`
  masks the real exception as a `NameError`).
- [x] 8.2 Run the acceptance script and capture the output; every criterion must read PASS.
  *Evidence:* `TOTAL: 9   PASS: 9   FAIL: 0` and `P0 ACCEPTANCE: ALL PASS`, run twice in a row with
  the same result (idempotent fixtures).
- [x] 8.3 Write `apps/feed_dealer/README.md` (install steps, layout, where DocTypes live) and record
  the Phase 1 handoff (hooks to implement, controllers stubbed).
- [x] 8.4 Re-run `migrate` + acceptance after the bridge recovered, and confirm no secrets are staged.
  *Evidence:* acceptance re-run after the two migrates → `TOTAL: 9   PASS: 9   FAIL: 0`;
  `grep` for `ngrok|api_secret|api_key|password|passphrase` over `apps/` and `deploy/` → no hits;
  `.env` and `.agent/` are git-ignored (verified with `git check-ignore -v`).
- [x] 8.5 Commit the app + change artifacts. The change defines the financial DocTypes and the
  permission model (safety-exclusion zone), so every commit waited for the user's explicit
  sign-off — and got it, commit by commit.
  *Evidence:* `eb75222` (initial: P0 + P1A + P1B), `0355d6b` (review fixes), `0e89ce7` (untrack
  bytecode), `7c62129` (P1C).

## 9. Change hygiene

- [x] 9.1 Update the change's own artifacts (`proposal.md`, `design.md`, the three specs) to describe
  the real target environment, the `Feed Batch` rename, and the v16 constraints, so the contract
  does not contradict what shipped.
- [x] 9.2 Validate the change with `openspec validate`, and record the P0 result in `working.md`.
  *Evidence:* `openspec validate p0-feed-dealer-foundation` → "is valid"; `openspec list` →
  `24/24 tasks`; `working.md` records the P0 result, the environment blockers and the v16 notes.

## 10. Phase 1 — P1A (invoice → batch debt) and P1B (payment allocation)

Added after P0: the P1 prompts were executed against this same change so the data-model contract
keeps describing what actually shipped.

- [x] 10.1 (P1A) Create one `Batch Debt` per `(custom_batch, item_tax_template)` group on
  `Sales Invoice.on_submit`, ignoring lines without a batch.
  *Evidence:* acceptance T1/T2/T3 — one batched line → 1 debt of 1,000,000; two batches → 2 debts
  (1,000,000 + 450,000); an unbatched line's 100,000 stays in AR only.
- [x] 10.2 (P1A fix) Make the idempotency key the grouping key, `(sales_invoice, batch,
  item_tax_template)`, and persist `item_tax_template` on the debt.
  *Evidence:* acceptance T6 — one invoice, one batch, KCT line + VAT line (1,000,000 each) → two
  debts. Before the fix the same scenario produced a single 1,000,000 debt (the second group was
  matched as "already existing" and dropped).
- [x] 10.3 (P1A fix) Complete a leftover draft debt for a group instead of skipping that group
  forever; skip only a group that already has a submitted debt.
  *Evidence:* the hook now selects `docstatus` 0 vs 1 explicitly (see
  `events/sales_invoice.py::on_submit`); T6's second run re-runs the whole suite with no duplicate
  debts.
- [x] 10.4 (P1A) Keep the late-fee rate sourced from `Feed Dealer Settings` with a 0.00022 floor,
  and compute overdue before status.
  *Evidence:* P1B T5 — fee changes when the Settings rate is probed (1,320 at the site rate vs
  6,000 at 0.001), and a partly paid overdue debt stays "Quá hạn" with the fee charged on the
  reduced outstanding.
- [x] 10.5 (P1B) Record each FIFO slice as a standalone submitted `Payment Allocation` linked to
  its `Payment Entry`.
  *Evidence:* P1B T1 — `ACC-PAY-2026-00123` 400,000 → `ALLOC-…`: 1 submitted allocation, debt paid
  400,000, outstanding 600,000, status "Một phần", batch total 600,000.
- [x] 10.6 (P1B) Allocate FIFO by `due_date` across the customer's open debts.
  *Evidence:* P1B T3 — one 1,200,000 payment covers the older debt first (1,000,000, due in 5 days)
  and leaves 200,000 on the newer one (due in 20 days).
- [x] 10.7 (P1B) Cancel the payment → cancel its allocations → recompute the debts they touched.
  *Evidence:* P1B T4 — after `cancel()`, allocations read `docstatus = 2`, the debt returns to
  1,000,000 / "Chưa trả", the batch total is restored, and re-paying settles it again.
- [x] 10.8 (P1B) No double allocation on a re-fired hook, and the batch view never exceeds the
  payment.
  *Evidence:* P1B T6 — re-running `on_submit` returns `already allocated 1,000,000 of 1,000,000`,
  allocation count unchanged, `allocated <= paid_amount`.
- [x] 10.9 Re-run P0 and P1A after P1B to prove no regression.
  *Evidence:* `P0 ACCEPTANCE: ALL PASS` (`TOTAL: 9  PASS: 9  FAIL: 0`) and
  `P1A ACCEPTANCE: ALL PASS` (`TOTAL: 8  PASS: 8  FAIL: 0`, re-run after the P1C head).
- [x] 10.10 Two gaps recorded in `design.md` D14 are closed in P1C (see 11.5, 11.6): ERPNext's
  *Unreconcile Payment* now has a hook, and the FIFO read takes a row lock.
  *Evidence:* `design.md` D16; `P1B T8` and `P1B T9` below.
- [x] 10.11 Commit the P1A fix + P1B work.
  *Evidence:* `0355d6b` (refund gate + NULL-safe idempotency key) and `0e89ce7` (stop tracking
  `__pycache__`), on top of `eb75222` (P0 + P1A + P1B).

## 11. Phase 1 — P1C (credit limit) and the P1B hardening

- [x] 11.1 One shared gate `feed_dealer.credit_limit.validate_credit_limit()` implementing
  `plan_final_v2.2_mustfix.md` MUST-3 (outstanding debt + submitted-uninvoiced orders + drafts).
  *Evidence:* `feed_dealer/credit_limit.py`; every check in `p1c_acceptance` exercises it through a
  real Sales Order, and T9 proves the hook and the whitelisted API call the same function object.
- [x] 11.2 Sales Order `validate` (draft + update, drafts counted) and `before_submit` (row lock on
  the Credit Score document + atomic re-read).
  *Evidence:* `P1C T1` — two 20,000,000 drafts hold credit and the third is refused
  ("Còn khả dụng: 10,000,000đ"); `P1C T2` — a 30,000,000 invoice arriving after the draft was
  created refuses its submit; `P1C T3` — limit lowered to 30,000,000 after two drafts, first order
  submits, second refused and stays `docstatus=0` (the write is blocked, not undone).
- [x] 11.3 A customer with no `Credit Score` document is blocked (limit 0) with a message naming
  the missing document; paying a debt frees the credit again.
  *Evidence:* `P1C T4` (blocked → accepted after the limit was granted) and `P1C T5` (40,000,000
  debt blocks a 30,000,000 draft; after the payment the same order submits).
- [x] 11.4 `Credit Score` controller: deterministic score/tier, calculated vs approved limit,
  manager-only override with reason and audit stamp.
  *Evidence:* `P1C T7` (`0→Đồng 39→Đồng 40→Bạc 59→Bạc 60→Vàng 79→Vàng 80→Kim Cương 100→Kim Cương`),
  `P1C T8` (score 60 / Vàng → `limit_by_score` 8,000,000 = avg 10,000,000 × 80%; 7,000,000 accepted,
  9,000,000 refused) and `P1C T6` (calculated 0 refused; staff attempt refused at the gate and not
  persisted; manager missing-reason and missing-limit refused; override 5,000,000 stamped with the
  manager and a timestamp; 4,000,000 accepted, 6,000,000 refused).
- [x] 11.5 Reverse the allocation layer when ERPNext unreconciles a payment.
  *Evidence:* `P1B T8` — `Unreconcile Payment` delinks `ACC-PAY-2026-00150` from the invoice while
  the payment stays submitted; the debt returns to 1,000,000 / "Chưa trả" and the batch total is
  restored.
- [x] 11.6 Lock the debt rows during FIFO allocation.
  *Evidence:* `P1B T9` — a spy on `_open_debts` sees `for_update=True` during a real Payment Entry
  submit, and a second DB connection's `SELECT … FOR UPDATE` on the same rows fails with
  `(1205, 'Lock wait timeout exceeded')`.
- [x] 11.7 Mutation-check every new guard (break it, watch the test go red, restore).
  *Evidence:* with the submit re-check disabled `P1C T2`/`T3` failed; with the unreconcile hook
  disabled `P1B T8` failed; with the lock disabled `P1B T9` failed (and the first version of T9
  stayed green — it was rewritten to watch the production path, then re-checked).
- [x] 11.8 Self-review the new code for the money/permission surface.
  *Evidence:* the whitelisted credit APIs returned any customer's position to any logged-in user
  (now `_require_credit_read`, covered by `P1C T10`); the full suites re-run green:
  `P0 9/9`, `P1A 8/8`, `P1B 9/9`, `P1C 10/10`. OpenCodeReview is not installed in this session, so
  the diff was reviewed by hand against injection/null/permission/money patterns.
- [x] 11.9 Commit P1C.
  *Evidence:* `7c62129` — 19 files, +1857/−64 (code + tests + artifacts). Approved by the user after
  the RESULT report; the credit limit is a hard business limit plus an authorisation rule, so the
  commit only landed after explicit sign-off.
