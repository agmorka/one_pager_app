# Sample One Pagers for Local Development & Testing

This directory contains sample One Pager YAML documents organized by OP-ID for use in `local-mock` development mode (no Databricks connection required).

## Directory Structure

```
tests/fixtures/sample_one_pagers/
├── OP-0001/                     # Person Master Data (Approved)
│   ├── OP-0001_v0.1.0.yml       # Initial draft (v0.1.0)
│   ├── OP-0001_v0.2.0.yml       # Updated draft (v0.2.0)
│   └── OP-0001_v1.0.0.yml       # Approved version (v1.0.0, current)
└── OP-0002/                     # Order Master Data (In Review)
    └── OP-0002_v0.3.0.yml
```

The current version, product name, owner, and status of each fixture match the corresponding row seeded by `MockDataAccess` (see `_seed_status_rows` in `mock.py`), so the Registry list and each One Pager document stay consistent.

## How It Works

**All versions for one OP-ID live in a single directory:**
- Each version is an immutable YAML file named `<OP-ID>_v<version>.yml` (e.g., `OP-0001_v0.1.0.yml`, `OP-0001_v1.0.0.yml`) — the data product name is **not** in the filename, so a product rename never touches files.
- The current version comes from the Delta `one_pager_status.version` column (authoritative); in `local-mock`, `MockDataAccess` supplies it in place of Delta. There is no `_latest.*` pointer file.
- Whether a version is Draft, In Review, or Approved is determined by the `onePagerStatus` field in the YAML file itself (sourced from Delta in production).

**No separate working/approved folders:**
- Simplifies the directory structure
- All versions are in one place for easy browsing and archival
- Status is determined by Delta table, not file path

## Usage

### Local-Mock Development

When `APP_MODE=local-mock`, the application runs entirely in-memory without Databricks. The `MockDataAccess` layer will eventually load these fixture files (Phase 2 implementation):

```python
# Future usage (Phase 2):
data_access = MockDataAccess()
one_pager = data_access.get_one_pager("OP-0001")  # Loads latest version from OP-0001/
```

### Unit & Integration Tests

Tests can reference these fixtures directly:

```python
import yaml
from pathlib import Path

fixture_path = Path(__file__).parent / "sample_one_pagers" / "OP-0001" / "OP-0001_v0.2.0.yml"
with open(fixture_path) as f:
    one_pager = yaml.safe_load(f)
```

### Running the App in Local-Mock Mode

```bash
cd /workspaces/OnePagerApp/app
streamlit run app.py -- --env APP_MODE=local-mock
```

The app will serve these fixture documents when browsing the Registry and Preview pages.

## Sample One Pagers Included

Every fixture corresponds 1:1 to a row in `_seed_status_rows` (`mock.py`). The current
version, product name, domain, owner, and statuses match on both sides.

| OP-ID | Product | Domain | Type | Current version | One Pager status | Owner |
|---|---|---|---|---|---|---|
| OP-0001 | Person Master Data | Customer | Foundational | 1.0.0 | Approved | Alice Brown |
| OP-0002 | Order Master Data | Sales | Foundational | 0.3.0 | In Review | Bob Smith |

**OP-0001** also retains earlier versions (`v0.1.0`, `v0.2.0`) to demonstrate a complete
draft-through-approval lifecycle; its current version is `v1.0.0`.

The current versions (OP-0001 `v1.0.0`, OP-0002 `v0.3.0`) are complete
`structure_one_pager_v_2.json` documents that pass the strict tier; their `useCases`
match the mock `use_case_references`. The older OP-0001 versions are
`structure_one_pager_v_1.json` documents, used to test schema-version pinning
(`v0.1.0` is an early draft and fails the retention rule).

## Adding More Fixtures

To add a new sample One Pager:

1. Create a new directory:
   ```
   mkdir tests/fixtures/sample_one_pagers/OP-XXXX
   ```

2. Create version files:
   ```
   tests/fixtures/sample_one_pagers/OP-XXXX/OP-XXXX_v0.1.0.yml
   tests/fixtures/sample_one_pagers/OP-XXXX/OP-XXXX_v0.2.0.yml
   ```

3. Ensure YAML content matches the schema it declares in `structureDefinition` (new fixtures: `schemas/structure_one_pager_v_2.json`).

## Implementation Phases

- **Phase 1 (Complete):** Fixtures created locally with simplified structure
- **Phase 2:** `MockDataAccess` updated to load from these fixture files, using its in-memory `version` as the current-version selector
- **Phase 3:** Preview page UI renders loaded One Pagers
- **Phase 4 onwards:** Tests use fixtures for E2E validation

## Notes

- Filenames are `<OP-ID>_v<version>.yml` — the data product name is **not** in the filename
- All versions stored in `<OP-ID>/` directory (no subdirectories like `archived/`, no `working/`/`approved/` split)
- The current version comes from Delta `one_pager_status.version` (from `MockDataAccess` in local-mock) — there is no `_latest.*` pointer
- Status (`Draft`, `In Review`, `Approved`) is stored in the YAML file's `onePagerStatus` field
- In production, status is determined by Delta `one_pager_status` table, not by file path
- Fixture files are version-controlled; updates require code review
- Fixtures are read-only at runtime: in `local-mock` mode, One Pagers created in the app are written to a temporary directory that is read before this folder, never into it
