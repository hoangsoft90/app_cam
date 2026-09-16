## Purpose

Establish the baseline master data and role model every later phase assumes: company/warehouse/UOM, feed item groups, customer tiers, wholesale/retail price lists, and the four feed-dealer roles with their permission posture.

## ADDED Requirements

### Requirement: Baseline masters exist and are resolvable by name

The system SHALL provide a seed routine that creates the baseline masters if absent, resolving every record by its name rather than by a hard-coded identifier, so that Python code never hard-codes Company, Warehouse or Price List names.

#### Scenario: Seed creates the required masters

- **WHEN** the seed routine runs against a site with `feed_dealer` installed
- **THEN** the following exist: a Company whose default currency is VND, a primary Warehouse, UOMs `Bao` / `Tấn` / `Kg`, Item Groups `Cám lợn` / `Cám gà` / `Cám cá` / `Thuốc thú y`, Customer Groups `Trại lớn` / `Trại vừa` / `Hộ nhỏ` / `Đại lý cấp 2`, and Price Lists `Giá sỉ` / `Giá lẻ`

#### Scenario: Seed is idempotent

- **WHEN** the seed routine runs a second time on the same site
- **THEN** it does not raise a duplicate-entry error and does not create duplicate masters

#### Scenario: UOM Bao converts to kilograms

- **WHEN** the UOM conversion for `Bao` is inspected on the target site
- **THEN** a `UOM Conversion Factor` record exists with `from_uom = Bao`, `to_uom = Kg` and `value = 25`, carrying the `category` that ERPNext v16 requires (the v15 `UOM.conversion_factor` field no longer exists)

### Requirement: Feed dealer roles exist

The system SHALL define four roles — `Feed Dealer Manager`, `Feed Dealer Staff`, `Feed Driver`, `Feed Farmer` — so that permissions can be assigned per DocType without per-user customisation.

#### Scenario: All four roles are present

- **WHEN** the Role list is queried after install and seed
- **THEN** all four feed-dealer roles exist exactly once

### Requirement: Manager holds full control on core documents

A user with only the `Feed Dealer Manager` role SHALL be able to create, read, write, submit and amend the feed-dealer transaction DocTypes.

#### Scenario: Manager can create and submit a Batch Debt

- **WHEN** a Manager-only user creates and submits a valid `Batch Debt`
- **THEN** the document is saved with `docstatus = 1`

### Requirement: Farmer is restricted to own records

A user with only the `Feed Farmer` role SHALL NOT read records belonging to another customer, and access to feed-dealer DocTypes SHALL be governed by an owner-based permission rule rather than unrestricted read.

#### Scenario: Farmer cannot read another customer's Feed Batch

- **WHEN** a Farmer-only user requests a `Feed Batch` owned by a different user
- **THEN** the read is denied and the record is absent from the list result

#### Scenario: Farmer can read own Feed Batch

- **WHEN** a Farmer-only user requests a `Feed Batch` that the same user owns
- **THEN** the record is returned

### Requirement: AI access posture is read-only by design

The permission model SHALL keep financial DocType writes limited to human-facing feed-dealer roles, so that a later read-only AI identity can be added without granting it any write path.

#### Scenario: No role grants write to an automated identity

- **WHEN** permissions on `Batch Debt`, `Payment Allocation` and `Credit Score` are inspected
- **THEN** no permission row targets a non-human feed-dealer role with `write` or `submit` enabled
