# One Pager Application — Requirements & Scope

## 1. Purpose

The One Pager Application is the interface for managing Data Product One Pagers throughout their lifecycle: creation, review, approval, and ongoing maintenance. A Data Product's One Pager must be **Approved** before development of that Data Product may begin; if the Data Product later changes, its One Pager is updated and goes through review again.

Each Data Product has exactly **one** One Pager (1:1 relationship). The One Pager's ID (`OP-####`) is stable and never changes, even across multiple Draft Update cycles.

The application's data model follows the JSON Schema in `structure_one_pager_v_1.json`, with `example_one_pager.yml` as a reference instance. Key schema fields include `structureDefinition` (which schema version the document follows) and `dataProduct` (the registered unique name of the Data Product, used as the organizing key in storage).

## 2. Roles

The application uses four roles:

| Role | Who this is | Responsibilities |
|---|---|---|
| **Owner** | The Data Product Owner, or a Subject Matter Expert (SME) assigned to that specific Data Product | Creates and edits One Pagers for the Data Product(s) they own or are assigned to, manages the shared Use Case list, submits for review, updates Approved One Pagers, progresses and changes Data Product status within allowed values, cancels own One Pagers |
| **Approver** | A reviewer representing Nykredit (the partner financial institution) | Approves or rejects submitted One Pagers, adds review comments (including section-level comments) |
| **Admin** | A Platform team representative | Administers reference data, may cancel a One Pager. Role membership itself is managed in Entra ID groups, outside the app |
| **Viewer** | Any employee | Browses and views One Pagers; read-only |

All users must be authenticated before accessing the application. A user whose account is not recognised (unknown domain or username format) is refused access.

Roles are assigned through Entra ID groups: one group each for Owner/SME, Approver and Admin. There is no Viewer group: every employee who can sign in is a Viewer. The group names are configuration, not code, so moving to other groups needs no change to the app. The groups do not exist yet; until they do, members of the environment's Data Platform Engineering group (`BEC_BECOC001_LHX_<ENV>_DataPlatEng`) hold the Owner/SME, Approver and Admin roles. Group membership is managed in Entra ID, not in the app, and a change applies from the user's next session. The Owner/SME group is needed to create One Pagers and manage Use Cases; editing an existing One Pager depends on being listed as its Owner or SME (below). A user may hold several roles; a user never approves or rejects a One Pager they are Owner or SME of (§6). See Architecture.md §4.

Edit rights are scoped **per Data Product**: only that Data Product's Owner and its explicitly assigned SMEs may create or edit its One Pager. Being an Owner or SME on one Data Product does not grant edit rights on another. The authenticated user is matched against `dataProductOwner`/`smes` by their stable corporate `initials` (not by name or email). The `email` field is stored for display and cross-reference but is not used for authorization, since the corporate email/username format may change over time.

**Corporate initials.** The username currently has the form `<corporate initials>adm@becoc001.onmicrosoft.com`, e.g. `x0wadm@becoc001.onmicrosoft.com` → `X0W`. The `adm` suffix may be dropped and the domain may change; both are configuration, not code. Corporate initials are assigned by the company, are always 3 letters or digits (upper or lower case does not matter; the app stores them upper case), and are not necessarily a person's name initials (the user above is Agnieszka Kępkowska, `AK`, but her corporate initials are `X0W`). The `initials` of Owners and SMEs in a One Pager are always **corporate** initials.

**Names.** A user's first name and surname are taken from the company directory when available; otherwise the app shows the corporate initials.

## 3. Browsing & Searching

- All authenticated users may view any One Pager regardless of role or ownership. The `dataClassification` field describes the classification of the Data Product's own data (e.g. whether CPR numbers in the product are Confidential), not the One Pager document itself — the document contains only business descriptions and metadata, not actual customer data.
- Users can browse the full list of One Pagers.
- Users can filter by product name (text search), One Pager status, Data Product status, owner, business domain, product type, and use case.
- Users can view full detail for a selected One Pager.
- A registry summary view shows status counts (metrics).

## 4. Creating One Pagers

- The Owner creates a new One Pager, initialized with One Pager status `Draft` and Data Product status `In Definition`.
- The creation date is recorded automatically.
- The **creator** (the authenticated user who created the record) is captured automatically and stored separately from the Data Product Owner — the two are often, but not always, the same person.
- The One Pager is assigned a unique ID (`OP-####`) automatically at creation; see §14 (Identifiers) for details.
- The document structure follows `structure_one_pager_v_1.json`, which records the Data Product Owner and any assigned SMEs for that Data Product.
- When adding use cases, the Owner can reference an existing use case from the shared registry or create a new one.

## 5. Editing One Pagers

- Only the Data Product Owner and the SMEs assigned to that specific Data Product may edit its One Pager; Owners/SMEs of other Data Products cannot.
- The Owner can edit a One Pager while its status is `Draft` or `Draft Update` (not while `In Review` or `Approved`). `Ready for Review` is a transient intermediate state that the user passes through during submission (see §6) and never rests in — it is not an editable state.
- The document is always validated against the JSON Schema.
- Editing an Approved One Pager requires an explicit "Update" action (with confirmation), which moves the One Pager to `Draft Update` while preserving the current Data Product status.

### Mandatory fields and validation approach

Rather than maintaining a separate, hand-written list of mandatory fields, the application derives field requirements directly from `structure_one_pager_v_1.json`, applied in two tiers:

1. **Save as Draft (lenient)** — only `Product Name` and `Description` are required. All other fields may be left incomplete so the Owner can save partial work at any time.
2. **Submit for review (strict)** — before a One Pager can move from `Ready for Review` to `In Review`, it must satisfy every `required` field and array `minItems` constraint defined in the JSON Schema (e.g. at least one use case, one business requirement, one data source, one data product preview row, a complete `businessProblemStatement`, etc.).

Beyond field presence, a small set of **conditional business rules** are enforced on top of the schema, matching the schema's own documented constraints, for example:
- `retentionRequirements` may not be empty when `containsPII` or `containsSensitiveData` is true, or when `classificationLevel` is not `Public`.
- `cdeCriticalityTiering` and `useCaseLinks` on a data element are only applicable when `isCriticalDataElement` is `true`, and must otherwise be null.

Using the schema as the single source of truth keeps validation consistent as the schema evolves and avoids a duplicate, drift-prone rules list.

## 6. Status Management & Review Workflow

The application tracks two independent status fields:
- **One Pager status** (document lifecycle): `Draft`, `Ready for Review`, `In Review`, `Approved`, `Draft Update`, `Cancelled`.
- **Data Product status** (product lifecycle): `In Definition`, `Ready for Development`, `In Development`, `Active`, `In Enhancement`, `Deprecated`, `Cancelled`.

### Transitions

**One Pager status transitions**

| Transition | Who |
|---|---|
| `Draft` → `Ready for Review` → `In Review` | Owner ("Submit for Review" — a single user action that moves the document atomically through `Ready for Review` to `In Review`; `Ready for Review` is a transient intermediate state visible in the change log but the user never rests in it) |
| `Draft Update` → `Ready for Review` → `In Review` | Owner (same atomic submission as above) |
| `In Review` → `Approved` | Approver (must not be listed as Owner or SME on the same One Pager — segregation of duties) |
| `In Review` → `Draft`, with a mandatory comment (→ `Draft Update` when the review was of an update, see [Decision_Log.md](Decision_Log.md) §16) | Approver (same segregation-of-duties constraint applies) |
| `Approved` → `Draft Update` | Owner (via the "Update" action, see §5) |
| `Draft` / `Ready for Review` / `In Review` → `Cancelled` (only while Data Product status is `In Definition`) | Owner (own One Pager) or Admin |

Cancelling a One Pager also sets its Data Product status to `Cancelled` (see the combinations table below). Cancellation is permanent — there is no transition out of `Cancelled` for either status.

**Data Product status transitions**

*Owner-initiated:*

| Transition | Who |
|---|---|
| `Ready for Development` → `In Development` (requires One Pager `Approved`) | Owner |
| `In Enhancement` → `In Development` (requires One Pager `Approved`) | Owner |
| `In Development` → `Active` | Owner |
| `Active` → `Deprecated`, with confirmation | Owner |

`Deprecated` is a terminal state — there is no transition out of it. If a deprecated Data Product needs to be revived, a new One Pager must be created.

*System-driven (triggered automatically by One Pager status changes):*

| Transition | Trigger |
|---|---|
| `In Definition` → `Ready for Development` | First approval of the One Pager |
| any post-approval status → `In Enhancement` | Re-approval after a Draft Update cycle (even if the DP had progressed to a later status such as `Active` — this signals that the updated definition requires re-implementation) |
| `In Definition` → `Cancelled` | One Pager is cancelled |

The Approver is the only role that can approve or reject a One Pager; the Admin's only One Pager status action is Cancel, which is an administrative/housekeeping action rather than a review decision. The Admin does not perform Data Product status transitions — those belong exclusively to the Data Product Owner and its assigned SMEs, consistent with the per-Data-Product edit authorization in §2.

**Segregation of duties:** A user who holds the Approver role may not approve or reject a One Pager on which they are listed as the Data Product Owner or as an SME. This prevents self-approval and is consistent with the banking/audit context. If no other Approver is available, the Admin must reassign the SME before the review can proceed.

**Atomic submission:** "Submit for Review" is a single user action (one button click) that performs strict validation and, if it passes, moves the document from `Draft` (or `Draft Update`) through `Ready for Review` to `In Review` in a single transaction. If any step fails, the entire operation rolls back — the document is never left stranded in `Ready for Review`. This state exists for audit-trail completeness (visible in the change log) but is not a user-facing resting state.

The application enforces these transition rules and re-validates field completeness before allowing a transition.

### Valid status combinations

| One Pager status | Valid Data Product status |
|---|---|
| `Draft` | `In Definition` |
| `Ready for Review` | `In Definition`; during the review of an update the preserved post-approval status ([Decision_Log.md](Decision_Log.md) §16) |
| `In Review` | `In Definition`; during the review of an update the preserved post-approval status ([Decision_Log.md](Decision_Log.md) §16) |
| `Approved` (first time) | `Ready for Development` (set automatically) |
| `Approved` (re-approval after a Draft Update cycle) | `In Enhancement` (set automatically), or any status the Data Product has since progressed to via Owner actions: `In Development`, `Active`, or `Deprecated` |
| `Draft Update` | Same value it had before entering `Draft Update` (preserved) |
| `Cancelled` | `Cancelled` |

The Owner can change Data Product status via the editor, but only to a value valid for the current One Pager status per this table — it is not fully independent of the One Pager's lifecycle.

## 7. Change Log & Version History

Every One Pager maintains a change log capturing version, date, author, and a summary of what changed, including status transitions (old → new status).

### Change log entries
- **System-generated entries**: created automatically for lifecycle events that don't require user input — e.g. initial creation, submission to `In Review`, approval, rejection (including the reviewer's comment), and cancellation. The summary text is generated from the event itself. When a single user action triggers both an OP status change and a DP status change (e.g. approval sets OP to `Approved` and DP to `Ready for Development`), the system creates **two separate change log entries** — one per status field — for auditability.
- **Owner-authored entries**: whenever the Owner saves content changes, they provide a short free-text description of what changed. This becomes the change log entry's summary for that save.

### Versioning

The version number (`MAJOR.MINOR.PATCH`) changes as follows:
- A content save by the Owner (while `Draft` or `Draft Update`) increments **MINOR** (e.g. `0.1.0` → `0.2.0`).
- Pure status transitions with no content change (e.g. `Draft` → `Ready for Review` → `In Review`, or a rejection back to `Draft`) do **not** change the version. After a rejection (e.g. version `0.3.0` was submitted and rejected back to `Draft`), the next content save continues incrementing MINOR from the last value (`0.3.0` → `0.4.0`).
- The first **Approval** sets the version to `1.0.0`. Each subsequent approval (after a `Draft Update` review cycle) increments **MAJOR** (`2.0.0`, `3.0.0`, ...).
- After an approval (e.g. `1.0.0`), content saves during a `Draft Update` increment MINOR from the current version: `1.0.0` → `1.1.0` → `1.2.0`. The subsequent re-approval then sets the version to `2.0.0`.
- **PATCH** is reserved for future use (e.g. minor corrections that do not require a full re-review) and is not used in the current phase.

> **Why the Owner cannot choose PATCH vs. MINOR:** Letting the Owner self-classify a save as a "small" (patch) vs. "regular" (minor) change was considered and rejected. The classification would be subjective and inconsistent across Owners, is redundant with the free-text change log summary (which already conveys the nature/significance of a change), and creates an audit risk — an Owner could mark a substantive change as "patch" to reduce scrutiny before resubmission, undermining the auditability requirement (§15). It would also add an extra decision to every save, working against the deliberately low-friction "Save as Draft" flow (§5). If PATCH is activated in the future, its use should be determined automatically by the application (e.g. detecting cosmetic-only diffs), not by user self-assessment.

Version History is part of the application: users can view the full list of versions for a One Pager (including interim minor versions and approved major milestones) and compare any two versions field-by-field. *(Deferred to a future release — the change log provides summary-level history in the current phase.)*

## 8. Export

- Users can export a One Pager to PDF, containing all sections in a readable, business-facing layout.

## 9. Use Case Management

- A dedicated page lists all use cases across all One Pagers, deduplicated by ID, showing which One Pagers reference each one.
- **All users, regardless of role, can browse the shared Use Case list** (read-only for Approver, Admin, and Viewer).
- **Use Case IDs are unique across the entire One Pager registry**, not just within a single One Pager.
- The Owner manages the shared Use Case list: creating new use cases and editing existing ones. Because a use case may be referenced by multiple One Pagers, removing a use case is handled as deprecation rather than deletion, to avoid breaking existing references.
- Use cases can be referenced/shared across multiple One Pagers.
- **Cross-referencing impact**: editing a use case changes it for all One Pagers that reference it. The use case's own change history (via `last_updated_by`/`last_updated_at`) tracks who changed it. Owners of referencing One Pagers are not notified in the current phase (notifications are deferred per §12), but the Use Cases page shows which One Pagers reference each use case, allowing the editor to assess impact before saving.

## 10. Concurrency Control (Locking)

- Only one user can edit a given One Pager at a time.
- A lock is acquired when a user enters edit mode and released on save, cancel, or submit.
- Locks automatically expire after 30 minutes.
- A user can manually release their own lock from the detail/preview page.
- Locked items are visually indicated in the registry and preview pages.

## 11. Help & Documentation

The application includes an in-app Help page that explains the two-status lifecycle model, shows visual flow diagrams for both status lifecycles, documents roles and responsibilities, provides workflow quick-reference guides, and shows the valid status combination table (§6).

## 12. Notifications *(future release)*

Notifying users when a One Pager requires their review or when its status changes is planned for a later release and is not part of the current phase.

## 13. Data Storage

### Repositories

The One Pager ecosystem uses two Git repositories, both maintained and modified by the Data Product Center team:

1. **OnePagerApp** — contains:
   - Application source code (backend, frontend, services)
   - CI/CD pipeline code and configuration
   - One Pager JSON Schema file (`structure_one_pager_v_1.json`)

2. **OnePagerRegistry** — contains:
   - Approved One Pagers (stored as YAML files in the `main` branch)
   - The authoritative, version-controlled record of all approved One Pagers
   - Connected to the One Pager Application via a service principal, allowing the application to publish approved One Pagers

### Storage Lifecycle

- **One Pager documents in progress** (`Draft`, `Ready for Review`, `In Review`, `Draft Update`) are stored as YAML files in an external Unity Catalog volume. All versions are retained (e.g., `OP-0001_v0.1.0.yml`, `OP-0001_v0.2.0.yml`, etc.), enabling full version history and comparison capability once the Version History feature is implemented in a future release.
- **Approved One Pager documents** are pushed from the One Pager Application to the `main` branch of the **OnePagerRegistry** repository via the service principal, making the approved version the authoritative, version-controlled record. Approved versions are also retained in the external volume for reference.
- **All other application data** — change log, review comments, locks, the shared Use Case registry, and status metadata — is stored in Delta tables in Unity Catalog.
- The storage mechanism is transparent to end users; they interact only with the One Pager and its status, regardless of where it is physically stored.

## 14. Identifiers

| ID | Format | Scope | Generation |
|---|---|---|---|
| One Pager ID | `OP-####` | Unique across all One Pagers in the registry | Assigned automatically by the application at creation; not entered manually |
| Use Case ID | `UC-###` | Unique across all One Pagers in the registry (a use case is a shared entity, see §9) | Assigned automatically by the application at creation; not entered manually |
| Business Requirement ID | `BR-###` | Unique within a One Pager | Assigned automatically by the application at creation |

All three ID types are present in every stored representation of a One Pager (the JSON Schema and the YAML file).

## 15. Non-Functional Requirements

| Category | Requirement |
|---|---|
| Security | All users must be authenticated; permission checks are enforced on the backend, not only hidden in the UI. |
| Auditability | Every change and status transition is recorded in the change log with author, date, and summary. |
| Concurrency | Pessimistic locking (§10) prevents concurrent edits to the same One Pager. |
| Data governance | Data elements may carry PII/sensitivity classification flags (per the schema); these are handled carefully in logs and exports. |
| Data portability | Approved One Pagers are stored as version-controlled YAML in Git, independent of the application. |
| External access | The Approver role may be held by an external representative of Nykredit (the partner financial institution); authentication must support this. |
| Accessibility | Standard accessibility practices apply (keyboard navigation, labeled fields, sufficient contrast). |

## 16. Out of Current Scope

| # | Topic | Disposition |
|---|---|---|
| 1 | **Deletion / archival of One Pagers** | No path to delete or archive a One Pager exists. `Cancelled` is permanent and the record stays in the registry. Archival (hiding cancelled/deprecated items from default views) may be added in a future release if registry clutter becomes a problem. |
| 2 | **Data Product rename** | Renaming the `dataProduct` field (the unique key and volume directory name) is not supported. If a Data Product must be renamed, the current One Pager is cancelled and a new one is created under the new name. |
| 3 | **Ownership transfer without content change** | Changing the Data Product Owner on an `Approved` One Pager requires a `Draft Update` cycle (edit the owner field, re-submit, re-approve). A lightweight transfer mechanism may be added later if this proves burdensome. |
| 4 | **Auto-save** | If a user's browser session is lost mid-edit (e.g. tab closed, network failure), unsaved content in `st.session_state` is lost. The lock expires after 30 minutes but data is not recovered. Periodic auto-save to a Delta-backed draft buffer may be added in a future release. |

## 17. Open Questions

| # | Question | Notes |
|---|---|---|
| 1 | ~~Should PATCH ever be used (e.g. for minor corrections that skip full re-review), or should it remain unused for now?~~ | **Decided:** Currently reserved/unused. If activated, PATCH must be determined automatically by the application, never by user self-selection (see rationale in §7). |
| 2 | ~~Is the merge of an approved One Pager’s YAML into the `main` branch fully automated by the application, or does it go through a manual pull-request/approval step?~~ | **Decided:** The application creates a pull request automatically on approval. If branch protection allows auto-merge, the process is fully automated; otherwise, an Admin confirms the merge as a delivery step, not a content review. |
| 3 | What is the expected scale (number of One Pagers, concurrent users) for performance planning? | Not yet specified. |
