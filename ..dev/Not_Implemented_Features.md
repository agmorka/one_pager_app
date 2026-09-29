# Not Implemented Features — Gap Analysis

This page compares the documentation in `docs/` with the code on `main` as of 2026-09-29 (after PR #4). It lists the features that the docs describe but the code does not implement yet.

Features the docs themselves mark as future work (Version History / version comparison, notifications, auto-save, archival, PATCH versioning) are listed separately at the end.

## What already exists

- **Registry page:** metrics row, filters (product name, OP status, DP status, owner, domain, type), pagination, a View button on each row, and a ➕ New button.
- **Preview page:** read-only header, status timeline, content sections, change log, review comments and lock indicator. Every action button is disabled ("coming soon").
- **Editor, create mode only:** Basics and the Business Problem Statement, via `workflow.create_one_pager`.
- **Use Cases page:** browse, filter, create, edit, deprecate and restore, with a "referenced by" list.
- **Core:** OP/UC ID generation, create-tier validation and JSON Schema validation, YAML document store, and Delta and mock data access.

---

## 1. Workflow / status state machine (Req §6, Backend §2–3)

`workflow.py` implements only the `create` transition. Missing:

| # | Feature | Doc ref |
|---|---|---|
| 1.1 | ~~Generic `TRANSITIONS` state machine (a single source of truth for allowed transitions)~~ **Done (Phase 5):** `state_machine.TRANSITIONS`, executed by `workflow.apply_transitions` (one conditional status update, one change-log insert, rollback on failure) | Backend §2 |
| 1.2 | ~~**Submit for Review**: atomic `Draft`/`Draft Update` → `Ready for Review` → `In Review`, with strict validation and rollback~~ **Done (Phase 5):** `workflow.submit_for_review`, the Editor's **Submit for Review** (disabled while there are unsaved changes); releases the lock | Req §6, Backend §2 |
| 1.3 | **Approve** (`In Review` → `Approved`): version set to `1.0.0` or next MAJOR, automatic DP status change, two change-log entries | Req §6–7, Backend §8 |
| 1.4 | ~~**Reject** (`In Review` → `Draft`) with a mandatory comment, stored in `review_comments`~~ **Done (Phase 6):** `workflow.reject_one_pager`; the comment is a document-level, unresolved review comment and is appended to the change-log summary; sets `reviewed_at` / `reviewed_by` | Req §6, Backend §2, §13 |
| 1.5 | **Update** (`Approved` → `Draft Update`): read the approved YAML and write a working copy, with a confirmation dialog | Req §5, Backend §2, UI §4.4 |
| 1.6 | ~~**Cancel** (`Draft`/`Ready for Review`/`In Review` → `Cancelled`, which also sets DP to `Cancelled`), by the Owner/SME or an Admin~~ **Done (Phase 5):** `workflow.cancel_one_pager`, **Cancel One Pager** in Preview with a confirmation dialog; releases any active lock | Req §6 |
| 1.7 | ~~Owner-initiated **DP status transitions**: start development, activate, deprecate (with confirmation)~~ **Done (Phase 5):** `workflow.change_data_product_status`, **Change DP Status** menu in Preview (only valid targets; Deprecate asks for confirmation). Only while the One Pager is `Approved`: in `Draft Update` the DP status is preserved (Req §6) | Req §6, Backend §3 |
| 1.8 | System-driven DP transitions (`Ready for Development` on first approval, `In Enhancement` on re-approval, `Cancelled` on cancel). **Cancel part done (Phase 5)**; the rules for the approval part are in `TRANSITIONS` and are applied by Approve in Phase 6 | Req §6, Backend §3 |
| 1.9 | ~~Enforcement of valid OP/DP status combinations~~ **Done (Phase 5):** `state_machine.VALID_COMBINATIONS`, checked on every transition | Req §6 |
| 1.10 | ~~Segregation of duties: an Approver cannot approve or reject a One Pager where they are Owner/SME~~ **Done (Phase 6):** checked by the review decisions from the `TRANSITIONS` guard (`segregation_of_duties`), logged as a permission denial | Req §6, Arch §4 |

## 2. Editing existing One Pagers (Req §5, UI §4.2)

The Editor has create mode and, from Phase 4, edit mode. Missing:

| # | Feature |
|---|---|
| 2.1 | ~~Edit mode for an existing One Pager (open from Preview → Edit, pre-filled form)~~ **Done (Phase 4):** `editing.open_for_edit`, `adapters/edit_mode.py`; acquires the lock on open and on every re-run (heartbeat) |
| 2.2 | ~~**Save Draft** with lenient validation, a required change summary, a MINOR version bump, a new YAML version file and a change-log entry (Backend §7)~~ **Done (Phase 4):** `editing.save_draft`; the caller's session must hold the edit lock; the status row update is conditional on the version the editor started from |
| 2.3 | Editor tabs other than Basics: ~~**Use Cases** (link existing / create new inline / unlink), **Business Requirements** (with automatic `BR-###` IDs), **Data Sources**, **Data Product Preview** (data element grid), **Classification** (with retention requirements), **Governance** (business concepts, CDE quality, CDE lineage), **Scope & Questions** (out of scope, open questions, assumptions)~~ **Done (Phase 4):** `app/adapters/edit_tabs.py`. Still missing: **Review** (validation checklist, resolve comments, submit) — Phase 6 |
| 2.4 | ~~Repeating-items pattern: add, edit and remove for array sections~~ **Done (Phase 4):** `app/adapters/repeating.py` (summary table, inline add/edit form, remove with confirmation) |
| 2.5 | ~~Validation error badges on tabs, plus a clickable validation summary~~ **Done (Phase 4):** badges show the strict-tier issues per tab (`editing.submission_issues`); every summary entry opens its tab. The badges sit in a line under the tab bar, because changing the tab labels would reset the selected tab |
| 2.6 | ~~Unsaved-changes guard on sidebar navigation~~ **Done (Phase 4):** Streamlit cannot block sidebar navigation, so the guard runs on the page the user lands on: unsaved changes are kept and a dialog offers **Return to the Editor** / **Discard changes**; a clean edit session is closed and its lock released. **Close editor** asks first when there are unsaved changes |
| 2.7 | ~~Sync of `one_pager_authorized_users` when the Owner or SMEs change on a save (insert, update, delete) (Backend §11)~~ **Done (Phase 4):** `editing.sync_authorized_users`, run by `save_draft` and undone if the save fails |
| 2.8 | ~~Linking a Use Case to a One Pager: `link_use_case` / `unlink_use_case` writes to `use_case_references` (Backend §9)~~ **Done (Phase 4):** `editing.link_use_case` / `unlink_use_case`; links made in the editor are written on Save Draft; deprecated Use Cases cannot be newly linked |

## 3. Validation (Req §5, Backend §4)

| # | Feature |
|---|---|
| 3.1 | ~~**Lenient tier** as its own function for saves. Only the create tier exists.~~ **Done (Phase 1):** `validate_lenient` |
| 3.2 | ~~**Strict tier** for submit: every `required` field and `minItems` constraint~~ **Done (Phase 1):** `validate_strict`, against the new v2 schema |
| 3.3 | **Done (Phase 1):** ~~Conditional business rules: `retentionRequirements` is required when data is PII, sensitive or not Public; `cdeCriticalityTiering` and `useCaseLinks` must be null unless the element is a CDE~~ |

## 4. Permissions & authorization (Req §2, Backend §5, Arch §4)

`permissions.py` is a v1 stub.

| # | Feature |
|---|---|
| 4.1 | Coarse role resolution from **Unity Catalog group membership** (Owner/SME, Approver, Admin, Viewer). Today every authenticated user may create One Pagers and manage Use Cases. **Interim (Phase 6):** `auth.resolve_roles` gives Approver/Admin from the initials in `ONE_PAGER_APP_APPROVERS` / `ONE_PAGER_APP_ADMINS`; switching to UC groups only changes that function |
| 4.2 | ~~Per-record check: `check_can_edit` against `one_pager_authorized_users`~~ **Done (Phase 4, step 17):** `permissions.check_can_edit` / `edit_denied_reason` (Owner/SME and status `Draft` / `Draft Update`); the Owner/SME UC group check still waits for 4.1 |
| 4.3 | ~~Real `get_action_states`. Today every action is hard-coded as disabled.~~ **Done (Phase 5):** derived from `TRANSITIONS` guards; actions whose service is not built yet stay disabled ("coming soon"). Approver/Admin roles wait for 4.1 |
| 4.4 | Pages hidden or shown by role (Review for Approvers only, Admin for Admins only, Editor for Owner/SME only) |
| 4.5 | Role badge next to the user in the sidebar |

## 5. Concurrency control / locking (Req §10, Backend §6)

`locking.py` implements acquire, heartbeat and expiry (Phase 3). The editor calls it once edit mode exists (Phase 4, 2.1).

| # | Feature |
|---|---|
| 5.1 | ~~Acquire a lock when the editor opens (same user and session, same user in another tab, other user, expired lock)~~ **Done (Phase 3):** `locking.acquire_lock`; the editor calls it from Phase 4 (2.1) |
| 5.2 | ~~Heartbeat on each re-run, with automatic expiry after 30 minutes~~ **Done (Phase 3):** `acquire_lock` / `heartbeat`, TTL from `ONE_PAGER_APP_LOCK_TTL_SECONDS` |
| 5.3 | Release the lock on submit, cancel or manual release (the **Release my lock** button in Preview). **Manual release done (Phase 3):** `locking.release_lock`. **Submit and cancel done (Phase 5)** |
| 5.4 | ~~Log an event when an expired lock is overridden~~ **Done (Phase 3)** |
| 5.5 | ~~Lock icon and holder initials in the **Registry** table~~ **Done (Phase 3)** |

## 6. Review & comments (Backend §13, UI §4.3–4.4)

| # | Feature |
|---|---|
| 6.1 | ~~**Review page** (`3_Review.py`): an Approver queue of `In Review` items, sorted oldest first~~ **Done (Phase 6):** `app/views/review.py`, `review.get_review_queue`; in the navigation for Approvers only; **Review** opens Preview in review mode |
| 6.2 | Approver adds section-level review comments in Preview |
| 6.3 | Owner marks comments as resolved (`resolved_by`, `resolved_at`) |
| 6.4 | ~~"Review mode" in Preview, with a Reject dialog~~ **Done (Phase 6):** opened from the Review queue; banner with **Back to Review queue**; **Reject** asks for a mandatory reason |

## 7. Git integration (Req §13, Arch §6, Backend §8)

There is no `git_integration.py`.

| # | Feature |
|---|---|
| 7.1 | Create a PR to the OnePagerRegistry repo on approval, targeting the environment branch (`ONE_PAGER_GIT_TARGET_BRANCH`) |
| 7.2 | Handling of the `pending_pr` flag, and a retry mechanism for failed PRs |
| 7.3 | Read the approved YAML from Git (for Update and export). Documents are only read from the volume or local store today. |
| 7.4 | Git configuration: repo URL, target branch and secret scope in `config.py` |

## 8. PDF export (Req §8, Backend §10)

| # | Feature |
|---|---|
| 8.1 | `export.py`: render all sections to PDF with resolved Use Cases. The library (`weasyprint` or `fpdf2`) is not chosen yet. |
| 8.2 | Enable the **Export PDF** button in Preview |

## 9. Help page (Req §11, UI §4.6)

| # | Feature |
|---|---|
| 9.1 | Help page (`6_Help.py`): lifecycle explanation, OP/DP state diagrams, valid-combinations table, roles, workflow quick reference, badge legend |
| 9.2 | ~~Service method that returns the serialized transitions for the Help page (Backend §14)~~ **Done (Phase 5):** `workflow.get_workflow_reference` |

## 10. Admin page (UI §4.7)

| # | Feature |
|---|---|
| 10.1 | Admin page (`7_Admin.py`), restricted to Admins |
| 10.2 | CRUD for reference data: business domains, product types, **source systems** (no `ref_source_systems` table/DDL exists yet) |
| 10.3 | View and edit status definitions (`ref_op_status` / `ref_dp_status`) |
| 10.4 | Pending PRs table with **Retry PR** |

## 11. Registry gaps (Req §3, UI §4.1)

| # | Feature |
|---|---|
| 11.1 | **Use case** filter. `RegistryFilter` has no use case field. |
| 11.2 | Clicking a metric card filters the table by that status |
| 11.3 | Sortable columns |
| 11.4 | ~~Lock indicator in the table (same item as 5.5)~~ **Done (Phase 3)** |

## 12. Preview gaps (UI §4.4)

| # | Feature |
|---|---|
| 12.1 | ~~Content sections that are not rendered: **Governance artifacts**, **Out of Scope**, **Open Questions** and **Assumptions**. `OnePagerDocument` does not model these fields either. Use Case IDs (`UC-###`) and BR IDs are not shown.~~ **Done (Phase 1):** modelled in `OnePagerDocument` (v2 schema) and rendered in Preview |
| 12.2 | Role- and status-dependent actions: Edit, Update, Change DP Status dropdown, Approve, Reject, Cancel, Add Comment, Resolve, Release lock, Export PDF. **Partly done (Phase 5):** the Preview action bar only shows the actions that apply to the user's role and the statuses; Edit and Release lock work |
| 12.3 | ~~Error states show raw exception text (`st.error(f"... {e}")`). The docs require a friendly message and a Retry button, with no internals shown.~~ **Done (Phase 0)** |
| 12.4 | ~~Opening Preview without an ID silently defaults to `OP-0001`~~ **Done (Phase 0):** Preview asks the user to pick a One Pager from the Registry |

## 13. App shell, theming & cross-cutting

| # | Feature | Doc ref |
|---|---|---|
| 13.1 | ~~**Environment badge** (DEV/INT/UAT/PRD) in the sidebar~~ **Done (Phase 0):** `ONE_PAGER_APP_ENVIRONMENT`, or derived from the catalog prefix | UI §2 |
| 13.2 | ~~BEC theme: `.streamlit/config.toml` is missing, and `apply_theme()` is commented out in `app.py`~~ **Done (Phase 0):** `app/.streamlit/config.toml`, `apply_theme()` enabled, dark blue sidebar | UI §3 |
| 13.3 | ~~Home page has placeholder content only (Registry is meant to be the landing page)~~ **Done (Phase 0):** Home page removed, Registry is the default page | UI §4.1 |
| 13.4 | ~~Structured security-event logging module (`audit.py`). Only ad-hoc `audit_logger` calls exist in create.~~ **Done (Phase 0):** `onepagerapp.audit`, used by create | Arch §8, Backend §14 |
| 13.5 | Caching strategy for the registry and use cases (short TTL, invalidated after writes). Only the reference data is cached. | UI §6 |
| 13.6 | Accessibility items: keyboard row activation, ARIA labels, errors shown next to their fields | UI §7 |
| 13.7 | ~~Schema evolution handling (validation pinned to `structureDefinition` for older documents)~~ **Done (Phase 1):** `structure_one_pager_v_2.json` added, v1 kept | Arch open item #5 |

## 14. Tooling / delivery (Project_Structure)

| # | Feature |
|---|---|
| 14.1 | ~~CI/CD pipeline (Azure Pipelines: ruff, mypy, pytest, bundle validate, staged deploys + Liquibase) — no pipeline file in the repo~~ **Won't do:** CI/CD and deployment are out of scope for this project |
| 14.2 | ~~`.env.example` referenced by the docs is missing~~ **Done (Phase 0)** |
| 14.3 | E2E/smoke tests for all pages (`tests/integration/test_app_pages.py`), security tests, and failure-mode tests (Testing_Strategy §5–9) |

---

## Recommended implementation order

The phases below order the items above by dependency. Each phase uses only what earlier phases built, so each phase can ship as one or more PRs and be tested on its own. Item numbers refer to the tables above. Unit tests are written in the same phase as the code they cover. Phase 11 adds only the cross-page E2E, security and failure-mode suites. Items marked **Won't do** (14.1, CI/CD and deployment) are left out of the order.

### Phase 0: Foundations and quick wins — ✅ done

These items are small and low risk, and later phases build on them.

| Order | Item | Why now |
|---|---|---|
| 1 | 14.2 `.env.example` | Makes local setup reproducible before more config (Git, roles) is added |
| 2 | 13.4 `audit.py` structured security-event logging | Transitions, locks and permission denials in later phases all log through it |
| 3 | 12.3 Friendly error states with Retry, 12.4 no silent `OP-0001` default | Small Preview fixes, done before Preview gains actions |
| 4 | 13.2 BEC theme, 13.1 environment badge, 13.3 Registry as landing page | Isolated app-shell changes |

### Phase 1: Document model and validation — ✅ done

Every editor tab, every transition and the PDF export depend on the full document model and the three validation tiers.

| Order | Item | Why now |
|---|---|---|
| 5 | 12.1 (model part) Add Governance, Out of Scope, Open Questions and Assumptions to `OnePagerDocument` | The editor tabs, strict validation and export all need these fields |
| 6 | 3.1 Lenient tier | Needed by Save Draft (2.2) |
| 7 | 3.2 Strict tier, 3.3 conditional business rules | Needed by Submit (1.2) and by the editor's validation summary (2.5) |
| 8 | 13.7 Schema evolution (pin validation to `structureDefinition`) | Easiest to design while the validators are being written |
| 9 | 12.1 (render part) Show the new sections, `UC-###` and `BR-###` IDs in Preview | A read-only check that the model is correct, before the editor can write to these fields |

### Phase 2: Permissions

Edit, workflow actions and page visibility all check roles, so roles come first.

| Order | Item | Why now |
|---|---|---|
| 10 | 4.1 Role resolution from Unity Catalog groups | Every later permission check reads this. Needs the group names decided (Arch §4 open item) |
| 11 | 4.2 `check_can_edit` against `one_pager_authorized_users` | Needed by edit mode (2.1) and lock acquisition (5.1) |
| 12 | 1.10 Segregation-of-duties check (as a permission function) | A pure function that Approve and Reject (1.3, 1.4) will call |
| 13 | 4.5 Role badge in the sidebar, 4.4 role-based page visibility | Applied to existing pages now. New pages (Review, Admin, Help) use the same helper when they are added |

### Phase 3: Locking — ✅ done

Locking must exist before users can edit existing records, or two editors can overwrite each other.

| Order | Item | Why now |
|---|---|---|
| 14 | ~~5.1 Acquire lock, 5.2 heartbeat and 30-minute expiry, 5.4 log expired-lock override~~ **Done** | Core of `locking.py` |
| 15 | ~~5.3 Manual release (**Release my lock** in Preview)~~ **Done** | Release on submit and cancel is wired up in Phase 5 |
| 16 | ~~5.5 / 11.4 Lock icon and holder initials in the Registry~~ **Done** | Reads the same lock data |

### Phase 4: Editing existing One Pagers — ✅ done

| Order | Item | Why now |
|---|---|---|
| 17 | ~~2.1 Edit mode (pre-filled Basics)~~ **Done** | Uses permissions (4.2) and locking (5.1) |
| 18 | ~~2.2 Save Draft (lenient validation, change summary, MINOR bump, new YAML version, change-log entry)~~ **Done** | The first write path for existing records. Every tab saves through it |
| 19 | ~~2.7 Sync `one_pager_authorized_users` on save~~ **Done** | Owner and SME changes on the Basics tab must update who may edit |
| 20 | ~~2.4 Repeating-items pattern~~ **Done** | A shared component used by most of the remaining tabs |
| 21 | ~~2.3 Editor tabs in this order: Business Requirements, Use Cases (with 2.8 `link_use_case` / `unlink_use_case`), Data Sources, Data Product Preview, Classification, Governance, Scope & Questions~~ **Done** | Simple arrays first. The Classification and Governance tabs rely on the conditional rules (3.3). The Review tab waits for Phase 6 |
| 22 | ~~2.5 Validation badges on tabs and the clickable summary~~ **Done** | Needs all tabs and the strict tier |
| 23 | ~~2.6 Unsaved-changes guard~~ **Done** | UX polish once the editor is complete |

### Phase 5: Workflow state machine (owner side) — ✅ done

| Order | Item | Why now |
|---|---|---|
| 24 | ~~1.1 `TRANSITIONS` state machine, 1.9 valid OP/DP combinations~~ **Done** | The single source of truth that every transition below uses |
| 25 | ~~9.2 Serialized transitions for the Help page~~ **Done** | Trivial once 1.1 exists |
| 26 | ~~4.3 Real `get_action_states`, 12.2 action buttons in Preview (Edit first)~~ **Done** | Buttons turn on one at a time as each transition lands |
| 27 | ~~1.2 Submit for Review (atomic, strict validation, rollback, releases lock)~~ **Done** | Needs strict validation (3.2) and locking (5.3) |
| 28 | ~~1.6 Cancel, with 1.8 (cancel part) DP → `Cancelled`~~ **Done** | Simple transition that also exercises the system DP transitions |
| 29 | ~~1.7 Owner-initiated DP transitions (start development, activate, deprecate)~~ **Done** | Uses the same state machine. Only reachable after Approve, but can be unit-tested now |

### Phase 6: Review and approval (approver side)

| Order | Item | Why now |
|---|---|---|
| 30 | ~~6.1 Review page (Approver queue)~~ **Done** | Needs `In Review` items from Submit (1.2) and the Approver role (4.1, interim config-based roles) |
| 31 | ~~6.4 Review mode in Preview, 1.4 Reject with a mandatory comment~~ **Done** | Reject writes the first `review_comments` rows |
| 32 | 1.3 Approve (version `1.0.0` / next MAJOR, two change-log entries), 1.8 (approval part) DP → `Ready for Development` / `In Enhancement` | Uses segregation of duties (1.10) |
| 33 | 6.2 Section-level review comments, 6.3 Owner resolves comments | Builds on the comment storage from Reject |
| 34 | 2.3 Editor Review tab (checklist, resolve comments, submit) | Needs Submit, comments and the strict tier |
| 35 | 1.5 Update (`Approved` → `Draft Update`) | Needs approved records. Reads the approved YAML from the volume for now. Phase 8 switches it to Git |

At the end of Phase 6 the full lifecycle works end to end, without Git.

### Phase 7: Registry polish and caching

| Order | Item | Why now |
|---|---|---|
| 36 | 11.1 Use case filter, 11.2 clickable metric cards, 11.3 sortable columns | Independent Registry improvements |
| 37 | 13.5 Registry and use case caching with invalidation after writes | Easiest now that every write path is known |

### Phase 8: Git integration

This phase is left until the lifecycle is stable, because it depends on external setup (repo, secret scope, service principal) and approval must work without it.

| Order | Item | Why now |
|---|---|---|
| 38 | 7.4 Git configuration in `config.py` | Prerequisite for the rest of the phase |
| 39 | 7.1 Create a PR on approval | Hooks into Approve (1.3) |
| 40 | 7.2 `pending_pr` flag and retry | Needed once PR creation can fail |
| 41 | 7.3 Read the approved YAML from Git (for Update and export) | Replaces the volume read in Update (1.5) |

### Phase 9: PDF export

| Order | Item | Why now |
|---|---|---|
| 42 | 8.1 `export.py` (choose `weasyprint` or `fpdf2`), 8.2 enable **Export PDF** | Needs every section modelled (Phase 1). Can move earlier if stakeholders ask for it, because it depends only on Phase 1 |

### Phase 10: Help and Admin pages

| Order | Item | Why now |
|---|---|---|
| 43 | 9.1 Help page | Describes the final lifecycle, using 9.2 |
| 44 | 10.1 Admin page, 10.3 status definitions, 10.2 reference-data CRUD (including the `ref_source_systems` Liquibase changeset) | `sourceSystem` is free text in the schema today, so the Data Sources tab does not need this table first |
| 45 | 10.4 Pending PRs table with **Retry PR** | Needs 7.2 |

### Phase 11: Hardening

| Order | Item | Why now |
|---|---|---|
| 46 | 13.6 Accessibility (keyboard row activation, ARIA labels, inline field errors) | Done once all pages exist, so it is done once |
| 47 | 14.3 E2E and smoke tests for all pages, security tests, failure-mode tests | Covers the finished page set and all transitions |

### Critical path

`Model + validation (P1)` → `Permissions (P2)` → `Locking (P3)` → `Edit + Save Draft (P4)` → `State machine + Submit (P5)` → `Review + Approve (P6)` → `Git (P8)`

Phases 7, 9 and 10 (except 10.4) are not on the critical path. They can run in parallel with Phases 5–6 if more than one person is working on the app.

---

## Explicitly deferred by the docs (future releases)

- **Version History:** list versions and compare them field by field (Req §7, Data_Model §7)
- **Notifications** (Req §12, Arch §9)
- **Auto-save** of drafts (Req §16 #4)
- **Archival or deletion** of One Pagers (Req §16 #1)
- **Data Product rename** and a lightweight **ownership transfer** (Req §16 #2–3)
- **PATCH** versioning (Req §7)
