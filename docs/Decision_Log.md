# Decision Log

This document captures significant design and development decisions made during the project.

---

## 1. Streamlit as UI Framework

**Context:** The team lacks web app development experience. A UI framework was needed for an internal enterprise tool hosted on Databricks.

**Decision:** Use Streamlit.

**Why it fits:**

- Pure Python — no HTML/CSS/JS or frontend tooling to learn
- Databricks Apps host Streamlit natively — zero-friction deployment
- Lowest learning curve for a non-web-dev team
- Built-in support for forms, tables, filters, status badges, and multi-page navigation — covers the current scope (CRUD + workflow + role-based views)
- No build pipeline (webpack, npm, etc.) to manage

**Known limitations accepted:**

- Every widget interaction re-runs the full page script — complex editor forms may feel sluggish
- No drag-and-drop, inline cell editing, or rich client-side interactions
- No push/real-time updates between users
- Session loss equals data loss for long editing sessions

**Why not alternatives:**

- **Flask/Django + templates** — requires HTML/CSS/JS and manual form/session handling
- **React/Vue + FastAPI** — full frontend framework plus REST API; months of ramp-up
- **Gradio** — weaker for multi-page apps with role-based navigation
- **Dash (Plotly)** — callback model harder to reason about than Streamlit's linear scripts
- **Retool / low-code** — not Databricks-native; adds vendor dependency; unlikely to fit UC auth model

**Risk mitigation:** The domain layer is pure Python with no Streamlit imports (see [Architecture.md](Architecture.md)). The UI is explicitly replaceable — if the Editor page outgrows Streamlit's widget model, a richer frontend can be swapped in without rewriting business logic.

---

## 2. Placement of `app.yml` in `app/`

**Context:** Databricks Apps requires `app.yml` (the runtime manifest) at the root of the directory specified by `source_code_path`. The `onepagerapp` package lives in `src/onepagerapp/`, and imports rely on the `onepagerapp` package name being preserved on the container.

**Decision:** Place `app.yml` in `app/` as the entry point directory for the Streamlit application.

**Why this works:**

- Keeps the `onepagerapp/` package structure intact on the container — imports like `from onepagerapp.adapters.theme` resolve correctly without `PYTHONPATH` hacks
- `app.yml` is a runtime/deployment artifact — it belongs with the deployable source, similar to how a `Dockerfile` or `Procfile` sits at the root of what gets deployed
- Follows the Databricks Apps platform convention

**Concerns accepted:**

- `src/` now mixes one deployment config file with source code — minor, since it's a single file
- Everything under `src/` gets uploaded to the workspace — if other packages or data files are added later, a `.databricksignore` may be needed to exclude them
- `app.yml` is not used during local development — developers may not know it's there without documentation (covered in [Dev_Notes.md](Dev_Notes.md))

**Alternative considered — `app.yml` in project root:**

- Would require setting `source_code_path` to `.` (project root)
- Uploads the entire repository (tests, docs, CI configs) to the workspace unless a `.databricksignore` is maintained
- Two YAML configs at the root (`databricks.yml` and `app.yml`) can be confusing — they serve different systems (DAB vs. Databricks Apps runtime)
- More files to exclude and maintain over time

**Note:** The app structure places `app.yml` in the `app/` directory alongside `app.py` (the Streamlit entry point), keeping all app-specific configuration and code together.

---

## 3. Data Access: SQL Warehouse + AppMode Pattern

**Context:** The app reads Delta tables (e.g. `ref_op_status`, `one_pager_status`). It needs to work in three scenarios: deployed on Databricks (using the logged-in user's permissions), local development without any Databricks connection, and local development with real table access for integration checks.

**Decision:** Use the Databricks SQL Connector (against a serverless SQL warehouse) behind a repository-pattern abstraction, controlled by an `APP_MODE` environment variable.

**Three modes:**

| `APP_MODE` | Data layer | Auth | Use case |
|---|---|---|---|
| `databricks` | `DeltaDataAccess` — SQL Connector to warehouse | Implicit Databricks Apps user identity | Deployed app |
| `local-integration` | `DeltaDataAccess` — SQL Connector to warehouse | Databricks CLI profile (OAuth via `databricks auth login`) | Local integration testing against real tables |
| `local-mock` | `MockDataAccess` — in-memory pandas DataFrames | None | Day-to-day UI/logic development, unit tests, CI |

**Why SQL Connector, not `databricks-connect`:**

- `databricks-connect` requires a matching cluster or serverless compute — version compatibility is fragile (v17.3 blocked serverless with `SparkConnectGrpcException`)
- SQL Connector talks to a SQL warehouse over HTTP — no Spark session, no cluster, no version coupling
- Returns data as standard Python types — no `.toPandas()` conversion needed
- SQL warehouses (especially serverless) start instantly and cost less for query-only workloads

**Connection config:**

- Workspace host and auth: Databricks CLI profile (`~/.databrickscfg`), referenced via `DATABRICKS_CONFIG_PROFILE` env var — no secrets in code or env files
- Warehouse ID, catalog, schema: env vars (`DATABRICKS_WAREHOUSE_ID`, `ONE_PAGER_APP_DATABRICKS_CATALOG`, `ONE_PAGER_APP_DATABRICKS_SCHEMA`), set in `app.yml` for deployed mode and overridable locally via `--env`
- `APP_MODE` is **not** set in `app.yml` — the config default (`databricks`) handles the deployed case, while `--env APP_MODE=local-mock` overrides it locally (app.yml env vars take precedence over `--env` flags, so overridable vars must stay out of `app.yml`)
- Auth on Databricks: the app inherits the calling user's identity automatically — queries run with their Unity Catalog permissions, not the service principal's. **Changed by §19:** reads still run as the user, writes now run as the service principal.

**Architecture:**

- `DataAccess` ABC in `onepagerapp/data_access/base.py` — defines the contract
- `DeltaDataAccess` in `onepagerapp/data_access/delta.py` — real SQL Connector implementation
- `MockDataAccess` in `onepagerapp/data_access/mock.py` — pandas fakes with sample data
- `create_data_access()` factory in `onepagerapp/data_access/factory.py` — selects the implementation based on `APP_MODE`
- `app.py` calls the factory during `init_services()` and stores the result in `st.session_state`
- Pages access data via `st.session_state.data_access` — no direct imports of either implementation

---

## 4. Bind the volume and SQL warehouse as app `resources` instead of `config.env`

**Context:** `ONE_PAGER_APP_VOLUME_PATH` and `DATABRICKS_WAREHOUSE_ID` were originally set via the DAB app resource's `config.env`, resolved once at `bundle deploy` time. In production, after deploying the app and then stopping and starting it again (no redeploy), the app crashed with:

```
ValidationError: 1 validation error for AppConfig
ONE_PAGER_APP_VOLUME_PATH
  Field required [type=missing, input_value={}, input_type=dict]
```

The same thing had already happened earlier with `PYTHONPATH`, set the same way. Both env vars simply weren't present in the container after a bare stop/start, even though they were correctly applied on the initial deploy.

**Decision:** Reference the volume and SQL warehouse from `app/app.yml` via `valueFrom` instead of `value`, so values are resolved from bound resources at runtime on every app start.

**Why this fixes it:**

- Databricks' `app.yaml`'s `env` section is read fresh from source on every app start, while static `config.env` values aren't guaranteed to be reapplied on a plain restart.
- `valueFrom` resolves the value from the bound resource at runtime, on every start, rather than baking in a static value at deploy time.
- Current implementation: `app/app.yml` references resources via `valueFrom` (e.g., `ONE_PAGER_APP_VOLUME_PATH` and `DATABRICKS_WAREHOUSE_ID`).

**Implementation status:**

- Resource bindings are managed through the Databricks Apps deployment mechanism and referenced in `app/app.yml`.

---

## 5. Distribute the `onepagerapp` wheel via a Unity Catalog volume

**Context:** The Streamlit app depends on the `onepagerapp` library (the same wheel built from `src/` and deployed as a DAB artifact for jobs/workflows). Databricks Apps installs Python dependencies from `app/requirements.txt` during its own `BUILD` phase, which runs in an isolated container that only has access to the public PyPI mirror (or a workspace-configured private index) and to Unity Catalog volumes bound to the app — it cannot reach arbitrary paths in the workspace filesystem (`/Workspace/...`) at install time, and the wheel isn't published to any PyPI-style index.

**Decision:** Copy the wheel built by the CI pipeline into a Unity Catalog volume (`/Volumes/<catalog>/onepager_app/one_pager_registry/wheels`), and reference the exact wheel path (e.g. `/Volumes/.../onepagerapp-0.1.6-py3-none-any.whl`) directly in `app/requirements.txt`, generated per-environment by the deploy pipeline (see [deploy-dab.yml](../azure-pipelines/templates/deploy-dab.yml)).

**How it works:**

- `deploy-dab.yml`'s "Copy wheel to shared volume" step downloads the wheel that DAB uploaded to the workspace (`databricks workspace export-dir`) and pushes it to the volume (`databricks fs cp`), replacing any previous `.whl` files there on every run.
- The "Generate app/requirements.txt" step computes the current `VERSION` from `__version.py` and writes the literal volume path — Databricks Apps requires this to be hard-coded; it does not expand environment variables or support `--find-links`/`--index-url` pointed at a volume directory.
- The volume is also bound as an app `resource` (`one-pager-registry-volume`, see Decision 4) so the app compute — and its `BUILD` phase — can actually reach `/Volumes/...`.

**Pros:**

- No need to publish `onepagerapp` to a real package index (internal Artifactory, PyPI, etc.) just to satisfy the app's install step — reuses the artifact DAB already builds and uploads.
- Keeps `onepagerapp` versioned and environment-scoped: each target (dev/int/uat/prd) has its own catalog/volume, so environments can't accidentally install another environment's build.
- Wheel and app deploy stay in the same pipeline run — no separate publish step to keep in sync.

**Cons:**

- **Version bumps are mandatory for every code change.** The app's `.venv` persists across deployments, and pip resolves `onepagerapp` by name+version, not by wheel content — if `VERSION` in `__version.py` doesn't change, pip reports "already satisfied" and silently skips installing the new wheel, even though the file on the volume was replaced. This has been the source of repeated "my fix isn't showing up" incidents.
- Extra pipeline complexity: the wheel has to be round-tripped through the workspace (`workspace export-dir`) before it can be pushed to the volume (`fs cp`), because `databricks fs` doesn't support `/Workspace` paths directly.
- Requires the app's service principal to have `USE CATALOG`/`USE SCHEMA`/read access on the volume, on top of the catalog access already needed for Decision 4 — one more permission dependency to keep granted per environment.
- No automatic cleanup/retention of old wheel versions in the volume — every version ever deployed accumulates there unless pruned manually.

**Alternative considered — private package index (Artifactory):** would let `requirements.txt` reference `onepagerapp==<version>` normally and let pip handle version resolution/upgrades correctly, avoiding the "already satisfied" trap entirely. Rejected for now to avoid adding a publish step and index credentials to the pipeline before it was clear the app actually needed the shared library; may be revisited if the version-bump requirement keeps causing deployment issues.

---

## 6. Volume File Layout for One Pager YAML Documents

**Context:** One Pager documents (content: business problem, requirements, use cases, etc.) are stored as YAML files in a Unity Catalog external volume during the pre-approval phase (`Draft`, `Ready for Review`, `In Review`, `Draft Update`), and in Git on the OnePagerRegistry repository after approval. The project needed to decide how to organize these files in the volume to support version history, discoverability, permission models, operational clarity, and resilience to data product renames.

**Decision:** Adopt a **flat, OP-ID-keyed layout** in which every version is an immutable file and the Delta `one_pager_status` table is the single source of truth for the current version:

```
<volume_root>/
  <OP-ID>/
    <OP-ID>_v0.1.0.yml       # All versions, immutable files
    <OP-ID>_v0.2.0.yml
    <OP-ID>_v0.3.0.yml
    <OP-ID>_v1.0.0.yml       # Approved versions also stored here (no separate folder)
    <OP-ID>_v2.0.0.yml
```

The current version is read from `one_pager_status.version`; the file to open is `<OP-ID>_v<version>.yml`. There is no pointer file to keep in sync.

**Volume paths per environment:**

| Environment | Volume Root |
|---|---|
| DEV | `/Volumes/dev_one_pager/app/one_pagers` |
| INT | `/Volumes/int_one_pager/app/one_pagers` |
| UAT | `/Volumes/uat_one_pager/app/one_pagers` |
| PRD | `/Volumes/prd_one_pager/app/one_pagers` |

**Example paths:**

- Draft version: `/Volumes/dev_one_pager/app/one_pagers/OP-0001/OP-0001_v0.2.0.yml`
- Approved version: `/Volumes/dev_one_pager/app/one_pagers/OP-0001/OP-0001_v1.0.0.yml`

**Why this structure was chosen:**

**Pros:**
- ✅ **Stable paths via OP-ID** — OP-ID is immutable (the primary key for the One Pager). Both the directory and every filename are derived from it, so paths never break on a product rename.
- ✅ **Rename is Delta-only** — The data product name is not in the path or filename, so renaming a product touches zero files; only the `one_pager_status.data_product` column changes. No copy/delete, no orphaned files.
- ✅ **Single source of truth for "current"** — The current version is `one_pager_status.version`. No `_latest.*` pointer to write on every save, and no risk of the pointer drifting from Delta.
- ✅ **Complete, immutable version history** — Every version (draft, approved, re-approved) is retained as an immutable file in one directory, enabling full audit trail and rollback without a separate `archived/` folder.
- ✅ **Content-only volume files** — YAML files hold document content only. All operational metadata (status, approval, lineage, PR link) lives in Delta tables — no `_metadata.json` sidecars.
- ✅ **Status determined by Delta, not path** — Whether a version is `Draft`, `In Review`, or `Approved` comes from `one_pager_status.one_pager_status`, so no `working/`/`approved/` path split is needed.

**Operations table:**

| Event | Action | Volume effect |
|---|---|---|
| **Create One Pager** | Write `OP-####_v0.1.0.yml`; insert Delta row (`version = 0.1.0`) | New file |
| **Save draft (content change)** | Write new immutable version file; bump `one_pager_status.version` | New file |
| **Rename data product** | Update `one_pager_status.data_product` only | None |
| **Submit for review** | Update Delta status only | None |
| **Approve** | Write MAJOR-bumped version file; update Delta status + `version`; push to Git via PR | New file |
| **Draft Update (re-edit approved)** | Read approved YAML from Git; write next MINOR version file; update Delta | New file |
| **Delete One Pager** | Delete the `<OP-ID>/` directory tree; update Delta | Directory removed |

**Metadata and status in Delta:**

All operational metadata (approval date, approver, Git PR link, lifecycle status, current version, etc.) is stored **only** in Delta tables, not in volume files:
- `one_pager_status.version` — current version (authoritative; determines the filename to read)
- `one_pager_status.one_pager_status` — current status (Draft, In Review, Approved, etc.)
- `one_pager_status.reviewed_at`, `reviewed_by` — approval timestamp and user
- `change_log` entries — full event history with `status_transition` events

**Alternatives evaluated:**

1. **Product name embedded in the filename** (`<OP-ID>_<data-product>_v<version>.yml`)
   - ✅ Self-documenting filenames
   - ❌ A product rename must copy+delete every version file (not atomic on object storage) for a purely cosmetic benefit already covered by the Delta `data_product` column

2. **`_latest.txt` / `_latest.yml` pointer for the current version**
   - ✅ Current version resolvable without a Delta query
   - ❌ Duplicates `one_pager_status.version`; adds a write on every save that can drift from Delta (the authoritative source)

3. **`working/` + `approved/` + `archived/` path split**
   - ✅ Lifecycle visible in the path
   - ❌ Status already lives in Delta; the split forces file moves on every transition and contradicts "status determined by Delta, not path"

4. **Product-first directory** (`<data_product>/...`)
   - ✅ Intuitive grouping by product, easy human browsing
   - ❌ Breaks on product rename (orphaned files); unstable paths; permission scoping by product harder

**Implementation:**

- The `DataAccess` layer abstracts volume paths. It builds `<volume_root>/<OP-ID>/<OP-ID>_v<version>.yml` from the OP-ID and the version read from Delta, so the UI never needs to know the directory structure.
- For `local-mock` mode (development without Databricks), sample One Pager YAML files are stored locally under `tests/fixtures/sample_one_pagers/` in the same flat `<OP-ID>/<OP-ID>_v<version>.yml` layout, with `MockDataAccess` supplying the "current version" in place of Delta.
- On product rename, the app updates the `one_pager_status.data_product` column only; no volume operation is involved.

**Migration path:**

If the structure needs to change in the future (e.g., to flatten further for cloud object storage), the abstraction layer makes it a localized change — only the path-construction logic in `DataAccess` needs updating; all clients remain unaffected.

**Disaster recovery:**

- To enumerate all One Pagers: query the Delta `one_pager_status` table (authoritative source); the volume mirrors it under `<OP-ID>/`.
- To find orphaned files: list volume directories and cross-reference with Delta; any OP-ID present in the volume but absent from Delta is orphaned.
- To recover a specific version: read `<OP-ID>/<OP-ID>_v<version>.yml` directly; all versions are retained immutably.


---

## 7. Use Case Registry Page (v1)

**Context:** The Use Cases page ([UI_Design.md](UI_Design.md) §4.5) is the first page that writes to Delta. Implementing it surfaced three questions the design docs leave open or that the rest of the code base does not yet support. See `..dev/Use_Cases_Page_Plan.md` §3 for the full plan.

**Decisions:**

1. **Deprecated Use Cases can be restored.** [Backend_Design.md](Backend_Design.md) §9 only defines `deprecate_use_case`. Deprecation is a soft state (`deprecated` flag), so the page also offers **Restore**, which clears the flag. This lets a mistaken deprecation be undone without a manual database fix. Deprecation asks for confirmation and lists the referencing One Pagers; restore does not ask.
2. **Write access is temporarily open to every authenticated user.** The docs restrict create/edit/deprecate to the Owner/SME group and make the page read-only for Approver, Admin and Viewer ([Requirements_and_Scope.md](Requirements_and_Scope.md) §9, [UI_Design.md](UI_Design.md) §2). UC group resolution (`auth.py`) does not exist yet, so `permissions.can_manage_use_cases()` is a v1 stub that allows any authenticated user. Every write action on the page goes through it, so enforcing the real rule is a change to that one function. [Backend_Design.md](Backend_Design.md) §15 open item #2 remains open. **Superseded by §21:** the stub is replaced by the Owner/SME group check.
3. **User-supplied values are bound as SQL parameters.** `DatabricksConnection.execute_statement()` now accepts named parameters (`:name` markers), passed to the SQL Statement Execution API as bound values. All Use Case queries use them, and the Preview queries were moved from the unsupported `%s` form to named markers. `LIKE` wildcards in search text are escaped. Statements the warehouse reports as failed now raise `StatementFailedError` instead of looking like empty results.

**ID generation:** `UC-###` IDs come from `id_sequences` via a compare-and-swap `UPDATE ... WHERE last_value = <read value>`. Success is decided by the statement's `num_affected_rows`, and conflicts are retried up to 5 times. Re-reading the counter to confirm success is not safe: two racing writers would both see the new value and hand out the same ID.

---

## 8. "Create" Validation Tier for New One Pagers

**Context:** Requirements §5 defines a lenient tier (only `productName` and `description` required) for saving drafts. Creating a One Pager also inserts a `one_pager_status` row whose storage keys are NOT NULL (`data_product`, `business_domain`, `data_product_type`, `owner_*`), and `dataProduct` cannot be renamed later (Requirements §16 #2).

**Decision:** A dedicated **create tier** in `validation.py` = lenient tier + `dataProduct`, `businessDomain` (active `ref_business_domains` value), `dataProductType` (active `ref_data_product_types` value and schema enum), and `dataProductOwner.name/initials/email`. SMEs are optional, but each SME row needs name, initials and email. The Delta DDL stays strict.

**Why:** These are exactly the fields the JSON Schema already lists as top-level `required`, so a document without them would not be schema-valid anyway; keeping the DDL strict preserves Registry filter quality. (Details: `..dev/New_One_Pager_Plan.md` D2.)

---

## 9. Creator vs. Owner on One Pager Creation

**Context:** Requirements §4 stores the creator separately from the Data Product Owner, while Data_Model §3 said the authenticated user is inserted as `owner` in `one_pager_authorized_users` at creation. The two conflict when someone creates a One Pager for another Owner.

**Decision:**
- The Owner fields are pre-filled with the current user and are editable.
- `one_pager_status.created_by` stores the creator's initials; YAML `createdBy` the creator's display name.
- `one_pager_authorized_users` is populated **from the document** (`dataProductOwner` → `owner`, `smes` → `sme`), never from the creator.
- Validation requires the creator's initials to appear as Owner or SME, so the creator keeps edit access. The Owner may not also be an SME; SME initials are unique.

**Why:** Keeps `one_pager_authorized_users` an exact mirror of the document (Backend_Design §11) and prevents Drafts that nobody present can edit. Silently adding the creator would grant edit rights nobody asked for.

---

## 10. `dataProduct` Format and Uniqueness

**Decision:** `dataProduct` must match `^[a-z][a-z0-9_]{1,62}$` (lowercase snake_case, 2–63 characters). Uniqueness is checked before an ID is reserved and re-checked after the `one_pager_status` row is inserted; if two creates race, the **lower OP ID wins** and the other is rolled back with an "already exists" error.

**Why:** The value is the unique key and the Git folder name (`<data_product>/<OP-ID>.yml`, Architecture §6), so it must be path-safe; existing names (`person`, `customer_master`) are already snake_case. Delta does not enforce `UNIQUE`, so the application must, deterministically.

---

## 11. Write Order for Creating a One Pager

**Context:** Architecture §7 / Data_Model §5 require "Delta first, then YAML" for content saves. A create spans the volume and three Delta tables, and the Statement Execution API offers no multi-table transaction.

**Decision:** For **creation only**, the order is: reserve the OP ID → write `OP-####_v0.1.0.yml` → insert `one_pager_authorized_users` → insert the `creation` `change_log` entry → insert `one_pager_status` **last**. If a Delta step fails, the rows already inserted for that OP ID are deleted (compensation) and the user sees "Create failed — your changes are preserved, please retry." The YAML file is left as an unreferenced orphan (detectable via §6 "Disaster recovery"); the OP ID is a harmless gap.

Deleting `change_log` rows is permitted only in this compensation path, for an OP ID whose status row never became visible. The append-only rule is unchanged for every created One Pager.

**Why:** The `one_pager_status` row is what makes a One Pager visible in the Registry and Preview; writing it last makes every partial failure invisible to users. "Delta first" would leave a visible row pointing to a missing document.

---

## 12. `structureDefinition` Value

**Decision:** New documents and `one_pager_status.structure_definition` use **`structure_one_pager_v_1.json`** — a file name resolved inside `schemas/`. The schema files are shipped inside the `onepagerapp` wheel (`onepagerapp/schemas/`) so validation also works in the deployed app.

**Why:** Matches the real repository layout and every existing fixture document; the previously documented `structure_one_pager/structure_one_pager_v_1.json` path does not exist. Validating against the version named in each document (Data_Model §6) keeps working: new schema versions are added as new files in `schemas/`.

---

## 13. Serializer vs. Schema Mismatch for List Sections (Resolved by §14)

**Context:** `documents/serialization.py` and the fixtures use `businessRequirements: requirement/priority`, `dataSources: sourceName/sourceType` and a `dataElementPreview` list, while `structure_one_pager_v_1.json` defines `businessRequirements: id/description`, `dataSources: name/sourceSystem/description` and no `dataElementPreview`.

**Decision:** The JSON Schema is the source of truth (Requirements §5). Creating a One Pager writes none of these lists, so the create feature leaves them unchanged. The Editor feature must align the serializer, `OnePagerDocument` and the fixtures with the schema **before** those editor tabs are built.

**Why:** Keeps the create change small without breaking Preview rendering of existing fixtures, and records the gap so it is not forgotten.


---

## 14. Structure Definition v2 and the Validation Tiers

**Context:** `structure_one_pager_v_1.json` had no data element grid, retention list, governance artifacts, out of scope, open questions or assumptions, and no `minItems`, so the strict tier (Requirements §5: "at least one use case, one business requirement, one data source, one data product preview row") had nothing to enforce. Its list shapes also disagreed with the serializer and fixtures (§13).

**Decision:** Add **`structure_one_pager_v_2.json`** and make it `CURRENT_STRUCTURE_DEFINITION`; v1 stays unchanged in `schemas/`.

- v2 follows the editor tabs in UI_Design §4.2: `useCases` holds `{useCaseId: UC-###}` references only (Data_Model §5); `businessRequirements` items are `id` (BR-###) / `requirement` / `priority` / `notes`; `dataSources` items are `name` / `sourceSystem` / `epoId` / `dataProvided` / `refreshFrequency`; `dataProductPreview` is the data element grid; `retentionRequirements` is a top-level list; `dataGovernanceArtifacts` has `businessConcepts`, `cdeQuality` and `cdeLineage`; `outOfScope`, `openQuestions` and `assumptions` are added. `businessProblemStatement` stays a single string.
- Top-level `required`, `minItems` and `minLength` define the **strict** tier (`validate_strict`), plus the conditional business rules (retention, CDE-only fields) and unique BR IDs within a One Pager (Requirements §14).
- The **lenient** tier (`validate_lenient`) requires only `productName` and `description`, and validates everything else against the same schema with `required`/`minItems`/`minLength` removed, so shapes and types are still checked. A new One Pager is checked with this tier.
- Every tier validates against the schema named in the document's own `structureDefinition` (Data_Model §6). Only versions listed in `SUPPORTED_STRUCTURE_DEFINITIONS` are accepted; a document without one uses the current schema.
- `OnePagerDocument` is v2-shaped. v1 documents load into it unchanged (inline Use Case objects, v1 item keys; `dataElementPreview` is read as `dataProductPreview`), and Preview shows either shape. Documents are always written with v2 keys; migrating a v1 document on its next save is part of Save Draft (Phase 4).
- Fixtures: OP-0001 v0.1.0/v0.2.0 are v1 documents in the v1 shape; the current versions (OP-0001 v1.0.0, OP-0002 v0.3.0) are complete v2 documents that pass the strict tier.

**Why:** A new file keeps every existing v1 document valid against the schema it was written with, and exercises the schema-evolution design before a real migration is needed. Deriving both tiers from one schema keeps the "schema is the single source of truth" rule (Requirements §5).

---

## 15. Edit Lock Storage and Timing

**Decision:**

- `locking.acquire_lock` implements the five cases of Backend_Design §6. A re-run by the lock holder in the same session is the heartbeat, so the editor calls `acquire_lock` on every re-run; `heartbeat` is also available for callers that already hold the lock. An expired lock of the same user (for example from a closed tab) is re-acquired without an override event; only taking over another user's expired lock is logged (`outcome=lock_override`).
- The takeover rule is evaluated inside the write: `DataAccess.write_lock` is a single `MERGE` that only replaces the row when it is missing, expired, or held by the same user and session. `acquire_lock` then reads the row back, so of two sessions racing for a lock exactly one holds it and the other is told who does. `refresh_lock` only updates the row of the same user and session, so a lock taken over after expiry is never revived by a late heartbeat.
- All lock timestamps are UTC. Naive timestamps read back from Delta are treated as UTC.
- The TTL is `ONE_PAGER_APP_LOCK_TTL_SECONDS` (default 1800 = 30 minutes, Requirements §10), so integration tests can use a few seconds (Testing_Strategy §4).
- An expired lock row stays in the table until it is overwritten or released; readers treat it as "not locked" (`locking.active_lock`).
- **Release my lock** (Preview) calls `locking.release_lock`: only the holder (`locked_by_initials == user.initials`) may release, from any session; anyone else gets `PermissionDeniedError` and a `permission_denied` event. It is the only Preview action enabled in Phase 3. Lock times are shown in UTC.
- The Registry reads the locks of the rows on the current page with one query (`DataAccess.get_locks`, filtered to active locks by `locking.get_active_locks`), never cached, and shows them in a **Lock** column as `🔒` plus the holder's initials. If the locks cannot be read, the table still renders with `?` in that column.

**Why:** Delta does not enforce the `locks` primary key, so a read-then-insert would let two editors both believe they hold the lock. A guarded MERGE plus a read-back gives the same guarantee as the ID allocation's compare-and-set (`id_generator.next_id`) without a separate lock service.

---

## 16. Data Product Status During the Review of an Update

**Context:** Requirements §6 lets an approved One Pager go `Approved` → `Draft Update` → `Ready for Review` → `In Review` → `Approved` while the Data Product status is preserved, but its valid-combinations table allows only `In Definition` with `Ready for Review` and `In Review`, and a Reject always returns to `Draft` (whose only valid DP status is `In Definition`). Taken literally, an update could never be submitted, and rejecting one would produce an invalid pair. The Cancel guard ("only while the Data Product is `In Definition`") already assumes that `Draft` / `Ready for Review` / `In Review` can carry other DP statuses.

**Decision:**

- `Ready for Review` and `In Review` are valid with `In Definition` (first review) and with every post-approval DP status (review of an update, DP preserved).
- A Reject returns to `Draft` when the Data Product is `In Definition`, and to **`Draft Update`** otherwise (`approver_reject` `In Review` → `Draft Update`, change-log summary "Update rejected"). `Draft` stays `In Definition` only, so a rejected update is never mistaken for a first draft, and Cancel stays unavailable for it as for any `Draft Update`.
- Approve tells a first approval from a re-approval by the DP status: `In Definition` → `Ready for Development`, anything else → `In Enhancement` (a DP already `In Enhancement` keeps it).

**Why:** It is the smallest change that makes the documented update cycle work end to end, keeps "Draft Update: DP preserved" true through the whole cycle, and keeps every status pair checked by `VALID_COMBINATIONS`.


---

## 17. Registry and Use Case List Caching

**Context:** UI_Design §6 asks for a short TTL on the Registry list and the Use Case registry, "invalidated explicitly after writes". `st.cache_data` cannot hash a `DataAccess` instance, and each Streamlit session builds its own instance, so the cache key has to say which data an instance reads.

**Decision:**

- `app/adapters/cache.py` caches `get_registry`, `get_registry_status_counts` and `get_use_cases` for 30 seconds (`LIST_CACHE_TTL_SECONDS`). The Registry page, the Use Cases page and the editor's Use Case picker read through it. Single records (Preview, Use Case details), locks, the review queue and editor content are not cached.
- The key includes `DataAccess.cache_scope`. `LakehouseAccess` returns `lakehouse:<catalog>.<schema>`, so every session of an app instance shares one cache. `MockDataAccess` (in-memory data per session) gets a random scope per instance, so sessions never see each other's mock data.
- Every app-layer write that can change these lists runs inside `writes_data()`: create, Save Draft, Submit, Approve, Reject, Cancel, Update, DP status changes, and Use Case create, edit, deprecate and restore (including the editor's inline create). It clears the three caches for all sessions when the write ends, **also when it fails**, because a failed write may have changed some rows before it rolled back. Review comments and locks do not change the lists, so they do not clear them.

**Why:** Clearing on write keeps a user's own change visible at once. Other app instances (replicas) see the change within the TTL. Clearing the whole cache is cheap, because writes are rare compared to Registry reads.

---

## 18. PDF Export Without a Third-Party PDF Library

**Context:** Backend_Design §10 and Project_Structure open item #8 left the PDF library open: `weasyprint` or `fpdf2`. `weasyprint` needs native libraries (Pango, cairo) that the Databricks Apps runtime does not guarantee. `fpdf2` is pure Python but pulls in `fonttools`, `defusedxml` and `Pillow`, and every new package has to be available in the approved Artifactory index before the app can be deployed.

**Decision:** Neither library. `onepagerapp/pdf.py` is a small PDF 1.4 writer built on the standard library (`zlib` for compression) and the standard Helvetica fonts, which every PDF reader has built in, so no font files are embedded. It supports exactly what the export needs: a title band, headings, wrapped paragraphs, "Label: value" lines, bullet lists, colored status badges (always with their text) and a page footer with "Page n of N". `onepagerapp/export.py` lays out the One Pager with it:

- A4, every section in editor-tab order, then the change log. Review comments are left out: they are part of the review conversation, not the business-facing document.
- Table sections (Use Cases, requirements, data elements, …) are printed as one block per row, the first column as its title and the other non-empty columns as labelled lines. The data element grid has ten columns, which do not fit an A4 page as a table.
- Text is encoded as WinAnsi (cp1252). That covers Danish and the other Western European letters; other characters are replaced by an ASCII form (`→` becomes `->`) or `?`.
- The export reads the current version the way Preview does. Reading an approved version from Git comes with the Git integration (Phase 8, item 7.3).

**Why:** No new dependency to approve, install or keep patched, and the output is predictable and fully unit-tested (the tests read the uncompressed page contents). The cost is a fixed, simple layout and no characters outside WinAnsi. If richer layout or full Unicode is needed later, `export.py` is the only caller of `pdf.py`, so switching to `fpdf2` changes those two modules only.

---

## 19. Data Access Identity: Reads as the User, Writes as the Service Principal

**Context:** The platform grants privileges to groups, never directly to users. Until now every SQL statement ran with the signed-in user's token (§3), while the YAML documents were written through the volume mount as the service principal, and Architecture §8 described a single service identity for all storage. Running every write as the user would require `MODIFY` grants for Owners/SMEs, Approvers and Admins on the app tables, which would let them change statuses, authorized users or the change log directly in the SQL editor, outside the workflow, validation and segregation-of-duties checks. Running everything as the service principal would drop Unity Catalog as a second line of defence and hide who read what.

**Decision (option B):**

- **Reads run as the user** (`x-forwarded-access-token`, scope `sql`). All employees (`account users`) have `SELECT` on the app tables.
- **Writes run as the service principal**, and so does the ID-sequence read that is part of a write. The lock and conditional-update checks are part of the guarded write statements themselves (`MERGE … WHEN MATCHED AND …`, `UPDATE … WHERE version = :expected_version`). No user group has `MODIFY` on the app tables or `WRITE VOLUME` on the registry volume.
- The connection takes an explicit identity on every statement (`Identity.USER` / `Identity.APP`, no default); `LakehouseAccess` runs every statement through `_read` or `_write`. Tests fail if a write statement is sent as the user or a new method is not classified. In `databricks` mode a missing user token raises (`MissingUserTokenError`) instead of falling back to the service principal.
- A missing grant on a **read** is shown to the user ("Your role does not have access to … Contact the platform team."); on a **write** it is a deployment error, logged with the details, with a generic message. An expired user token on a read gives "Your session has expired. Please reload the page." (Streamlit keeps the token of the first connection for the session); writes are not affected.
- The YAML documents on the volume are read and written through the app's volume mount, i.e. as the service principal; accepted because every user may view every One Pager.
- Every write carries the acting user's corporate initials (audit columns or the change-log entry of the same operation), because Delta history now shows only the service principal.
- `local-integration` uses the CLI profile for both identities.

**Why:** Users cannot bypass the app's rules, while reads keep per-user Unity Catalog checks and per-user platform audit logs. The cost is that platform write history shows the service principal; the app's audit columns, change log and security-event log are the record of who changed what. Implementation: `..dev/User_Identity_And_Access_Plan.md` Phases 3–4.

---

## 20. Configurable Username Format and Corporate Initials

**Context:** Usernames look like `x0wadm@becoc001.onmicrosoft.com`. The user part is the corporate initials plus `adm`; the suffix may be dropped and the domain may change. Corporate initials may contain digits (`X0W`) and differ from name initials (`AK`). `auth.py` had the format hard-coded (`<2–4 letters>ADM`), guessed initials from other usernames, and `validation.py` rejected digits in initials, so `X0W` could not be an Owner or SME.

**Decision:**

- Settings `ONE_PAGER_APP_USER_DOMAINS` (default `becoc001.onmicrosoft.com`), `ONE_PAGER_APP_USERNAME_SUFFIXES` (default `adm`; an empty entry allows usernames without a suffix) and `ONE_PAGER_APP_INITIALS_PATTERN` (default `^[A-Z0-9]{3}$`: corporate initials are always 3 letters or digits, case does not matter).
- Old and new domains are never valid at the same time; a domain change is a configuration change at the switch. `X0Wadm` is today's Databricks username for the initials `X0W`; if usernames are later aligned to `X0W`, the suffix setting changes to `adm,` for the switch and then to empty.
- Initials are extracted by domain check, suffix strip (longest match), upper-casing and pattern check. A username that does not match gives **no** initials; the guessing fallbacks are removed.
- The same pattern validates the Owner/SME `initials` in One Pagers; the value is upper-cased first, so `x0w` is stored as `X0W`.
- What happens to a username without initials is §23; where the name comes from is §24.

**Why:** A format change becomes a configuration change, a wrong or unknown account can never be mapped to someone else's initials, and users with digits in their corporate initials can own One Pagers. Implementation: `..dev/User_Identity_And_Access_Plan.md` Phase 1.

---

## 21. Roles from Entra ID Groups, with an Interim Group

**Context:** The Owner/SME, Approver and Admin roles were meant to come from groups (Architecture §4), but no groups existed, so Approvers and Admins were configured as initials in `ONE_PAGER_APP_APPROVERS` / `ONE_PAGER_APP_ADMINS`, and creating One Pagers and managing Use Cases was open to every user (§7). Automatic identity management is enabled on the Databricks account, so Entra ID groups can be used directly.

**Decision:**

- One Entra ID group per role; the names are settings `ONE_PAGER_APP_GROUP_OWNER_SME`, `ONE_PAGER_APP_GROUP_APPROVER`, `ONE_PAGER_APP_GROUP_ADMIN`, with a `{env}` placeholder for the environment.
- **Interim:** the dedicated groups do not exist yet. All three settings default to `PAG-BEC-LHX-{env}-DataPlatEng-Base` (`…-DEV-…`, `…-INT-…`, `…-TST-…`, `…-UAT-…`, `…-PRD-…`). Its members act as Owner/SME, Approver and Admin; everyone else is a Viewer. The app logs a warning and, outside DEV, shows an "interim roles" notice while the default is in use.
- **Viewer = every employee**; there is no Viewer group.
- Membership is checked once per session as the user: `is_account_group_member(:group) OR is_member(:group)`. A failed check gives Viewer only.
- `can_create_one_pager` and `can_manage_use_cases` require the Owner/SME group (`Actor.OWNER_SME_GROUP`), in the pages and in the services (`create_one_pager` and the Use Case writes take the session's roles). Editing an existing One Pager still needs only the per-record Owner/SME listing, so business Owners/SMEs listed on a One Pager can edit it without the group.
- `ONE_PAGER_APP_APPROVERS` / `ONE_PAGER_APP_ADMINS` are removed; local mock mode uses `ONE_PAGER_APP_MOCK_GROUPS` (default: the interim group, i.e. every role).
- Membership is managed in Entra ID by the Data Platform Engineering team; the app has no role administration.

**Why:** Access is managed through the company's standard group process and access reviews, nobody can grant themselves a role in the app, and moving from the interim group to dedicated groups is a configuration change only. Business Owners/SMEs and Nykredit reviewers outside DataPlatEng cannot create One Pagers or review until the dedicated groups exist, which is accepted for the interim. Implementation: `..dev/User_Identity_And_Access_Plan.md` Phase 6.

---

## 22. Actor Columns on the Reference Tables

**Context:** Writes run as the app's service principal (§19), so Delta history shows the service principal, not the person. Every operational table already records the acting user (Data_Model §5), but the Admin-managed `ref_*` tables had no "changed by" column, so an Admin's change to reference data was recorded only in the security-event log, which is kept for a limited time and is not next to the data.

**Decision:** Add nullable `last_updated_by` (initials) and `last_updated_at` columns to all five `ref_*` tables (Liquibase `ddl/ref_audit_columns.sql`). Every Admin add or edit sets them; the data access write methods require the actor, so a write without one does not type-check. Seeded rows keep NULL until an Admin changes them. A deleted value leaves no row, so its record is the `delete_reference_value` security event (deletes are allowed only for unused values). The security event per change stays as the second record.

**Why:** Who changed a reference value is visible next to the value itself and survives log retention, at the cost of two columns per table. An audit-event-only approach was rejected because it depends on log retention and is not visible in the data.

---

## 23. Fail Closed on an Unrecognised Account

**Context:** The app used to fall back to `SELECT current_user()` when the proxy headers were missing, and guessed initials from usernames that did not follow the corporate format. With writes running as the service principal (§19), a wrong or guessed identity would be written into the audit columns and could match someone else's initials.

**Decision:**

- The username comes from one source per mode: deployed, only the Databricks Apps proxy headers (`x-forwarded-preferred-username`, then `x-forwarded-email`); `local-integration`, `SELECT current_user()` with the CLI profile; `local-mock`, `ONE_PAGER_APP_MOCK_USER`.
- No username, a username that gives no initials (§20) or a failed identity lookup → an "Access denied" page and `st.stop()`, before any page or data access is created. There is no read-only mode (the answer to the open question was "deny access").
- The refusal is logged once per session as `action=access_app outcome=permission_denied`, with the username, because there are no initials (the one exception to "initials only" in the security log).
- Every service entry point checks again (`permissions.require_identity`), so a missing identity can never reach a write even if a page is reached.

**Why:** Nobody can act under a guessed identity, and an unknown account (guest, service account, changed domain without a configuration change) is visible in the log instead of silently getting someone's rights. Implementation: `..dev/User_Identity_And_Access_Plan.md` Phase 2.

---

## 24. First Name and Surname from the Workspace Directory

**Context:** The username contains only the corporate initials, which differ from personal initials, and the app showed a name guessed from the username ("X0wadm").

**Decision:**

- Once per session, the app reads the user's entry from the workspace directory: SCIM `Me` (`GET /api/2.0/preview/scim/v2/Me`) with the user's forwarded token and the user API scope `iam.current-user:read`. Never with the service principal, which would return the app's own entry.
- The name shown is `givenName familyName`, else `displayName`, else the initials; nothing is guessed from the username. The primary email pre-fills the Owner row when creating a One Pager.
- A failed lookup is logged and the initials are shown; it never blocks the app. `local-integration` uses the CLI profile; `local-mock` uses `ONE_PAGER_APP_MOCK_USER_NAME`.
- The name is for display only (sidebar, change log, YAML, comments, locks, PDF), never for authorization.
- If the directory turns out to have no names, a `ref_users` table managed on the Admin page is the fallback (not built).

**Why:** Users see real names without another system or an Entra app registration (Microsoft Graph would need one and admin consent), and the lookup runs with the user's own, minimal scope. Implementation: `..dev/User_Identity_And_Access_Plan.md` Phase 5.

---

## 25. Environment Derived from the Registry Volume

**Context:** The environment decides the sidebar badge and the `{env}` of the role group names (§21). `app.yml` is the same for every environment, so it cannot set a per-environment value, and the catalog setting falls back to its DEV default, so every environment would have resolved the DEV DataPlatEng group.

**Decision:** The environment is `ONE_PAGER_APP_ENVIRONMENT` when set, otherwise the catalog prefix of the registry volume (`ONE_PAGER_APP_VOLUME_PATH`, set per environment by the app resource: `/Volumes/prd_bia_meta/...` → `PRD`), otherwise the catalog setting's prefix, otherwise DEV. TST was added as an environment, because the app is deployed there.

**Why:** The one value that already differs per environment determines the environment, so no per-environment app configuration is needed. `ONE_PAGER_APP_DATABRICKS_CATALOG` itself still defaults to `dev_bia_meta`; deriving it the same way is an open follow-up, needed before the app uses the tables of a non-DEV environment. Implementation: `..dev/User_Identity_And_Access_Plan.md` Phase 7.
