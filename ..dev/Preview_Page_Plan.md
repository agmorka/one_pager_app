# Preview Page — Implementation Plan

## 1. Purpose

This document defines the ordered implementation plan for the Preview page (`app/views/preview.py`), taking it from non-existent to a fully data-backed, read-only view of a single One Pager.

It implements the design defined in [UI_Design.md](../docs/UI_Design.md) §4.4, using the tables defined in [Data_Model.md](../docs/Data_Model.md) §3, the document schema in [structure_one_pager_v_1.json](../schemas/structure_one_pager_v_1.json), the workflow/permission rules from [Backend_Design.md](../docs/Backend_Design.md) §2–§6, the layering rules from [Project_Structure.md](../docs/Project_Structure.md) §2b, and the test coverage expectations from [Testing_Strategy.md](../docs/Testing_Strategy.md).

It follows the same phased structure as [Registry_Page_Plan.md](Registry_Page_Plan.md).

## 2. Current State vs. Target

There is **no** Preview page today. Only [home.py](../app/views/home.py) and [registry.py](../app/views/registry.py) are registered in [app.py](../app/app.py)'s `st.navigation(...)`.

**Target:** a read-only, business-readable view of one One Pager selected by `one_pager_id`, showing:

- Header (product name, ID, OP status badge, DP status badge, version, owner)
- Status timeline (Draft → Ready for Review → In Review → Approved)
- Role/status-conditional action bar (Edit, Update, Approve, Reject, Change DP Status, Cancel, Export PDF)
- Content sections rendered as collapsible business-readable blocks (same order as the editor tabs)
- Review comments (with resolve / add-comment in review mode)
- Change log
- Lock indicator (release for the holder; read-only notice for others)
- States: Loading / Populated / Not found / Error

Per [Project_Structure.md](../docs/Project_Structure.md) §2b, the view must be **thin** — no business logic, no direct Delta/volume/Git access. All reads go through the `DataAccess` layer; all state changes go through a future service layer.

### Content source split (important)

Per [Data_Model.md](../docs/Data_Model.md) §1, the One Pager **document content** (description, business problem, use cases, data sources, etc.) lives as **YAML in a UC volume (pre-approval) / Git (post-approval)** — *not* in Delta. Only operational data (status row, change log, review comments, locks) is in Delta. Preview therefore composes two sources:

| Region | Source |
|---|---|
| Header, status badges, version, owner | `one_pager_status` (Delta) |
| Content sections (all schema fields) | YAML document (volume or Git) |
| Change log | `change_log` (Delta) |
| Review comments | `review_comments` (Delta) |
| Lock indicator | `locks` (Delta) |

### Blocking gaps

| # | Gap | Where |
|---|---|---|
| 1 | No Preview page exists and it is not registered in navigation | [app.py](../app/app.py) |
| 2 | `DataAccess` cannot read a single One Pager document, its change log, review comments, or lock | [data_access/base.py](../src/onepagerapp/data_access/base.py) |
| 3 | No volume/Git read path — content YAML cannot be loaded | `data_access/` (volume reader not implemented) |
| 4 | No DDL for `change_log`, `review_comments`, `locks` (registry plan already covers `one_pager_status` + ref tables) | `liquibase/bia_meta/onepager_app/ddl/` |
| 5 | No domain models for the full document, change-log entries, review comments, lock | [models.py](../src/onepagerapp/models.py) |
| 6 | No permission/workflow/locking service to gate the action buttons | `src/onepagerapp/` (no `permissions.py` / `workflow.py` / `locking.py` yet) |
| 7 | No PDF export module | `src/onepagerapp/` (no `export.py`) |
| 8 | No selection mechanism to carry `one_pager_id` from Registry → Preview | Registry row-click is a deferred feature in [Registry_Page_Plan.md](Registry_Page_Plan.md) §3 |

## 3. Phase 0 — Resolve Contradictions & Scope First

| # | Item | Decision needed |
|---|---|---|
| 1 | v1 scope | Recommend shipping **read-only Preview**: header, status timeline, content sections, change log, read-only review comments, and lock indicator (display only). Defer every state-changing action to later phases (see below). |
| 2 | Content source for v1 | Pre-approval reads from the volume; post-approval reads from Git. Recommend implementing the **volume read path first** and deferring the Git read path until the approval flow exists — approved documents can temporarily read the last volume copy in DEV. Record the choice in [Decision_Log.md](../docs/Decision_Log.md). |
| 3 | Navigation entry | Preview needs a `one_pager_id`. Until Registry row-click exists, add a temporary selector (text input / dropdown of IDs) so Preview is verifiable standalone in mock mode. |
| 4 | Export PDF | Confirm whether Export PDF is in v1 or deferred. Recommend **deferring** (`export.py` + template are open items in [UI_Design.md](../docs/UI_Design.md) §8 and [Testing_Strategy.md](../docs/Testing_Strategy.md)). |

### Actions recommended for deferral (need the service layer)

| Action | Blocked by |
|---|---|
| Edit / Update | Editor page + `workflow.py` + `permissions.py` + lock acquisition |
| Approve / Reject | `workflow.py` (segregation-of-duties) + `review_comments` writes + Git PR |
| Change DP Status | DP-status state machine in `workflow.py` |
| Cancel | `workflow.py` cancel transition + lock release |
| Add / Resolve review comment | `review_comments` writes + `permissions.py` |
| Release my lock | `locking.py` release + ownership check |
| Export PDF | `export.py` + PDF template |

> v1 renders these buttons **disabled with a "coming soon" tooltip** (or hides them), so the layout matches [UI_Design.md](../docs/UI_Design.md) §4.4 while the service layer is built out.

## 4. Phase 1 — Storage

Unblocks the operational data (change log, comments, lock) shown on the page. (The `one_pager_status` + ref tables are already covered by [Registry_Page_Plan.md](Registry_Page_Plan.md) Phase 1.)

| # | Step | Detail |
|---|---|---|
| 1 | Add DDL files | `change_log.sql`, `review_comments.sql`, `locks.sql` under `liquibase/bia_meta/onepager_app/ddl/` — columns exactly per [Data_Model.md](../docs/Data_Model.md) §3, including `GENERATED ALWAYS AS IDENTITY` PKs for `change_log`/`review_comments` |
| 2 | Wire up the root changelog | Add `include:` entries after the tables from the registry plan |
| 3 | Add DEV seed data | A few `change_log`, `review_comments`, and a sample `locks` row for existing seeded `one_pager_status` IDs, so every page region can be verified end-to-end |
| 4 | Sample YAML documents | Add DEV sample One Pager YAML files to the volume path (or a local fixtures dir for mock mode) matching [structure_one_pager_v_1.json](../schemas/structure_one_pager_v_1.json) |

## 5. Phase 2 — Core Layer (`src/onepagerapp/`, no Streamlit)

| # | Step | Detail |
|---|---|---|
| 5 | Add domain models to [models.py](../src/onepagerapp/models.py) | `OnePagerDocument` (typed view of the schema: basics, owner, SMEs, business problem, use cases, business requirements, data sources, classification, CDE flags, change log), `ChangeLogEntry(version, date, author, event_type, summary, from_status, to_status, status_field)`, `ReviewComment(id, section, reviewer, comment, resolved, resolved_by, created_at, resolved_at)`, `LockInfo(locked_by_initials, locked_by_name, session_id, acquired_at, expires_at)`, and a composed `PreviewData(header, document, change_log, review_comments, lock)` |
| 6 | Extend the `DataAccess` ABC | `get_one_pager(one_pager_id) -> PreviewData \| None` (returns `None` for not-found), plus focused readers it composes: `get_one_pager_status(id)`, `read_document(id, status)` (volume/Git YAML), `get_change_log(id)`, `get_review_comments(id)`, `get_lock(id)` |
| 7 | Implement in `mock.py` **first** | In-memory document(s), change-log entries, review comments, and lock state keyed by `one_pager_id`. Unblocks the entire Preview UI under `APP_MODE=local-mock` without Databricks or a volume |
| 8 | Implement in `lakehouse.py` | Parameterized single-row reads of `one_pager_status`, `change_log` (ordered newest-first), `review_comments`, and `locks`; document read from the UC volume (approved → Git, per Phase 0 decision). Reuse the existing `_escape_sql_string` / parameterized pattern — never f-string `one_pager_id` into SQL |
| 9 | Read-only permission helper | A minimal `can_view()` (always true for authenticated users per [Backend_Design.md](../docs/Backend_Design.md) §5) plus a **stub** `permissions.py` that computes which action buttons *would* be enabled by role+status — used only to render disabled/enabled buttons in v1; full enforcement lands with the service layer |

> **Security:** `one_pager_id` and any future comment text must flow through parameterized statements (`parameters=` on the SQL Execution API). YAML content is untrusted display data — render as text, never as `unsafe_allow_html`. Covered by the SQL-injection / XSS tests in [Testing_Strategy.md](../docs/Testing_Strategy.md) §6.

## 6. Phase 3 — Presentation (`app/`)

| # | Step | Detail |
|---|---|---|
| 10 | Create `app/views/preview.py` (thin view) | Resolve `one_pager_id` (from session/query param, temp selector in v1) → `get_one_pager(id)` → render header, timeline, sections, comments, change log, lock. No composition/business logic in the view |
| 11 | Register the page | Add `st.Page("views/preview.py", title="Preview")` to [app.py](../app/app.py) navigation |
| 12 | Reuse theme badges | Render OP/DP status via existing `status_badge()` + cached `get_op_status_colors` / `get_dp_status_colors` from [theme.py](../app/adapters/theme.py). Add a small status-timeline renderer (dot + label sequence, current step marked) |
| 13 | Render content sections | Collapsible `st.expander` blocks in editor-tab order ([UI_Design.md](../docs/UI_Design.md) §4.4): Description, Business Problem, Use Cases (table), Business Requirements, Data Sources, Data Product Preview, Classification, Governance, Scope & Questions. Business-readable formatting — never raw YAML/JSON |
| 14 | Render change log & review comments | Change log newest-first (version, date, author, summary). Review comments grouped by section with resolved/unresolved indicator (text + icon, not color alone) |
| 15 | Action bar (v1 = display only) | Render the role/status-conditional button set from [UI_Design.md](../docs/UI_Design.md) §4.4 action matrix, using the Phase 2 permission stub. Buttons disabled with "coming soon" until the service layer exists |
| 16 | Lock indicator | Holder sees "Release my lock" (disabled in v1); others see read-only "Locked by {name} since {time}" notice |
| 17 | Implement all page states | Loading (`st.spinner`), Populated, Not found ("One Pager not found." + link back to Registry), Error (friendly banner + Retry, no raw exception text) — per [UI_Design.md](../docs/UI_Design.md) §4.4 |
| 18 | Caching | Do **not** cache the document, lock, or review queue (must be fresh per [UI_Design.md](../docs/UI_Design.md) §6). Reference tables stay `@st.cache_data`. Lock is read fresh on every re-run |

## 7. Phase 4 — Tests

Per [Testing_Strategy.md](../docs/Testing_Strategy.md).

| # | Layer | Coverage |
|---|---|---|
| 19 | Unit | `MockDataAccess.get_one_pager` contract (found / not-found); document model round-trip against the schema; change-log ordering (newest-first); review-comment grouping/resolved logic; permission-stub button matrix (each role × status → expected enabled actions per [UI_Design.md](../docs/UI_Design.md) §4.4) |
| 20 | Integration | `tests/integration/test_preview_repository.py` — round-trip a `one_pager_status` row + change log + review comment + lock against a live warehouse; read a YAML document from the volume; pass a `one_pager_id` containing SQL metacharacters to prove parameterization |
| 21 | Smoke | `AppTest` rendering of Preview in mock mode: Populated (all regions present), Not-found, and Error states |

## 8. Phase 5 — Ship

| # | Step | Detail |
|---|---|---|
| 22 | Quality gates | `ruff check` + `ruff format` + `mypy` + `pytest tests/unit/` |
| 23 | Bump `VERSION` | In [__version.py](../src/onepagerapp/__version.py). Per [Dev_Notes.md](../docs/Dev_Notes.md), pip skips reinstalling the wheel without a version bump, so the deploy silently does nothing |
| 24 | Deploy | Bundle deploy → Liquibase migrate → verify in DEV |

## 9. Sequencing Note

Phase 1 and steps 5–7 + Phase 3 can run **in parallel**: build the entire Preview UI against `MockDataAccess` (in-memory document + operational data), then drop in `LakehouseAccess` + the volume reader once the tables and DDL exist.

The state-changing actions (Edit, Approve, Reject, Change DP Status, Cancel, comments, lock release, Export PDF) are intentionally deferred to the service-layer phases — they depend on `workflow.py`, `permissions.py`, `locking.py`, `export.py`, and the Editor page, none of which exist yet. Shipping Preview read-only first delivers immediate value and gives the approval/editing work a place to attach.
