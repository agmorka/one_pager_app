
# One Pager Application

A Streamlit-based web application for managing Data Product One Pagers through their complete lifecycle, including creation, review, approval, and publishing workflows.

## Table of Contents

- [Running Locally](#running-locally)
  - [Prerequisites](#prerequisites)
  - [Option 1: Run with Streamlit](#option-1-run-with-streamlit)
  - [Option 2: Run with Databricks Apps](#option-2-run-with-databricks-apps)
- [Running Tests](#running-tests)
  - [Install Test Dependencies](#install-test-dependencies)
  - [Run All Tests](#run-all-tests)
  - [Run Specific Test Suites](#run-specific-test-suites)
  - [Run Tests with Coverage Report](#run-tests-with-coverage-report)
- [Code Quality with Ruff](#code-quality-with-ruff)
  - [Format Python Files](#format-python-files)
  - [Check and Fix Issues Automatically](#check-and-fix-issues-automatically)
- [Type Checking with Mypy](#type-checking-with-mypy)
  - [Run Type Checking](#run-type-checking)
- [Documentation](#documentation)

## Running Locally

### Prerequisites
- Python 3.11 or higher
- `uv` package manager installed
- For Databricks Apps: Databricks CLI installed

### App Modes

The application supports three modes controlled by the `APP_MODE` environment variable:

| Mode | Description |
|---|---|
| `local-mock` | Uses in-memory fake data. No Databricks connection needed. Best for UI/logic development. |
| `local-integration` | Connects to real Delta tables via a SQL warehouse. Requires Databricks CLI auth. |
| `databricks` (default) | Used when deployed to Databricks. Auth is automatic via user identity. |

### User Identity

Usernames look like `x0wadm@becoc001.onmicrosoft.com`. The app derives the user's **corporate initials** (`X0W`), which are the key for all permissions, from the username. The format is configuration (see [docs/Architecture.md](docs/Architecture.md) §4):

| Setting | Default | Meaning |
|---|---|---|
| `ONE_PAGER_APP_USER_DOMAINS` | `becoc001.onmicrosoft.com` | Accepted username domains, comma-separated. |
| `ONE_PAGER_APP_USERNAME_SUFFIXES` | `adm` | Suffixes stripped from the user part. An empty entry means "no suffix" (`adm,` accepts `x0wadm@` and `x0w@`). |
| `ONE_PAGER_APP_INITIALS_PATTERN` | `^[A-Z0-9]{3}$` | Valid initials after upper-casing. Also used for Owner/SME initials. |
| `ONE_PAGER_APP_MOCK_USER` | `lduadm@becoc001.onmicrosoft.com` | Signed-in user in `local-mock` mode. |
| `ONE_PAGER_APP_MOCK_USER_NAME` | `Local Dev User` | Name shown for the `local-mock` user (the other modes read the name from the workspace directory). |

A username that does not match these settings gets no initials: the app shows an "Access denied" page instead of the app, and logs the refusal. Deployed, the username comes only from the Databricks Apps proxy headers.

The name shown in the app (sidebar, change log, YAML, comments, PDF) is read once per session from the workspace directory (SCIM `Me`); when it is not available, the initials are shown.

Roles come from group membership, checked once per session (see [docs/Architecture.md](docs/Architecture.md) §4). Every signed-in user is a Viewer. `{env}` in a group name is replaced with the environment (`DEV`, `INT`, `TST`, `UAT`, `PRD`):

| Setting | Default | Role |
|---|---|---|
| `ONE_PAGER_APP_GROUP_OWNER_SME` | `BEC_BECOC001_LHX_{env}_DataPlatEng` | Owner/SME: may create One Pagers and manage Use Cases. |
| `ONE_PAGER_APP_GROUP_APPROVER` | `BEC_BECOC001_LHX_{env}_DataPlatEng` | Approver: reviews One Pagers. |
| `ONE_PAGER_APP_GROUP_ADMIN` | `BEC_BECOC001_LHX_{env}_DataPlatEng` | Admin: uses the Admin page. |

The defaults are the interim Data Platform Engineering group until the dedicated role groups exist; switching is a configuration change only.

In `local-mock` mode the memberships come from `ONE_PAGER_APP_MOCK_GROUPS` (comma-separated group names, `{env}` allowed). The default is the interim group, so the mock user has every role; set it to an empty value to try the app as a Viewer, or to one group to try one role.

**Data access.** Deployed, Delta reads run as the signed-in user (their groups' Unity Catalog grants apply) and all writes run as the app's service principal; the app records the user's initials on every write (see [docs/Architecture.md](docs/Architecture.md) §8). The app needs the user API scopes `sql` and `iam.current-user:read`. In `local-integration` both use your Databricks CLI profile.

### All Settings

Every setting is an environment variable read by `AppConfig` ([src/onepagerapp/config.py](src/onepagerapp/config.py)). Deployed, they come from [app/app.yml](app/app.yml).

| Setting | Default | Meaning |
|---|---|---|
| `APP_MODE` | `databricks` | `local-mock`, `local-integration` or `databricks` (see App Modes). |
| `ONE_PAGER_APP_VOLUME_PATH` | — (required) | Folder of the One Pager YAML files: the registry volume (`/Volumes/<catalog>/onepager_app/one_pager_registry`) or, in `local-mock`, a local folder such as `tests/fixtures/sample_one_pagers`. |
| `ONE_PAGER_APP_DATABRICKS_CATALOG` | `dev_bia_meta` | Catalog of the app tables. |
| `ONE_PAGER_APP_DATABRICKS_SCHEMA` | `onepager_app` | Schema of the app tables. |
| `DATABRICKS_WAREHOUSE_ID` | — | SQL warehouse for the Delta tables (any value in `local-mock`). |
| `ONE_PAGER_APP_ENVIRONMENT` | derived | `DEV`, `INT`, `TST`, `UAT` or `PRD`, for the sidebar badge and `{env}` in group names. When empty: the catalog prefix of the registry volume, else of the catalog setting, else `DEV`. |
| `ONE_PAGER_APP_LOCK_TTL_SECONDS` | `1800` | How long an edit lock lives after the editor's last activity. |
| `ONE_PAGER_APP_USER_DOMAINS`, `ONE_PAGER_APP_USERNAME_SUFFIXES`, `ONE_PAGER_APP_INITIALS_PATTERN` | see User Identity | Username format. |
| `ONE_PAGER_APP_MOCK_USER`, `ONE_PAGER_APP_MOCK_USER_NAME`, `ONE_PAGER_APP_MOCK_GROUPS` | see User Identity | The `local-mock` user, its name and groups. |
| `ONE_PAGER_APP_GROUP_OWNER_SME`, `ONE_PAGER_APP_GROUP_APPROVER`, `ONE_PAGER_APP_GROUP_ADMIN` | see User Identity | Role groups. |
| `CLOUD_ROLE_NAME` | `OnePagerApp` | Service name in logs and telemetry. |

### Option 1: Run with Databricks Apps (recommended)

```bash
cd app/
databricks apps run-local --profile dev \
   --env APP_MODE=local-mock \
   --env ONE_PAGER_APP_VOLUME_PATH=/tmp/vol \
   --env DATABRICKS_WAREHOUSE_ID=abc123
```

Open the URL shown in the terminal (default: `http://localhost:8001`).

To connect to real tables instead:

```bash
cd app/
databricks apps run-local --profile dev \
   --env APP_MODE=local-integration \
   --env ONE_PAGER_APP_VOLUME_PATH=/Volumes/dev_bia_meta/onepager_app/one_pager_registry \
   --env DATABRICKS_WAREHOUSE_ID=4efe1f3d3f86e320   
```

### Option 2: Run with Streamlit directly

1. Install dependencies:
   ```bash
   uv sync
   ```

2. Mock mode (no Databricks connection):
   ```bash
   APP_MODE=local-mock DATABRICKS_WAREHOUSE_ID=abc123 ONE_PAGER_APP_VOLUME_PATH=/tmp/vol uv run streamlit run app/app.py
   ```

3. Integration mode (connects to real tables in DEV environment):
   ```bash
   APP_MODE=local-integration \
     DATABRICKS_CONFIG_PROFILE=dev \
     DATABRICKS_WAREHOUSE_ID=4efe1f3d3f86e320 \
     ONE_PAGER_APP_VOLUME_PATH=/Volumes/dev_bia_meta/onepager_app/one_pager_registry \
     uv run streamlit run app/app.py
   ```

4. Open your browser and navigate to `http://localhost:8501`.

### Databricks CLI Authentication

For `local-integration` mode (and for deploying), you need a Databricks CLI profile pointing to your workspace.

1. Create the `dev` profile:
   ```bash
   databricks auth login --host https://adb-7405612109850616.16.azuredatabricks.net --profile dev
   ```
   This opens a browser for OAuth login and saves the profile to `~/.databrickscfg`.

2. Verify it works:
   ```bash
   databricks auth env --profile dev
   ```

To re-authenticate later (e.g. after token expiry), run the same `databricks auth login` command again.

## Pre-merge Checklist

Before pushing to a remote branch, verify all of the following pass with zero errors:

1. [Format Python files](#format-python-files)
2. [Check and fix lint issues](#check-and-fix-issues-automatically)
3. [Run type checking](#run-type-checking)
4. [Run unit tests](#run-specific-test-suites)
5. [Run the app locally](#option-1-run-with-databricks-apps-recommended) in mock mode

## Deploying to Databricks

### Infrastructure Setup

The app's Unity Catalog objects (SQL warehouse, volume, service principals, access grants) are provisioned and managed via Terraform rather than through the deployment bundle. This approach was also followed for `one-pager-app`.See [docs/One_Pager_App_Infrastructure_Setup.md](docs/One_Pager_App_Infrastructure_Setup.md) for details.

### Prerequisites

- Databricks CLI installed and configured with your workspace credentials
- Access to a Databricks workspace
- Appropriate permissions to deploy apps/bundles in your workspace

### Deploy from Local to Databricks

To deploy the application to Databricks from your local machine:

0. Create connection profile for dev workspace:
   ```bash
   databricks auth login
   ```

2. In project root folder deploy using Databricks Bundles to development environment:
   ```bash
   databricks bundle deploy -t local -p dev
   ```

3. Run the deployed app:
   ```bash
   databricks bundle run one-pager-app -t local -p dev
   ```

4. Access the application through your Databricks workspace

#### ⚠️ Important: Stop the App When Not in Use

**Always stop the application after you finish using it** to avoid unnecessary resource consumption and charges:

```bash
databricks bundle run one-pager-app -t local -p dev --stop
```

Or manually stop it through the Databricks workspace UI by navigating to Apps and terminating the running instance.

## Running Tests

The project includes unit and integration tests using pytest with coverage reporting.

### Install Test Dependencies

```bash
uv sync --group dev
```

### Run All Tests

```bash
uv run pytest
```

### Run Specific Test Suites

Run only unit tests:
```bash
uv run pytest tests/unit
```

Run only integration tests:
```bash
uv run pytest tests/integration
```

### Run Tests with Coverage Report

```bash
uv run pytest --cov
```

This generates a coverage report showing how much of the code is covered by tests.

## Code Quality with Ruff

The project uses ruff for code formatting and linting.

### Format Python Files

This will format all Python files in the src directory according to ruff's formatting rules:

```bash
uv run ruff format src/
```

### Check and Fix Issues Automatically

```bash
uv run ruff check --fix src/
```

## Type Checking with Mypy

The project uses mypy for static type checking.

### Run Type Checking

```bash
uv run mypy --config-file pyproject.toml
```

This performs static type checking on the entire project using the configuration defined in `pyproject.toml`.

## Documentation

For detailed information about the application, see the [docs](docs/) folder.