# Use Cases Page — Implementation Plan

## 1. Purpose

This document defines the ordered implementation plan for the Use Cases page (`app/views/use_cases.py`), taking it from non-existent to a fully data-backed page for managing the shared Use Case dictionary (registry).

It implements the design defined in [UI_Design.md](../docs/UI_Design.md) §4.5, the requirements in [Requirements_and_Scope.md](../docs/Requirements_and_Scope.md) §9 and §14, using the tables defined in [Data_Model.md](../docs/Data_Model.md) §3–§4, the Use Case service rules from [Backend_Design.md](../docs/Backend_Design.md) §5 and §9, the layering rules from [Project_Structure.md](../docs/Project_Structure.md) §2b, and the test coverage expectations from [Testing_Strategy.md](../docs/Testing_Strategy.md).

It follows the same phased structure as [Registry_Page_Plan.md](Registry_Page_Plan.md) and [Preview_Page_Plan.md](Preview_Page_Plan.md). It supersedes the earlier draft [use-cases-page-implementation-plan.md](use-cases-page-implementation-plan.md) — see §10 for what changed and why.

## 2. Current State vs. Target

There is **no** Use Cases page today. [app.py](../app/app.py) registers only Home, Registry, and Preview in `st.navigation(...)`.

**Target:** a page where every authenticated user can browse the shared Use Case registry, and Owners/SMEs can maintain it:

- Table of all Use Cases: ID, Persona, Goal, Priority, "Used by N OPs", deprecated marker
- Filters: text search (persona / goal), priority, "Show deprecated"
- Detail expander per row: all fields + **list of referencing One Pager IDs** (so an editor can assess impact before saving — Requirements §9)
- `[+ New Use Case]` → form → new `UC-###` ID allocated by the app (never typed by the user — Requirements §14)
- `[Edit]` → pre-filled form; `[Deprecate]` → confirmation dialog (UI_Design §5); `[Restore]` for deprecated rows
- States: Loading / Populated / Empty / Empty-after-filter / Error

Per [Project_Structure.md](../docs/Project_Structure.md) §2b, the view must be **thin**: filtering, pagination, ID generation, and validation live in `src/onepagerapp/`, never in the view.

### Blocking gaps

| # | Gap | Where |
|---|---|---|
| 1 | No DDL for `use_cases`, `use_case_references`, `id_sequences` | `liquibase/bia_meta/onepager_app/ddl/` |
| 2 | No Use Case models (`UseCase`, filter, page) | [models.py](../src/onepagerapp/models.py) |
| 3 | `DataAccess` has no Use Case read/write methods and **no write methods at all** yet — this is the first page that writes to Delta | [data_access/base.py](../src/onepagerapp/data_access/base.py) |
| 4 | `DatabricksConnection.execute_statement(statement)` accepts **no parameters**, yet [lakehouse.py](../src/onepagerapp/data_access/lakehouse.py) Preview methods already call it with `parameters=[...]` and `%s` placeholders (a `TypeError` at runtime in `databricks` / `local-integration` mode). There is no safe way to bind user-typed free text | [data_access/connection.py](../src/onepagerapp/data_access/connection.py) |
| 5 | No ID generator for `UC-###` (nor OP/BR) | `src/onepagerapp/` |
| 6 | No way to derive the current user's **initials** (needed for `created_by` / `last_updated_by`) | [permissions.py](../src/onepagerapp/permissions.py) / future `auth.py` |
| 7 | No "can manage Use Cases" permission check | [permissions.py](../src/onepagerapp/permissions.py) |
| 8 | Sample YAML fixtures store Use Cases **inline as full objects without IDs**, while [Data_Model.md](../docs/Data_Model.md) §5 says the YAML stores only `useCaseId` references | `tests/fixtures/sample_one_pagers/` |

## 3. Phase 0 — Resolve Contradictions & Scope First

| # | Item | Decision needed / recommendation |
|---|---|---|
| 1 | v1 scope | Ship **browse + filter + detail (with referencing OPs) + create + edit + deprecate + restore**. Defer link/unlink of a Use Case to a One Pager — that belongs to the Editor's Use Cases tab (UI_Design §4.2). |
| 2 | Restore (un-deprecate) | Not in [Backend_Design.md](../docs/Backend_Design.md) §9 (only `deprecate_use_case`). Recommend **including** it — deprecation is a soft state and a mistaken deprecation should be reversible without a DB fix. Record in [Decision_Log.md](../docs/Decision_Log.md). |
| 3 | Who may write | Docs are clear: Owner/SME group only; Approver/Admin/Viewer are read-only (Requirements §9, UI_Design §2 visibility table, Backend_Design §5). But group resolution (`auth.py`) does not exist yet. Recommend a **stub** `can_manage_use_cases(user) -> bool` in `permissions.py` that returns `True` for any authenticated user in v1, and route **every** write button through it — the later switch to real group checks is then a one-function change. Record the temporary relaxation in the Decision Log. Backend_Design §15 open item #2 (must the editor own a referencing DP?) stays open; v1 follows the current rule "any Owner/SME can manage any Use Case". |
| 4 | SQL safety | The Registry plan mandates parameterized statements; the implemented Registry uses `_escape_sql_string`. Use Case fields are **long user-typed free text** — recommend doing this properly: add parameter binding to `DatabricksConnection` (Phase 2, step 5) and use it for every Use Case query. |
| 5 | Initials format | [Requirements_and_Scope.md](../docs/Requirements_and_Scope.md) §2 and [Testing_Strategy.md](../docs/Testing_Strategy.md) (`auth.py`) define the corporate username as `<initials>ADM@BECOC001.onmicrosoft.com` → e.g. `MJOADM@…` → `MJO`. Recommend `extract_initials()` implementing that rule, with a deterministic fallback for other formats (mock user, local dev). Do **not** invent a "first letter of first/last name" rule as the primary path. |
| 6 | Mock-mode references | Because fixtures (gap 8) carry no UC IDs, `MockDataAccess` seeds its own `use_case_references` in memory (e.g. OP-0001 → UC-001, UC-002; OP-0002 → UC-002). Migrating the YAML to ID-only references is Editor work, not this page's. |
| 7 | Caching | UI_Design §6 suggests a short-TTL cache. The implemented Registry does not cache its list query. Recommend **no cache** for the Use Case list (small table, and it must reflect writes immediately); keep `st.cache_data` for reference data only. |
| 8 | Page file name | UI_Design names it `5_Use_Cases.py` (multipage convention); the app uses `st.navigation` with `app/views/*.py` → `app/views/use_cases.py`. Place it after Preview in the nav, matching UI_Design §2 order. |

## 4. Phase 1 — Storage

| # | Step | Detail |
|---|---|---|
| 1 | Add DDL files | `use_cases.sql`, `use_case_references.sql`, `id_sequences.sql` under `liquibase/bia_meta/onepager_app/ddl/` — columns exactly per [Data_Model.md](../docs/Data_Model.md) §3, same style as `locks.sql` (`-- changeset onepagerapp:cur_<table>-001`, `--rollback DROP TABLE …`). `use_cases.deprecated` gets `DEFAULT false` (needs `delta.feature.allowColumnDefaults` table property) or the app always writes it explicitly — pick one, the app writing it explicitly is simpler. Composite PK `(one_pager_id, use_case_id)` on `use_case_references`. |
| 2 | Seed `id_sequences` | `('OP', 0), ('UC', 0), ('BR', 0)` in the same changeset as the `CREATE TABLE` (as `ref_op_status.sql` does). |
| 3 | Wire up the root changelog | Add to [root.changelog.databricks.yaml](../liquibase/bia_meta/onepager_app/root.changelog.databricks.yaml): `id_sequences` with the no-dependency tables, then `use_cases`, then `use_case_references` after `one_pager_status`. |
| 4 | Add DEV seed data | `..dev/seed_use_cases_dev.sql` (like `seed_one_pager_status_dev.sql`): a handful of Use Cases (at least one deprecated), `use_case_references` rows pointing at the seeded One Pagers, and `id_sequences.UC` bumped to the highest seeded number. |

## 5. Phase 2 — Core Layer (`src/onepagerapp/`, no Streamlit)

| # | Step | Detail |
|---|---|---|
| 5 | Parameter binding in `DatabricksConnection` | `execute_statement(statement, parameters: dict[str, Any] \| None = None)` → pass `parameters=[StatementParameterListItem(name=k, value=…, type=…)]` to the SDK; SQL uses named markers (`:persona`). Backwards compatible for existing callers. Note: the Preview methods' `%s` + `parameters=[...]` calls must move to `:one_pager_id` + a dict at the same time, or they stay broken — fix in the same PR or a small preceding one. |
| 6 | Add models to [models.py](../src/onepagerapp/models.py) | `UseCase(use_case_id, persona, goal, scenario, decision_enabled, priority, deprecated, created_by, created_at, last_updated_by, last_updated_at, reference_count=0)`, `UseCaseFilter(search, priority, include_deprecated=False)`, `UseCasePage` (same shape/properties as `RegistryPage`), `UseCaseInput(persona, goal, scenario, decision_enabled, priority)` for create/edit payloads, and `PRIORITY_OPTIONS = ("Must Have", "High", "Medium", "Low")` kept in sync with `useCases.items.priority.enum` in [structure_one_pager_v_1.json](../schemas/structure_one_pager_v_1.json). |
| 7 | Input validation | `validate_use_case_input(data) -> list[str]` (new small module, e.g. `use_cases.py` or inside a future `validation.py`): all five fields required after `strip()`, priority ∈ `PRIORITY_OPTIONS`, a sane max length per field, HTML tags stripped (Testing_Strategy §6 — "sanitized on storage"). Pure function → trivially unit-testable; the view only displays its messages. |
| 8 | Initials + permission stubs | `extract_initials(user: str \| None) -> str` (rule from Phase 0 #5) and `can_manage_use_cases(user: str \| None) -> bool` (v1: authenticated → `True`) in [permissions.py](../src/onepagerapp/permissions.py), following the existing stub/docstring style. `extract_initials` moves to `auth.py` when that module lands. |
| 9 | Extend the `DataAccess` ABC | `get_use_cases(filter, page, page_size) -> UseCasePage` (with `reference_count`), `get_use_case(use_case_id) -> UseCase \| None`, `get_use_case_references(use_case_id) -> list[str]` (OP IDs, sorted), `create_use_case(data: UseCaseInput, user_initials) -> str`, `update_use_case(use_case_id, data, user_initials) -> None`, `set_use_case_deprecated(use_case_id, deprecated, user_initials) -> None`. Update/deprecate on a missing ID raise a typed `NotFoundError` (not a bare `RuntimeError`) so the view can show a precise message. |
| 10 | Implement in `mock.py` **first** | In-memory `use_cases` list + `use_case_references` set + UC counter, seeded per Phase 0 #6. `reference_count` is **derived** from the references set, never stored. Real filter/sort/paginate logic. Note that `MockDataAccess` lives in `st.session_state`, so mock writes persist per browser session — fine for local dev. Unblocks the whole UI under `APP_MODE=local-mock`. |
| 11 | Generic ID generator in `lakehouse.py` | `_next_id(id_type: str) -> int`, reused later for OP/BR: read `last_value` → `UPDATE id_sequences SET last_value = :new WHERE id_type = :t AND last_value = :cur` → **check the statement's `num_affected_rows` = 1** (it is in the SDK result) → otherwise retry (max 5, then raise). Do **not** confirm success by re-reading the value: two writers racing on the same `cur` would both read `cur + 1` and both believe they won → duplicate IDs. Also catch Delta `ConcurrentAppendException`/conflict errors and retry (Data_Model §4). Format with `f"UC-{n:03d}"`; raise a clear error above 999 (Data_Model §8 open item #1). A gap after a failed insert is harmless. |
| 12 | Implement in `lakehouse.py` | List query = `use_cases LEFT JOIN (SELECT use_case_id, COUNT(*) AS n FROM use_case_references GROUP BY use_case_id)` + `WHERE` built from bound parameters + `ORDER BY use_case_id LIMIT/OFFSET`, plus a `COUNT(*)` for totals. Escape `%`/`_` in the search term for `LIKE` (`ESCAPE '\\'`). Writes set `last_updated_by`/`last_updated_at = current_timestamp()`; create sets `deprecated = false` explicitly. All values bound via step 5 — no f-string user input. |

> **Security:** every user-typed value (persona, goal, scenario, decision enabled, search) goes through bound parameters. Stored text is untrusted display data — render with `st.text` / escaped markdown, never `unsafe_allow_html`. Covered by the injection/XSS tests in Phase 4.

## 6. Phase 3 — Presentation (`app/`)

| # | Step | Detail |
|---|---|---|
| 13 | Create `app/views/use_cases.py` (thin view) | Module docstring + structure like [registry.py](../app/views/registry.py): services guard → header with `[+ New Use Case]` (shown only if `can_manage_use_cases`) → filter bar (`FILTER_DEFAULTS` + Clear filters) → `get_use_cases(...)` → table → pagination (`use_cases_page` in session state; reset to 1 when filters change). |
| 14 | Table | Button-per-row pattern from Registry (`_ROW_COLUMN_RATIOS`, keyboard-accessible — UI_Design §7): ID, Persona, Goal, Priority, "Used by N OP(s)", status. Deprecated rows show a **"Deprecated" text label** plus muted styling — never color alone. |
| 15 | Detail expander | One `st.expander(f"{id} — {persona}")` per row (or open for the selected row): all fields, created/updated by/at, **Referenced by: OP-0001, OP-0003** (from `get_use_case_references`), and Edit / Deprecate / Restore buttons (gated by `can_manage_use_cases`). |
| 16 | Create / Edit form | One `@st.dialog` (available in pinned Streamlit 1.38) wrapping an `st.form`; pre-filled for Edit. On submit: `validate_use_case_input` → show errors next to the form and keep the user's input (UI_Design §5) → call data access → put a flash message in `st.session_state` → `st.rerun()`. Show the flash on the next run (a `st.success` placed right before `st.rerun()` is never seen). On Edit, show "This Use Case is referenced by N One Pagers — changes apply to all of them" above Save (Requirements §9 cross-referencing impact). |
| 17 | Deprecate confirmation | `@st.dialog` with [Confirm] + [Cancel] (UI_Design §5 lists "Deprecate Use Case" as a confirmed action). If `reference_count > 0`, the dialog lists the referencing OPs and explains they keep the Use Case but it can no longer be linked to new One Pagers (Backend_Design §9). Restore needs no confirmation. |
| 18 | Register the page | Add `st.Page("views/use_cases.py", title="Use Cases")` after Preview in [app.py](../app/app.py). |
| 19 | Implement all page states | Loading (`st.spinner`), Populated, Empty ("No Use Cases yet" + New button for managers), Empty-after-filter ("No Use Cases match your filters" + Clear filters), Error (friendly banner + Retry, no raw exception text — UI_Design §5). Write failures: friendly error inside the dialog, input preserved. |

## 7. Phase 4 — Tests

Per [Testing_Strategy.md](../docs/Testing_Strategy.md). Unit tests use `tmp_path` for the document store and `@pytest.mark.unit`.

| # | Layer | Coverage |
|---|---|---|
| 20 | Unit — core | `validate_use_case_input` (each missing field, bad priority, max length, HTML stripping); `extract_initials` (`MJOADM@BECOC001.onmicrosoft.com` → `MJO`, fallback formats, `None`); `can_manage_use_cases`; `UseCasePage` pagination properties; ID formatting and >999 guard. |
| 21 | Unit — `MockDataAccess` contract | Deprecated hidden by default / shown with the flag; search on persona OR goal (case-insensitive); priority filter; AND semantics; pagination boundaries; create returns the next sequential `UC-###` and persists; update/deprecate/restore change audit columns; `NotFoundError` on unknown ID; `reference_count` matches `get_use_case_references`. |
| 22 | Unit — `LakehouseAccess` with a fake connection | Inject a fake `DatabricksConnection` that records statements + parameters: user input never appears in the SQL text (only in bound params); `LIKE` wildcards escaped; `_next_id` retries when `num_affected_rows = 0` and succeeds on the next attempt; gives up after 5 attempts. |
| 23 | Integration | `tests/integration/test_use_cases_repository.py` — create/edit/deprecate/restore round-trip against a live warehouse; values containing `'`, `;`, `--`, `%` stored verbatim; two concurrent `create_use_case` calls (threads) → distinct IDs (Testing_Strategy §4). |
| 24 | Smoke | `AppTest` of `views/use_cases.py` in mock mode: Populated, Empty-after-filter, Error (data access raising), and create-flow happy path. |

## 8. Phase 5 — Ship

| # | Step | Detail |
|---|---|---|
| 25 | Quality gates | `ruff check` + `ruff format` + `mypy` + `pytest tests/unit/` |
| 26 | Docs | Decision Log entries (Phase 0 #2, #3, #4); mark the Use Case items resolved where applicable in [Data_Model.md](../docs/Data_Model.md) / [UI_Design.md](../docs/UI_Design.md). |
| 27 | Bump `VERSION` | In [__version.py](../src/onepagerapp/__version.py). Per [Dev_Notes.md](../docs/Dev_Notes.md), pip skips reinstalling the wheel without a version bump, so the deploy silently does nothing. |
| 28 | Deploy | Bundle deploy → Liquibase migrate (creates the three tables + seed) → run DEV seed → verify in DEV. |

## 9. Sequencing Note

Phase 1 and steps 6–10 + Phase 3 can run **in parallel**: build and verify the entire page against `MockDataAccess`, then drop in `LakehouseAccess` once the tables exist.

Step 5 (parameter binding) is a prerequisite for steps 11–12 and also unblocks the currently broken Preview queries in Databricks mode — consider landing it first as its own small PR.

`_next_id` is written generically so the Editor can reuse it for `OP-####` / `BR-###`, and `get_use_cases` / `get_use_case_references` are what the Editor's "Link Existing" picker and the Registry's deferred Use Case filter will need — this page lays the ground for both.

## 10. Changes vs. the Earlier Draft

[use-cases-page-implementation-plan.md](use-cases-page-implementation-plan.md) got the overall shape right (soft delete, registry-style layout, `st.dialog`), but these points are changed here:

| Earlier draft | Problem | This plan |
|---|---|---|
| ID generation confirms success by re-reading `last_value` | Two writers with the same `cur` both see `cur + 1` → **duplicate `UC-###`** | Check `num_affected_rows` of the guarded `UPDATE` (step 11) |
| "Any authenticated user may write" as the rule | Contradicts Requirements §9 / UI_Design §2 (read-only for Approver/Admin/Viewer) | Same v1 behaviour, but behind a `can_manage_use_cases` stub every write goes through (Phase 0 #3) |
| Shows only a reference **count** | Requirements §9 wants *which* One Pagers reference each Use Case | `get_use_case_references` + detail expander (steps 9, 15) |
| Deprecate: warning then immediate `st.rerun()` | Warning is never visible; UI_Design §5 requires a confirmation dialog | Confirm dialog listing referencing OPs (step 17) |
| `st.success(...)` then `st.rerun()` | Message is never shown | Flash message via session state (step 16) |
| f-string SQL + `_escape_sql_string` | Registry plan mandates parameters; `LIKE` wildcards unescaped | Bound parameters (step 5), wildcard escaping (step 12) |
| Initials from `alice.brown@…` → `AB` | Conflicts with the documented `<initials>ADM@…` format | `extract_initials` per Requirements §2 (Phase 0 #5) |
| Mock stores `reference_count` statically | Drifts from the actual references | Derived from an in-memory references set (step 10) |
| Validation inside the view | Violates "thin views" (Project_Structure §2b) | Pure `validate_use_case_input` in core (step 7) |
