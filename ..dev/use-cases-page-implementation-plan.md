# Implementation Plan — Use Cases Page

> Audience: an implementer (including a less-capable model) who will build this
> feature end-to-end. Every decision has already been made for you and is
> explained. Follow the steps in order. Copy the code skeletons and adapt names
> only where explicitly told to.

---

## 1. Goal

Add a **Use Cases** page to the Streamlit app that lets users **manage the shared
Use Case registry**. Use Cases are stored in a **Delta table** (`use_cases`) and
are shared across One Pagers (a single Use Case can be referenced by many One
Pagers).

The page must support:

1. **List** all Use Cases in a paginated, filterable table.
2. **Create** a new Use Case.
3. **Edit** an existing Use Case.
4. **Deprecate / Restore** a Use Case (soft delete — never hard delete).
5. **Show how many One Pagers reference** each Use Case (read-only indicator).

This mirrors the existing **Registry** page (`app/views/registry.py`) in
structure and coding style. When in doubt, copy the Registry page's patterns.

---

## 2. Context — how the codebase is layered

The app uses a clean layered architecture. You will touch one file (or a small
number) per layer. Read these existing files first — they are your templates:

| Layer | File(s) | Role |
|---|---|---|
| Domain models | `src/onepagerapp/models.py` | Plain `@dataclass` objects passed between layers. |
| Data access interface | `src/onepagerapp/data_access/base.py` | Abstract `DataAccess` (ABC). Declares every data method. |
| Real data access | `src/onepagerapp/data_access/lakehouse.py` | `LakehouseAccess` — runs SQL against Delta via `DatabricksConnection`. |
| Mock data access | `src/onepagerapp/data_access/mock.py` | `MockDataAccess` — in-memory fake used for local-mock mode and unit tests. |
| SQL execution | `src/onepagerapp/data_access/connection.py` | `DatabricksConnection.execute_statement(sql)` runs any SQL statement. |
| Streamlit views | `app/views/*.py` | One module per page. Module-level code runs on render. |
| Navigation | `app/app.py` | Registers pages via `st.navigation([...])`. |
| Theming helpers | `app/adapters/theme.py` | Badge/color helpers. |
| DB schema (DDL) | `liquibase/bia_meta/onepager_app/ddl/*.sql` | One SQL file per table + seed data. |
| DDL registration | `liquibase/bia_meta/onepager_app/root.changelog.databricks.yaml` | Lists DDL files in apply order. |

**Golden rule:** every new data method must be added in **all three** of
`base.py` (abstract), `lakehouse.py` (real), and `mock.py` (fake). If you add it
to only one, the app or the tests will break.

---

## 3. Data model (already specified)

The `use_cases`, `use_case_references`, and `id_sequences` tables are already
designed in [docs/Data_Model.md](../docs/Data_Model.md) §3–§4 but the DDL files
**do not exist yet**. You will create them.

### 3.1 `use_cases` — the registry (single source of truth)

| Column | Type | Nullable | Description |
|---|---|---|---|
| `use_case_id` | STRING | No | PK. Auto-generated `UC-###`. |
| `persona` | STRING | No | Role/job title of the consumer. |
| `goal` | STRING | No | What the persona wants to achieve. |
| `scenario` | STRING | No | How they use the data product. |
| `decision_enabled` | STRING | No | What decision/action this enables. |
| `priority` | STRING | No | `Must Have` / `High` / `Medium` / `Low`. |
| `deprecated` | BOOLEAN | No | Soft-delete flag. Default `false`. |
| `created_by` | STRING | No | Initials of creator. |
| `created_at` | TIMESTAMP | No | Creation timestamp. |
| `last_updated_by` | STRING | No | Initials of last editor. |
| `last_updated_at` | TIMESTAMP | No | Last modification timestamp. |

### 3.2 `use_case_references` — junction table

| Column | Type | Nullable | Description |
|---|---|---|---|
| `one_pager_id` | STRING | No | FK → `one_pager_status.one_pager_id`. |
| `use_case_id` | STRING | No | FK → `use_cases.use_case_id`. |

Composite PK: (`one_pager_id`, `use_case_id`). This page only **reads** this
table (to show reference counts). Writing to it happens in the One Pager editor,
which is out of scope here.

### 3.3 `id_sequences` — ID counter

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id_type` | STRING | No | PK. `OP` / `UC` / `BR`. |
| `last_value` | INT | No | Last assigned numeric value. Next ID = `last_value + 1`. |

Seed rows: `('OP', 0)`, `('UC', 0)`, `('BR', 0)`.

---

## 4. Design decisions (made for you, with rationale)

Read these before coding. They resolve every ambiguity.

### D1 — Standalone management page (not embedded in the editor)
**Decision:** Build a dedicated top-level page `Use Cases`, styled like the
Registry page.
**Why:** Use Cases are a *shared registry* (Data_Model §3). Managing them
centrally (create/edit/deprecate) is a distinct task from linking them into a
specific One Pager. A standalone page matches the data's "single source of
truth" nature and reuses the proven Registry layout.

### D2 — Soft delete only (deprecate / restore)
**Decision:** Never physically delete a row. Toggle the `deprecated` boolean.
The list defaults to hiding deprecated rows; a checkbox reveals them; deprecated
rows can be restored.
**Why:** Other One Pagers may reference a Use Case. Hard delete would create
dangling references. The schema already includes `deprecated` for exactly this
reason (Data_Model §3).

### D3 — Block deprigrade wording; show reference count, warn before deprecate
**Decision:** Show a "Referenced by N" count per Use Case (from
`use_case_references`). Deprecating is always allowed, but if `N > 0` show a
warning that the Use Case is still referenced.
**Why:** Deprecation is a soft state; owners of referencing One Pagers should
eventually stop using it, but we must not block the registry maintainer. A
warning gives visibility without hard coupling.

### D4 — Priority is a fixed enum
**Decision:** Priority options are exactly `["Must Have", "High", "Medium",
"Low"]`, defined as a module constant `PRIORITY_OPTIONS`.
**Why:** These are the values in `structure_one_pager_v_1.json`
(`useCases.items.priority.enum`). Keep them in sync with the schema.

### D5 — ID generation via `id_sequences` with compare-and-swap + retry
**Decision:** Generate `UC-###` by atomically incrementing the `UC` row in
`id_sequences`:
1. `SELECT last_value` for `id_type='UC'`.
2. `UPDATE ... SET last_value = <current>+1 WHERE id_type='UC' AND last_value = <current>`.
3. `SELECT last_value` again and confirm it equals `<current>+1`. If not, another
   writer won the race — retry from step 1 (max 5 attempts).
4. Format as `f"UC-{new_value:03d}"`.

**Why:** Delta Lake does not enforce uniqueness, and there is no
`UPDATE ... RETURNING`. The compare-and-swap `WHERE last_value = <current>`
guard plus a re-read makes concurrent generation safe at the expected scale
(dozens/hundreds of Use Cases), exactly as Data_Model §4 anticipates. An ID gap
on a failed insert is harmless (also per §4).

Encapsulate this in a **private helper** `_generate_use_case_id()` on
`LakehouseAccess`. `MockDataAccess` uses a simple in-memory counter.

### D6 — Author identity in v1: derive initials from the current user
**Decision:** `created_by` / `last_updated_by` store **initials**. Since a full
identity service does not exist yet, derive initials from the current user
string with a small helper: take the local part of an email / username, split on
`.`/`-`/`_`/space, uppercase the first letter of each of the first two tokens
(e.g. `alice.brown@company.com` → `AB`; fallback to first two chars uppercased).
**Why:** The schema wants initials (Data_Model §5, "Delta stores initials").
Permissions are a documented v1 stub (`src/onepagerapp/permissions.py`), so a
deterministic best-effort derivation is acceptable and keeps the audit columns
populated. Put this helper in `src/onepagerapp/permissions.py` as
`initials_from_user(user: str) -> str` so it can be reused later.

### D7 — Write authorization: any authenticated user (v1)
**Decision:** Any authenticated user may create/edit/deprecate a Use Case in v1.
**Why:** Consistent with the current v1 permission model (all authenticated
users can read the registry; state-change enforcement is deferred to a future
service layer per `permissions.py` and Backend_Design §5). Do **not** build a
role system here. Record who did what via the audit columns (D6).

### D8 — Filtering & pagination mirror the Registry
**Decision:** Provide server-side filtering (text search on persona/goal,
priority dropdown, include-deprecated toggle) and pagination, using new
`UseCaseFilter` / `UseCasePage` dataclasses that parallel `RegistryFilter` /
`RegistryPage`.
**Why:** Consistency with the existing Registry page reduces cognitive load and
lets you copy proven code. Server-side pagination scales the same way.

### D9 — Create/Edit UX uses `st.dialog` modals
**Decision:** "New Use Case" opens a modal (`@st.dialog`) with a form. Each row's
"Edit" opens the same modal pre-filled. Submit calls the data-access write
method, then `st.rerun()`.
**Why:** Modals keep the list view clean and give a focused create/edit
experience. `st.dialog` is the standard Streamlit primitive for this. (If the
installed Streamlit version lacks `st.dialog`, fall back to an `st.expander`
containing an `st.form` — same form code.)

### D10 — SQL injection protection
**Decision:** Escape **every** user-supplied string with the existing
`_escape_sql_string` static method (doubles single quotes) before interpolating
into SQL. Wrap all interpolated string literals in single quotes.
**Why:** The Databricks SQL statement API is used with string interpolation
throughout `lakehouse.py`; the established mitigation is `_escape_sql_string`.
Follow the same pattern for consistency and safety (OWASP A03: Injection).

---

## 5. Step-by-step implementation

Do these in order. After each numbered section, the app should still run.

### Step 1 — Create the DDL files

Create three files under `liquibase/bia_meta/onepager_app/ddl/`. Match the
existing style (see `one_pager_status.sql`, `change_log.sql`,
`ref_business_domains.sql`).

**File: `liquibase/bia_meta/onepager_app/ddl/use_cases.sql`**
```sql
-- liquibase formatted sql

-- changeset onepagerapp:cur_use_cases-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.use_cases (
    use_case_id         STRING      NOT NULL PRIMARY KEY,
    persona             STRING      NOT NULL,
    goal                STRING      NOT NULL,
    scenario            STRING      NOT NULL,
    decision_enabled    STRING      NOT NULL,
    priority            STRING      NOT NULL,
    deprecated          BOOLEAN     NOT NULL,
    created_by          STRING      NOT NULL,
    created_at          TIMESTAMP   NOT NULL,
    last_updated_by     STRING      NOT NULL,
    last_updated_at     TIMESTAMP   NOT NULL
);

--rollback DROP TABLE ${catalog.name}.onepager_app.use_cases;
```

**File: `liquibase/bia_meta/onepager_app/ddl/use_case_references.sql`**
```sql
-- liquibase formatted sql

-- changeset onepagerapp:cur_use_case_references-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.use_case_references (
    one_pager_id        STRING      NOT NULL,
    use_case_id         STRING      NOT NULL,
    CONSTRAINT pk_use_case_references PRIMARY KEY (one_pager_id, use_case_id)
);

--rollback DROP TABLE ${catalog.name}.onepager_app.use_case_references;
```

**File: `liquibase/bia_meta/onepager_app/ddl/id_sequences.sql`**
```sql
-- liquibase formatted sql

-- changeset onepagerapp:cur_id_sequences-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.id_sequences (
    id_type     STRING      NOT NULL PRIMARY KEY,
    last_value  INT         NOT NULL
);

INSERT INTO ${catalog.name}.onepager_app.id_sequences (id_type, last_value)
VALUES
    ('OP', 0),
    ('UC', 0),
    ('BR', 0);

--rollback DROP TABLE ${catalog.name}.onepager_app.id_sequences;
```

> Note on composite PK syntax: if the Databricks/Liquibase runtime rejects the
> named `CONSTRAINT ... PRIMARY KEY (...)` form, fall back to creating the table
> with just the two columns (no PK clause). Delta does not physically enforce
> PKs anyway (Data_Model §3 note); uniqueness is enforced in app code.

### Step 2 — Register the DDL files in the changelog

Edit
[liquibase/bia_meta/onepager_app/root.changelog.databricks.yaml](../liquibase/bia_meta/onepager_app/root.changelog.databricks.yaml).
Add the new includes. `id_sequences` and `use_cases` have no dependencies;
`use_case_references` logically depends on both `one_pager_status` and
`use_cases`, so place it after them. Final file:

```yaml
databaseChangeLog:
    # Reference tables (no dependencies)
    - include:
        file: ddl/ref_op_status.sql
    - include:
        file: ddl/ref_dp_status.sql
    - include:
        file: ddl/ref_business_domains.sql
    - include:
        file: ddl/ref_data_product_types.sql

    # ID sequences
    - include:
        file: ddl/id_sequences.sql

    # Operational tables
    - include:
        file: ddl/one_pager_status.sql
    - include:
        file: ddl/change_log.sql
    - include:
        file: ddl/review_comments.sql
    - include:
        file: ddl/locks.sql

    # Use case registry
    - include:
        file: ddl/use_cases.sql
    - include:
        file: ddl/use_case_references.sql
```

### Step 3 — Add domain models

Edit [src/onepagerapp/models.py](../src/onepagerapp/models.py). Append the
following at the end. Keep the existing docstring style (concise, with
`Attributes:` blocks).

```python
# ============================================================================
# Use Cases Page Models
# ============================================================================


@dataclass
class UseCase:
    """A single Use Case from the use_cases registry.

    Attributes:
        use_case_id: Primary key (e.g. "UC-001").
        persona: Role/job title of the consumer.
        goal: What the persona wants to achieve.
        scenario: How they use the data product.
        decision_enabled: What decision/action this enables.
        priority: "Must Have" | "High" | "Medium" | "Low".
        deprecated: Soft-delete flag.
        created_by: Initials of creator.
        created_at: Creation timestamp.
        last_updated_by: Initials of last editor.
        last_updated_at: Last modification timestamp.
        reference_count: Number of One Pagers referencing this Use Case
            (populated by list queries; 0 when unknown).
    """

    use_case_id: str
    persona: str
    goal: str
    scenario: str
    decision_enabled: str
    priority: str
    deprecated: bool
    created_by: str
    created_at: datetime
    last_updated_by: str
    last_updated_at: datetime
    reference_count: int = 0


@dataclass
class UseCaseFilter:
    """Filtering criteria for Use Case registry queries.

    All fields optional. None means "no filter on this dimension". Combined
    with AND semantics.

    Attributes:
        search: Partial, case-insensitive match on persona OR goal.
        priority: Exact match on priority.
        include_deprecated: When False (default), deprecated rows are excluded.
    """

    search: Optional[str] = None
    priority: Optional[str] = None
    include_deprecated: bool = False


@dataclass
class UseCasePage:
    """Paginated result set from a Use Case query.

    Attributes:
        rows: UseCase objects for the current page.
        total_rows: Total rows matching the filter (all pages).
        page: Current page number (1-indexed).
        page_size: Rows per page.
    """

    rows: list["UseCase"]
    total_rows: int
    page: int
    page_size: int = field(default=10)

    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return (self.total_rows + self.page_size - 1) // self.page_size

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size
```

`datetime`, `field`, and `Optional` are already imported at the top of
`models.py` — do not re-import them.

### Step 4 — Add the initials helper

Edit [src/onepagerapp/permissions.py](../src/onepagerapp/permissions.py). Add
this module-level function (near the top, after the imports):

```python
def initials_from_user(user: str | None) -> str:
    """Best-effort derive user initials from an email or username.

    Examples:
        "alice.brown@company.com" -> "AB"
        "bob_smith"               -> "BS"
        "charlie"                 -> "CH"
        None / ""                 -> "??"
    """
    if not user:
        return "??"
    local = user.split("@", 1)[0]
    tokens = [t for t in re.split(r"[.\-_ ]+", local) if t]
    if len(tokens) >= 2:
        return (tokens[0][0] + tokens[1][0]).upper()
    if tokens and len(tokens[0]) >= 2:
        return tokens[0][:2].upper()
    if tokens:
        return tokens[0][0].upper()
    return "??"
```

Add `import re` at the top of the file with the other imports.

### Step 5 — Extend the `DataAccess` interface

Edit [src/onepagerapp/data_access/base.py](../src/onepagerapp/data_access/base.py).

1. Add to the `from onepagerapp.models import (...)` block:
   `UseCase, UseCaseFilter, UseCasePage`.
2. Add these abstract methods at the end of the class (keep the docstring
   style):

```python
    # ========================================================================
    # Use Cases Page Methods
    # ========================================================================

    @abstractmethod
    def get_use_cases(
        self, filter: UseCaseFilter, page: int, page_size: int
    ) -> UseCasePage:
        """Query Use Cases with filtering and pagination.

        Applies filters with AND semantics. Each returned UseCase has its
        reference_count populated from use_case_references.

        Args:
            filter: UseCaseFilter (None fields = no filter on that dimension).
            page: 1-indexed page number.
            page_size: Rows per page.

        Returns:
            UseCasePage with the requested page and total count.
        """
        ...

    @abstractmethod
    def get_use_case(self, use_case_id: str) -> UseCase | None:
        """Fetch a single Use Case by ID (with reference_count), or None."""
        ...

    @abstractmethod
    def create_use_case(
        self,
        persona: str,
        goal: str,
        scenario: str,
        decision_enabled: str,
        priority: str,
        created_by_initials: str,
    ) -> str:
        """Create a Use Case. Returns the new use_case_id (UC-###).

        Generates the ID atomically from id_sequences. Sets deprecated=false
        and both audit timestamps to now.
        """
        ...

    @abstractmethod
    def update_use_case(
        self,
        use_case_id: str,
        persona: str,
        goal: str,
        scenario: str,
        decision_enabled: str,
        priority: str,
        updated_by_initials: str,
    ) -> None:
        """Update an existing Use Case's editable fields and audit columns."""
        ...

    @abstractmethod
    def set_use_case_deprecated(
        self, use_case_id: str, deprecated: bool, updated_by_initials: str
    ) -> None:
        """Set the deprecated flag (soft delete / restore) + audit columns."""
        ...
```

### Step 6 — Implement in `MockDataAccess`

Edit [src/onepagerapp/data_access/mock.py](../src/onepagerapp/data_access/mock.py).

1. Add `UseCase, UseCaseFilter, UseCasePage` to the models import block.
2. In `__init__`, seed an in-memory store and an ID counter:

```python
    def __init__(self, document_store: OnePagerDocumentStore) -> None:  # noqa: D107
        self._document_store = document_store
        self._uc_counter = 2  # highest seeded UC number
        self._use_cases: list[UseCase] = [
            UseCase(
                use_case_id="UC-001",
                persona="Risk Analyst",
                goal="Assess counterparty exposure",
                scenario="Reviews daily exposure dashboards",
                decision_enabled="Approve or block new trades",
                priority="Must Have",
                deprecated=False,
                created_by="AB",
                created_at=datetime(2026, 8, 1, 9, 0),
                last_updated_by="AB",
                last_updated_at=datetime(2026, 8, 1, 9, 0),
                reference_count=2,
            ),
            UseCase(
                use_case_id="UC-002",
                persona="Finance Controller",
                goal="Reconcile month-end balances",
                scenario="Compares ledger vs. source systems",
                decision_enabled="Sign off on financial close",
                priority="High",
                deprecated=False,
                created_by="BS",
                created_at=datetime(2026, 8, 5, 10, 0),
                last_updated_by="BS",
                last_updated_at=datetime(2026, 8, 5, 10, 0),
                reference_count=0,
            ),
        ]
```

3. Add the method implementations at the end of the class:

```python
    # ========================================================================
    # Use Cases Page Methods
    # ========================================================================

    def get_use_cases(
        self, filter: UseCaseFilter, page: int, page_size: int
    ) -> UseCasePage:
        rows = list(self._use_cases)
        if not filter.include_deprecated:
            rows = [r for r in rows if not r.deprecated]
        if filter.search:
            needle = filter.search.lower()
            rows = [
                r for r in rows
                if needle in r.persona.lower() or needle in r.goal.lower()
            ]
        if filter.priority:
            rows = [r for r in rows if r.priority == filter.priority]

        rows.sort(key=lambda r: r.use_case_id)
        total = len(rows)
        offset = (page - 1) * page_size
        return UseCasePage(
            rows=rows[offset : offset + page_size],
            total_rows=total,
            page=page,
            page_size=page_size,
        )

    def get_use_case(self, use_case_id: str) -> UseCase | None:
        return next(
            (r for r in self._use_cases if r.use_case_id == use_case_id), None
        )

    def create_use_case(
        self,
        persona: str,
        goal: str,
        scenario: str,
        decision_enabled: str,
        priority: str,
        created_by_initials: str,
    ) -> str:
        self._uc_counter += 1
        new_id = f"UC-{self._uc_counter:03d}"
        now = datetime.now()
        self._use_cases.append(
            UseCase(
                use_case_id=new_id,
                persona=persona,
                goal=goal,
                scenario=scenario,
                decision_enabled=decision_enabled,
                priority=priority,
                deprecated=False,
                created_by=created_by_initials,
                created_at=now,
                last_updated_by=created_by_initials,
                last_updated_at=now,
                reference_count=0,
            )
        )
        return new_id

    def update_use_case(
        self,
        use_case_id: str,
        persona: str,
        goal: str,
        scenario: str,
        decision_enabled: str,
        priority: str,
        updated_by_initials: str,
    ) -> None:
        uc = self.get_use_case(use_case_id)
        if uc is None:
            raise RuntimeError(f"Use Case {use_case_id} not found")
        uc.persona = persona
        uc.goal = goal
        uc.scenario = scenario
        uc.decision_enabled = decision_enabled
        uc.priority = priority
        uc.last_updated_by = updated_by_initials
        uc.last_updated_at = datetime.now()

    def set_use_case_deprecated(
        self, use_case_id: str, deprecated: bool, updated_by_initials: str
    ) -> None:
        uc = self.get_use_case(use_case_id)
        if uc is None:
            raise RuntimeError(f"Use Case {use_case_id} not found")
        uc.deprecated = deprecated
        uc.last_updated_by = updated_by_initials
        uc.last_updated_at = datetime.now()
```

### Step 7 — Implement in `LakehouseAccess`

Edit [src/onepagerapp/data_access/lakehouse.py](../src/onepagerapp/data_access/lakehouse.py).

1. Add `UseCase, UseCaseFilter, UseCasePage` to the models import block.
2. Add a small helper to run a scalar query (or reuse `execute_statement`
   directly). Add the methods below at the end of the class. Study
   `get_registry` first — the WHERE-building and result-parsing patterns are
   identical.

```python
    # ========================================================================
    # Use Cases Page Methods
    # ========================================================================

    def _use_cases_where(self, filter: UseCaseFilter) -> str:
        """Build a WHERE clause for use_cases from a filter (values escaped)."""
        clauses: list[str] = []
        if not filter.include_deprecated:
            clauses.append("deprecated = false")
        if filter.search:
            esc = self._escape_sql_string(filter.search)
            clauses.append(
                f"(LOWER(persona) LIKE LOWER('%{esc}%') "
                f"OR LOWER(goal) LIKE LOWER('%{esc}%'))"
            )
        if filter.priority:
            esc = self._escape_sql_string(filter.priority)
            clauses.append(f"priority = '{esc}'")
        return " AND ".join(clauses) if clauses else "1=1"

    def get_use_cases(
        self, filter: UseCaseFilter, page: int, page_size: int
    ) -> UseCasePage:
        uc_fqn = f"{self._fqn_prefix}.use_cases"
        ref_fqn = f"{self._fqn_prefix}.use_case_references"
        where_clause = self._use_cases_where(filter)

        # Total count
        count_query = f"SELECT COUNT(*) FROM {uc_fqn} WHERE {where_clause}"  # noqa: S608
        try:
            resp = self._connection.execute_statement(count_query)
            rows = (resp.result.data_array if resp.result else None) or []
            total_rows = int(rows[0][0]) if rows else 0
        except Exception as e:
            logger.error(f"Failed to count use cases: {e}")
            raise RuntimeError(f"Failed to count Use Cases: {e}") from e

        # Page data with reference counts via LEFT JOIN + GROUP BY
        offset = (page - 1) * page_size
        query = (
            f"SELECT uc.use_case_id, uc.persona, uc.goal, uc.scenario, "
            f"uc.decision_enabled, uc.priority, uc.deprecated, uc.created_by, "
            f"uc.created_at, uc.last_updated_by, uc.last_updated_at, "
            f"COUNT(ref.one_pager_id) AS reference_count "
            f"FROM {uc_fqn} uc "
            f"LEFT JOIN {ref_fqn} ref ON uc.use_case_id = ref.use_case_id "
            f"WHERE {where_clause} "
            f"GROUP BY uc.use_case_id, uc.persona, uc.goal, uc.scenario, "
            f"uc.decision_enabled, uc.priority, uc.deprecated, uc.created_by, "
            f"uc.created_at, uc.last_updated_by, uc.last_updated_at "
            f"ORDER BY uc.use_case_id "
            f"LIMIT {page_size} OFFSET {offset}"
        )  # noqa: S608
        try:
            resp = self._connection.execute_statement(query)
            schema = resp.manifest.schema if resp.manifest else None
            columns = [c.name for c in (schema.columns if schema else None) or []]
            data = (resp.result.data_array if resp.result else None) or []
            df = pd.DataFrame(data, columns=columns) if columns else pd.DataFrame()
            page_rows = [self._row_to_use_case(row) for _, row in df.iterrows()]
            return UseCasePage(
                rows=page_rows,
                total_rows=total_rows,
                page=page,
                page_size=page_size,
            )
        except Exception as e:
            logger.error(f"Failed to fetch use case page: {e}")
            raise RuntimeError(f"Failed to fetch Use Cases: {e}") from e

    def get_use_case(self, use_case_id: str) -> UseCase | None:
        uc_fqn = f"{self._fqn_prefix}.use_cases"
        ref_fqn = f"{self._fqn_prefix}.use_case_references"
        esc = self._escape_sql_string(use_case_id)
        query = (
            f"SELECT uc.use_case_id, uc.persona, uc.goal, uc.scenario, "
            f"uc.decision_enabled, uc.priority, uc.deprecated, uc.created_by, "
            f"uc.created_at, uc.last_updated_by, uc.last_updated_at, "
            f"COUNT(ref.one_pager_id) AS reference_count "
            f"FROM {uc_fqn} uc "
            f"LEFT JOIN {ref_fqn} ref ON uc.use_case_id = ref.use_case_id "
            f"WHERE uc.use_case_id = '{esc}' "
            f"GROUP BY uc.use_case_id, uc.persona, uc.goal, uc.scenario, "
            f"uc.decision_enabled, uc.priority, uc.deprecated, uc.created_by, "
            f"uc.created_at, uc.last_updated_by, uc.last_updated_at"
        )  # noqa: S608
        resp = self._connection.execute_statement(query)
        schema = resp.manifest.schema if resp.manifest else None
        columns = [c.name for c in (schema.columns if schema else None) or []]
        data = (resp.result.data_array if resp.result else None) or []
        if not data:
            return None
        df = pd.DataFrame(data, columns=columns)
        return self._row_to_use_case(df.iloc[0])

    @staticmethod
    def _row_to_use_case(row) -> UseCase:
        return UseCase(
            use_case_id=str(row["use_case_id"]),
            persona=str(row["persona"]),
            goal=str(row["goal"]),
            scenario=str(row["scenario"]),
            decision_enabled=str(row["decision_enabled"]),
            priority=str(row["priority"]),
            deprecated=bool(row["deprecated"]),
            created_by=str(row["created_by"]),
            created_at=row["created_at"],
            last_updated_by=str(row["last_updated_by"]),
            last_updated_at=row["last_updated_at"],
            reference_count=int(row["reference_count"]),
        )

    def _generate_use_case_id(self) -> str:
        """Atomically allocate the next UC-### id (compare-and-swap + retry)."""
        seq_fqn = f"{self._fqn_prefix}.id_sequences"
        for _ in range(5):
            read = self._connection.execute_statement(
                f"SELECT last_value FROM {seq_fqn} WHERE id_type = 'UC'"  # noqa: S608
            )
            rows = (read.result.data_array if read.result else None) or []
            if not rows:
                raise RuntimeError("id_sequences row for 'UC' is missing")
            current = int(rows[0][0])
            new_value = current + 1
            self._connection.execute_statement(
                f"UPDATE {seq_fqn} SET last_value = {new_value} "  # noqa: S608
                f"WHERE id_type = 'UC' AND last_value = {current}"
            )
            verify = self._connection.execute_statement(
                f"SELECT last_value FROM {seq_fqn} WHERE id_type = 'UC'"  # noqa: S608
            )
            vrows = (verify.result.data_array if verify.result else None) or []
            if vrows and int(vrows[0][0]) == new_value:
                return f"UC-{new_value:03d}"
        raise RuntimeError("Failed to allocate a Use Case ID after retries")

    def create_use_case(
        self,
        persona: str,
        goal: str,
        scenario: str,
        decision_enabled: str,
        priority: str,
        created_by_initials: str,
    ) -> str:
        new_id = self._generate_use_case_id()
        fqn = f"{self._fqn_prefix}.use_cases"
        vals = [
            new_id, persona, goal, scenario, decision_enabled, priority,
            created_by_initials, created_by_initials,
        ]
        p, g, s, d, pr, cb = (
            self._escape_sql_string(persona),
            self._escape_sql_string(goal),
            self._escape_sql_string(scenario),
            self._escape_sql_string(decision_enabled),
            self._escape_sql_string(priority),
            self._escape_sql_string(created_by_initials),
        )
        query = (
            f"INSERT INTO {fqn} (use_case_id, persona, goal, scenario, "  # noqa: S608
            f"decision_enabled, priority, deprecated, created_by, created_at, "
            f"last_updated_by, last_updated_at) VALUES "
            f"('{new_id}', '{p}', '{g}', '{s}', '{d}', '{pr}', false, "
            f"'{cb}', current_timestamp(), '{cb}', current_timestamp())"
        )
        try:
            self._connection.execute_statement(query)
        except Exception as e:
            logger.error(f"Failed to insert use case: {e}")
            raise RuntimeError(f"Failed to create Use Case: {e}") from e
        return new_id

    def update_use_case(
        self,
        use_case_id: str,
        persona: str,
        goal: str,
        scenario: str,
        decision_enabled: str,
        priority: str,
        updated_by_initials: str,
    ) -> None:
        fqn = f"{self._fqn_prefix}.use_cases"
        uid = self._escape_sql_string(use_case_id)
        p, g, s, d, pr, ub = (
            self._escape_sql_string(persona),
            self._escape_sql_string(goal),
            self._escape_sql_string(scenario),
            self._escape_sql_string(decision_enabled),
            self._escape_sql_string(priority),
            self._escape_sql_string(updated_by_initials),
        )
        query = (
            f"UPDATE {fqn} SET persona = '{p}', goal = '{g}', "  # noqa: S608
            f"scenario = '{s}', decision_enabled = '{d}', priority = '{pr}', "
            f"last_updated_by = '{ub}', last_updated_at = current_timestamp() "
            f"WHERE use_case_id = '{uid}'"
        )
        try:
            self._connection.execute_statement(query)
        except Exception as e:
            logger.error(f"Failed to update use case: {e}")
            raise RuntimeError(f"Failed to update Use Case: {e}") from e

    def set_use_case_deprecated(
        self, use_case_id: str, deprecated: bool, updated_by_initials: str
    ) -> None:
        fqn = f"{self._fqn_prefix}.use_cases"
        uid = self._escape_sql_string(use_case_id)
        ub = self._escape_sql_string(updated_by_initials)
        flag = "true" if deprecated else "false"
        query = (
            f"UPDATE {fqn} SET deprecated = {flag}, "  # noqa: S608
            f"last_updated_by = '{ub}', last_updated_at = current_timestamp() "
            f"WHERE use_case_id = '{uid}'"
        )
        try:
            self._connection.execute_statement(query)
        except Exception as e:
            logger.error(f"Failed to change deprecated flag: {e}")
            raise RuntimeError(f"Failed to update Use Case: {e}") from e
```

> The unused `vals` list in `create_use_case` above is illustrative — delete it;
> the escaped locals are what the query uses.

### Step 8 — Build the Streamlit page

Create **`app/views/use_cases.py`**. Model it on `app/views/registry.py`
(cached reference loaders, module-level render body, error boundaries). Full
skeleton:

```python
"""Use Cases page — manage the shared Use Case registry.

Lists Use Cases (paginated, filterable), and supports create, edit, and
deprecate/restore. Use Cases are stored in the Delta `use_cases` table and are
shared across One Pagers.
"""

import logging

import streamlit as st

from onepagerapp.models import UseCaseFilter
from onepagerapp.permissions import initials_from_user

logger = logging.getLogger(__name__)

ROWS_PER_PAGE = 20
PRIORITY_OPTIONS = ["Must Have", "High", "Medium", "Low"]


def _current_user_initials() -> str:
    user = st.session_state.get("current_user")
    return initials_from_user(user)


@st.dialog("Use Case")
def _use_case_dialog(data_access, existing=None) -> None:
    """Create (existing=None) or edit an existing UseCase."""
    is_edit = existing is not None
    st.subheader("Edit Use Case" if is_edit else "New Use Case")
    with st.form("use_case_form"):
        persona = st.text_input("Persona", value=existing.persona if is_edit else "")
        goal = st.text_area("Goal", value=existing.goal if is_edit else "")
        scenario = st.text_area("Scenario", value=existing.scenario if is_edit else "")
        decision_enabled = st.text_area(
            "Decision enabled",
            value=existing.decision_enabled if is_edit else "",
        )
        priority = st.selectbox(
            "Priority",
            options=PRIORITY_OPTIONS,
            index=PRIORITY_OPTIONS.index(existing.priority) if is_edit else 0,
        )
        submitted = st.form_submit_button("Save")

    if submitted:
        fields = {
            "persona": persona.strip(),
            "goal": goal.strip(),
            "scenario": scenario.strip(),
            "decision_enabled": decision_enabled.strip(),
            "priority": priority,
        }
        missing = [k for k, v in fields.items() if not v]
        if missing:
            st.error(f"All fields are required. Missing: {', '.join(missing)}")
            return
        initials = _current_user_initials()
        try:
            if is_edit:
                data_access.update_use_case(
                    existing.use_case_id, **fields, updated_by_initials=initials
                )
                st.success(f"Updated {existing.use_case_id}")
            else:
                new_id = data_access.create_use_case(
                    **fields, created_by_initials=initials
                )
                st.success(f"Created {new_id}")
            st.rerun()
        except Exception:
            logger.exception("Failed to save use case")
            st.error("Failed to save Use Case. Check the logs and try again.")


def _render_table(data_access, page_obj) -> None:
    header = st.columns([1.2, 2, 3, 1.2, 1.2, 1, 1])
    labels = ["ID", "Persona", "Goal", "Priority", "Referenced", "Edit", "State"]
    for col, label in zip(header, labels, strict=False):
        col.markdown(f"**{label}**")

    for uc in page_obj.rows:
        c = st.columns([1.2, 2, 3, 1.2, 1.2, 1, 1])
        c[0].write(uc.use_case_id)
        c[1].write(uc.persona)
        c[2].write(uc.goal)
        c[3].write(uc.priority)
        c[4].write(str(uc.reference_count))
        if c[5].button("Edit", key=f"edit_{uc.use_case_id}"):
            _use_case_dialog(data_access, existing=uc)
        if uc.deprecated:
            if c[6].button("Restore", key=f"restore_{uc.use_case_id}"):
                data_access.set_use_case_deprecated(
                    uc.use_case_id, False, _current_user_initials()
                )
                st.rerun()
        else:
            if c[6].button("Deprecate", key=f"deprecate_{uc.use_case_id}"):
                if uc.reference_count > 0:
                    st.warning(
                        f"{uc.use_case_id} is referenced by "
                        f"{uc.reference_count} One Pager(s). Deprecating anyway."
                    )
                data_access.set_use_case_deprecated(
                    uc.use_case_id, True, _current_user_initials()
                )
                st.rerun()


# ============================================================================
# Page body
# ============================================================================

st.title("Use Cases")
st.markdown("Manage the shared Use Case registry.")

try:
    data_access = st.session_state.data_access
except (AttributeError, KeyError):
    st.error("Services not initialized. Please refresh the page.")
    st.stop()

if st.button("➕ New Use Case"):
    _use_case_dialog(data_access, existing=None)

fcol1, fcol2, fcol3 = st.columns([2, 1, 1])
with fcol1:
    search = st.text_input("Search persona or goal", key="uc_search")
with fcol2:
    priority = st.selectbox("Priority", options=["All", *PRIORITY_OPTIONS], key="uc_priority")
with fcol3:
    include_deprecated = st.checkbox("Show deprecated", key="uc_show_deprecated")

if "use_cases_page" not in st.session_state:
    st.session_state.use_cases_page = 1

current_filter = UseCaseFilter(
    search=search or None,
    priority=priority if priority != "All" else None,
    include_deprecated=include_deprecated,
)

try:
    with st.spinner("Loading Use Cases..."):
        page_obj = data_access.get_use_cases(
            current_filter, st.session_state.use_cases_page, ROWS_PER_PAGE
        )
except Exception:
    logger.exception("Failed to load use cases")
    st.error(
        "**Unable to load Use Cases.** Ensure the `use_cases`, "
        "`use_case_references`, and `id_sequences` tables are deployed via "
        "Liquibase, then try again."
    )
    st.stop()

if not page_obj.rows:
    st.info("No Use Cases match the current filters.")
else:
    st.markdown(f"**Showing {len(page_obj.rows)} of {page_obj.total_rows} Use Cases**")
    _render_table(data_access, page_obj)

    if page_obj.total_pages > 1:
        pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
        with pcol1:
            if page_obj.has_previous and st.button("← Previous"):
                st.session_state.use_cases_page -= 1
                st.rerun()
        with pcol2:
            st.markdown(
                f"<div style='text-align:center'>Page {page_obj.page} of "
                f"{page_obj.total_pages}</div>",
                unsafe_allow_html=True,
            )
        with pcol3:
            if page_obj.has_next and st.button("Next →"):
                st.session_state.use_cases_page += 1
                st.rerun()
```

> If `st.dialog` is unavailable in the installed Streamlit version, replace the
> `@st.dialog("Use Case")` decorator with rendering the same form inside an
> `st.expander("New / Edit Use Case", expanded=True)` and drop the decorator.

### Step 9 — Register the page in navigation

Edit [app/app.py](../app/app.py). Add the page to `st.navigation`:

```python
    pg = st.navigation(
        [
            st.Page("views/home.py", title="Home"),
            st.Page("views/registry.py", title="Registry"),
            st.Page("views/use_cases.py", title="Use Cases"),
            st.Page("views/preview.py", title="Preview"),
        ]
    )
```

### Step 10 — Tests

Add unit tests under `tests/unit/`. Follow the existing pytest style
(`@pytest.mark.unit`). Create `tests/unit/test_use_cases_mock.py`:

```python
import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import UseCaseFilter
from onepagerapp.permissions import initials_from_user


def _mock() -> MockDataAccess:
    return MockDataAccess(OnePagerDocumentStore("/tmp/onepager-test"))


@pytest.mark.unit
def test_list_excludes_deprecated_by_default():
    da = _mock()
    da.set_use_case_deprecated("UC-001", True, "ZZ")
    page = da.get_use_cases(UseCaseFilter(), page=1, page_size=10)
    ids = [r.use_case_id for r in page.rows]
    assert "UC-001" not in ids


@pytest.mark.unit
def test_include_deprecated_shows_all():
    da = _mock()
    da.set_use_case_deprecated("UC-001", True, "ZZ")
    page = da.get_use_cases(
        UseCaseFilter(include_deprecated=True), page=1, page_size=10
    )
    ids = [r.use_case_id for r in page.rows]
    assert "UC-001" in ids


@pytest.mark.unit
def test_create_returns_new_id_and_persists():
    da = _mock()
    new_id = da.create_use_case(
        persona="P", goal="G", scenario="S",
        decision_enabled="D", priority="High", created_by_initials="XY",
    )
    assert new_id.startswith("UC-")
    assert da.get_use_case(new_id) is not None


@pytest.mark.unit
def test_search_filters_on_persona_and_goal():
    da = _mock()
    page = da.get_use_cases(
        UseCaseFilter(search="risk"), page=1, page_size=10
    )
    assert all(
        "risk" in r.persona.lower() or "risk" in r.goal.lower()
        for r in page.rows
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "user,expected",
    [
        ("alice.brown@company.com", "AB"),
        ("bob_smith", "BS"),
        ("charlie", "CH"),
        (None, "??"),
    ],
)
def test_initials_from_user(user, expected):
    assert initials_from_user(user) == expected
```

Run with: `pytest tests/unit -m unit -q` (or the project's configured test
command — check `pyproject.toml`).

---

## 6. Verification checklist

Work through this after implementing:

- [ ] `pytest tests/unit -m unit` passes.
- [ ] App starts in local-mock mode; the **Use Cases** tab appears in the sidebar.
- [ ] The table lists the two seeded mock Use Cases (UC-001, UC-002).
- [ ] "New Use Case" opens a modal; submitting an empty form shows validation errors.
- [ ] Creating a valid Use Case adds a new row with a `UC-###` id.
- [ ] "Edit" pre-fills the modal; saving updates the row.
- [ ] "Deprecate" hides the row by default; "Show deprecated" reveals it; "Restore" brings it back.
- [ ] Deprecating a Use Case with `reference_count > 0` shows the warning.
- [ ] Search and priority filters narrow the list.
- [ ] Pagination controls appear when there are more than `ROWS_PER_PAGE` rows.
- [ ] No linting errors (`ruff`/project linter) in the new/edited files.

---

## 7. Files touched — summary

**New files**
- `liquibase/bia_meta/onepager_app/ddl/use_cases.sql`
- `liquibase/bia_meta/onepager_app/ddl/use_case_references.sql`
- `liquibase/bia_meta/onepager_app/ddl/id_sequences.sql`
- `app/views/use_cases.py`
- `tests/unit/test_use_cases_mock.py`

**Edited files**
- `liquibase/bia_meta/onepager_app/root.changelog.databricks.yaml` (register 3 DDLs)
- `src/onepagerapp/models.py` (add `UseCase`, `UseCaseFilter`, `UseCasePage`)
- `src/onepagerapp/permissions.py` (add `initials_from_user` + `import re`)
- `src/onepagerapp/data_access/base.py` (5 abstract methods + import)
- `src/onepagerapp/data_access/mock.py` (implement 5 methods + seed data + import)
- `src/onepagerapp/data_access/lakehouse.py` (implement 5 methods + helpers + import)
- `app/app.py` (register the page)

---

## 8. Out of scope (do NOT build here)

- Linking Use Cases into a specific One Pager (writing `use_case_references`) —
  that belongs to the One Pager editor.
- Role-based write permissions (v1 allows any authenticated user; see D7).
- OP-#### / BR-### generation (only UC-### is needed now; the `id_sequences`
  table is seeded for all three, and `_generate_use_case_id` can be generalized
  later).
- Version history / audit UI for Use Cases beyond the audit columns.
```
