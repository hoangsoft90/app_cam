## Purpose

Define the contract for running the `feed_dealer` app on the user's **existing** ERPNext v16 site: how the app reaches the bench, how it is installed and migrated, how the site proves it is reachable, and the rule that the repository tracks no credentials.

This capability replaces an earlier draft that specified a Docker Compose stack built by this repository. That stack was removed: the ERPNext runtime is owned by the user's machine, and the only durable artifact of this change is the app source under `apps/feed_dealer`.

## ADDED Requirements

### Requirement: The app is installed as a versioned app on the target site

`feed_dealer` SHALL be installed on the target ERPNext site by `bench install-app`, from source that lives in this repository (`apps/feed_dealer`), so that no DocType, role or master definition exists **only** in the target database.

#### Scenario: The site reports the app as installed

- **WHEN** an authenticated `frappe.utils.change_log.get_versions` request is made to the site
- **THEN** the response includes a `feed_dealer` entry alongside `frappe` and `erpnext`

#### Scenario: App source is the deployable unit

- **WHEN** `apps/feed_dealer` is inspected
- **THEN** it contains the DocType JSON/controller files, `hooks.py`, `patches.txt` and `pyproject.toml`, and installing it requires no file that is absent from the repository

### Requirement: The target runtime is an existing ERPNext v16 site

P0 SHALL target the site that already exists, and SHALL NOT require this repository to build or manage the ERPNext runtime.

#### Scenario: Site version is recorded, not assumed

- **WHEN** the target site's versions are read
- **THEN** `frappe` is 16.x and `erpnext` is 16.x, and the app's code depends only on behaviour verified against those versions

#### Scenario: Coexisting apps and data are left alone

- **WHEN** the site's installed apps and pre-existing masters are inspected before and after this change
- **THEN** the other installed apps are unchanged, and companies, UOMs, warehouses, item groups and customers that existed before are neither renamed, re-parented nor overwritten

### Requirement: The site answers HTTP and Administrator authenticates

The site SHALL be reachable over HTTPS and the `Administrator` identity SHALL authenticate successfully, so acceptance evidence can be gathered without a browser session.

#### Scenario: Site responds to an unauthenticated ping

- **WHEN** an HTTP request is made to the site's `api/method/ping` endpoint
- **THEN** the response is HTTP 200

#### Scenario: Token authentication identifies Administrator

- **WHEN** an API request carrying the configured API key/secret calls `frappe.auth.get_logged_user`
- **THEN** the response body is `{"message": "Administrator"}`

### Requirement: Migration is clean and re-runnable

`bench migrate` SHALL complete successfully on the target site with `feed_dealer` installed, and SHALL be re-runnable without error or structural change to the installed DocTypes.

#### Scenario: Migrate creates the app's DocTypes

- **WHEN** `bench --site <site> migrate` runs after this change is deployed
- **THEN** it completes without a DocType or schema error, and every DocType in module `Feed Dealer` is present

#### Scenario: A second migrate changes nothing structural

- **WHEN** `bench migrate` is run a second time
- **THEN** it completes successfully and the set of module `Feed Dealer` DocTypes, and the field set of each, is unchanged

### Requirement: DocType names do not collide with ERPNext's own

A DocType name is global across all installed apps, so this app SHALL NOT define a DocType whose name already belongs to `frappe` or `erpnext`.

#### Scenario: No shipped DocType name shadows an existing one

- **WHEN** every DocType name shipped by `feed_dealer` is compared against the DocTypes shipped by `frappe` and `erpnext`
- **THEN** there is no name collision

#### Scenario: A collided DocType is restored to its owning app

- **WHEN** a DocType that belongs to another app has a module owned by this app
- **THEN** the collision is removed from this app's source, and after the next migrate the DocType again reports its owning app's module

### Requirement: Credentials are not committed

Repository-tracked files SHALL NOT contain the site URL, API key, API secret, database password or administrator password.

#### Scenario: Only variable names appear in tracked files

- **WHEN** tracked files are searched for the values used to reach the site
- **THEN** no tracked file contains the real value, and the only reference is the environment variable names (`ERPNEXT_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET`) read from an untracked `.env`
