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
| 1.1 | Generic `TRANSITIONS` state machine (a single source of truth for allowed transitions) | Backend §2 |
| 1.2 | **Submit for Review**: atomic `Draft`/`Draft Update` → `Ready for Review` → `In Review`, with strict validation and rollback | Req §6, Backend §2 |
| 1.3 | **Approve** (`In Review` → `Approved`): version set to `1.0.0` or next MAJOR, automatic DP status change, two change-log entries | Req §6–7, Backend §8 |
| 1.4 | **Reject** (`In Review` → `Draft`) with a mandatory comment, stored in `review_comments` | Req §6, Backend §2, §13 |
| 1.5 | **Update** (`Approved` → `Draft Update`): read the approved YAML and write a working copy, with a confirmation dialog | Req §5, Backend §2, UI §4.4 |
| 1.6 | **Cancel** (`Draft`/`Ready for Review`/`In Review` → `Cancelled`, which also sets DP to `Cancelled`), by the Owner/SME or an Admin | Req §6 |
| 1.7 | Owner-initiated **DP status transitions**: start development, activate, deprecate (with confirmation) | Req §6, Backend §3 |
| 1.8 | System-driven DP transitions (`Ready for Development` on first approval, `In Enhancement` on re-approval, `Cancelled` on cancel) | Req §6, Backend §3 |
| 1.9 | Enforcement of valid OP/DP status combinations | Req §6 |
| 1.10 | Segregation of duties: an Approver cannot approve or reject a One Pager where they are Owner/SME | Req §6, Arch §4 |

## 2. Editing existing One Pagers (Req §5, UI §4.2)

The Editor supports **create mode only**. Missing:

| # | Feature |
|---|---|
| 2.1 | Edit mode for an existing One Pager (open from Preview → Edit, pre-filled form) |
| 2.2 | **Save Draft** with lenient validation, a required change summary, a MINOR version bump, a new YAML version file and a change-log entry (Backend §7) |
| 2.3 | Editor tabs other than Basics: **Use Cases** (link existing / create new inline / unlink), **Business Requirements** (with automatic `BR-###` IDs), **Data Sources**, **Data Product Preview** (data element grid), **Classification** (with retention requirements), **Governance** (business concepts, CDE quality, CDE lineage), **Scope & Questions** (out of scope, open questions, assumptions), and **Review** (validation checklist, resolve comments, submit) |
| 2.4 | Repeating-items pattern: add, edit and remove for array sections |
| 2.5 | Validation error badges on tabs, plus a clickable validation summary |
| 2.6 | Unsaved-changes guard on sidebar navigation |
| 2.7 | Sync of `one_pager_authorized_users` when the Owner or SMEs change on a save (insert, update, delete) (Backend §11) |
| 2.8 | Linking a Use Case to a One Pager: `link_use_case` / `unlink_use_case` writes to `use_case_references` (Backend §9) |

## 3. Validation (Req §5, Backend §4)

| # | Feature |
|---|---|
| 3.1 | **Lenient tier** as its own function for saves. Only the create tier exists. |
| 3.2 | **Strict tier** for submit: every `required` field and `minItems` constraint |
| 3.3 | Conditional business rules: `retentionRequirements` is required when data is PII, sensitive or not Public; `cdeCriticalityTiering` and `useCaseLinks` must be null unless the element is a CDE |

## 4. Permissions & authorization (Req §2, Backend §5, Arch §4)

`permissions.py` is a v1 stub.

| # | Feature |
|---|---|
| 4.1 | Coarse role resolution from **Unity Catalog group membership** (Owner/SME, Approver, Admin, Viewer). Today every authenticated user may create One Pagers and manage Use Cases. |
| 4.2 | Per-record check: `check_can_edit` against `one_pager_authorized_users` |
| 4.3 | Real `get_action_states`. Today every action is hard-coded as disabled. |
| 4.4 | Pages hidden or shown by role (Review for Approvers only, Admin for Admins only, Editor for Owner/SME only) |
| 4.5 | Role badge next to the user in the sidebar |

## 5. Concurrency control / locking (Req §10, Backend §6)

Only reading a lock (`get_lock`) exists. There is no `locking.py`.

| # | Feature |
|---|---|
| 5.1 | Acquire a lock when the editor opens (same user and session, same user in another tab, other user, expired lock) |
| 5.2 | Heartbeat on each re-run, with automatic expiry after 30 minutes |
| 5.3 | Release the lock on submit, cancel or manual release (the **Release my lock** button in Preview) |
| 5.4 | Log an event when an expired lock is overridden |
| 5.5 | Lock icon and holder initials in the **Registry** table |

## 6. Review & comments (Backend §13, UI §4.3–4.4)

| # | Feature |
|---|---|
| 6.1 | **Review page** (`3_Review.py`): an Approver queue of `In Review` items, sorted oldest first |
| 6.2 | Approver adds section-level review comments in Preview |
| 6.3 | Owner marks comments as resolved (`resolved_by`, `resolved_at`) |
| 6.4 | "Review mode" in Preview, with a Reject dialog |

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
| 9.2 | Service method that returns the serialized transitions for the Help page (Backend §14) |

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
| 11.4 | Lock indicator in the table (same item as 5.5) |

## 12. Preview gaps (UI §4.4)

| # | Feature |
|---|---|
| 12.1 | Content sections that are not rendered: **Governance artifacts**, **Out of Scope**, **Open Questions** and **Assumptions**. `OnePagerDocument` does not model these fields either. Use Case IDs (`UC-###`) and BR IDs are not shown. |
| 12.2 | Role- and status-dependent actions: Edit, Update, Change DP Status dropdown, Approve, Reject, Cancel, Add Comment, Resolve, Release lock, Export PDF |
| 12.3 | Error states show raw exception text (`st.error(f"... {e}")`). The docs require a friendly message and a Retry button, with no internals shown. |
| 12.4 | Opening Preview without an ID silently defaults to `OP-0001` |

## 13. App shell, theming & cross-cutting

| # | Feature | Doc ref |
|---|---|---|
| 13.1 | **Environment badge** (DEV/INT/UAT/PRD) in the sidebar | UI §2 |
| 13.2 | BEC theme: `.streamlit/config.toml` is missing, and `apply_theme()` is commented out in `app.py` | UI §3 |
| 13.3 | Home page has placeholder content only (Registry is meant to be the landing page) | UI §4.1 |
| 13.4 | Structured security-event logging module (`audit.py`). Only ad-hoc `audit_logger` calls exist in create. | Arch §8, Backend §14 |
| 13.5 | Caching strategy for the registry and use cases (short TTL, invalidated after writes). Only the reference data is cached. | UI §6 |
| 13.6 | Accessibility items: keyboard row activation, ARIA labels, errors shown next to their fields | UI §7 |
| 13.7 | Schema evolution handling (validation pinned to `structureDefinition` for older documents) | Arch open item #5 |

## 14. Tooling / delivery (Project_Structure)

| # | Feature |
|---|---|
| 14.1 | CI/CD pipeline (Azure Pipelines: ruff, mypy, pytest, bundle validate, staged deploys + Liquibase) — no pipeline file in the repo |
| 14.2 | `.env.example` referenced by the docs is missing |
| 14.3 | E2E/smoke tests for all pages (`tests/integration/test_app_pages.py`), security tests, and failure-mode tests (Testing_Strategy §5–9) |

---

## Explicitly deferred by the docs (future releases)

- **Version History:** list versions and compare them field by field (Req §7, Data_Model §7)
- **Notifications** (Req §12, Arch §9)
- **Auto-save** of drafts (Req §16 #4)
- **Archival or deletion** of One Pagers (Req §16 #1)
- **Data Product rename** and a lightweight **ownership transfer** (Req §16 #2–3)
- **PATCH** versioning (Req §7)
