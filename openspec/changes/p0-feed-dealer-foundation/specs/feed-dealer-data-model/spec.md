## Purpose

Define the core DocType contract for the feed dealer system: the allocation-only `Batch Debt`, its opening-balance rule, the read-only derived fields that must never be typed by a user, audit tracking on financial documents, the Single settings record, and the hook surface later phases will implement.

## ADDED Requirements

### Requirement: Core DocType inventory is installed

The `feed_dealer` app SHALL install the following DocTypes under module `Feed Dealer`: `Feed Dealer Settings` (Single), `Feed Batch`, `Batch Debt`, `Payment Allocation`, `Credit Score`, `Collateral`, `Action Item`, `Data Processing Consent`, `Debt Confirmation Slip`, `Batch Operation`, `Sales Return Request`, `AI Workflow Config`, `E-Invoice Log`, plus the child tables they reference.

Every DocType SHALL ship a Python controller whose class name follows Frappe's rule (`classname = doctype.replace(" ", "").replace("-", "")`), including child tables — a child table without a controller breaks `migrate` for its parent.

#### Scenario: DocTypes are present after migrate

- **WHEN** the DocType list is queried for module `Feed Dealer` after install and migrate
- **THEN** every DocType named above exists and is not marked as a custom (non-app) DocType

#### Scenario: Submittable DocTypes declare submittability

- **WHEN** `Batch Debt`, `Payment Allocation`, `Batch Operation`, `Debt Confirmation Slip` and `Sales Return Request` are inspected
- **THEN** each has `is_submittable = 1`

### Requirement: Payment Allocation is a standalone allocation record

`Payment Allocation` SHALL be a standalone submittable DocType (`is_submittable = 1`, not a child table) that links to its `Payment Entry`, so one FIFO slice of a payment is its own auditable, individually cancellable document.

It SHALL NOT be a child table of `Payment Entry`: `Document._submit()` writes the parent row *before* `doc_events.on_submit` fires, so a child row appended in the hook needs a nested re-save to persist at all, and cancelling the payment would leave the slice behind.

#### Scenario: Allocation is created per FIFO slice

- **WHEN** a submitted `Payment Entry` for a Customer is inspected after submit
- **THEN** one submitted `Payment Allocation` exists per debt slice the payment covered, each carrying `payment_entry`, `batch_debt`, `paid_amount`, `previous_paid` and `outstanding_after`

#### Scenario: Cancelling the payment reverses the allocations

- **WHEN** the `Payment Entry` is cancelled
- **THEN** every `Payment Allocation` it created has `docstatus = 2` and the debts they covered are recomputed back to their pre-payment outstanding amount

### Requirement: Batch Debt is an allocation layer only

`Batch Debt.outstanding_amount`, `paid_amount`, `returned_amount`, `status`, `overdue_days` and `late_payment_fee` SHALL be derived values and SHALL be marked read-only in the DocType metadata, so no user or integration can type them directly.

#### Scenario: Derived fields are read-only in metadata

- **WHEN** the `Batch Debt` DocType definition is inspected
- **THEN** each of `paid_amount`, `returned_amount`, `outstanding_amount`, `status`, `overdue_days` and `late_payment_fee` has `read_only = 1`

#### Scenario: Submitting with a typed derived value does not persist the typed value

- **WHEN** a `Batch Debt` is created with a hand-supplied `outstanding_amount` that contradicts `allocated_amount`
- **THEN** the stored `outstanding_amount` equals the value produced by the controller from `allocated_amount`, and the hand-supplied value is discarded

### Requirement: Sales invoice is conditional on opening balance

`Batch Debt.sales_invoice` SHALL NOT be a mandatory field at the DocType level (`reqd = 0`), and SHALL become mandatory only when `is_opening_balance = 0`. Opening-balance debts SHALL instead accept an opening journal entry reference.

#### Scenario: Ordinary debt without a sales invoice is rejected

- **WHEN** a `Batch Debt` with `is_opening_balance = 0` and an empty `sales_invoice` is saved
- **THEN** the save is rejected with a message naming the missing `sales_invoice` field

#### Scenario: Ordinary debt with a sales invoice is accepted

- **WHEN** a `Batch Debt` with `is_opening_balance = 0` and a submitted `Sales Invoice` is saved
- **THEN** the document saves successfully

#### Scenario: Opening-balance debt without a sales invoice is accepted

- **WHEN** a `Batch Debt` with `is_opening_balance = 1`, an `opening_journal_entry` and an empty `sales_invoice` is saved
- **THEN** the document saves and submits successfully

### Requirement: Batch Debt records the tax group it was sliced from

`Batch Debt` SHALL persist the invoice line's tax template (`item_tax_template`) that its slice came from, because an invoice can carry more than one tax treatment for the same batch and each is a separate debt.

#### Scenario: Two tax groups on one batch produce two debts

- **WHEN** a submitted `Sales Invoice` has two lines against the same `Feed Batch` with different `item_tax_template`
- **THEN** two `Batch Debt` documents exist for that invoice, one per tax template, and their `allocated_amount` sum to the net amount of those lines

#### Scenario: Re-submitting an invoice does not duplicate a tax group

- **WHEN** the `Sales Invoice.on_submit` hook runs again for an invoice whose tax group already has a submitted debt
- **THEN** no second debt is created for that group, and a group whose earlier attempt left a draft debt is completed rather than skipped

### Requirement: Opening-balance debts are flagged for reconciliation routing

`Batch Debt` SHALL record `is_opening_balance` and an opening journal entry reference, so that later payment allocation can route reconciliation to a Journal Entry instead of a Sales Invoice.

#### Scenario: Opening balance fields exist

- **WHEN** the `Batch Debt` DocType definition is inspected
- **THEN** it exposes a checkbox `is_opening_balance` and a link field `opening_journal_entry` targeting `Journal Entry`

### Requirement: Financial DocTypes are audited

DocTypes carrying financial or legal weight (`Batch Debt`, `Payment Allocation`, `Credit Score`, `Collateral`, `Debt Confirmation Slip`, `Batch Operation`) SHALL enable change tracking so every modification is logged.

#### Scenario: Track changes enabled

- **WHEN** each financial/legal DocType named above is inspected
- **THEN** it has `track_changes = 1`

### Requirement: Feed Dealer Settings is a Single holding operational parameters

The system SHALL provide a Single DocType `Feed Dealer Settings` exposing at minimum `late_payment_interest_rate`, `late_payment_mode`, `ai_model_name`, `voice_recording_retention_days` and `stt_provider`, and SHALL persist changes to them.

#### Scenario: Interest rate persists

- **WHEN** `late_payment_interest_rate` is set to a value and the settings document is saved
- **THEN** re-reading the Single returns the same value

#### Scenario: Settings document is a Single

- **WHEN** the `Feed Dealer Settings` DocType definition is inspected
- **THEN** `issingle = 1` and no other instance of the settings document can be created

### Requirement: Feed Batch supports split/merge and livestock sales

The lứa nuôi DocType SHALL be named `Feed Batch` (never `Batch`, which belongs to ERPNext's stock module) and SHALL carry the fields needed later for batch surgery: `parent_batch`, `split_operation`, `has_been_split` and a `livestock_sales` child table. Its document name SHALL be the plan's batch code (`LOT-{YYYY}-{#####}`).

#### Scenario: Split/merge fields exist on Feed Batch

- **WHEN** the `Feed Batch` DocType definition is inspected
- **THEN** it exposes `parent_batch` (Link to `Feed Batch`), `split_operation` (Link to `Batch Operation`), `has_been_split` (Check) and `livestock_sales` (Table)

#### Scenario: The lứa nuôi DocType does not shadow ERPNext's Batch

- **WHEN** the `Batch` DocType is inspected on the target site
- **THEN** its module is `Stock` (ERPNext's own), and the lứa nuôi records live in the separate `Feed Batch` DocType

### Requirement: Hook surface is registered as a skeleton

`hooks.py` SHALL register `on_submit` and `on_cancel` handlers for `Sales Invoice`, `on_submit` and `on_cancel` handlers for `Payment Entry`, and an `on_update` handler for `Feed Batch`, so later phases only fill in bodies rather than re-wiring the event surface.

#### Scenario: Doc events are registered

- **WHEN** `hooks.py` is inspected
- **THEN** `doc_events` contains entries for `Sales Invoice` (`on_submit`, `before_cancel`, `on_cancel`), `Payment Entry` (`on_submit`, `on_cancel`), `Sales Order` (`validate`, `before_submit`), `Unreconcile Payment` (`on_submit`) and `Feed Batch` (`on_update`), each pointing at an importable function belonging to this app

#### Scenario: Registered handlers are importable stubs

- **WHEN** each registered handler path is imported
- **THEN** the import succeeds and calling it on a document does not raise, because P0 bodies are deliberate no-op stubs

### Requirement: AI kill switch is configurable

The system SHALL provide an `AI Workflow Config` DocType whose records expose `workflow_name`, `enabled`, `last_disabled_by`, `reason` and a disabled-at timestamp, so any AI workflow can be switched off without a deploy.

#### Scenario: AI workflow can be disabled

- **WHEN** an `AI Workflow Config` record is created with `enabled = 0`, a reason and a disabling user
- **THEN** the record saves and reports `enabled` as false on re-read

### Requirement: Credit limit is enforced on Sales Order (draft and submit)

The system SHALL refuse a Sales Order whose value would push the customer past their approved credit limit, where the committed amount is the sum of non-settled submitted `Batch Debt` outstanding, submitted orders that are not yet fully billed, and draft orders. The check SHALL run through one shared function for every caller (Desk hook, whitelisted API, mobile clients), SHALL count open drafts so a customer cannot bypass the limit by creating many drafts, and SHALL re-check atomically at submit with a row lock on the customer's `Credit Score` document. The approved limit is `Credit Score.credit_limit`, and a customer with no `Credit Score` document has a limit of zero.

#### Scenario: A draft order beyond the approved limit is refused

- **WHEN** a customer with a 50,000,000 limit already holds 40,000,000 in draft orders and a 20,000,000 draft is created
- **THEN** the creation is rejected with the limit, the committed amount and the available amount, and the draft is not stored

#### Scenario: The submit is re-checked, and a refused submit does not write

- **WHEN** the committed total grows after a draft was created (a direct invoice appears, or the limit is lowered) and the draft is then submitted
- **THEN** the submit is rejected and the order keeps `docstatus = 0`

#### Scenario: Settling debt frees credit again

- **WHEN** the customer's outstanding debt is paid off
- **THEN** the available credit increases by the settled amount and an order that was refused before is accepted

#### Scenario: A customer without a Credit Score cannot buy on credit

- **WHEN** a Sales Order is created for a customer with no `Credit Score` document
- **THEN** it is rejected and the message names the missing document (the gate fails closed)

#### Scenario: The credit position API is permission-checked

- **WHEN** a user without read access to a customer's `Credit Score` calls the credit-position API
- **THEN** the call is rejected with a permission error instead of returning that customer's limit

### Requirement: Credit Score derives tier, limits and overrides deterministically

The `Credit Score` controller SHALL recompute, without any AI/LLM step, the score (`50 + 5 × on-time − 10 × late + 2 × completed batches`, clamped to 0–100), the tier, `limit_by_score` (tier percentage from `Feed Dealer Settings` × the customer's average submitted invoice total), `limit_by_collateral` (collateral at effective value), `limit_by_guarantee` and the final `credit_limit` (the minimum of the dimensions above zero, or `manual_limit` when overridden).

#### Scenario: Tier follows the frozen score table

- **WHEN** the score is 39, 40, 59, 60, 79, 80 or 100
- **THEN** the tier is Đồng, Bạc, Bạc, Vàng, Vàng, Kim Cương and Kim Cương respectively

#### Scenario: Only a manager may override, with a reason, and the override is stamped

- **WHEN** a user without a manager role sets `manual_override`, or a manager sets it without a reason or without a limit
- **THEN** the save is rejected
- **AND WHEN** a manager saves the override with a reason and a limit
- **THEN** `credit_limit` becomes the manual limit and `override_by` / `override_date` carry the manager and the timestamp

### Requirement: Unreconciling a payment reverses its allocation

When ERPNext's `Unreconcile Payment` delinks a submitted Payment Entry from an invoice, the system SHALL cancel the `Payment Allocation` records whose `Batch Debt` belongs to a delinked invoice and recompute those debts, so no debt keeps reporting payment that AR no longer recognises.

#### Scenario: A settled debt reopens after unreconcile

- **WHEN** a payment that settled a batch debt is unreconciled
- **THEN** its allocations are cancelled, the debt returns to its full outstanding amount with status "Chưa trả", the batch total is restored, and the Payment Entry itself remains submitted

### Requirement: FIFO allocation locks the debt rows it consumes

The system SHALL lock the customer's open `Batch Debt` rows (ordered oldest `due_date` first) while allocating a payment, so two payments submitted concurrently cannot both consume the same outstanding amount.

#### Scenario: A second session cannot take a locked debt row

- **WHEN** one session holds the allocation's row lock and another session issues `SELECT … FOR UPDATE` on the same rows
- **THEN** the second session fails with a lock-wait timeout and the allocation path is proven to request the lock during a real Payment Entry submit
