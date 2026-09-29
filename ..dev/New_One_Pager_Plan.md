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

## 3. Phase 0 — Decisions

All questions raised while analysing the docs are **decided** below. Each decision states what was chosen, the alternatives considered, and why. Decisions marked **(log)** are copied into [Decision_Log.md](../docs/Decision_Log.md) in step 33, because they refine or deviate from what the docs currently say.

### Decision summary

| # | Question | Decision |
|---|---|---|
| D1 | What does this feature cover? | Create a Draft from the **Basics** fields (+ optional Business Problem Statement). Editing, other tabs, locking, submit → Editor plan |
| D2 | Which fields are required at creation? | A new **"create" validation tier**: `dataProduct`, `productName`, `description`, `businessDomain`, `dataProductType`, Owner name/initials/email |
| D3 | Creator vs. Owner — who gets edit rights? | Authorized users come from the **document** (Owner + SMEs); the **creator must be the Owner or an SME** |
| D4 | `dataProduct` format and uniqueness | `^[a-z][a-z0-9_]{1,62}$`; pre-check + post-insert re-check; lower `OP` number wins a race |
| D5 | Write order across volume + Delta | YAML → authorized users → change log → `one_pager_status` **last**; compensate on failure |
| D6 | ID generation | Shared `id_generator.py`, compare-and-swap on `id_sequences`, 5 retries, `OP-{n:04d}` |
| D7 | Identity and permission check | `auth.py` extracts initials (corporate `…ADM@` pattern first); `can_create_one_pager()` = any authenticated user in v1 |
| D8 | Where does the create form live? | New `app/views/editor.py` ("Editor" page) in create mode; opened by [+ New] via session state |
| D9 | `structureDefinition` value and document content | `structure_one_pager_v_1.json` (file name resolved inside `schemas/`); document validated with `jsonschema` before writing |
| D10 | Change log on creation | One `creation` entry, `0.1.0`, "Initial draft created", `to_status = Draft` |
| D11 | Input hygiene and field limits | Trim + strip HTML + fixed max lengths; initials `^[A-Z]{2,5}$`; bound SQL parameters |
| D12 | Mock-mode document writes | `OnePagerDocumentStore` gets an optional `write_path`; mock uses a per-process temp dir, fixtures are read-only |
| D13 | Serializer vs. schema mismatch for list sections | Not touched by creation; the JSON Schema is the source of truth and the Editor plan aligns serializer + fixtures |
| D14 | DEV `OP` sequence start | Seed `OP` = 6 in DEV (highest seeded `OP-0006`); mock sequence starts after its highest seeded ID |
| D15 | UI widget choices | `st.form` for scalar fields, `st.data_editor(num_rows="dynamic")` for SMEs, `st.dialog` for the Cancel confirmation |

### D1 — Scope: create only, Basics section only
**Decision:** This feature delivers **creation** of a Draft One Pager from the **Basics** fields (`dataProduct`, `productName`, `businessDomain`, `dataProductType`, `description`, `dataProductOwner`, `smes`) plus an **optional** `businessProblemStatement`. Editing an existing One Pager, the other editor tabs, Save Draft (MINOR bumps), locking and Submit for Review belong to the **Editor plan** (next feature).
**Alternatives considered:** building the full 10-tab Editor at once (too large for one vertical slice, and blocked by the Use Cases and locking work); a separate "New One Pager" page unrelated to the Editor (would be thrown away when the Editor arrives).
**Why:** Creation is the only path that needs the OP ID, `id_sequences`, the authorized-users bootstrap and the "initial draft" change log entry. Shipping it alone gives a usable slice (new One Pagers appear in the Registry and Preview) and lays the service-layer foundation (`workflow.py`, `validation.py`, `id_generator.py`, `auth.py`) that editing extends. The Business Problem Statement is included because it is a single text area with no dependencies and is the next thing an Owner writes.

### D2 — Fields required at creation **(log)**
Requirements §5 says the lenient tier requires only `productName` and `description`. The Delta row and storage layout, however, need more values that cannot be supplied later without a key change.

**Decision:** add a named **create tier** to `validation.py` (lenient tier + storage keys). The Delta DDL is **not** loosened.

| Field | Required at create | Reason |
|---|---|---|
| `dataProduct` | Yes | Unique key of the Data Product (Data_Model §3), NOT NULL in `one_pager_status`; renaming is out of scope (Requirements §16 #2), so it must be right at creation |
| `productName` | Yes | Lenient tier |
| `description` | Yes | Lenient tier |
| `businessDomain` | Yes | NOT NULL column; must be an **active** value of `ref_business_domains` |
| `dataProductType` | Yes | NOT NULL column; must be an **active** value of `ref_data_product_types` **and** in the schema enum (`Foundational` / `Integrated` / `Augmented`) |
| `dataProductOwner.name`, `.initials`, `.email` | Yes | NOT NULL `owner_*` columns; schema `dataProductOwner.required`; `initials` is the authorization key |
| `dataProductOwner.team` | No | Nullable column, optional in schema |
| `smes[]` | No — but each SME row needs name, initials and email | Schema `smes.items.required` |
| `businessProblemStatement` | No | Written in the Editor later |

**Why:** the schema's top-level `required` list already contains all of these (except `smes`/`businessProblemStatement`), so a document missing them would not even be schema-valid. Keeping the DDL strict preserves data quality for the Registry filters.

### D3 — Creator vs. Owner **(log)**
Requirements §4 stores the **creator** separately from the **Owner**; Data_Model §3 says "the authenticated user is inserted as `owner`" on creation. These conflict when someone creates a One Pager on behalf of another Owner.

**Decision:**
1. The Owner fields are **pre-filled with the current user** (initials from D7, name/email from the login) and are editable.
2. `one_pager_status.created_by` = creator's initials; YAML `createdBy` = creator's display name.
3. `one_pager_authorized_users` is populated **from the document**: `dataProductOwner` → role `owner`, each of `smes` → role `sme`. The creator is **not** added automatically.
4. Validation requires that the **creator's initials appear as the Owner or as an SME**; otherwise the form shows "Add yourself as an SME to keep edit access to this One Pager".
5. The Owner may not also be listed as an SME, and SME initials must be unique.

**Alternatives considered:** always insert the creator as `owner` (the Data_Model wording) — makes the Delta table disagree with the YAML Owner and breaks the "YAML is authoritative for owner/SMEs" rule of Data_Model §5; silently add the creator as SME — grants edit rights nobody asked for.
**Why:** it keeps `one_pager_authorized_users` a pure mirror of the document (Backend_Design §11) and guarantees that the person who created the Draft can continue editing it. Data_Model §3 wording is updated in step 33.

### D4 — `dataProduct` format and uniqueness **(log)**
**Decision:**
- Format: `^[a-z][a-z0-9_]{1,62}$` (lowercase letter first, then lowercase letters, digits, underscore; 2–63 chars). Input is trimmed; it is **not** silently lower-cased — the user sees a field error and the rule in the help text.
- Pre-check with `data_product_exists()` before an ID is consumed → field error "A One Pager for this Data Product already exists (OP-####)" with a link to it.
- Race protection: after inserting the status row, `count_data_product()`; if > 1, the row with the **higher `one_pager_id`** loses — it is compensated (D5) and the user gets the same "already exists" error.

**Alternatives considered:** kebab-case or free text — `dataProduct` is used as the Git folder name (`<data_product>/<OP-ID>.yml`, Architecture §6) and as a unique key, so it must be path-safe and unambiguous; existing samples (`person`, `customer_master`) are already snake_case. Relying on the Delta `UNIQUE` keyword — Delta does not enforce it (Data_Model §3 note).
**Why:** deterministic, path-safe, and consistent with existing data; "lower ID wins" is deterministic even if both writers re-check at the same time.

### D5 — Write order and failure handling for a multi-store create **(log)**
Architecture §7 / Data_Model §5 say "Delta first, then YAML" for **content saves**, where a row already exists. For **creation** nothing references the new OP ID until its `one_pager_status` row exists.

**Decision:** creation uses this order:
1. Generate `OP-####` (a gap on a later failure is harmless — Data_Model §4).
2. Write `OP-####/OP-####_v0.1.0.yml` to the volume.
3. Insert the `one_pager_authorized_users` rows.
4. Insert the `change_log` `creation` entry.
5. Insert the `one_pager_status` row **last** — this is what makes the One Pager visible in the Registry and Preview.

Failure handling:
- Step 2 fails → nothing to undo; return `CreateError`.
- Step 3, 4 or 5 fails → best-effort `DELETE` of the rows steps 3–4 inserted for that OP ID; the YAML file is left in place (an orphan under an unreferenced OP ID is harmless and detectable by the Decision_Log §6 "orphaned files" check).
- In all cases a structured audit event is logged (OP ID, user initials, failed step — no field values) and the form content stays in `st.session_state` (UI_Design §4.2 "Save error").

Deleting `change_log` rows is allowed **only** in this compensation path, for an OP ID whose `one_pager_status` row was never committed — i.e. for an event that, from the application's point of view, never happened. The append-only rule applies unchanged to every visible One Pager.

**Alternatives considered:** Delta first (status row first) — a failure after it leaves a visible Registry row whose document is missing, so Preview shows "not found" until someone cleans up; a Delta multi-statement transaction — not available through the Statement Execution API used by `DatabricksConnection`.
**Why:** with the status row last, every partial failure is invisible to users; this is a deliberate, documented exception to "Delta first" that applies to creation only.

### D6 — ID generation
**Decision:** `src/onepagerapp/id_generator.py` exposes `next_id(data_access, id_type) -> str` over a `DataAccess.next_sequence_value(id_type) -> int` primitive. `LakehouseAccess` implements it with the compare-and-swap algorithm from [use-cases-page-implementation-plan.md](use-cases-page-implementation-plan.md) D5 (read → `UPDATE … WHERE last_value = :current` → re-read → retry up to 5 times, then raise). Formats: `OP-{n:04d}`, `UC-{n:03d}`, `BR-{n:03d}`.
Coordination: whichever of this plan and the Use Cases plan is built first creates `id_sequences.sql` and the generator; the other reuses them. If the Use Cases plan already added a private `_generate_use_case_id()`, it is replaced by a call to `next_id(…, "UC")`.
**Why:** one tested implementation for all three ID types, as Backend_Design §14 intends (`id_generator.py`), and the CAS pattern is already agreed for this codebase.

### D7 — Identity and permissions in v1
**Decision:**
- New `src/onepagerapp/auth.py`:
  - `initials_from_username(username)`: if the username matches `^([A-Za-z]{2,5})ADM@` return the group upper-cased (`MJOADM@BECOC001.onmicrosoft.com` → `MJO`, Architecture §4); otherwise fall back to the existing heuristic (`alice.brown@…` → `AB`).
  - `resolve_current_user(username) -> CurrentUser(username, initials, display_name)`; `display_name` is derived from the username (no directory lookup in v1).
  - `extract_initials` is removed from `preview.py`, which calls `auth.initials_from_username` instead; the Use Cases plan's `initials_from_user` becomes this function.
- `permissions.py` gets `can_create_one_pager(user) -> bool` and `PermissionDeniedError`. **v1 returns `True` for any authenticated user.** The same function is called by the Registry (show/hide [+ New]) and by `workflow.create_one_pager` (enforcement), so adding the Owner/SME UC-group check later is a one-function change.

**Alternatives considered:** blocking creation until UC group names are decided — would block the feature on an unrelated provisioning task (Architecture §4 lists group names as undecided).
**Why:** consistent with the Use Cases plan D7 and the current permission stub; server-side enforcement point exists from day one.

### D8 — Page placement and navigation
UI_Design §4.1 says [+ New] "opens the Editor with a blank document"; §2 says the Editor is "hidden unless editing".
**Decision:** create `app/views/editor.py` now, with **create mode only** (the Editor plan adds edit mode to the same file). Register it in `st.navigation` as **"Editor"**. [+ New] sets `st.session_state["editor_mode"] = "create"` and calls `st.switch_page("views/editor.py")` — the same mechanism Registry → Preview already uses. Opened without an `editor_mode` (e.g. from the sidebar), the page shows "Start from the Registry ([+ New]) or from a One Pager's [Edit] action" and a link to the Registry.
**Why:** Streamlit 1.38 cannot hide a single page in `st.navigation`, and `st.switch_page` only works for registered pages, so the page must be registered; the empty-state message is the closest to "hidden unless editing". The UI_Design note is updated in step 33.

### D9 — Initial document content and `structureDefinition` **(log)**
**Decision:**
- `structureDefinition` = **`structure_one_pager_v_1.json`**, defined once as `CURRENT_STRUCTURE_DEFINITION` in `validation.py` and written to both the YAML and `one_pager_status.structure_definition`. The validator resolves it as a file name inside `schemas/`.
- The created YAML contains: `structureDefinition`, `dataProduct`, `productName`, `businessDomain`, `dataProductType`, `onePagerStatus: Draft`, `dataProductStatus: In Definition`, `version: 0.1.0`, `dataProductOwner`, `smes` (only if any), `description`, `businessProblemStatement` (only if given), `createdBy` (creator display name), `createdAt`, `lastUpdated` (same ISO-8601 UTC instant as `createdAt`), and a one-item `changeLog` (`0.1.0`, that instant, creator display name, "Initial draft created").
- The dict is validated with `jsonschema` (Draft-07) against `schemas/structure_one_pager_v_1.json` before anything is written; a schema error here is a programming error → `CreateError`, logged.

**Alternatives considered:** `structure_one_pager/structure_one_pager_v_1.json` as in Data_Model §3 — there is no `structure_one_pager/` folder anywhere in the repo, while all fixture documents and the actual schema file already use the bare file name.
**Why:** matches the real repository layout and every existing document, so no fixture changes are needed; Data_Model §3/§6 examples are updated in step 33. Data_Model §6 (validate against the version declared in the document) keeps working: new schema versions are added as new files in `schemas/`.

### D10 — Change log for creation
**Decision:** exactly one entry: `event_type = "creation"`, `version = "0.1.0"`, `summary = "Initial draft created"`, `from_status = NULL`, `to_status = "Draft"`, `status_field = "one_pager_status"`, author = creator. No owner-authored change summary is requested on creation.
**Why:** Requirements §7 lists "initial creation" as a system-generated entry. The "two entries" rule applies when one action *changes* both statuses; creation *initialises* them, and `In Definition` is implied by `Draft` (Requirements §6 valid combinations), so a second entry would add noise without audit value.

### D11 — Input hygiene and field limits
**Decision:** `validation.sanitize_text(value, max_len)` trims, strips HTML tags and rejects (field error, no truncation) values over the limit. Limits:

| Field | Rule |
|---|---|
| `dataProduct` | D4 pattern (max 63) |
| `productName`, owner/SME `name`, `team` | max 200 |
| `description`, `businessProblemStatement` | max 5 000 |
| owner/SME `initials` | `^[A-Z]{2,5}$` (input upper-cased) |
| owner/SME `email` | simple `^[^@\s]+@[^@\s]+\.[^@\s]+$`, max 254 |

All SQL values go through bound parameters (step 5); content is rendered without `unsafe_allow_html`; logs never contain field values (Architecture §8).
**Why:** Architecture §8 requires sanitising and a maximum length but gives no numbers; these limits comfortably fit real content, and rejecting instead of truncating avoids silently losing text.

### D12 — Mock-mode document writes
**Decision:** `OnePagerDocumentStore.__init__(base_path, write_path=None)`. When `write_path` is set, `write()` goes there and `read()` / `_latest_file()` look in `write_path` first, then `base_path`. `factory.py` passes `write_path=tempfile.mkdtemp(prefix="onepager-mock-")` in `local-mock` mode only; `databricks` / `local-integration` keep a single path.
**Alternatives considered:** a new env var for the mock write folder (more configuration for no benefit); a separate `LayeredDocumentStore` class (duplicates path logic).
**Why:** fixtures under `tests/fixtures/sample_one_pagers/` stay read-only; created documents live as long as the process, which matches the lifetime of the in-memory mock tables.

### D13 — Serializer vs. schema mismatch for list sections **(log)**
**Decision:** the JSON Schema is the source of truth (Requirements §5). Creation writes none of the mismatched lists (`businessRequirements`, `dataSources`, `dataElementPreview`), so this feature **does not change** their serialization. The Editor plan must align the serializer, `OnePagerDocument` and the fixtures with the schema (`id`/`description`, `name`/`sourceSystem`) before those tabs are built. Logged as a known gap.
**Why:** keeps this change small and avoids breaking the Preview rendering of existing fixtures; the gap is recorded so it is not forgotten.

### D14 — `OP` sequence start
**Decision:** the Liquibase seed sets all counters to `0` (Data_Model §3). The DEV seed script [seed_one_pager_status_dev.sql](seed_one_pager_status_dev.sql) additionally runs `UPDATE id_sequences SET last_value = 6 WHERE id_type = 'OP'` (it seeds `OP-0001`…`OP-0006`). `MockDataAccess` initialises its OP counter to the highest seeded mock ID (currently 2).
**Why:** new IDs must never collide with seeded rows; environments without seed data start at `OP-0001` as specified.

### D15 — UI widget choices
**Decision:** scalar fields in an `st.form` (no re-run per keystroke); SMEs in `st.data_editor(num_rows="dynamic")` with columns name / initials / email / team, placed outside the form (data editors with dynamic rows work more reliably outside forms); Cancel confirmation via `st.dialog` (available in Streamlit 1.38). Business Domain / Product Type use `st.selectbox` with `index=None` and a "Select…" placeholder so nothing is pre-selected by accident.
**Why:** standard Streamlit primitives already available in the pinned version; matches UI_Design §5 confirmation-dialog pattern.

### Explicitly deferred

| Feature | Belongs to |
|---|---|
| Other editor tabs (Use Cases, BRs, Sources, Data Product Preview, Classification, Governance, Scope & Questions, Review) | Editor plan |
| Save Draft (MINOR bump), change summary field | Editor plan |
| Locking on enter/exit | Editor plan + `locking.py` |
| Submit for Review, strict validation | Editor plan + `workflow.py` transitions |
| Linking Use Cases at creation | Use Cases plan + Editor plan (`use_case_references`) |
| BR-### generation | Editor plan (Business Requirements tab) |
| UC-group-based role check | Once group names are decided (Architecture §4) |
| Serializer/schema alignment for list sections | Editor plan (D13) |

## 4. Phase 1 — Storage

| # | Step | Detail |
|---|---|---|
| 1 | Add `id_sequences.sql` (if not already added by the Use Cases plan) | Columns per [Data_Model.md](../docs/Data_Model.md) §3; seed `('OP', 0), ('UC', 0), ('BR', 0)` in the same changeset. Append `UPDATE … id_sequences SET last_value = 6 WHERE id_type = 'OP'` to [seed_one_pager_status_dev.sql](seed_one_pager_status_dev.sql) (D14) |
| 2 | Add `one_pager_authorized_users.sql` | Columns per Data_Model §3; composite logical PK (`one_pager_id`, `user_initials`) |
| 3 | Register in [root.changelog.databricks.yaml](../liquibase/bia_meta/onepager_app/root.changelog.databricks.yaml) | `id_sequences` after the reference tables; `one_pager_authorized_users` after `one_pager_status` |
| 4 | DEV seed for authorized users | Add rows for the already-seeded `one_pager_status` records so the table is consistent from day one |

## 5. Phase 2 — Core Layer (`src/onepagerapp/`, no Streamlit)

| # | Step | Detail |
|---|---|---|
| 5 | **Fix parameterized SQL** (prerequisite) | Extend `DatabricksConnection.execute_statement(statement, parameters: list[StatementParameterListItem] \| None = None)` and pass them through to `statement_execution.execute_statement`. The Statement Execution API uses **named** markers (`:one_pager_id`), not `%s` — convert the existing Preview readers too. This fixes Preview in `databricks` mode as a side effect |
| 6 | Dependencies | Add `jsonschema==4.26.0` (the version already in `uv.lock`) to `pyproject.toml` `dependencies`; `uv lock` |
| 7 | Models in [models.py](../src/onepagerapp/models.py) | `PersonRef(name, initials, email, team=None)`; `NewOnePagerInput(data_product, product_name, business_domain, data_product_type, description, owner: PersonRef, smes: list[PersonRef], business_problem_statement="")`; `CurrentUser(username, initials, display_name)`; `ValidationError(field_path, message)` (Backend_Design §4); `CreateResult(one_pager_id, version)`. Extend `OnePagerDocument` with `created_by`, `created_at`, `last_updated`, `change_log: list[dict]` (all optional/defaulted so existing readers keep working) |
| 8 | Serialization in [serialization.py](../src/onepagerapp/documents/serialization.py) | Read/write `createdBy`, `createdAt`, `lastUpdated`, `changeLog`; omit `businessProblemStatement` when empty. List-section keys (`businessRequirements`, `dataSources`, `dataElementPreview`) are **left unchanged** here (D13) |
| 9 | `auth.py` | `initials_from_username()` per D7; `resolve_current_user(username) -> CurrentUser`. Replace `extract_initials` in [preview.py](../app/views/preview.py) with this |
| 10 | `validation.py` | `sanitize_text(value, max_len)`; `validate_create(input) -> list[ValidationError]` (D2 fields, D4 pattern, email format, SME required fields, D3 creator ∈ owner/SMEs, no duplicate SME initials, owner not also an SME); `validate_schema(doc_dict) -> list[ValidationError]` using `jsonschema` (Draft-07) against `schemas/structure_one_pager_v_1.json`. Errors are returned, never raised (Backend_Design §4) |
| 11 | `id_generator.py` | `next_id(data_access, id_type)` → `OP-0001` etc. per D6; formatting table `{"OP": 4, "UC": 3, "BR": 3}` |
| 12 | `permissions.py` | Add `can_create_one_pager(user: CurrentUser) -> bool` (v1: authenticated) and `PermissionDeniedError` |
| 13 | Extend the `DataAccess` ABC | `next_sequence_value(id_type) -> int`; `data_product_exists(data_product) -> bool`; `count_data_product(data_product) -> int`; `insert_authorized_users(one_pager_id, users: list[tuple[PersonRef, role]])`; `append_change_log(entry: ChangeLogEntry)`; `insert_one_pager_status(row)`; `delete_one_pager_records(one_pager_id)` (compensation only — deletes status/authorized-users/change-log rows for an OP that never became visible). Document writes stay on `OnePagerDocumentStore.write()` |
| 14 | Implement in `mock.py` **first** | Refactor the hardcoded registry rows / headers / change logs into **mutable dicts built in `__init__`** so `get_registry`, `get_registry_status_counts`, `get_one_pager_status`, `get_change_log` read from them and the new writes append to them. Mock OP counter starts at 2 (D14). Add the optional `write_path` to `OnePagerDocumentStore` and pass a temp dir from `factory.py` in `local-mock` mode (D12) so fixtures are never modified (gap #7) |
| 15 | Implement in `lakehouse.py` | Parameterized `INSERT`s for the three tables; CAS `UPDATE` on `id_sequences` with re-read + retry; `SELECT COUNT(*)` for uniqueness; compensation `DELETE`s scoped by `one_pager_id`. Timestamps via `current_timestamp()` in SQL; the same instant is used for YAML `createdAt` (pass it as a parameter to keep them identical) |
| 16 | `workflow.py` — `create_one_pager(input, user, data_access, document_store) -> CreateResult \| list[ValidationError]` | Orchestrates: permission check → sanitize → `validate_create` → uniqueness pre-check → `next_id("OP")` → build `OnePagerDocument` (D9) → `validate_schema` → write steps 2–5 of D5 → post-insert uniqueness re-check (D4) → return result. On storage failure: compensate, log a structured audit event (no field values, Architecture §8), raise/return `CreateError` with a user-safe message. Pure Python, no Streamlit; dependencies injected for testability |

> **Security:** every value reaching SQL goes through bound parameters (step 5). Descriptions/names are untrusted — stored sanitized, rendered as text. Log only OP IDs, user initials and outcomes, never field values.

## 6. Phase 3 — Presentation (`app/`)

| # | Step | Detail |
|---|---|---|
| 17 | Resolve the current user once | In [app.py](../app/app.py), store `st.session_state.current_user_info = auth.resolve_current_user(username)` next to `current_user`, **before** `pg.run()` so pages can use it on first render (today the user is resolved after the page runs) |
| 18 | [+ New] on the Registry | Top-right of the header and in the "No One Pagers yet" empty state ([UI_Design.md](../docs/UI_Design.md) §4.1). Shown only if `can_create_one_pager(user)`. On click: clear any previous create draft in session, set `editor_mode = "create"`, `st.switch_page("views/editor.py")` |
| 19 | Create `app/views/editor.py` (thin view) | Header "New One Pager" + `Draft` badge + `v0.1.0`. **Basics** section: Data Product (text, help text with naming rule), Product Name, Business Domain (select, active ref values), Product Type (select), Description (text area), Owner (name / initials / email / team — pre-filled from current user), SMEs (`st.data_editor` with `num_rows="dynamic"`, columns name/initials/email/team), optional Business Problem Statement. Use schema `description`s as placeholder/help text (UI_Design §4.2 "New (empty form)"). All form values live in `st.session_state["create_form"]` so they survive re-runs and failed saves |
| 20 | Bottom bar | **[Create Draft]** → `workflow.create_one_pager(...)`; **[Cancel]** → `st.dialog` confirmation if any field is filled (D15), then back to the Registry. No change-summary field on creation (D10) |
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
| 33 | Docs | Add D2, D3, D4, D5, D9, D13 to [Decision_Log.md](../docs/Decision_Log.md) (new §7–§12). Update [Data_Model.md](../docs/Data_Model.md) §3 (`one_pager_authorized_users` populated from Owner/SMEs, not the creator; `structure_definition` example = `structure_one_pager_v_1.json`) and §6; update [UI_Design.md](../docs/UI_Design.md) §2/§4.2 (Editor registered but shows a start message without intent; create mode shows Basics + Business Problem only) |
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
- `..dev/seed_one_pager_status_dev.sql` (DEV `OP` sequence, authorized-users seed)
- `docs/Decision_Log.md`, `docs/Data_Model.md`, `docs/UI_Design.md`, `src/onepagerapp/__version.py`

## 11. Sequencing Note

Step 5 (parameterized SQL) is a prerequisite for every Lakehouse write and should land first as its own small change. After that, Phase 1 and steps 7–14 + Phase 3 can run **in parallel**: build the whole create flow against `MockDataAccess` and a temp-dir document store, then plug in `LakehouseAccess` (step 15) once `id_sequences` and `one_pager_authorized_users` exist.

Coordinate with the Use Cases plan: both need `id_sequences` and an initials helper — whichever lands first creates them and the other reuses them. The `workflow.py`, `validation.py`, `auth.py` and `id_generator.py` modules created here are the foundation the Editor plan extends (Save Draft, Submit for Review, locking).
