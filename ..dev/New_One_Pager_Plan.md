# New One Pager (Create Flow) — Implementation Plan

## 1. Purpose

This document defines the ordered implementation plan for **creating a new One Pager**: the Owner clicks **[+ New]** on the Registry, fills in the basic fields, and the app creates a `Draft` One Pager (`OP-####`, version `0.1.0`, Data Product status `In Definition`) that immediately appears in the Registry and opens in the Preview page.

It implements:

- The creation rules in [Requirements_and_Scope.md](../docs/Requirements_and_Scope.md) §4 (Creating), §5 (lenient validation), §7 (change log & versioning), §14 (identifiers).
- The `create` transition in [Backend_Design.md](../docs/Backend_Design.md) §2 (guard: Owner/SME group; side effects: create YAML, create Delta row, assign OP-ID, append change log) and the validation/permission rules in §4–§5.
- The tables in [Data_Model.md](../docs/Data_Model.md) §3–§5 (`one_pager_status`, `one_pager_authorized_users`, `change_log`, `id_sequences`).
- The volume layout in [Decision_Log.md](../docs/Decision_Log.md) §6 (`<volume_root>/<OP-ID>/<OP-ID>_v0.1.0.yml`).
- The Registry **[+ New]** button and the Editor **"New (empty form)"** state in [UI_Design.md](../docs/UI_Design.md) §4.1–§4.2.
- The layering rules in [Project_Structure.md](../docs/Project_Structure.md) §2b and the test expectations in [Testing_Strategy.md](../docs/Testing_Strategy.md).

It follows the same phased structure as [Registry_Page_Plan.md](Registry_Page_Plan.md) and [Preview_Page_Plan.md](Preview_Page_Plan.md), and borrows the "decisions made up front" style of [use-cases-page-implementation-plan.md](use-cases-page-implementation-plan.md).

## 2. Current State vs. Target

**Today** the app is read-only: Registry and Preview are data-backed ([registry.py](../app/views/registry.py), [preview.py](../app/views/preview.py)), but nothing in the codebase writes to Delta or to the document volume from the UI.

What already exists and can be reused:

| Asset | Where | Reuse |
|---|---|---|
| YAML document store with `write(one_pager_id, document, version)` | [documents/store.py](../src/onepagerapp/documents/store.py) | Writes `<id>/<id>_v<version>.yml` — exactly the layout creation needs |
| `OnePagerDocument` ↔ YAML serialization | [documents/serialization.py](../src/onepagerapp/documents/serialization.py) | Build the initial document; needs small additions (§5, step 8) |
| `one_pager_status`, `change_log` DDL | [liquibase/.../ddl/](../liquibase/bia_meta/onepager_app/ddl/) | Target tables for the new row + creation log entry |
| `ref_business_domains`, `ref_data_product_types` readers | [data_access/base.py](../src/onepagerapp/data_access/base.py) | Populate the Domain / Type dropdowns |
| Registry → Preview navigation via `st.session_state["preview_one_pager_id"]` + `st.switch_page` | [registry.py](../app/views/registry.py) `_navigate_to_preview` | Same mechanism to land on the new One Pager after creation |
| Permission stub module | [permissions.py](../src/onepagerapp/permissions.py) | Home for `can_create_one_pager()` |

**Target:**

- Registry shows a **[+ New]** button (header, and in the "No One Pagers yet" empty state).
- It opens the **Editor page in create mode**, showing the **Basics** section only (Data Product, Product Name, Business Domain, Product Type, Description, Owner, SMEs).
- **[Create Draft]** runs validation and a single service call that assigns the `OP-####` ID, writes `OP-####_v0.1.0.yml`, inserts the `one_pager_status` row, the `one_pager_authorized_users` rows, and the `creation` change log entry.
- On success the user lands on **Preview** for the new One Pager; the Registry lists it as `Draft` / `In Definition` / `0.1.0`.
- **[Cancel]** returns to the Registry without writing anything.

### Blocking gaps

| # | Gap | Where |
|---|---|---|
| 1 | `DatabricksConnection.execute_statement(statement)` takes no `parameters` argument, yet `lakehouse.py` already calls it with `parameters=[...]` and `%s` markers (Preview readers). Any parameterized write will fail the same way | [connection.py](../src/onepagerapp/data_access/connection.py), [lakehouse.py](../src/onepagerapp/data_access/lakehouse.py) |
| 2 | No DDL for `id_sequences` or `one_pager_authorized_users` | `liquibase/bia_meta/onepager_app/ddl/` |
| 3 | `DataAccess` has no write methods (ID generation, insert status row / authorized users / change log, uniqueness check) | [data_access/base.py](../src/onepagerapp/data_access/base.py) |
| 4 | No service layer: no `workflow.py`, `validation.py`, `id_generator.py`, `auth.py` | `src/onepagerapp/` |
| 5 | Initials extraction lives in the Preview **view** (`extract_initials`) and does not handle the corporate `<INITIALS>ADM@BECOC001.onmicrosoft.com` format (`MJOADM@…` → `MJO`, per [Architecture.md](../docs/Architecture.md) §4) | [preview.py](../app/views/preview.py) |
| 6 | `MockDataAccess` returns hardcoded rows from inside each method — a newly created One Pager cannot appear in Registry/Preview in `local-mock` mode | [mock.py](../src/onepagerapp/data_access/mock.py) |
| 7 | In `local-mock`, `ONE_PAGER_APP_VOLUME_PATH` points at `tests/fixtures/sample_one_pagers/` — writing there would pollute the test fixtures | [factory.py](../src/onepagerapp/data_access/factory.py) |
| 8 | `OnePagerDocument` has no `createdBy` / `createdAt` / `lastUpdated` / `changeLog` fields, and the serializer's list-item shapes (`requirement`/`priority`, `sourceName`/`sourceType`, `dataElementPreview`) do not match [structure_one_pager_v_1.json](../schemas/structure_one_pager_v_1.json) (`id`/`description`, `name`/`sourceSystem`, no `dataElementPreview`) | [models.py](../src/onepagerapp/models.py), [serialization.py](../src/onepagerapp/documents/serialization.py) |
| 9 | `jsonschema` is only a transitive dependency (in `uv.lock`, not in `pyproject.toml`) | [pyproject.toml](../pyproject.toml) |
| 10 | No Editor page, no **[+ New]** button | `app/views/`, [registry.py](../app/views/registry.py) |

## 3. Phase 0 — Decisions (resolve before coding)

Each decision below has a recommendation. Record the ones marked **(log)** in [Decision_Log.md](../docs/Decision_Log.md).

### D1 — v1 scope: create only, Basics section only
**Recommendation:** This feature delivers **creation** of a Draft with the **Basics** fields. Editing an existing One Pager, the other nine editor tabs, Save Draft (MINOR bumps), locking, and Submit for Review are the **Editor plan** (next feature) and are out of scope here.
**Why:** Creation is the only path that needs the OP ID, the `id_sequences` table, the authorized-users bootstrap and the "initial draft" change log entry. Keeping it separate ships a usable vertical slice (new One Pagers appear in the registry) and lays the service-layer foundation (`workflow.py`, `validation.py`, `id_generator.py`, `auth.py`) that editing will extend.

### D2 — Fields required at creation
Requirements §5 says the lenient tier needs only `productName` and `description`. But the Delta row and storage layout also need values that cannot be filled in later without a key change:

| Field | Required at create | Reason |
|---|---|---|
| `dataProduct` | Yes | Unique key of the Data Product (Data_Model §3), NOT NULL in `one_pager_status`; rename is out of scope (Requirements §16 #2) — so it must be right at creation |
| `productName` | Yes | Lenient tier |
| `description` | Yes | Lenient tier |
| `businessDomain` | Yes | NOT NULL in `one_pager_status`; dropdown from active `ref_business_domains` |
| `dataProductType` | Yes | NOT NULL; dropdown from active `ref_data_product_types` (schema enum `Foundational`/`Integrated`/`Augmented`) |
| `dataProductOwner.name/initials/email` | Yes | NOT NULL `owner_*` columns; `initials` is the authorization key |
| `dataProductOwner.team` | No | Nullable |
| `smes[]` | No (each SME needs name/initials/email if present) | Schema `smes.items.required` |
| `businessProblemStatement` | No | Optional here; filled in via the Editor later |

**Recommendation:** implement this as a named **"create" tier** in `validation.py` (lenient tier + the keys above), not by loosening the Delta DDL. **(log)**

### D3 — Creator vs. Owner
Requirements §4 stores the **creator** separately from the **Owner**; Data_Model §3 (`one_pager_authorized_users` sync) says "the authenticated user is inserted as `owner`" on creation. These conflict when someone creates a One Pager on behalf of another Owner.
**Recommendation:** pre-fill the Owner fields with the current user (editable). Write `created_by` = creator's initials. Populate `one_pager_authorized_users` **from the document** (`dataProductOwner` → `owner`, `smes` → `sme`), not from the creator. Require that the creator's initials appear as Owner **or** SME, otherwise block with "Add yourself as an SME to keep edit access" — this prevents creating a Draft nobody present can edit. **(log)**

### D4 — `dataProduct` format and uniqueness
The schema only says "registered unique name". It is used as the unique key and in the Git path (`<data_product>/<OP-ID>.yml`, Architecture §6).
**Recommendation:** enforce `^[a-z][a-z0-9_]{1,62}$` (matches existing samples `person`, `customer_master`), normalize by trimming, and reject duplicates with a pre-check (`data_product_exists()`). Because Delta does not enforce `UNIQUE`, re-check after insert (count rows for the `data_product`); if > 1, roll back the newer row and report "already exists". **(log)** — confirm the naming rule with the Data Product Center team.

### D5 — Write order and failure handling for a multi-store create
Architecture §7 / Data_Model §5 say "Delta first, then YAML" for **saves**. For **creation**, nothing references the new OP ID until the `one_pager_status` row exists, so a different order makes every partial failure harmless:

1. Generate `OP-####` (a gap on later failure is harmless — Data_Model §4).
2. Write `OP-####/OP-####_v0.1.0.yml` to the volume (an orphan file under an unreferenced OP-ID is harmless and detectable — Decision_Log §6 "Disaster recovery").
3. Insert `one_pager_authorized_users` rows.
4. Insert the `change_log` `creation` entry.
5. Insert the `one_pager_status` row **last** — this is what makes the One Pager visible in Registry/Preview.

On a failure at step 3–5, best-effort delete what steps 3–4 inserted for that OP-ID, log it, return a `CreateError`; the form content stays in `st.session_state` (UI_Design §4.2 "Save error"). The file from step 2 is left in place (never referenced). **(log)** — this is a deliberate, documented deviation from "Delta first" that applies to creation only.

### D6 — ID generation
**Recommendation:** `id_generator.py` with `next_id(id_type) -> str` over a `DataAccess.next_sequence_value(id_type)` primitive, using the compare-and-swap + re-read + retry (max 5) algorithm from [use-cases-page-implementation-plan.md](use-cases-page-implementation-plan.md) D5. Format `OP-{n:04d}`. If the Use Cases plan lands first, **generalize its `_generate_use_case_id()`** rather than adding a second implementation; `id_sequences.sql` is created by whichever plan lands first.

### D7 — Identity and permissions in v1
**Recommendation:**
- Add `src/onepagerapp/auth.py` with `initials_from_username(username)`: first match the corporate pattern `^([A-Za-z]{2,4})ADM@` → uppercase group; otherwise fall back to the current heuristic. Move `extract_initials` out of `preview.py` to use it. (The Use Cases plan's `initials_from_user` should become an alias of this.)
- Add `can_create_one_pager(user) -> bool` to `permissions.py`. UC group names are still undecided (Architecture §4), so v1 returns `True` for any authenticated user, consistent with the Use Cases plan D7 — **but** the check is called both by the Registry (to show [+ New]) and by the service (to enforce), so plugging in the group check later is a one-function change.

### D8 — Page placement and navigation
UI_Design §4.1 says [+ New] "opens the Editor with a blank document"; §2 says the Editor is "hidden unless editing".
**Recommendation:** create `app/views/editor.py` now, with **create mode only** (the Editor plan adds edit mode). Streamlit 1.38 cannot hide a single page in `st.navigation`, and `st.switch_page` requires the page to be registered, so register it as **"Editor"**; when opened directly without a create/edit intent in session state it shows "Start from the Registry ([+ New]) or from a One Pager's [Edit] action" with a link to the Registry.
Intent is passed like Registry → Preview: `st.session_state["editor_mode"] = "create"` then `st.switch_page("views/editor.py")`.

### D9 — Initial document content and schema version
The created YAML contains: `structureDefinition`, `dataProduct`, `productName`, `businessDomain`, `dataProductType`, `onePagerStatus: Draft`, `dataProductStatus: In Definition`, `version: 0.1.0`, `dataProductOwner`, `smes` (if any), `description`, `businessProblemStatement` (if given), `createdBy` (display name — Data_Model §5), `createdAt`, `lastUpdated`, and a one-item `changeLog` (`0.1.0`, now, creator name, "Initial draft created").
**Recommendation:** pin `structureDefinition` to a single constant (`CURRENT_STRUCTURE_DEFINITION`) and use the same string in `one_pager_status.structure_definition`. Fixtures use `structure_one_pager_v_1.json` while Data_Model §3 uses `structure_one_pager/structure_one_pager_v_1.json` — pick one. **(log)**
Validate the generated dict with `jsonschema` against the schema before writing — it must pass all top-level `required` fields.

### D10 — Change log for creation
One entry: `event_type = "creation"`, `version = "0.1.0"`, `summary = "Initial draft created"`, `to_status = "Draft"`, `status_field = "one_pager_status"`, `from_status = NULL`. No owner-authored change summary is asked for on creation (the event is system-generated, Requirements §7).

### D11 — Input hygiene
All free-text fields are trimmed, HTML tags stripped and length-capped before storage (Architecture §8) via `validation.sanitize_text()`; rendered later without `unsafe_allow_html`. Email format validated with a simple pattern (schema `format: email`). All SQL uses bound parameters (fix from gap #1) — never f-strings of user input.

### Explicitly deferred

| Feature | Blocked by / belongs to |
|---|---|
| Other editor tabs (Business Problem, Use Cases, BRs, Sources, …) | Editor plan |
| Save Draft (MINOR bump), change summary field | Editor plan |
| Locking on enter/exit | Editor plan + `locking.py` |
| Submit for Review | Editor plan + `workflow.py` transitions + strict validation |
| Linking Use Cases at creation | Use Cases plan + Editor plan (`use_case_references`) |
| BR-### generation | Editor plan (Business Requirements tab) |
| UC-group-based role check | Group names not decided (Architecture §4) |

## 4. Phase 1 — Storage

| # | Step | Detail |
|---|---|---|
| 1 | Add `id_sequences.sql` (if not already added by the Use Cases plan) | Columns per [Data_Model.md](../docs/Data_Model.md) §3; seed `('OP', 0), ('UC', 0), ('BR', 0)` in the same changeset. If DEV already has seeded `one_pager_status` rows (see [seed_one_pager_status_dev.sql](seed_one_pager_status_dev.sql)), the DEV seed must set `OP` to the highest existing number so new IDs do not collide |
| 2 | Add `one_pager_authorized_users.sql` | Columns per Data_Model §3; composite logical PK (`one_pager_id`, `user_initials`) |
| 3 | Register in [root.changelog.databricks.yaml](../liquibase/bia_meta/onepager_app/root.changelog.databricks.yaml) | After the reference tables, before/alongside the operational tables; `id_sequences` must precede any code that creates IDs |
| 4 | DEV seed for authorized users | Add rows for the already-seeded `one_pager_status` records so the table is consistent from day one |

## 5. Phase 2 — Core Layer (`src/onepagerapp/`, no Streamlit)

| # | Step | Detail |
|---|---|---|
| 5 | **Fix parameterized SQL** (prerequisite) | Extend `DatabricksConnection.execute_statement(statement, parameters: list[StatementParameterListItem] \| None = None)` and pass them through to `statement_execution.execute_statement`. The Statement Execution API uses **named** markers (`:one_pager_id`), not `%s` — convert the existing Preview readers too. This fixes Preview in `databricks` mode as a side effect |
| 6 | Dependencies | Add `jsonschema` explicitly to `pyproject.toml` `dependencies` (pin to the version already in `uv.lock`); `uv lock` |
| 7 | Models in [models.py](../src/onepagerapp/models.py) | `PersonRef(name, initials, email, team=None)`; `NewOnePagerInput(data_product, product_name, business_domain, data_product_type, description, owner: PersonRef, smes: list[PersonRef], business_problem_statement="")`; `CurrentUser(username, initials, display_name)`; `ValidationError(field_path, message)` (Backend_Design §4); `CreateResult(one_pager_id, version)`. Extend `OnePagerDocument` with `created_by`, `created_at`, `last_updated`, `change_log: list[dict]` (all optional/defaulted so existing readers keep working) |
| 8 | Serialization in [serialization.py](../src/onepagerapp/documents/serialization.py) | Read/write `createdBy`, `createdAt`, `lastUpdated`, `changeLog`; omit `businessProblemStatement` when empty. Align list-item keys with the schema (`businessRequirements: id/description`, `dataSources: name/sourceSystem/description`) **or** record that the schema needs a v1.1 — creation writes none of these lists, so this can be a follow-up, but log it as a known gap |
| 9 | `auth.py` | `initials_from_username()` per D7; `resolve_current_user(username) -> CurrentUser`. Replace `extract_initials` in [preview.py](../app/views/preview.py) with this |
| 10 | `validation.py` | `sanitize_text(value, max_len)`; `validate_create(input) -> list[ValidationError]` (D2 fields, D4 pattern, email format, SME required fields, D3 creator ∈ owner/SMEs, no duplicate SME initials, owner not also an SME); `validate_schema(doc_dict) -> list[ValidationError]` using `jsonschema` (Draft-07) against `schemas/structure_one_pager_v_1.json`. Errors are returned, never raised (Backend_Design §4) |
| 11 | `id_generator.py` | `next_id(data_access, id_type)` → `OP-0001` etc. per D6; formatting table `{"OP": 4, "UC": 3, "BR": 3}` |
| 12 | `permissions.py` | Add `can_create_one_pager(user: CurrentUser) -> bool` (v1: authenticated) and `PermissionDeniedError` |
| 13 | Extend the `DataAccess` ABC | `next_sequence_value(id_type) -> int`; `data_product_exists(data_product) -> bool`; `count_data_product(data_product) -> int`; `insert_authorized_users(one_pager_id, users: list[tuple[PersonRef, role]])`; `append_change_log(entry: ChangeLogEntry)`; `insert_one_pager_status(row)`; `delete_one_pager_records(one_pager_id)` (compensation only — deletes status/authorized-users/change-log rows for an OP that never became visible). Document writes stay on `OnePagerDocumentStore.write()` |
| 14 | Implement in `mock.py` **first** | Refactor the hardcoded registry rows / headers / change logs into **mutable dicts built in `__init__`** so `get_registry`, `get_registry_status_counts`, `get_one_pager_status`, `get_change_log` read from them and the new writes append to them. Mock sequence starts at the highest seeded OP number. Mock document writes go to a **temp directory** layered over the fixtures dir (read: temp first, then fixtures) so fixtures are never modified (gap #7) — e.g. a small `LayeredDocumentStore` or a `write_path` argument on `OnePagerDocumentStore` wired in `factory.py` |
| 15 | Implement in `lakehouse.py` | Parameterized `INSERT`s for the three tables; CAS `UPDATE` on `id_sequences` with re-read + retry; `SELECT COUNT(*)` for uniqueness; compensation `DELETE`s scoped by `one_pager_id`. Timestamps via `current_timestamp()` in SQL; the same instant is used for YAML `createdAt` (pass it as a parameter to keep them identical) |
| 16 | `workflow.py` — `create_one_pager(input, user, data_access, document_store) -> CreateResult \| list[ValidationError]` | Orchestrates: permission check → sanitize → `validate_create` → uniqueness pre-check → `next_id("OP")` → build `OnePagerDocument` (D9) → `validate_schema` → write steps 2–5 of D5 → post-insert uniqueness re-check (D4) → return result. On storage failure: compensate, log a structured audit event (no field values, Architecture §8), raise/return `CreateError` with a user-safe message. Pure Python, no Streamlit; dependencies injected for testability |

> **Security:** every value reaching SQL goes through bound parameters (step 5). Descriptions/names are untrusted — stored sanitized, rendered as text. Log only OP IDs, user initials and outcomes, never field values.

## 6. Phase 3 — Presentation (`app/`)

| # | Step | Detail |
|---|---|---|
| 17 | Resolve the current user once | In [app.py](../app/app.py), store `st.session_state.current_user_info = auth.resolve_current_user(username)` next to `current_user`, **before** `pg.run()` so pages can use it on first render (today the user is resolved after the page runs) |
| 18 | [+ New] on the Registry | Top-right of the header and in the "No One Pagers yet" empty state ([UI_Design.md](../docs/UI_Design.md) §4.1). Shown only if `can_create_one_pager(user)`. On click: clear any previous create draft in session, set `editor_mode = "create"`, `st.switch_page("views/editor.py")` |
| 19 | Create `app/views/editor.py` (thin view) | Header "New One Pager" + `Draft` badge + `v0.1.0`. **Basics** section: Data Product (text, help text with naming rule), Product Name, Business Domain (select, active ref values), Product Type (select), Description (text area), Owner (name / initials / email / team — pre-filled from current user), SMEs (`st.data_editor` with `num_rows="dynamic"`, columns name/initials/email/team), optional Business Problem Statement. Use schema `description`s as placeholder/help text (UI_Design §4.2 "New (empty form)"). All form values live in `st.session_state["create_form"]` so they survive re-runs and failed saves |
| 20 | Bottom bar | **[Create Draft]** → `workflow.create_one_pager(...)`; **[Cancel]** → confirmation dialog if any field is filled, then back to the Registry. No change-summary field on creation (D10) |
| 21 | Results | Validation errors: inline `st.error` under each offending field plus a summary list at the bottom (UI_Design §4.2 "Validation errors"). Duplicate `dataProduct`: field-level error "A One Pager for this Data Product already exists" (+ link to it if the ID is known). Storage failure: banner "Create failed — your changes are preserved, please retry." (no raw exception). Success: clear `create_form`, set `preview_one_pager_id = new_id`, `st.toast("Created OP-####")`, `st.switch_page("views/preview.py")` |
| 22 | Double-submit guard | Disable **[Create Draft]** while the call runs and store the created ID in session; a re-run after success must not create a second One Pager |
| 23 | Register the page | Add `st.Page("views/editor.py", title="Editor")` to `st.navigation` in [app.py](../app/app.py); direct-visit state per D8 |
| 24 | Caching | Ref lookups stay `@st.cache_data`. If any registry-level cache is added later, clear it after a successful create (UI_Design §6: "invalidated explicitly after writes") |

## 7. Phase 4 — Tests

Per [Testing_Strategy.md](../docs/Testing_Strategy.md). Services receive fakes by constructor/argument injection — no patching of internals.

| # | Layer | Coverage |
|---|---|---|
| 25 | Unit — `test_auth.py` | `MJOADM@BECOC001.onmicrosoft.com` → `MJO`; `alice.brown@company.com` → `AB`; empty/unknown formats |
| 26 | Unit — `test_validation.py` | Create tier: passes with the D2 minimum; each missing required field → one `ValidationError` with the right `field_path`; bad `dataProduct` pattern; bad email; SME missing initials; creator not in owner/SMEs; HTML stripped and length capped by `sanitize_text`; generated document passes `jsonschema` |
| 27 | Unit — `test_id_generator.py` | Formatting (`OP-0001`, `UC-001`, `BR-001`); CAS conflict on first attempt → retry succeeds; exhausted retries → error |
| 28 | Unit — `test_workflow_create.py` | Happy path with `MockDataAccess` + temp-dir store: status `Draft` / `In Definition`, version `0.1.0`, `created_by` = creator, authorized users = owner + SMEs, one `creation` change-log entry, YAML file exists at `OP-####/OP-####_v0.1.0.yml` and round-trips; new row visible in `get_registry` and `get_one_pager`; duplicate `dataProduct` rejected; permission denied path; **failure injection**: store write fails → nothing in Delta fakes; status insert fails → authorized users + change log compensated, `CreateError` returned |
| 29 | Unit — serialization | New fields round-trip; empty `businessProblemStatement` omitted; fixtures still load |
| 30 | Integration — `tests/integration/test_create_one_pager.py` | Against a live warehouse with a reserved ID range / cleanup (`OP-99%`): create round-trip across all three tables + volume; two concurrent `next_sequence_value("OP")` calls (threads) → distinct values; a `productName` / `description` containing `'; DROP TABLE …` stored verbatim (proves parameterization); teardown deletes rows and the volume folder |
| 31 | Smoke — `AppTest` (mock mode) | Registry shows [+ New]; Editor create mode renders; submitting empty form shows errors; filling the minimum and clicking [Create Draft] lands on Preview for the new ID |

## 8. Phase 5 — Ship

| # | Step | Detail |
|---|---|---|
| 32 | Quality gates | `ruff check` + `ruff format` + `mypy` + `pytest tests/unit/` |
| 33 | Docs | Record D2, D3, D4, D5, D9 in [Decision_Log.md](../docs/Decision_Log.md); note in [UI_Design.md](../docs/UI_Design.md) §4.2 that the Editor's create mode shows Basics only in v1 |
| 34 | Bump `VERSION` | In [__version.py](../src/onepagerapp/__version.py) (currently `0.1.5.dev2`). Per [Dev_Notes.md](../docs/Dev_Notes.md), pip skips reinstalling the wheel without a version bump |
| 35 | Deploy | Bundle deploy → Liquibase migrate (new `id_sequences`, `one_pager_authorized_users`) → set the DEV `OP` sequence past seeded IDs → create a One Pager in DEV and verify Registry, Preview, the three tables and the volume file |

## 9. Verification Checklist

- [ ] Registry shows **[+ New]**; clicking it opens the Editor in create mode with the Owner pre-filled.
- [ ] Submitting an empty form shows an error for every required field; nothing is written.
- [ ] Invalid `dataProduct` or duplicate `dataProduct` is rejected with a field-level message.
- [ ] A valid submission creates `OP-####` (next number), lands on Preview, and shows `Draft`, `In Definition`, `v0.1.0`, the owner, and a change-log line "Initial draft created".
- [ ] The Registry lists the new row; the metric card for `Draft` increases by one.
- [ ] `OP-####/OP-####_v0.1.0.yml` exists and validates against the schema.
- [ ] `one_pager_authorized_users` contains the owner (`owner`) and each SME (`sme`).
- [ ] In `local-mock`, `tests/fixtures/sample_one_pagers/` is unchanged after creating.
- [ ] Clicking [Create Draft] twice / re-running after success does not create a duplicate.
- [ ] Preview still works in `databricks` mode (parameterized SQL fix).

## 10. Files Touched — Summary

**New files**
- `liquibase/bia_meta/onepager_app/ddl/id_sequences.sql` (unless added by the Use Cases plan)
- `liquibase/bia_meta/onepager_app/ddl/one_pager_authorized_users.sql`
- `src/onepagerapp/auth.py`
- `src/onepagerapp/validation.py`
- `src/onepagerapp/id_generator.py`
- `src/onepagerapp/workflow.py`
- `app/views/editor.py`
- `tests/unit/test_auth.py`, `test_validation.py`, `test_id_generator.py`, `test_workflow_create.py`
- `tests/integration/test_create_one_pager.py`

**Edited files**
- `liquibase/bia_meta/onepager_app/root.changelog.databricks.yaml`
- `pyproject.toml`, `uv.lock` (`jsonschema`)
- `src/onepagerapp/data_access/connection.py` (parameters)
- `src/onepagerapp/data_access/base.py`, `mock.py`, `lakehouse.py`, `factory.py`
- `src/onepagerapp/documents/serialization.py` (and `store.py` if the layered write path lives there)
- `src/onepagerapp/models.py`, `src/onepagerapp/permissions.py`
- `app/app.py`, `app/views/registry.py`, `app/views/preview.py` (use `auth.initials_from_username`)
- `docs/Decision_Log.md`, `docs/UI_Design.md`, `src/onepagerapp/__version.py`

## 11. Sequencing Note

Step 5 (parameterized SQL) is a prerequisite for every Lakehouse write and should land first as its own small change. After that, Phase 1 and steps 7–14 + Phase 3 can run **in parallel**: build the whole create flow against `MockDataAccess` and a temp-dir document store, then plug in `LakehouseAccess` (step 15) once `id_sequences` and `one_pager_authorized_users` exist.

Coordinate with the Use Cases plan: both need `id_sequences` and an initials helper — whichever lands first creates them and the other reuses them. The `workflow.py`, `validation.py`, `auth.py` and `id_generator.py` modules created here are the foundation the Editor plan extends (Save Draft, Submit for Review, locking).
