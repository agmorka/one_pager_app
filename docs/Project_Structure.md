# One Pager Application — Project Structure & Tooling

## 1. Purpose

This document defines the repository layout, dependency management, environment topology, secrets handling, and CI/CD pipeline for the One Pager Application. It implements the technical choices made in [One_Pager_App_Architecture.md](../architecture/One_Pager_App_Architecture.md).

## 2. Repository Layout

The application lives in a single Azure DevOps Git repository. The domain/service layer is an in-repo Python package directory, not a separately versioned/installed package.

```
OnePagerApp/
├── .azuredevops/                   # Azure DevOps configuration
├── .devcontainer/                  # Dev container configuration
│   ├── Dockerfile
│   ├── Dockerfile_liquibase
│   ├── devcontainer.json
│   └── postStartCommand.sh
├── azure-pipelines/                # CI/CD pipeline definitions
│   ├── Branch-CD.yml
│   ├── CD.yml
│   ├── CI.yml
│   ├── config/                     # Environment-specific pipeline config
│   │   ├── ARTIFACTORY.yml
│   │   ├── DEV.yml
│   │   ├── INT.yml
│   │   ├── PRD.yml
│   │   └── UAT.yml
│   └── templates/                  # Reusable pipeline templates
│       ├── approve-env.yml
│       ├── build.yml
│       ├── deploy-dab.yml
│       ├── deploy-liquibase.yml
│       ├── pre-merge-checks.yml
│       ├── semantic-versioning-check.yml
│       ├── docker/
│       │   └── docker-unit-test.yml
│       ├── liquibase/
│       │   ├── docker-prepare-liquibase.yml
│       │   ├── docker-run-liquibase.yml
│       │   ├── get-token.yml
│       │   └── get-warehouse.yml
│       └── scripts/
│           └── validate_tags.py
├── docs/                           # Project documentation
│   ├── Architecture.md
│   ├── Backend_Design.md
│   ├── Data_Model.md
│   ├── Decision_Log.md
│   ├── Dev_Notes.md
│   ├── Project_Structure.md
│   ├── README.md
│   ├── Requirements_and_Scope.md
│   ├── Testing_Strategy.md
│   └── UI_Design.md
├── liquibase/                      # Liquibase changelogs for Delta table provisioning
│   ├── README.md
│   └── bia_meta/
│       ├── run.sh
│       └── onepager_app/
│           ├── liquibase.properties
│           └── root.changelog.databricks.yaml
├── app/                            # Streamlit application — entry point and UI components
│   ├── .streamlit/
│   │   └── config.toml             # BEC theme (UI_Design.md §3); Streamlit is started from app/
│   ├── app.py                      # Streamlit entry point — bootstraps shared services, global error boundary
│   ├── app.yml                     # Databricks App configuration (runtime manifest)
│   ├── adapters/                   # Presentation helpers (theming, etc.)
│   │   ├── __init__.py
│   │   └── theme.py                # BEC theming constants, status badge colors
│   ├── assets/                     # Static assets (images, etc.)
│   └── views/                      # Streamlit page modules
│       ├── registry.py             # Browse/search/filter all One Pagers (landing page)
│       ├── preview.py              # Read-only view of one One Pager
│       ├── editor.py               # Create (and later edit) a One Pager
│       └── use_cases.py            # Use Case registry
├── resources/
│   └── schemas/                    # JSON Schema files (source of truth for validation)
│       └── structure_one_pager_v_1.json
├── src/                            # Core application package and domain layer
│   └── onepagerapp/                # Main application package — pure Python, no Streamlit imports
│       ├── __init__.py
│       ├── __version.py            # Version constant
│       ├── audit.py                # Structured security-event logging
│       ├── config.py               # Configuration and AppConfig model
│       └── data_access/            # Domain/data access layer — pure Python abstraction
│           ├── __init__.py
│           ├── base.py             # DataAccess ABC — contract for all implementations
│           ├── delta.py            # DeltaDataAccess — SQL Connector implementation
│           ├── factory.py          # create_data_access() factory — selects implementation
│           └── mock.py             # MockDataAccess — in-memory pandas fakes for dev/testing
├── tests/
│   ├── __init__.py
│   ├── unit/                       # Pure pytest — no Streamlit or Databricks dependency
│   │   ├── __init__.py
│   │   └── test_sample_unit_test.py
│   └── integration/                # Tests requiring a live Databricks connection or Streamlit AppTest
│       ├── __init__.py
│       └── test_sample_integration_test.py
├── .env.example                    # Local configuration template (copy to .env)
├── .gitattributes
├── .gitignore
├── coverage.xml                    # Test coverage report
├── databricks.yml                  # Databricks Asset Bundle definition
├── pyproject.toml                  # Project metadata, dependencies, tool config (ruff, mypy, pytest)
├── test_results.xml                # Test results report
├── uv.lock                         # Locked dependency versions (managed by uv)
└── README.md                       # Setup, run, test, deploy instructions
```

### Key conventions
- `app/` contains all Streamlit-dependent code: the entry point (`app.py`), UI adapters, views, and the app configuration manifest (`app.yml`).
- `src/onepagerapp/` is the core library: pure Python with no Streamlit imports. It provides configuration, data access abstraction, and domain logic.
- `src/onepagerapp/data_access/` defines a `DataAccess` ABC and multiple implementations (SQL Connector for Databricks, in-memory mock for local dev/testing). The app selects the implementation at runtime based on `APP_MODE` env var.
- Views in `app/views/` only import from `onepagerapp` (config, data_access) — no direct Delta table access, volume I/O, or Git operations in views.
- `app.py` is the composition root: it instantiates shared services (config, data access) once at startup, stores them in `st.session_state`, and wraps view dispatch in a global error boundary that shows user-friendly messages and logs exceptions.
- `resources/schemas/` is the single location for JSON Schema files; validation always reads from here.

## 2b. Separation of Core Logic and UI Code

### What is separated?

**`src/onepagerapp/` — Core library (pure Python)**
- Configuration management (`config.py`)
- Data access abstraction and implementations (`data_access/`)
- Business logic, validation, and workflow rules (future: `domain/`, `services/`)
- **Zero Streamlit imports** — this code runs independently of any UI framework

**`app/` — Streamlit application code**
- Page scripts (`app.py`, `views/registry.py`, `views/preview.py`, `views/editor.py`, `views/use_cases.py`)
- UI components and adapters (`adapters/theme.py`)
- Streamlit-specific session management
- Deployment manifest (`app.yml`)

### Why is this separation important?

**1. Testability**
- Core logic in `src/onepagerapp/` can be unit-tested with `pytest` — **no Streamlit runtime required**
- Data access implementations can be tested with in-memory mocks via the `MockDataAccess` class
- No need for Streamlit's `@st.cache` or `st.session_state` fixtures in unit tests
- Example: [tests/unit/test_sample_unit_test.py](../tests/unit/test_sample_unit_test.py) imports from `onepagerapp.config` and `onepagerapp.data_access` without starting a Streamlit app

**2. Reusability and Independence**
- The `onepagerapp` core package can be imported by other tools or services:
  - Scheduled jobs that run in Databricks Workflows
  - Notebooks for data analysis
  - Future web frontends (Flask, FastAPI, React)
  - CLI tools
- The package's version is managed independently in `__version.py` and published as a wheel in the CI/CD pipeline

**3. UI Replaceability**
- If Streamlit outgrows your needs (e.g., complex form interactions, drag-and-drop), the business logic remains unchanged
- A replacement frontend (Flask, React, etc.) only needs to import from `onepagerapp` and follow the same `DataAccess` contract
- See [Architecture.md § 1 — Streamlit as UI Framework](Architecture.md#1-streamlit-as-ui-framework) for the rationale behind choosing Streamlit and how it remains replaceable

**4. Deployment Flexibility**
- The core package is packaged as a wheel and deployed separately from the Streamlit app
- This enables:
  - Redeploying the app without rebuilding the core (faster iteration)
  - Sharing the core across multiple services (single source of truth)
  - Version control of the core independent of the app UI
- See [Decision_Log.md § 3 — Wheel-based dependency for Databricks Apps](Decision_Log.md#3-publishing-onepagerapp-as-a-wheel-dependency) for deployment details

### Design principles enforced by this separation

| Principle | Implementation |
|---|---|
| **Views are thin** | Page scripts call into `onepagerapp` services; no business logic in views |
| **Pure domain layer** | Validation, workflows, permissions all in `src/onepagerapp/` — testable without Streamlit |
| **Dependency injection** | `app.py` resolves services once and passes them to views via `st.session_state` |
| **Single responsibility** | Core logic has no UI concerns; views have no storage/business logic concerns |

## 3. Dependency Management

| Tool | Purpose |
|---|---|
| `uv` | Package installer and lockfile manager (replaces pip + pip-compile) |
| `pyproject.toml` | Project metadata, dependency declaration, and tool configuration (ruff, mypy, pytest) |
| `uv.lock` | Locked, reproducible dependency versions — committed to the repo |

Core dependencies (expected):
- `streamlit` — UI framework
- `databricks-sdk` — Databricks APIs (workspace, volumes, groups, Git)
- `databricks-sql-connector` — Delta table access via SQL
- `jsonschema` — Schema validation
- `pyyaml` — YAML read/write
- `pydantic` — Typed models for internal use (optional, for structured access to schema sections)
- PDF export rendering: no library; `onepagerapp/pdf.py` writes the PDF with the standard library ([Decision_Log.md](Decision_Log.md) §18)
- `structlog` — Structured logging for security-event audit trail

Dev dependencies:
- `ruff` — linting and formatting
- `mypy` — static type checking
- `pytest` — unit and integration tests

> **Note:** Dependency vulnerability scanning in CI is handled via **GitHub Advanced Security**.
>
> `uv` is installed globally as a system tool (not a project dependency). The README documents installation instructions.

## 4. Environment Topology

Four Databricks workspaces, one per environment:

| Environment | Workspace | Purpose |
|---|---|---|
| DEV | Development workspace | Developer testing, local iteration |
| INT | Integration/test workspace | Automated integration tests, pre-merge validation |
| UAT | User Acceptance Testing workspace | Stakeholder validation before production |
| PRD | Production workspace | Live application |

Each environment has its own:
- **Unity Catalog catalog** — a dedicated catalog per environment (e.g. `dev_one_pager`, `prd_one_pager`), providing full isolation of schemas, tables, and volumes from other applications. Actual naming follows BEC's catalog naming convention.
- **Unity Catalog schema** within that catalog (e.g. `app`) for all Delta tables.
- **Unity Catalog external volume** for in-progress YAML files.
- **Unity Catalog groups** for RBAC (Owner/SME, Approver, Admin — group names per BEC's naming convention, replicated per workspace where needed).
- **Databricks App deployment** (separate app instance per workspace).

The Databricks Asset Bundle (`databricks.yml`) defines targets for each environment, parameterizing catalog/schema/volume names and app configuration so the same codebase deploys to any environment without code changes.

```yaml
# databricks.yml (simplified)
bundle:
  name: one-pager-app

targets:
  dev:
    workspace:
      host: https://dev-workspace.databricks.com
    variables:
      catalog: dev_one_pager
      schema: app
      volume_path: /Volumes/dev_one_pager/app/one_pagers
      git_target_branch: dev
  int:
    workspace:
      host: https://int-workspace.databricks.com
    variables:
      catalog: int_one_pager
      schema: app
      volume_path: /Volumes/int_one_pager/app/one_pagers
      git_target_branch: int
  uat:
    workspace:
      host: https://uat-workspace.databricks.com
    variables:
      catalog: uat_one_pager
      schema: app
      volume_path: /Volumes/uat_one_pager/app/one_pagers
      git_target_branch: uat
  prd:
    workspace:
      host: https://prd-workspace.databricks.com
    variables:
      catalog: prd_one_pager
      schema: app
      volume_path: /Volumes/prd_one_pager/app/one_pagers
      git_target_branch: main
```

(Actual workspace URLs, catalog names, and volume paths are placeholders — to be filled in during provisioning.)

## 5. Secrets & Configuration

- **No secrets in source code.** Webhook URLs, service principal credentials, Git PATs (for PR creation), and any other sensitive values are stored in Databricks secret scopes, not in `databricks.yml`, `pyproject.toml`, or any committed file.
- **Environment-specific configuration** (catalog/schema names, volume paths, Git repo URL for the approved-One-Pager repository, Git target branch) is injected as **app environment variables** by the Databricks Asset Bundle deployment (the bundle target's `env` block maps bundle variables to env vars the app reads at runtime via `src/onepagerapp/config.py`). Key env vars: `ONE_PAGER_CATALOG`, `ONE_PAGER_SCHEMA`, `ONE_PAGER_VOLUME_PATH`, `ONE_PAGER_GIT_REPO_URL`, `ONE_PAGER_GIT_TARGET_BRANCH`, `ONE_PAGER_SECRET_SCOPE`. See `.env.example` for the full list.
- **The JSON Schema file** (`resources/schemas/structure_one_pager_v_1.json`) is committed to the repo and deployed with the app — it is configuration in the sense that it drives validation, but it is not secret.

## 6. CI/CD Pipeline (Azure Pipelines)

```mermaid
flowchart LR
    PR[Pull Request] --> LINT[ruff check + format]
    LINT --> TYPE[mypy]
    TYPE --> TEST[pytest unit/]
    TEST --> VALIDATE[databricks bundle validate]
    VALIDATE --> DEV_DEPLOY[Deploy to DEV]
    DEV_DEPLOY --> DEV_LB[Liquibase migrate DEV]
    DEV_LB --> INT_GATE[Manual gate]
    INT_GATE --> INT_DEPLOY[Deploy to INT]
    INT_DEPLOY --> INT_LB[Liquibase migrate INT]
    INT_LB --> INT_TEST[Integration tests]
    INT_TEST --> UAT_GATE[Manual gate]
    UAT_GATE --> UAT_DEPLOY[Deploy to UAT]
    UAT_DEPLOY --> UAT_LB[Liquibase migrate UAT]
    UAT_LB --> PRD_GATE[Manual gate]
    PRD_GATE --> PRD_DEPLOY[Deploy to PRD]
    PRD_DEPLOY --> PRD_LB[Liquibase migrate PRD]
```

### Pipeline stages

| Stage | Trigger | What it does |
|---|---|---|
| **PR validation** | Every pull request | `ruff check` + `ruff format --check`, `mypy`, `pytest tests/unit/`, `databricks bundle validate`. Dependency vulnerability scanning is handled separately via GitHub Advanced Security. |
| **Deploy to DEV** | PR merge to `main` | `databricks bundle deploy -t dev`, then Liquibase migrate |
| **Deploy to INT** | Manual approval gate | `databricks bundle deploy -t int`, then Liquibase migrate, then integration tests |
| **Deploy to UAT** | Manual approval gate | `databricks bundle deploy -t uat`, then Liquibase migrate |
| **Deploy to PRD** | Manual approval gate | `databricks bundle deploy -t prd`, then Liquibase migrate |

- Unit tests run on every PR and must pass before merge.
- Integration tests run in INT after deployment (they need a live Databricks connection).
- Manual approval gates between INT → UAT → PRD ensure human review before each promotion.
- The pipeline uses a service principal with scoped permissions per target workspace.

## 7. Git Branching Strategy

- **`main`** is the deployment source — every merge to `main` triggers a DEV deploy.
- **Feature branches** (`feature/<short-description>`) are used for all development; merged to `main` via pull request with required PR validation passing.
- **No long-lived environment branches** (no `dev`/`uat`/`prd` branches) — the same `main` code is deployed to all environments via the bundle targets, differing only in configuration.
- This is the **application repo**. The separate **One Pager registry repo** (where approved YAML files are merged on approval, per architecture doc §6) follows its own branching/protection rules and is not covered here.

## 8. Delta Table Provisioning

Delta tables (change log, reviews, locks, use case registry, status metadata, ID sequences) are provisioned using **Liquibase** changelogs stored in the `liquibase/` directory. Liquibase runs **immediately after** `databricks bundle deploy` in each environment (before integration tests), ensuring tables exist before the app handles its first request.

ID-sequence tables are seeded with initial counter values (starting at 1) as part of their Liquibase changeset, so the app can generate IDs from first use without manual intervention.

Table definitions will be specified in Step 4 (data model design) and translated into Liquibase changesets.

## 9. Local Development

To run the app locally for development/debugging:

1. Install `uv` globally (see README for instructions).
2. Run `uv sync` to install all dependencies from `uv.lock`.
3. Copy `.env.example` to `.env` and fill in the required values (pointing at the DEV workspace catalog/schema/volume).
4. Run `streamlit run app/app.py` from the project root.

A live Databricks workspace connection (DEV) is required even for local development, since the app reads/writes Delta tables and UC volumes. Unit tests (`pytest tests/unit/`) run without any Databricks connection — they test pure domain logic only.

## 10. Open Items

| # | Item | Notes |
|---|---|---|
| 1 | Actual catalog/schema/volume names per environment | Must follow BEC's Unity Catalog naming convention. |
| 2 | Actual UC group names per environment | Must follow BEC's group naming convention (carried over from architecture doc). |
| 3 | Service principal setup: CI/CD pipeline deployment | Separate from the app's runtime identity. Needed for `databricks bundle deploy`. |
| 4 | Service principal setup: app runtime identity | The app's own identity for volume/Delta/Git access at runtime. |
| 5 | Git PAT or service connection for PR creation | The app needs credentials to create PRs in the One Pager registry repo on approval. Stored in a Databricks secret scope. |
| 6 | Azure Pipelines service connection to Databricks workspaces | Needed for the CI/CD pipeline to deploy bundles. |
| 7 | Branch protection rules on the One Pager registry repo | Carried over from architecture doc — determines whether auto-merge is possible on approval PRs. |
| 8 | ~~PDF export library choice~~ | **Resolved:** neither `weasyprint` nor `fpdf2`; a small built-in writer (`onepagerapp/pdf.py`), see [Decision_Log.md](Decision_Log.md) §18. |
| 9 | Schema evolution strategy | How the app handles documents written against an older schema version (carried over from architecture doc open item #5; to be resolved in Step 4). |
