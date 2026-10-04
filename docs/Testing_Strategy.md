# One Pager Application — Testing Strategy

## 1. Purpose

This document defines the test pyramid, test categories, tooling, and CI integration for the One Pager Application. It ensures that the backend logic (Steps 4–5) and UI (Step 6) are testable from day one, and that tests fit into the CI/CD pipeline defined in [One_Pager_App_Project_Structure.md](One_Pager_App_Project_Structure.md) §6.

## 2. Test Pyramid

```
        ┌───────────────┐
        │   E2E / Smoke │  ← few, slow, fragile — only critical paths
        ├───────────────┤
        │  Integration  │  ← moderate count, needs live Databricks
        ├───────────────┤
        │     Unit      │  ← many, fast, no external deps
        └───────────────┘
```

| Layer | Count | Speed | Dependencies | Runs in CI |
|---|---|---|---|---|
| Unit | Many (50+) | Fast (<1s each) | None — pure Python | Every PR (PR validation stage) |
| Integration | Moderate (10–20) | Slower (seconds each) | Live Databricks workspace (DEV/INT) | After deploy to INT |
| E2E / Smoke | Few (3–5) | Slowest | Streamlit AppTest + live workspace | After deploy to INT |

## 3. Unit Tests (`tests/unit/`)

Pure `pytest` — no Streamlit, no Databricks connection, no network. Tests run in <10 seconds total.

### What to test

| Module | Test file | What's covered |
|---|---|---|
| `workflow.py` | `test_workflow.py` | Every legal OP status transition succeeds; every illegal transition raises `InvalidTransitionError`; guard conditions enforced (strict validation required, DP status check for cancel); side effects triggered correctly. **Atomic submit test**: submit performs Draft → Ready for Review → In Review in a single transaction; if the second step fails, the entire operation rolls back (document stays in Draft). **Dual change log entries on approval**: verify that approval creates two change log entries (one for OP status, one for DP status). **Also covers DP status transitions**: `Ready for Development → In Development`, `In Development → Active`, `Active → Deprecated` (with confirmation guard), `In Enhancement → In Development`; system-triggered transitions (`In Definition → Ready for Development` on first approval, `→ In Enhancement` on re-approval, `→ Cancelled` on cancel). |
| `validation.py` | `test_validation.py` | Lenient tier: passes with only productName + description; fails without them. Strict tier: passes with a fully valid document; fails for each `required` field missing, each `minItems` violation, each conditional business rule (retention, CDE tiering). HTML tag stripping verified on free-text fields (change summaries, comments, descriptions). |
| `permissions.py` | `test_permissions.py` | Each role can/cannot perform each operation per the permission matrix; per-record ownership check passes/fails based on initials matching. **Segregation-of-duties test**: Approver who is also Owner/SME on a given OP → approve/reject rejected with `PermissionDeniedError`. **Owner/SME self-removal test**: current Owner saves a change removing themselves from the owner/SME list → verify next operation is rejected. |
| `id_generator.py` | `test_id_generator.py` | Correct formatting (`OP-0001`, `UC-001`, `BR-001`); increment logic; concurrent-call safety (two rapid increments with mock-conflict response → retry succeeds, no collision) |
| `models.py` | `test_models.py` | Pydantic model serialization/deserialization round-trips; validates against schema |
| `locking.py` | `test_locking.py` | Acquire/reuse/warn/reject/override logic based on user, session, expiry state (mock repository). Lock TTL is read from config (configurable for tests). |
| `validation.py` (schema) | `test_schema.py` | JSON Schema edge cases: boundary values, optional vs required fields, enum validation, pattern matching for IDs |
| `auth.py` | `test_auth.py` | Initials extraction from the Databricks username with the configured domains, suffixes and pattern: `x0wadm@becoc001.onmicrosoft.com` → `X0W`, `x0w@becoc001.onmicrosoft.com` → `X0W` (empty suffix allowed), case-insensitive, changed domain via config, unknown domain / suffix only / invalid characters → no initials (never a guess). Display name from a mocked SCIM `Me` response, with fallback to the initials. Role resolution from mocked group-membership results: each group → role, no group → Viewer, failed check → Viewer, `{env}` placeholder resolved per environment |
| `data_access/lakehouse.py` (identity) | `test_lakehouse_identity.py` | Every read method runs as the user and every write method (and every read inside a write, e.g. ID sequence, lock check) as the service principal; any `INSERT` / `UPDATE` / `DELETE` / `MERGE` sent as the user fails the test; a missing user token in `databricks` mode raises instead of falling back to the service principal; every write binds the acting user's initials |
| `config.py` | `test_config.py` | Env var reading for all expected variables; missing required env var raises clear error; defaults applied where defined; invalid values handled gracefully |
| `audit.py` | `test_audit.py` | Security event formatting (correct structure, no PII in output); verify events are logged for: failed permission checks, lock overrides, rejected edit attempts |
| `export.py`, `pdf.py` | `test_export.py` | PDF writer: text encoding and wrapping, page breaks, valid PDF structure; export: every section rendered, Use Case resolution from mocked Delta, audit event, permission and not-found errors; Preview offers the download. Reading approved versions from Git is tested with the Git integration (Phase 8) |
| `repository.py` (Use Cases) | `test_use_cases.py` | Create/edit/deprecate/link/unlink operations; permission checks; deprecated UC cannot be linked; junction table operations |
| `repository.py` (reviews) | `test_review_comments.py` | Rejection stores comment with `resolved=false`; old comments preserved across rejection cycles; Owner resolves comment (`resolved_by`/`resolved_at` set); comments are never deleted |
| `workflow.py` (error handling) | `test_error_handling.py` | Content save: Delta write succeeds → YAML write fails (mocked) → verify change log entry is rolled back and `SaveError` returned with content reference. Approval: Delta succeeds → Git PR fails → verify `pending_pr = true` and no rollback of approval. |

### Mocking strategy
- Repository/Delta access → mocked via dependency injection (the service layer accepts a repository interface; tests pass a fake in-memory implementation).
- Volume/YAML access → mocked (service layer accepts a volume interface; tests pass an in-memory dict).
- Auth/role groups → mocked (tests pass a fake `CurrentUser` and role set; `MockDataAccess` returns group memberships from `ONE_PAGER_APP_MOCK_GROUPS`).
- No `unittest.mock.patch` of internals — prefer constructor injection for clean, fast tests.

## 4. Integration Tests (`tests/integration/`)

Require a live Databricks workspace connection (DEV or INT). Run after `databricks bundle deploy` + Liquibase migration in the INT environment.

### What to test

| Test file | What's covered |
|---|---|
| `test_repository.py` | Delta CRUD round-trips: create a One Pager status row, read it back, update status, append change log, read change log. Verify `GENERATED ALWAYS AS IDENTITY` works for change_log/review_comments PKs. Verify ID sequence atomic increment under simulated concurrency (two rapid increments with threading → no collision). Use Case CRUD: create, edit, deprecate, link/unlink via junction table. SME table: add/remove SME rows, verify permission change propagation. |
| `test_volume.py` | Write a YAML file to the UC volume, read it back, verify content. Delete after test. |
| `test_locking_integration.py` | Acquire lock in Delta, verify heartbeat update, verify expiry detection (uses a short configurable TTL via `config.py`, not the 30-min production default). |
| `test_git_integration.py` | (If feasible in INT) Create a PR against a test branch, verify it exists, clean up. May be skipped if Git repo access is not available in INT — deferred to UAT manual testing if needed. |

### Test data strategy
- Integration tests create their own test data (using unique test-prefixed IDs like `OP-9990`, `UC-990`) and clean up after themselves via pytest fixture teardown.
- **Pre-suite cleanup sweep**: `conftest.py` deletes all rows with test-prefixed IDs (e.g. `OP-99%`, `UC-99%`) before the suite runs, as a guard against orphaned data from prior crashed runs.
- Tests never modify production-like data or shared reference tables.
- A `conftest.py` fixture provides a test-scoped catalog/schema/volume path (separate from real app data if possible, or using a reserved ID range).
- **Lock TTL**: `config.py` exposes the lock expiry duration as a configurable value (default 30 min in production, overridable to seconds in tests).

## 5. E2E / Smoke Tests (`tests/integration/test_app_pages.py`)

Use Streamlit's `AppTest` framework to simulate user interactions against the running app with a live backend.

### Critical paths to smoke-test

| # | Scenario | What it verifies |
|---|---|---|
| 1 | **Create → Save → Submit → Approve** | Owner creates a new One Pager, fills required fields, saves as draft, submits for review. Approver approves. Verify statuses in Delta, version = `1.0.0`, change log entries exist. |
| 2 | **Update → Re-submit → Re-approve** | Owner clicks Update on an Approved OP, edits content, saves, submits. Approver approves. Verify DP status → `In Enhancement`, version = `2.0.0`. |
| 3 | **Reject → Re-edit → Re-submit** | Approver rejects with comment. Verify status back to Draft, review comment stored. Owner edits and re-submits. |

These tests are intentionally few — they cover the end-to-end critical path and catch integration issues between pages, but are not the primary line of defense (unit tests are).

### AppTest limitations and user-switching
- Streamlit AppTest runs pages in-process without a browser — it cannot test JavaScript-dependent behavior, real browser sessions, or multi-user concurrency.
- **Multi-role scenarios** (e.g. scenario 1 requires Owner + Approver): tests mock `auth.py` to return different `AuthenticatedUser` objects between the submit and approve steps. This simulates role-switching within a single test without requiring actual multi-user sessions.
- **Multi-tab `st.tabs` interaction**: AppTest support for `st.tabs` is limited. If the Databricks Apps Streamlit version does not support tab interaction in AppTest, the smoke tests will call the service layer directly for form-fill steps (bypassing the UI) and only test page rendering and action buttons via AppTest.
- For true multi-user scenarios (e.g., locking conflicts between two users), manual testing in UAT is required.

## 6. Security Tests

Embedded in unit tests, not a separate layer:

| What | Where |
|---|---|
| Every restricted operation attempted by each unauthorized role → rejected | `test_permissions.py` |
| Ownership check bypass attempt (wrong initials) → rejected | `test_permissions.py` |
| SQL injection attempt in free-text fields → parameterized queries prevent it | `test_repository.py` (integration) — insert content with SQL metacharacters, verify no injection |
| XSS attempt in free-text fields → sanitized on storage | `test_validation.py` — verify HTML tags are stripped |

## 7. Performance / Load Testing

Not automated in CI for the current phase. The expected scale (hundreds of One Pagers, single-digit concurrent users) is low enough that performance issues are unlikely.

If performance concerns arise:
- Profile the Registry page query with a seeded dataset of 500+ rows.
- Profile the Editor save flow under load (multiple rapid saves).
- Use Databricks SQL query profiling to identify slow Delta queries.

## 8. Manual Testing (UAT)

The UAT environment is for stakeholder validation. Key scenarios that require manual testing (not automatable with AppTest):

- Multi-user locking conflict (two real users in two browsers)
- External Approver (Nykredit) login and approval flow
- PDF export visual quality review
- Help page diagram rendering and accuracy
- Accessibility: screen reader testing, keyboard-only navigation through all pages
- Environment badge correctness (UAT badge shows on UAT, not DEV/PRD)

### Identity and access smoke test (DEV, then each environment)

Run after deploying the identity and access changes (`..dev/User_Identity_And_Access_Plan.md` Phases 1–7), first in DEV. While the interim group is used, **member** means a member of `PAG-BEC-LHX-<ENV>-DataPlatEng-Base` and **non-member** any other employee. Once the dedicated groups exist, repeat with a real Viewer, Owner/SME and Approver test user.

Preparation: two members (A and B) and one non-member (C). A is the Owner of the One Pager created below; B is not Owner or SME of it.

| # | Who | Step | Expected |
|---|---|---|---|
| 1 | A | Open the app | Sidebar: real name and initials, e.g. "Agnieszka Kępkowska (X0W)"; role badges Owner/SME, Approver, Admin; environment badge matches the environment; "Interim roles" notice outside DEV. |
| 2 | C | Open the app | Name shown, role badge **Viewer**; no Review and no Admin page; Registry has no **New**. |
| 3 | C | Browse Registry, Preview, Use Cases; export a PDF | Everything readable; no error (reads run as C, through the `account users` grants). |
| 4 | A | Create a One Pager with A as Owner, edit it, **Save Draft** | Saved; change log shows A's name. |
| 5 | A | Submit for Review | Status In Review. |
| 6 | B | Review page: reject with a comment | Status Draft; review comment by B. |
| 7 | A | Resolve the comment, submit again | In Review. |
| 8 | A | Try to approve | Not possible: A is the Owner (segregation of duties). |
| 9 | B | Approve | Approved, version 1.0.0. |
| 10 | A | Admin page: deactivate and reactivate a business domain | Saved. |
| 11 | — | In the SQL editor: `DESCRIBE HISTORY <catalog>.onepager_app.one_pager_status` | The writes show the app's **service principal**. |
| 12 | — | `SELECT last_updated_by, reviewed_by FROM <catalog>.onepager_app.one_pager_status WHERE one_pager_id = '<id>'`, and `last_updated_by` of the domain changed in step 10 | The **initials** of A and B, not the service principal. |
| 13 | C | In the SQL editor: `UPDATE <catalog>.onepager_app.one_pager_status SET product_name = 'x' WHERE one_pager_id = '<id>'` | Refused: users have no `MODIFY`. |
| 14 | A | Keep a browser tab open for more than one hour, then open another One Pager | Either it loads, or "Your session has expired. Please reload the page."; after a reload everything works. No stack trace. |
| 15 | — | Open the app with an account outside the configured domain (if one is available) | "Access denied" page; a `permission_denied` event with `action=access_app` in the log. |

Record the result per environment (date, tester, any deviation) in the deployment ticket.

## 9. Negative / Failure-Mode Tests

Covered in unit tests via mocked failures:

| Scenario | Expected behavior | Test file |
|---|---|---|
| Delta connection unavailable on save | Graceful error returned; content preserved in session | `test_error_handling.py` |
| Volume write fails after Delta write succeeds | Delta change log entry rolled back; error shown | `test_error_handling.py` |
| Git PR creation fails on approval | `pending_pr` flag set to `true`; approval not rolled back | `test_error_handling.py` |
| Lock table unreachable on editor open | Editor shows error message (not crash) | `test_locking.py` |
| Missing/invalid env vars on startup | Clear error message from `config.py` | `test_config.py` |

## 10. CI Integration

| Pipeline stage | What runs |
|---|---|
| **PR validation** | `ruff check` + `ruff format --check`, `mypy`, `pytest tests/unit/`, `databricks bundle validate`. Dependency vulnerability scanning handled separately via GitHub Advanced Security. |
| **Deploy to DEV** | `databricks bundle deploy -t dev` + Liquibase migrate. No automated tests (developer iteration environment). |
| **Deploy to INT** | `databricks bundle deploy -t int` + Liquibase migrate + `pytest tests/integration/` (all integration + smoke tests). |
| **Deploy to UAT** | `databricks bundle deploy -t uat` + Liquibase migrate. Manual testing checklist (§8) executed by stakeholders. |
| **Deploy to PRD** | `databricks bundle deploy -t prd` + Liquibase migrate. No automated tests — confidence comes from INT + UAT passing. |

### Test failure handling
- Unit test failure → PR cannot be merged.
- Integration test failure → INT deployment is flagged; fix required before promoting to UAT.
- UAT issues → logged as bugs, fixed in a new PR, re-validated through the full pipeline.

## 11. Open Items

| # | Item | Notes |
|---|---|---|
| 1 | Git integration test feasibility in INT | Depends on whether the test workspace has access to a test Git repo for PR creation. May require a dedicated test branch/repo. |
| 2 | Test data isolation strategy | Dedicated test schema/volume vs. reserved ID range in shared schema — to be decided during setup. |
| 3 | AppTest Streamlit version compatibility | `streamlit.testing.v1.AppTest` API may differ across Streamlit versions; verify against the version bundled in Databricks Apps runtime. |
