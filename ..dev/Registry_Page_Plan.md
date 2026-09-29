# Registry Page — Implementation Plan

## 1. Purpose

This document defines the ordered implementation plan for the Registry page (`app/views/registry.py`), taking it from the current static mock-up to a fully data-backed page.

It implements the design defined in [UI_Design.md](UI_Design.md) §4.1, using the tables defined in [Data_Model.md](Data_Model.md) §3, the layering rules from [Project_Structure.md](Project_Structure.md) §2b, and the test coverage expectations from [Testing_Strategy.md](Testing_Strategy.md).

## 2. Current State vs. Target

[app/views/registry.py](../app/views/registry.py) is currently a standalone mock-up:

- The table is a hardcoded empty `pd.DataFrame` with `TODO` markers for real data.
- Status colors are hardcoded in [app/adapters/theme.py](../app/adapters/theme.py).
- Filtering and pagination logic live in the view.

The last point violates the "views are thin — no business logic in views" principle from [Project_Structure.md](Project_Structure.md) §2b.

### Blocking gaps

| # | Gap | Where |
|---|---|---|
| 1 | All `include:` entries are commented out — even the existing `ref_op_status.sql` is never deployed | [root.changelog.databricks.yaml](../liquibase/bia_meta/onepager_app/root.changelog.databricks.yaml) |
| 2 | No DDL for `one_pager_status`, `ref_dp_status`, `ref_business_domains`, `ref_data_product_types`, `use_case_references`, `locks` | `liquibase/bia_meta/onepager_app/ddl/` |
| 3 | `DataAccess` exposes only `get_current_user`, `read_table`, `get_ref_op_status` | [data_access/base.py](../src/onepagerapp/data_access/base.py) |
| 4 | Badge colors hardcoded instead of read from `ref_*_statuses.badge_color` | [adapters/theme.py](../app/adapters/theme.py) |

## 3. Phase 0 — Resolve Contradictions First

| # | Item | Decision needed |
|---|---|---|
| 1 | Badge colors conflict: [UI_Design.md](UI_Design.md) §3 specifies `#6B6B6B` (Draft) and `#D4A000` (In Review); [Data_Model.md](Data_Model.md) §3 seed data specifies `#808080` and `#F9BD00` | Pick one palette, make the `ref_*_statuses` seed the single source of truth, record the choice in [Decision_Log.md](Decision_Log.md) |
| 2 | Scope of v1 | Recommend deferring the four features below and shipping metrics + filters + table + pagination |

### Features recommended for deferral

| Feature | Blocked by |
|---|---|
| Use Case filter | `use_case_references` + `use_cases` tables |
| Lock icon (🔒) column | `locks` table |
| Row click → Preview | Preview page does not exist |
| `[+ New]` button | Editor page and `permissions.py` do not exist |

## 4. Phase 1 — Storage

Unblocks everything that needs real data.

| # | Step | Detail |
|---|---|---|
| 3 | Add DDL files | `one_pager_status.sql`, `ref_dp_status.sql`, `ref_business_domains.sql`, `ref_data_product_types.sql` under `liquibase/bia_meta/onepager_app/ddl/` — columns exactly per [Data_Model.md](Data_Model.md) §3 |
| 4 | Wire up the root changelog | Add `include:` entries — reference tables first, then `one_pager_status` |
| 5 | Add DEV seed data | A handful of sample `one_pager_status` rows so the page can be verified end-to-end |

## 5. Phase 2 — Core Layer (`src/onepagerapp/`, no Streamlit)

| # | Step | Detail |
|---|---|---|
| 6 | Add `models.py` | `RegistryFilter` (product_name, op_status, dp_status, owner, domain, type), `RegistryRow`, `RegistryPage(rows, total_rows, page, page_size)`, `StatusRef(status, display_label, sort_order, badge_color)` |
| 7 | Extend the `DataAccess` ABC | `get_registry(filter, page, page_size) -> RegistryPage`, `get_registry_status_counts(filter) -> dict[str, int]`, `get_ref_dp_status()`, `get_ref_business_domains()`, `get_ref_data_product_types()` |
| 8 | Implement in `mock.py` **first** | In-memory rows plus the real filter/paginate logic. Unblocks the entire UI under `APP_MODE=local-mock` without touching Databricks |
| 9 | Implement in `lakehouse.py` | Server-side `WHERE` / `ORDER BY` / `LIMIT` + `OFFSET`; a `GROUP BY one_pager_status` aggregate for the metric cards |

> **Security:** every filter value must go through parameterized statements (`parameters=` on the SQL Execution API). Never f-string user input into SQL. Covered by the SQL-injection test in [Testing_Strategy.md](Testing_Strategy.md) §6.

## 6. Phase 3 — Presentation (`app/`)

| # | Step | Detail |
|---|---|---|
| 10 | Refactor `adapters/theme.py` | Drop the hardcoded color dicts; build the color map from the reference tables. `status_badge()` renders `[colored dot] + [text label]` per [UI_Design.md](UI_Design.md) §3 — dot plus text, never color alone (accessibility) |
| 11 | Rewrite `views/registry.py` as a thin view | Read reference data → render metric cards from `get_registry_status_counts()` → render filters populated from the reference tables → call `get_registry(...)` → render table + pagination. No filtering or slicing logic left in the view |
| 12 | Implement all page states | Loading (`st.spinner`), Populated, Empty ("No One Pagers yet"), Empty-after-filter ("No One Pagers match your filters" + Clear filters), Error (friendly banner + Retry, no raw exception text) — per [UI_Design.md](UI_Design.md) §4.1 |
| 13 | Cache reference lookups | `@st.cache_data` on the `ref_*` lookups (static per session). Do **not** cache the registry query itself |

## 7. Phase 4 — Tests

Per [Testing_Strategy.md](Testing_Strategy.md).

| # | Layer | Coverage |
|---|---|---|
| 14 | Unit | Filter combination logic (AND semantics), pagination boundaries, status-count aggregation, `MockDataAccess` contract |
| 15 | Integration | `tests/integration/test_registry_repository.py` — round-trip against a live warehouse; include a filter value containing SQL metacharacters to prove parameterization |
| 16 | Smoke | `AppTest` rendering of the Registry page in mock mode |

## 8. Phase 5 — Ship

| # | Step | Detail |
|---|---|---|
| 17 | Quality gates | `ruff check` + `ruff format` + `mypy` + `pytest tests/unit/` |
| 18 | Bump `VERSION` | In [__version.py](../src/onepagerapp/__version.py). Per [Dev_Notes.md](Dev_Notes.md), pip skips reinstalling the wheel without a version bump, so the deploy silently does nothing |
| 19 | Deploy | Bundle deploy → Liquibase migrate → verify in DEV |

## 9. Sequencing Note

Phase 1 and steps 6–8 + Phase 3 can run **in parallel**: build the entire UI against `MockDataAccess`, then drop in `LakehouseAccess` once the tables exist.

This preserves the current "simple view without real data underneath" during development, but structures it so the swap to real data is a one-line change in [data_access/factory.py](../src/onepagerapp/data_access/factory.py) rather than a rewrite of the page.
