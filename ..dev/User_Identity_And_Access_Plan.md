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

Answer these before or during Phase 1. Phases 1–3 do not depend on them. Phases 4–7 do.

| # | Question | Needed by | Owner |
|---|---|---|---|
| Q1 | Initials format: allowed characters (letters and digits?), minimum and maximum length, always upper case? | Phase 1 | Business / platform |
| Q2 | During the switch from `x0wadm@` to `x0w@`, do both usernames exist at the same time? Should both map to the same initials? (Assumed yes.) | Phase 1 | Platform |
| Q3 | During a domain change, are old and new domains valid at the same time? | Phase 1 | Platform |
| Q4 | A username that does not match the configured format (unknown domain, service account, guest): **deny access** or **read-only**? (Recommended: deny.) | Phase 2 | Business |
| Q5 | Is Databricks Apps **user authorization** enabled for the app in every environment, with scopes `sql` and `iam.current-user:read`? | Phase 4 | Platform |
| Q6 | Are `givenName` / `familyName` / `displayName` filled in for users in the workspace? Check with `GET /api/2.0/preview/scim/v2/Me`. | Phase 5 | Platform |
| Q7 | Names of the Owner/SME, Approver, Admin (and Viewer, if any) groups per environment. Does `SCIM Me` return nested group memberships, or is `is_account_group_member()` needed? | Phase 6 | Platform — **answered, see §4.1** |
| Q8 | Can the service principal get `SELECT` + `MODIFY` on the `onepager_app` schema and `WRITE VOLUME` on the registry volume, while user groups get only `SELECT` / `READ VOLUME`? | Phase 4 | Platform |

Record every answer in [Decision_Log.md](../docs/Decision_Log.md).

### 4.1 Answers received (2026-09-30)

| # | Question | Answer | Consequence for the plan |
|---|---|---|---|
| A1 | Do the role groups exist? | **No.** Use the built-in `users` group for every role until the real groups are requested and created. | All group names are settings with default `users`. Changing to the real groups later is a configuration change only (Phase 6). |
| A2 | How do Entra ID groups reach Databricks? | **Automatic identity management is enabled.** | Entra ID groups can be used in Databricks directly, without a sync job, and nested Entra groups count. Once created, the real role groups can be referenced by their Entra name. |
| A3 | Who manages group membership? | **The requester's team, for now.** | No in-app role management (no role table, no Admin page for roles). Membership changes happen in Entra ID. |
| A4 | Who is a Viewer? | **All employees.** | No Viewer group and no Viewer check: every signed-in, recognised user is at least a Viewer. |

**Interim consequence of A1: every user has every role.** While all role groups are `users`, every signed-in user is Owner/SME-eligible, Approver and Admin. The per-One-Pager rules still hold (only listed Owners/SMEs edit a One Pager; nobody reviews a One Pager they own or are SME on). This matches how the app behaves today, but:

- It is acceptable in DEV/INT/TST. **Do not go live in UAT/PRD with `users` as the Approver or Admin group.** Phase 7 adds a start-up warning, and a check blocks PRD until real groups are configured.
- Request the three groups early, because the platform lead time is outside this plan. Suggested names: `OPA-OwnerSME-<ENV>`, `OPA-Approver-<ENV>`, `OPA-Admin-<ENV>` (follow the platform naming convention if one exists).

## 5. Phase 1 — Configurable Username Format and Initials

**Goal:** `x0wadm@becoc001.onmicrosoft.com` → `X0W`, and the suffix and domain can be changed through configuration only.

1. **Add settings to `AppConfig`** ([config.py](../src/onepagerapp/config.py)):
   - `ONE_PAGER_APP_USER_DOMAINS` — comma-separated list of accepted domains, default `becoc001.onmicrosoft.com`. A list lets old and new domains work side by side (Q3).
   - `ONE_PAGER_APP_USERNAME_SUFFIXES` — comma-separated list of suffixes to strip from the user part, default `adm`. An empty entry means "no suffix", so `adm,` accepts both `x0wadm` and `x0w` (Q2).
   - `ONE_PAGER_APP_INITIALS_PATTERN` — regular expression for valid initials, default `^[A-Z0-9]{2,5}$` (Q1).
   - Properties `user_domains`, `username_suffixes`, `initials_pattern` that normalise (lower case domains and suffixes, compiled pattern).
2. **Rewrite `initials_from_username`** in [auth.py](../src/onepagerapp/auth.py):
   - Signature `initials_from_username(username, config) -> str | None`.
   - Split on `@`. Return `None` if the domain is not in `user_domains`.
   - Strip the **longest** matching suffix (so an empty suffix never wins over `adm`). Upper-case the rest. Return it only if it matches `initials_pattern`, else `None`.
   - Remove the guessing fallbacks (`alice.brown` → `AB`, first 3 letters, `"??"`). An unrecognised username must never produce initials that could match someone else.
3. **Keep one implementation.** Update `permissions.extract_initials` and every caller (`resolve_current_user`, the Use Cases page) to pass the config. Callers that received `"??"` now receive `None`. Phase 2 decides what happens then.
4. **Mock mode user.** Replace the hardcoded `local-dev-user@mock.local` in [mock.py](../src/onepagerapp/data_access/mock.py) with a setting `ONE_PAGER_APP_MOCK_USER` (default `ldu@mock.local`) and add `mock.local` to the domains in [.env.example](../.env.example), so mock mode goes through the same parsing as production.
5. **Allow digits in stored initials.** Make `INITIALS_PATTERN` in [validation.py](../src/onepagerapp/validation.py) use `config.initials_pattern`, so the Owner/SME rows accept `X0W`. Update the form hints on the Editor Basics tab and the Help page text.
6. **Tests** ([tests/unit/test_auth.py](../tests/unit/test_auth.py), [test_config.py](../tests/unit/test_config.py), [test_validation.py](../tests/unit/test_validation.py)):
   - `x0wadm@becoc001.onmicrosoft.com` → `X0W`; `x0w@becoc001.onmicrosoft.com` → `X0W` (suffix list `adm,`).
   - Upper/lower case in user and domain; unknown domain → `None`; changed domain via config → works.
   - Suffix only (`adm@…`) → `None`; too long / invalid characters → `None`.
   - Validation accepts `X0W` and rejects `x0w`, `X`, `X0W!`.
7. **Docs.** Update [Architecture.md](../docs/Architecture.md) §4 and [Requirements_and_Scope.md](../docs/Requirements_and_Scope.md) §2: the format is configurable, and corporate initials differ from personal initials. Add the three settings to [.env.example](../.env.example) and [README.md](../README.md).

**Done when:** all unit tests pass, and in mock mode a user configured as `x0wadm@becoc001.onmicrosoft.com` can create and edit a One Pager with Owner initials `X0W`.

## 6. Phase 2 — Trusted Identity Only, Fail Closed

**Goal:** the app knows exactly who the user is, from the Databricks Apps proxy only, and refuses access otherwise. This matters more under Option B, because writes will run with the service principal's full rights.

1. **Identity source per mode** in [app.py](../app/app.py) `get_logged_user`:
   - `databricks`: only `x-forwarded-email` / `x-forwarded-preferred-username` (set by the Databricks Apps proxy). **No** `SELECT current_user()` fallback.
   - `local-integration`: `SELECT current_user()` with the CLI profile (unchanged).
   - `local-mock`: `ONE_PAGER_APP_MOCK_USER`.
2. **Fail closed.** If there is no username, or `initials_from_username` returns `None`, show a clear page ("Your account `…` is not recognised by the One Pager App. Contact the platform team.") and stop the script (`st.stop()`). No page is rendered and no data access is created for that session. (Or read-only, per Q4.)
3. **`CurrentUser` stays the single source** of identity for every service call. Add a check in the service layer entry points (workflow, editing, review, locking, use cases, admin) that `user.initials` is set, so a missing identity can never reach a write.
4. **Log** the unrecognised username (without tokens) with a `permission_denied` audit event ([audit.py](../src/onepagerapp/audit.py)).
5. **Tests:** header present / missing per mode; unknown domain → access page; `SELECT current_user()` not called in `databricks` mode.

**Done when:** a request without proxy headers, or with an unknown domain, never reaches a page, and tests cover each mode.

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

## 10. Phase 6 — Roles from Groups

**Goal:** replace the interim initials lists with group membership (Not_Implemented_Features.md 4.1). Decided in §4.1: Entra ID groups through automatic identity management, `users` as the interim group for every role, all employees are Viewers.

**Role model**

| Role | Source | Check |
|---|---|---|
| Viewer | Every signed-in, recognised user (A4) | None: the default when no other role applies |
| Owner/SME: may create a One Pager, manage Use Cases | Group `ONE_PAGER_APP_GROUP_OWNER_SME` | Group membership at login |
| Owner/SME: may edit *this* One Pager | `one_pager_authorized_users` | Initials match (already built) |
| Approver (reviewer) | Group `ONE_PAGER_APP_GROUP_APPROVER` | Group membership at login |
| Admin | Group `ONE_PAGER_APP_GROUP_ADMIN` | Group membership at login |

1. **Settings** in [config.py](../src/onepagerapp/config.py) and [.env.example](../.env.example):
   - `ONE_PAGER_APP_GROUP_OWNER_SME`, `ONE_PAGER_APP_GROUP_APPROVER`, `ONE_PAGER_APP_GROUP_ADMIN`, each with default **`users`** (A1).
   - Validate that a value is a single group name (no commas, not empty).
2. **Resolve the groups once per session**, as the user (`Identity.USER` from Phase 4; until Phase 4 is merged, the current user-token connection), in one statement:

   ```sql
   SELECT
     is_member(:owner_sme) OR is_account_group_member(:owner_sme) AS owner_sme,
     is_member(:approver)  OR is_account_group_member(:approver)  AS approver,
     is_member(:admin)     OR is_account_group_member(:admin)     AS admin
   ```

   - `users` is a **workspace** system group, so `is_account_group_member('users')` is false; `is_member` covers it. The real role groups will be **account** groups (Entra ID through automatic identity management), which `is_account_group_member` covers, including nested Entra groups. Checking both makes the switch from `users` to the real groups a configuration change only.
   - Put this in a new `DataAccess.get_group_memberships(groups) -> dict[str, bool]`. In `MockDataAccess` it returns memberships from `ONE_PAGER_APP_MOCK_GROUPS` (comma-separated), so roles can be tested locally.
   - **Verify in DEV first** (an early spike, can be done right after Phase 2): run the query for `users` and for one Entra group as a test user.
   - If the query fails, the user gets **Viewer only** (fail closed) and the error is logged. The app still opens.
3. **`resolve_roles`** in [auth.py](../src/onepagerapp/auth.py) builds the roles from that result: `Actor.APPROVER`, `Actor.ADMIN`, and a new `Actor.OWNER_SME_GROUP` for the general "may create" right. `Actor.OWNER_SME` stays the per-record role. Store the result in `st.session_state.current_user_roles`, as today; a role change applies from the next session.
4. **Replace the stubs** in [permissions.py](../src/onepagerapp/permissions.py): `can_create_one_pager` and `can_manage_use_cases` require `Actor.OWNER_SME_GROUP`. The per-record checks against `one_pager_authorized_users` and segregation of duties stay unchanged.
5. **Remove** `ONE_PAGER_APP_APPROVERS` / `ONE_PAGER_APP_ADMINS` (replaced by `ONE_PAGER_APP_MOCK_GROUPS` for local testing).
6. **Role badge** next to the user in the sidebar (Not_Implemented_Features.md 4.5): Viewer, Owner/SME, Approver, Admin (several can apply).
7. **Interim warning.** When the Approver or Admin group is `users`, log a warning at start-up and show a small "Roles not configured: all users have all roles" notice in the sidebar, in every environment except DEV.
8. **No in-app role management** (A3): membership is changed in Entra ID by the owning team. Document the request process in the Help page ("How do I become an Approver?").
9. **Tests:** role mapping per group result; no group = Viewer; query error = Viewer; `users` default gives every role; `can_create_one_pager` / `can_manage_use_cases` follow the group; mock groups setting.

**Switching to the real groups later:** create the three Entra groups, add members, then set the three settings per environment and restart the app. No code change.

**Done when:** in DEV, changing `ONE_PAGER_APP_GROUP_APPROVER` from `users` to a group you are not in hides the Review page after a new session.

## 11. Phase 7 — Deployment, Grants and Token Expiry

1. **App configuration** ([app.yml](../app/app.yml), bundle resources):
   - Enable user authorization with scopes `sql` and `iam.current-user:read`.
   - Add the new settings from Phases 1, 5 and 6 per environment (domains, suffixes, pattern, group names; groups = `users` until the real groups exist).
   - Give the Databricks App's `CAN_USE` permission to all employees (A4), e.g. the workspace `users` group.
   - **PRD guard:** when `ONE_PAGER_APP_ENVIRONMENT` is `PRD` and the Approver or Admin group is `users`, the app refuses to start with a clear log message. UAT gets the warning from Phase 6 item 7.
2. **Grant matrix.** Add to [One_Pager_App_Infrastructure_Setup.md](../docs/One_Pager_App_Infrastructure_Setup.md) and hand to the platform team:

   | Principal | Catalog / schema | Tables | Volume | Warehouse |
   |---|---|---|---|---|
   | App service principal | `USE CATALOG`, `USE SCHEMA` | `SELECT`, `MODIFY` on all app tables | `READ VOLUME`, `WRITE VOLUME` | `CAN_USE` |
   | `account users` (all employees = Viewers, A4) | `USE CATALOG`, `USE SCHEMA` | `SELECT` on all app tables | none (YAML is read by the service principal) | `CAN_USE` |
   | Any user group | — | **no** `MODIFY` | **no** `WRITE VOLUME` | — |

   Unity Catalog grants must go to an **account** group: the workspace `users` group cannot receive Unity Catalog privileges, so the read grants use the built-in `account users` group. The role groups need no Unity Catalog grants of their own, because all writes go through the service principal.

3. **Token expiry test.** Streamlit reads `x-forwarded-access-token` from the headers of the first connection. Keep a session open for more than an hour and check that reads still work. If they fail, catch the "token expired" error on reads and ask the user to reload the page (a clear message instead of a stack trace). Writes are not affected, because they use the service principal.
4. **Smoke test in DEV**: browse, create, edit, submit, approve (as a second user who is not Owner/SME), reject, admin edit. Check the audit columns and `DESCRIBE HISTORY`. While all groups are `users`, test the Viewer-only path by pointing the three group settings at a group the test user is not in. Repeat with real Viewer, Owner/SME and Approver test users once the real groups exist.
5. **Promote** to INT, TST, UAT, PRD with the per-environment settings and grants.

## 12. Phase 8 — Documentation and Close-out

1. [Architecture.md](../docs/Architecture.md) §4: identity (configurable format, directory name), Option B (reads as user, writes as service principal), and why.
2. [Backend_Design.md](../docs/Backend_Design.md) §5: role resolution from groups; the table of checks.
3. [Data_Model.md](../docs/Data_Model.md): new audit columns on `ref_*` (if chosen in Phase 3); note that Delta history shows the service principal.
4. [Decision_Log.md](../docs/Decision_Log.md): new entries for Option B, the fail-closed identity rule, the initials format and the directory lookup.
5. [Not_Implemented_Features.md](Not_Implemented_Features.md): mark 4.1 and 4.5 as done.
6. [Requirements_and_Scope.md](../docs/Requirements_and_Scope.md) §2 and [Architecture.md](../docs/Architecture.md) §4: Viewer = all employees, no Viewer group; group names are settings; interim `users` groups.
7. [README.md](../README.md) and [.env.example](../.env.example): all new settings.

## 13. Summary of Order

| Order | Phase | Depends on | Platform needed |
|---|---|---|---|
| 1 | Phase 0 — questions | — | yes (answers) |
| 2 | Phase 1 — configurable initials | Q1–Q3 (defaults can be used first) | no |
| 3 | Phase 2 — trusted identity, fail closed | Phase 1, Q4 | no |
| 4 | Phase 3 — audit trail complete | — | Liquibase change for `ref_*` |
| 5 | Phase 4 — reads as user, writes as service principal | Phases 2, 3; Q5, Q8 | grants |
| 6 | Phase 5 — name from directory | Q5, Q6 | user scopes |
| 7 | Phase 6 — roles from groups | Phase 2 (the DEV spike can run earlier) | none now (`users`); real groups later, configuration only |
| 8 | Phase 7 — deployment, grants, token test | Phases 4–6 | yes |
| 9 | Phase 8 — documentation | all | no |
