# User Identity and Data Access — Implementation Plan

## 1. Purpose

This document is the ordered plan for two related changes:

1. **User identity.** Derive the corporate initials (the authorization key) from the logged-in username in a way that is configurable, show a real first name and surname, and resolve roles from groups.
2. **Data access (Option B).** Run **reads as the user** (on-behalf-of-user token, so Unity Catalog group grants apply) and **writes as the app's service principal** (so users cannot change the app's tables outside the app). The app records who made each change in its own audit columns.

The phases are listed in the order they should be implemented. Each phase can be merged on its own and leaves the app working.

## 2. Platform Rules

| # | Rule | Consequence for the app |
|---|---|---|
| R1 | Users log in as `x0wadm@becoc001.onmicrosoft.com`. The user part is corporate initials + `adm` today and may become just the initials. The domain may change. | The suffix and the domain must be configuration, not code. Corporate initials (`X0W`) may contain digits and are not the same as personal initials (`AK`). |
| R2 | Users have no direct privileges on Databricks resources. Privileges are granted to roles (groups). | Reads as the user need `SELECT` grants to groups. Writes go through the service principal, which holds `MODIFY`. Users get no `MODIFY`. |
| R3 | The user's first name and surname are not known yet. | Look them up in the workspace directory (SCIM `Me`) with the user's token. Fall back to the initials, never to a name guessed from the username. |

## 3. Current State

| Area | Today | Where |
|---|---|---|
| Username → initials | Hardcoded `^([A-Za-z]{2,4})ADM$`. The domain is ignored. Guessing fallbacks (`alice.brown` → `AB`, first 3 letters). `x0wadm` only works by accident through the "first 3 letters" fallback. | [auth.py](../src/onepagerapp/auth.py) `initials_from_username` |
| Display name | Guessed from the username: `x0wadm@…` → "X0wadm". | [auth.py](../src/onepagerapp/auth.py) `display_name_from_username` |
| Initials validation | `^[A-Z]{2,5}$`: **`X0W` is rejected** as Owner/SME initials. | [validation.py](../src/onepagerapp/validation.py) `INITIALS_PATTERN` |
| Identity source | `x-forwarded-preferred-username`, then `x-forwarded-email`, then `SELECT current_user()`. | [app.py](../app/app.py) `get_logged_user` |
| SQL access | Every statement runs with the user's token (`x-forwarded-access-token`). | [connection.py](../src/onepagerapp/data_access/connection.py), [lakehouse.py](../src/onepagerapp/data_access/lakehouse.py) |
| YAML documents | Written through the volume mount as the service principal. Already matches Option B. | [documents/store.py](../src/onepagerapp/documents/store.py) |
| Roles | Approver/Admin from initials listed in `ONE_PAGER_APP_APPROVERS` / `ONE_PAGER_APP_ADMINS`. Create One Pager and Manage Use Cases allow every signed-in user. | [auth.py](../src/onepagerapp/auth.py) `resolve_roles`, [permissions.py](../src/onepagerapp/permissions.py) |
| Audit columns | Delta tables store initials in `created_by`, `last_updated_by`, `reviewed_by`, `author_initials`, `resolved_by`, `locked_by_initials`. Reference tables (`ref_*`) have **no** "changed by" column. | [Data_Model.md](../docs/Data_Model.md) §3 |

## 4. Phase 0 — Decisions and Platform Prerequisites

Answer these before or during Phase 1. Phases 1–3 do not depend on them. Phases 4–7 do. **All eight are now answered: see §4.1 (Q7) and §4.2 (Q1–Q6, Q8).**

| # | Question | Needed by | Owner |
|---|---|---|---|
| Q1 | Initials format: allowed characters (letters and digits?), minimum and maximum length, always upper case? | Phase 1 | Business / platform — **answered, see §4.2** |
| Q2 | During the switch from `x0wadm@` to `x0w@`, do both usernames exist at the same time? Should both map to the same initials? (Assumed yes.) | Phase 1 | Platform — **answered, see §4.2** |
| Q3 | During a domain change, are old and new domains valid at the same time? | Phase 1 | Platform — **answered, see §4.2** |
| Q4 | A username that does not match the configured format (unknown domain, service account, guest): **deny access** or **read-only**? (Recommended: deny.) | Phase 2 | Business — **answered, see §4.2** |
| Q5 | Is Databricks Apps **user authorization** enabled for the app in every environment, with scopes `sql` and `iam.current-user:read`? | Phase 4 | Platform — **answered, see §4.2** |
| Q6 | Are `givenName` / `familyName` / `displayName` filled in for users in the workspace? Check with `GET /api/2.0/preview/scim/v2/Me`. | Phase 5 | Platform — **answered, see §4.2** |
| Q7 | Names of the Owner/SME, Approver, Admin (and Viewer, if any) groups per environment. Does `SCIM Me` return nested group memberships, or is `is_account_group_member()` needed? | Phase 6 | Platform — **answered, see §4.1** |
| Q8 | Can the service principal get `SELECT` + `MODIFY` on the `onepager_app` schema and `WRITE VOLUME` on the registry volume, while user groups get only `SELECT` / `READ VOLUME`? | Phase 4 | Platform — **answered, see §4.2** |

Record every answer in [Decision_Log.md](../docs/Decision_Log.md).

### 4.1 Answers received (2026-09-30)

| # | Question | Answer | Consequence for the plan |
|---|---|---|---|
| A1 | Do the role groups exist? | **No.** Until the real groups are requested and created, use the Data Platform Engineering group of each environment for every role: `BEC_BECOC001_LHX_<ENV>_DataPlatEng`, i.e. `BEC_BECOC001_LHX_DEV_DataPlatEng`, `…_INT_…`, `…_UAT_…`, `…_PRD_…`. | All group names are settings whose default is this group for the current environment. Changing to the real groups later is a configuration change only (Phase 6). |
| A2 | How do Entra ID groups reach Databricks? | **Automatic identity management is enabled.** | Entra ID groups can be used in Databricks directly, without a sync job, and nested Entra groups count. Once created, the real role groups can be referenced by their Entra name. |
| A3 | Who manages group membership? | **The requester's team, for now.** | No in-app role management (no role table, no Admin page for roles). Membership changes happen in Entra ID. |
| A4 | Who is a Viewer? | **All employees.** | No Viewer group and no Viewer check: every signed-in, recognised user is at least a Viewer. |

**Interim consequence of A1: DataPlatEng members have every role, everyone else is a Viewer.** While all role groups are `BEC_BECOC001_LHX_<ENV>_DataPlatEng`, its members are Owner/SME-eligible, Approver and Admin; all other employees are Viewers. The per-One-Pager rules still hold (only listed Owners/SMEs edit a One Pager; nobody reviews a One Pager they own or are SME on). Keep in mind:

- Business Owners/SMEs and Nykredit reviewers outside DataPlatEng **cannot create, edit or review** until the real groups exist (or they are added to DataPlatEng). This is a real limit for UAT/PRD.
- Phase 6 adds a start-up warning and a sidebar notice while the interim group is used.
- Request the three groups early, because the platform lead time is outside this plan. Suggested names: `OPA-OwnerSME-<ENV>`, `OPA-Approver-<ENV>`, `OPA-Admin-<ENV>` (follow the platform naming convention if one exists).

### 4.2 Answers received for Q1–Q6 and Q8 (2026-09-30)

| # | Answer | Consequence for the plan |
|---|---|---|
| Q1 | Initials are letters and digits, **always exactly 3 characters**. Upper or lower case does not matter. | Default `ONE_PAGER_APP_INITIALS_PATTERN` is `^[A-Z0-9]{3}$`. Initials are upper-cased **before** the pattern check, both at login and when Owner/SME initials are entered, so `x0w` is accepted and stored as `X0W`. |
| Q2 | `X0W` are the initials in the BEC domain; `X0Wadm` is the username in Databricks. In the future this may be aligned so users log in to Databricks as `X0W`. No overlap period was mentioned. | Default `ONE_PAGER_APP_USERNAME_SUFFIXES` is `adm` only (strict: `x0w@…` is not accepted today). When the usernames are aligned, set it to `adm,` for the switch and to empty afterwards. Configuration only, no code change. Because the pattern requires exactly 3 characters, an unstripped `x0wadm` can never be taken as initials. |
| Q3 | **No.** Old and new domains are not valid at the same time. | `ONE_PAGER_APP_USER_DOMAINS` holds one production domain. A domain change is a configuration change made at the time of the switch. The setting stays a list only so that mock mode can add `mock.local`. |
| Q4 | **Deny access.** | Phase 2 shows the "account not recognised" page and stops. No read-only mode. |
| Q5 | **Yes**, user authorization is enabled with scopes `sql` and `iam.current-user:read`. | Phases 4 and 5 are unblocked. Phase 7 still checks the scopes in `app.yml` for every environment. |
| Q6 | **Probably yes** (not yet verified). | Verify at the start of Phase 5 with `GET /api/2.0/preview/scim/v2/Me` as a real user in DEV. If names are empty, Phase 5 falls back to the initials (step 8), so nothing breaks. |
| Q7 | Group names: answered in §4.1. Nested groups: not known. | No answer needed from the platform team. Phase 6 uses `is_account_group_member()`, which covers nested Entra groups, and the DEV spike in Phase 6 step 2 confirms it. |
| Q8 | **Yes**, the service principal already has this access. | Phase 4 is unblocked. Phase 7 still checks the second half of the question: that no user group has `MODIFY` on the app tables or `WRITE VOLUME` on the volume. |

## 5. Phase 1 — Configurable Username Format and Initials

**Goal:** `x0wadm@becoc001.onmicrosoft.com` → `X0W`, and the suffix and domain can be changed through configuration only.

1. **Add settings to `AppConfig`** ([config.py](../src/onepagerapp/config.py)):
   - `ONE_PAGER_APP_USER_DOMAINS` — comma-separated list of accepted domains, default `becoc001.onmicrosoft.com`. Old and new domains are never valid at the same time (Q3); a domain change is a configuration change at the switch. It is a list so mock mode can add `mock.local`.
   - `ONE_PAGER_APP_USERNAME_SUFFIXES` — comma-separated list of suffixes to strip from the user part, default `adm`. An empty entry means "no suffix", so `adm,` accepts both `x0wadm` and `x0w`: use it for the future switch to `x0w@` usernames, then leave it empty (Q2).
   - `ONE_PAGER_APP_INITIALS_PATTERN` — regular expression for valid initials, default `^[A-Z0-9]{3}$` (Q1: letters and digits, exactly 3 characters). The pattern is checked after upper-casing.
   - Properties `user_domains`, `username_suffixes`, `initials_pattern` that normalise (lower case domains and suffixes, compiled pattern).
2. **Rewrite `initials_from_username`** in [auth.py](../src/onepagerapp/auth.py):
   - Signature `initials_from_username(username, config) -> str | None`.
   - Split on `@`. Return `None` if the domain is not in `user_domains`.
   - Strip the **longest** matching suffix (so an empty suffix never wins over `adm`). Upper-case the rest. Return it only if it matches `initials_pattern`, else `None`.
   - Remove the guessing fallbacks (`alice.brown` → `AB`, first 3 letters, `"??"`). An unrecognised username must never produce initials that could match someone else.
3. **Keep one implementation.** Update `permissions.extract_initials` and every caller (`resolve_current_user`, the Use Cases page) to pass the config. Callers that received `"??"` now receive `None`. Phase 2 decides what happens then.
4. **Mock mode user.** Replace the hardcoded `local-dev-user@mock.local` in [mock.py](../src/onepagerapp/data_access/mock.py) with a setting `ONE_PAGER_APP_MOCK_USER` (default `ldu@mock.local`) and add `mock.local` to the domains in [.env.example](../.env.example), so mock mode goes through the same parsing as production.
5. **Allow digits in stored initials.** Make `INITIALS_PATTERN` in [validation.py](../src/onepagerapp/validation.py) use `config.initials_pattern`, so the Owner/SME rows accept `X0W`. Upper-case the entered value before the check and store it upper-case, so `x0w` is accepted and saved as `X0W` (Q1: case does not matter). Update the form hints on the Editor Basics tab and the Help page text.
6. **Tests** ([tests/unit/test_auth.py](../tests/unit/test_auth.py), [test_config.py](../tests/unit/test_config.py), [test_validation.py](../tests/unit/test_validation.py)):
   - `x0wadm@becoc001.onmicrosoft.com` → `X0W`; `x0w@becoc001.onmicrosoft.com` → `X0W` (suffix list `adm,`); `x0w@becoc001.onmicrosoft.com` → `None` with the default suffix `adm`.
   - Upper/lower case in user and domain; unknown domain → `None`; changed domain via config → works.
   - Suffix only (`adm@…`) → `None`; too long / invalid characters → `None`.
   - Validation accepts `X0W` and `x0w` (normalised to `X0W`) and rejects `X`, `X0`, `X0WA`, `X0W!`.
7. **Docs.** Update [Architecture.md](../docs/Architecture.md) §4 and [Requirements_and_Scope.md](../docs/Requirements_and_Scope.md) §2: the format is configurable, and corporate initials differ from personal initials. Add the three settings to [.env.example](../.env.example) and [README.md](../README.md).

**Done when:** all unit tests pass, and in mock mode a user configured as `x0wadm@becoc001.onmicrosoft.com` can create and edit a One Pager with Owner initials `X0W`.

**Status (2026-09-30): implemented.** Notes on the implementation:

- The mock user default is `lduadm@becoc001.onmicrosoft.com` (not `ldu@mock.local`), so it is accepted with the default domain and suffix and needs no extra setting.
- The sample One Pagers and the `MockDataAccess` seed use 3-character initials (`ABR`, `BSM`, `CDA`, `DPI` instead of `AB`, `BS`, `CD`, `DP`). Tests build users with `tests/users.make_user` instead of parsing usernames.
- Until Phase 2, an unrecognised username gets a `CurrentUser` with empty initials (and the username-based display name). Empty initials match no Owner, SME, Approver or Admin, and the Use Cases page refuses writes without initials.
- A recognised user's display name is their initials until Phase 5 reads the name from the directory.
- `permissions.extract_initials` was removed; the Use Cases page reads the initials from the session's `CurrentUser`.
- The validation message and form hints say "3 letters or digits". If `ONE_PAGER_APP_INITIALS_PATTERN` is changed, update `INITIALS_RULE` in `validation.py` and the hints too.

## 6. Phase 2 — Trusted Identity Only, Fail Closed

**Goal:** the app knows exactly who the user is, from the Databricks Apps proxy only, and refuses access otherwise. This matters more under Option B, because writes will run with the service principal's full rights.

1. **Identity source per mode** in [app.py](../app/app.py) `get_logged_user`:
   - `databricks`: only `x-forwarded-email` / `x-forwarded-preferred-username` (set by the Databricks Apps proxy). **No** `SELECT current_user()` fallback.
   - `local-integration`: `SELECT current_user()` with the CLI profile (unchanged).
   - `local-mock`: `ONE_PAGER_APP_MOCK_USER`.
2. **Fail closed.** If there is no username, or `initials_from_username` returns `None`, show a clear page ("Your account `…` is not recognised by the One Pager App. Contact the platform team.") and stop the script (`st.stop()`). No page is rendered and no data access is created for that session. There is no read-only mode (Q4: deny access).
3. **`CurrentUser` stays the single source** of identity for every service call. Add a check in the service layer entry points (workflow, editing, review, locking, use cases, admin) that `user.initials` is set, so a missing identity can never reach a write.
4. **Log** the unrecognised username (without tokens) with a `permission_denied` audit event ([audit.py](../src/onepagerapp/audit.py)).
5. **Tests:** header present / missing per mode; unknown domain → access page; `SELECT current_user()` not called in `databricks` mode.

**Done when:** a request without proxy headers, or with an unknown domain, never reaches a page, and tests cover each mode.

**Status (2026-10-01): implemented.** Notes on the implementation:

- `app.py` loads the configuration, resolves the user and only then creates the data access. In `local-integration` the data access is created first, because the identity comes from `SELECT current_user()`.
- A failed identity lookup (e.g. the warehouse is unreachable in `local-integration`) is treated like a missing username: access denied.
- The refusal is logged once per session as `action=access_app outcome=permission_denied user=- username=<username>`.
- `permissions.require_identity` runs first in the workflow, editing, review, locking and admin entry points (admin and the review queue through `check_can_administer` / `check_can_review`), and in the Use Cases page's writes.

## 7. Phase 3 — Complete the Audit Trail in the App's Tables

**Goal:** before writes move to the service principal (Phase 4), every write stores **who** made it, because Delta history will show only the service principal afterwards.

1. **Review every write method** in [lakehouse.py](../src/onepagerapp/data_access/lakehouse.py) and [base.py](../src/onepagerapp/data_access/base.py) and list which column records the acting user:

   | Table | Actor column today | Action |
   |---|---|---|
   | `one_pager_status` | `created_by`, `last_updated_by`, `reviewed_by` | Check that every update sets `last_updated_by` (including status-only changes, PR retry). |
   | `change_log` | `author_initials` | OK. |
   | `review_comments` | `reviewer_initials`, `resolved_by` | OK. |
   | `locks` | `locked_by_initials` | OK. |
   | `use_cases` | `created_by`, `last_updated_by` | OK. |
   | `one_pager_authorized_users` | none | Covered by the `change_log` entry of the save; confirm and document. |
   | `use_case_references` | none | Covered by the `change_log` entry of the save; confirm and document. |
   | `ref_*` (Admin page) | **none** | Add `last_updated_by` and `last_updated_at` (Liquibase change in the DDL repository), or write an audit event per change. Decide and record. |
   | `id_sequences` | none | System table; no actor needed. |

2. **Make the actor mandatory** in the `DataAccess` write signatures that lack it (e.g. `insert_reference_value(..., user_initials)`), so a write without an actor does not type-check.
3. **Audit events.** Make sure [audit.py](../src/onepagerapp/audit.py) logs one event per write with the user's initials and the One Pager ID. This is the second record of who did what.
4. **Tests:** extend [test_lakehouse_writes.py](../tests/unit/test_lakehouse_writes.py) to assert the actor parameter is bound on every write statement.

**Done when:** every write statement carries the acting user's initials, or is documented as covered by a `change_log` entry.

**Status (2026-10-01): implemented.** Notes on the implementation:

- Review result: every update of `one_pager_status` sets `last_updated_by` (create, save, every transition via `apply_transitions`); compensation restores the previous row with its previous actor. PR retry is not implemented yet (the button is disabled), so it writes nothing. The table of actors per table is in Data_Model §5.
- `ref_*` decision: actor columns `last_updated_by` / `last_updated_at` (Liquibase `ddl/ref_audit_columns.sql`, Decision_Log §22). The changeset must run before this code is deployed, because the writes now set these columns.
- `delete_reference_value`, the compensating deletes and `id_sequences` have no actor; the reasons are listed in `test_lakehouse_writes.WRITES_WITHOUT_ACTOR`.
- Use Case writes now go through `use_cases.create_use_case` / `update_use_case` / `set_use_case_deprecated`, which check the user and log an event; before, the pages wrote directly with no event.

## 8. Phase 4 — Split Data Access: Reads as User, Writes as Service Principal

**Goal:** Option B. Needs Q5 and Q8.

1. **Two identities in the connection** ([connection.py](../src/onepagerapp/data_access/connection.py)):
   - Add an `Identity` enum: `USER` and `APP`.
   - `USER`: token from `x-forwarded-access-token` (as today). In `databricks` mode, if the header is missing, raise a clear error; **never** fall back to the service principal for a read.
   - `APP`: the service principal credentials the Databricks Apps runtime provides (`WorkspaceClient()` with its default auth, which reads `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`). Reuse one client; do not create one per statement.
   - `local-integration`: both identities use the CLI profile (same person). Document that local runs cannot show permission differences.
   - `execute_statement(statement, parameters, *, identity)` — no default, so every call site chooses on purpose.
2. **Route every `LakehouseAccess` method** ([lakehouse.py](../src/onepagerapp/data_access/lakehouse.py)):
   - **Reads → `USER`:** `read_table`, `get_ref_*`, `get_registry*`, `get_one_pager*`, `get_change_log`, `get_review_comments`, `get_lock(s)`, `get_authorized_users`, `get_use_case*`, `get_linked_use_case_ids`, `get_pending_pr_rows`, `get_current_user` (local-integration only).
   - **Writes → `APP`:** every `INSERT` / `UPDATE` / `DELETE` / `MERGE` (status, change log, authorized users, review comments, locks, use cases, use case references, reference data, status definitions).
   - **Reads that are part of a write → `APP`:** `get_sequence_value` + `compare_and_set_sequence` (ID generation), the existing-lock check inside `write_lock`, and the conditional-update checks. They must see exactly what the writer sees and must work even if a user's `SELECT` grant is missing.
   - Add a small helper pair (`_read(...)`, `_write(...)`) so the choice is visible in each method.
3. **Permission errors.** Map Unity Catalog "permission denied" / "insufficient privileges" responses from **reads** to a user-facing message: "Your role does not have access to … Contact the platform team." A permission error on a **write** is a deployment error (the service principal is missing a grant): log it with details and show a generic error.
4. **Volume.** No change: YAML files are already written as the service principal through the mount. Document this in [Architecture.md](../docs/Architecture.md). Reading YAML also runs as the service principal; accepted, because all signed-in users may view every One Pager (Backend_Design.md §5).
5. **Tests** ([test_connection.py](../tests/unit/test_connection.py), [test_lakehouse_writes.py](../tests/unit/test_lakehouse_writes.py), a new `test_lakehouse_identity.py`):
   - Each `LakehouseAccess` method uses the expected identity (mock the connection and assert `identity=`).
   - A test that fails if a statement starting with `INSERT` / `UPDATE` / `DELETE` / `MERGE` is sent as `USER`.
   - Missing user token in `databricks` mode → error, not fallback.
6. **Update the docstrings** of `LakehouseAccess` and `DatabricksConnection` that say "uses the end-user's token".

**Done when:** in DEV, a user who belongs only to the Viewer group can browse everything; an Owner can save; and `DESCRIBE HISTORY` on `one_pager_status` shows the service principal while `last_updated_by` shows the user's initials.

**Status (2026-10-01): implemented in code; the DEV check above is still to be done.** Notes on the implementation:

- Step 1 first ran every statement as `USER` (behaviour unchanged except the missing-token fallback); step 2 then routed the writes to `APP` through `_read` / `_write`.
- The existing-lock check and the conditional-update checks are part of the guarded write statements themselves (`MERGE ... WHEN MATCHED AND`, `UPDATE ... WHERE version = :expected_version`), so they run as `APP` with the write. The only separate read that is part of a write is `get_sequence_value`.
- Permission errors are recognised from the Unity Catalog message (`INSUFFICIENT_PERMISSIONS`, `PERMISSION_DENIED`, "does not have … on …") or the SDK's `PermissionDenied` (not retried). Reads raise `ReadAccessDeniedError` (message for the user, naming the object when reported); writes raise `WriteAccessDeniedError` (generic message, details logged). The pages show the read message in their load-error banners.
- Service-layer reads before a write (e.g. reading the status row before a save, or the lock before acquiring it) run as the user, like any read; a user without `SELECT` therefore cannot start the write, which is the intended order of checks.

## 9. Phase 5 — First Name and Surname from the Directory

**Goal:** show "Agnieszka Kępkowska" instead of "X0wadm". Needs Q5 and Q6.

1. **New module** `src/onepagerapp/directory.py`: `get_me(token) -> DirectoryUser | None` calls `GET /api/2.0/preview/scim/v2/Me` with the **user's** token (scope `iam.current-user:read`) and returns `display_name`, `given_name`, `family_name`, `emails` and `groups` (the groups are used in Phase 6).
2. **Display name rule** in [auth.py](../src/onepagerapp/auth.py) `resolve_current_user`: `givenName familyName` → `displayName` → the initials. Remove `display_name_from_username`.
3. **Call once per session** in [app.py](../app/app.py) `resolve_user` and keep the result in `st.session_state.current_user_info`. A directory failure must not block the app: log it and fall back to the initials.
4. **Where the name is used** (no change needed, they already read `user.display_name`): YAML `createdBy`, change log `author_name`, review comments `reviewer_name`, locks `locked_by_name`, PDF "exported by", sidebar.
5. **Pre-fill the Owner row** on the Editor Basics tab with the current user's name, initials and email when creating a One Pager (optional, but useful now that the name is known).
6. **Mock mode:** return a configurable name (`ONE_PAGER_APP_MOCK_USER_NAME`) so the UI shows a real-looking name locally.
7. **Tests:** SCIM response parsing (full name, only `displayName`, empty), fallback on HTTP error, no call in mock mode.
8. **If Q6 says names are not filled in:** use a `ref_users` table (initials → name, email) managed on the Admin page instead, as a follow-up. Microsoft Graph is possible but needs an Entra app registration and consent.

**Done when:** in DEV the sidebar and a new change log entry show your real name.

**Status (2026-10-01): implemented in code; the DEV check above is still to be done.** Notes on the implementation:

- `directory.get_me` uses the SDK's `current_user.me()`, which calls `GET /api/2.0/preview/scim/v2/Me`. `lookup_directory_user` picks the token by mode: the forwarded user token when deployed (no lookup without it; never the service principal, which would return the app's own entry), the CLI profile in `local-integration`, and `ONE_PAGER_APP_MOCK_USER_NAME` (no call) in `local-mock`.
- The directory entry is kept in `st.session_state.current_user_directory`; its `groups` are for Phase 6.
- The sidebar showed the raw username; it now shows "Name (INITIALS)".
- `CurrentUser` has a new optional `email` (primary directory email), used to pre-fill the Owner email on create.
- Step 8 (`ref_users` table) is not needed unless the DEV check shows that the names are empty (Q6 was "probably yes"). If they are, the app keeps working and shows the initials.

## 10. Phase 6 — Roles from Groups

**Goal:** replace the interim initials lists with group membership (Not_Implemented_Features.md 4.1). Decided in §4.1: Entra ID groups through automatic identity management, `BEC_BECOC001_LHX_<ENV>_DataPlatEng` as the interim group for every role, all employees are Viewers.

**Role model**

| Role | Source | Check |
|---|---|---|
| Viewer | Every signed-in, recognised user (A4) | None: the default when no other role applies |
| Owner/SME: may create a One Pager, manage Use Cases | Group `ONE_PAGER_APP_GROUP_OWNER_SME` | Group membership at login |
| Owner/SME: may edit *this* One Pager | `one_pager_authorized_users` | Initials match (already built) |
| Approver (reviewer) | Group `ONE_PAGER_APP_GROUP_APPROVER` | Group membership at login |
| Admin | Group `ONE_PAGER_APP_GROUP_ADMIN` | Group membership at login |

1. **Settings** in [config.py](../src/onepagerapp/config.py) and [.env.example](../.env.example):
   - `ONE_PAGER_APP_GROUP_OWNER_SME`, `ONE_PAGER_APP_GROUP_APPROVER`, `ONE_PAGER_APP_GROUP_ADMIN`, each with default **`BEC_BECOC001_LHX_{env}_DataPlatEng`** (A1).
   - `{env}` is replaced with the environment from `AppConfig.environment` (`DEV`, `INT`, `UAT`, `PRD`), so one default works in every environment and nothing has to be set per environment for now. A value without `{env}` is used as is.
   - A test pins the resolved names: DEV → `BEC_BECOC001_LHX_DEV_DataPlatEng`, INT → `…_INT_…`, UAT → `…_UAT_…`, PRD → `…_PRD_…`.
   - TST appears in [One_Pager_App_Infrastructure_Setup.md](../docs/One_Pager_App_Infrastructure_Setup.md) but not in the `Environment` enum; if the app runs in TST, add it to the enum (and check that `BEC_BECOC001_LHX_TST_DataPlatEng` exists).
   - Validate that a value is a single group name (no commas, not empty).
2. **Resolve the groups once per session**, as the user (`Identity.USER` from Phase 4; until Phase 4 is merged, the current user-token connection), in one statement:

   ```sql
   SELECT
     is_member(:owner_sme) OR is_account_group_member(:owner_sme) AS owner_sme,
     is_member(:approver)  OR is_account_group_member(:approver)  AS approver,
     is_member(:admin)     OR is_account_group_member(:admin)     AS admin
   ```

   - The interim group and the future role groups are Entra ID groups, available as **account** groups through automatic identity management; `is_account_group_member` covers them, including nested Entra groups. `is_member` additionally covers **workspace-local** groups, so a workspace group can also be configured if ever needed.
   - Put this in a new `DataAccess.get_group_memberships(groups) -> dict[str, bool]`. In `MockDataAccess` it returns memberships from `ONE_PAGER_APP_MOCK_GROUPS` (comma-separated), so roles can be tested locally.
   - **Verify in DEV first** (an early spike, can be done right after Phase 2): run the query for `BEC_BECOC001_LHX_DEV_DataPlatEng` as a member and as a non-member.
   - If the query fails, the user gets **Viewer only** (fail closed) and the error is logged. The app still opens.
3. **`resolve_roles`** in [auth.py](../src/onepagerapp/auth.py) builds the roles from that result: `Actor.APPROVER`, `Actor.ADMIN`, and a new `Actor.OWNER_SME_GROUP` for the general "may create" right. `Actor.OWNER_SME` stays the per-record role. Store the result in `st.session_state.current_user_roles`, as today; a role change applies from the next session.
4. **Replace the stubs** in [permissions.py](../src/onepagerapp/permissions.py): `can_create_one_pager` and `can_manage_use_cases` require `Actor.OWNER_SME_GROUP`. The per-record checks against `one_pager_authorized_users` and segregation of duties stay unchanged.
5. **Remove** `ONE_PAGER_APP_APPROVERS` / `ONE_PAGER_APP_ADMINS` (replaced by `ONE_PAGER_APP_MOCK_GROUPS` for local testing).
6. **Role badge** next to the user in the sidebar (Not_Implemented_Features.md 4.5): Viewer, Owner/SME, Approver, Admin (several can apply).
7. **Interim warning.** While any role group is still the DataPlatEng default, log a warning at start-up and, in every environment except DEV, show a small "Interim roles: DataPlatEng members act as Owner/SME, Approver and Admin" notice in the sidebar.
8. **No in-app role management** (A3): membership is changed in Entra ID by the owning team. Document the request process in the Help page ("How do I become an Approver?").
9. **Tests:** role mapping per group result; no group = Viewer; query error = Viewer; `{env}` resolution per environment; DataPlatEng member gets every role, non-member is Viewer; `can_create_one_pager` / `can_manage_use_cases` follow the group; mock groups setting.

**Switching to the real groups later:** create the three Entra groups, add members, then set the three settings per environment and restart the app. No code change.

**Done when:** in DEV, a DataPlatEng member sees Review and Admin, a non-member sees only the Viewer pages, and changing `ONE_PAGER_APP_GROUP_APPROVER` to another group changes this after a new session.

**Status (2026-10-01): implemented in code; the DEV check above is still to be done** (it is also the early spike of step 2: run the membership query as a DataPlatEng member and as a non-member). Notes on the implementation:

- `.env.example` no longer exists; the settings are documented in `config.py` and the README.
- TST was added to the `Environment` enum, because the app is deployed there.
- `ONE_PAGER_APP_MOCK_GROUPS` defaults to the interim group, so the local-mock user has every role (as `ONE_PAGER_APP_APPROVERS=LDU` used to give the Approver role); an empty value makes it a Viewer.
- `app.py` resolves the identity first (fail closed, Phase 2), then creates the data access, then resolves the roles once per session (`resolve_session_roles`), because the membership check needs the data access.
- `create_one_pager` and the Use Case write services take a required `roles` argument, so the service layer enforces the Owner/SME group, not only the pages.
- The sidebar shows "Viewer" only when the user has no other role.

## 11. Phase 7 — Deployment, Grants and Token Expiry

1. **App configuration** ([app.yml](../app/app.yml), bundle resources):
   - Enable user authorization with scopes `sql` and `iam.current-user:read`.
   - Add the new settings from Phases 1 and 5 per environment (domains, suffixes, pattern). The group settings can stay unset while the DataPlatEng default is used; set them per environment once the real groups exist.
   - Give the Databricks App's `CAN_USE` permission to all employees (A4), e.g. the workspace `users` group.
   - Check that `BEC_BECOC001_LHX_<ENV>_DataPlatEng` exists in each environment's account before deploying there. A missing group means every user is a Viewer.
2. **Grant matrix.** Add to [One_Pager_App_Infrastructure_Setup.md](../docs/One_Pager_App_Infrastructure_Setup.md) and hand to the platform team:

   | Principal | Catalog / schema | Tables | Volume | Warehouse |
   |---|---|---|---|---|
   | App service principal | `USE CATALOG`, `USE SCHEMA` | `SELECT`, `MODIFY` on all app tables | `READ VOLUME`, `WRITE VOLUME` | `CAN_USE` |
   | `account users` (all employees = Viewers, A4) | `USE CATALOG`, `USE SCHEMA` | `SELECT` on all app tables | none (YAML is read by the service principal) | `CAN_USE` |
   | Any user group | — | **no** `MODIFY` | **no** `WRITE VOLUME` | — |

   Unity Catalog grants must go to an **account** group: the workspace `users` group cannot receive Unity Catalog privileges, so the read grants use the built-in `account users` group. The role groups need no Unity Catalog grants of their own, because all writes go through the service principal.

3. **Token expiry test.** Streamlit reads `x-forwarded-access-token` from the headers of the first connection. Keep a session open for more than an hour and check that reads still work. If they fail, catch the "token expired" error on reads and ask the user to reload the page (a clear message instead of a stack trace). Writes are not affected, because they use the service principal.
4. **Smoke test in DEV**: browse, create, edit, submit, approve (as a second user who is not Owner/SME), reject, admin edit. Check the audit columns and `DESCRIBE HISTORY`. While the DataPlatEng group is used, run the Owner/SME, Approver and Admin steps as DataPlatEng members and the Viewer steps as a non-member. Repeat with real Viewer, Owner/SME and Approver test users once the real groups exist.
5. **Promote** to INT, TST, UAT, PRD with the per-environment settings and grants.

## 12. Phase 8 — Documentation and Close-out

1. [Architecture.md](../docs/Architecture.md) §4: identity (configurable format, directory name), Option B (reads as user, writes as service principal), and why.
2. [Backend_Design.md](../docs/Backend_Design.md) §5: role resolution from groups; the table of checks.
3. [Data_Model.md](../docs/Data_Model.md): new audit columns on `ref_*` (if chosen in Phase 3); note that Delta history shows the service principal.
4. [Decision_Log.md](../docs/Decision_Log.md): new entries for Option B, the fail-closed identity rule, the initials format and the directory lookup.
5. [Not_Implemented_Features.md](Not_Implemented_Features.md): mark 4.1 and 4.5 as done.
6. [Requirements_and_Scope.md](../docs/Requirements_and_Scope.md) §2 and [Architecture.md](../docs/Architecture.md) §4: Viewer = all employees, no Viewer group; group names are settings; interim `BEC_BECOC001_LHX_<ENV>_DataPlatEng` group.
7. [README.md](../README.md) and [.env.example](../.env.example): all new settings.

## 13. Summary of Order

| Order | Phase | Depends on | Platform needed |
|---|---|---|---|
| 1 | Phase 0 — questions | — | yes (answers) |
| 2 | Phase 1 — configurable initials | Q1–Q3 (answered, §4.2) | no |
| 3 | Phase 2 — trusted identity, fail closed | Phase 1, Q4 (answered: deny) | no |
| 4 | Phase 3 — audit trail complete | — | Liquibase change for `ref_*` |
| 5 | Phase 4 — reads as user, writes as service principal | Phases 2, 3; Q5, Q8 (answered: yes) | grants |
| 6 | Phase 5 — name from directory | Q5, Q6 (Q6 to verify in DEV) | user scopes |
| 7 | Phase 6 — roles from groups | Phase 2 (the DEV spike can run earlier) | none now (DataPlatEng default); real groups later, configuration only |
| 8 | Phase 7 — deployment, grants, token test | Phases 4–6 | yes |
| 9 | Phase 8 — documentation | all | no |
