
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

A username that does not match these settings gets no initials and cannot edit or manage anything.

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

Start Streamlit from the `app/` directory so it picks up the BEC theme in `app/.streamlit/config.toml`.

1. Install dependencies:
   ```bash
   uv sync
   ```

2. Create your local configuration from the template and adjust it if needed (`.env` is git-ignored):
   ```bash
   cp .env.example .env
   ```
   The template runs in mock mode against the sample One Pagers in `tests/fixtures/sample_one_pagers`. Every variable is described in `.env.example`.

3. Run the app:
   ```bash
   cd app
   uv run --env-file ../.env streamlit run app.py
   ```

4. For integration mode (real tables in the DEV environment), set these values in `.env`:
   ```bash
   APP_MODE=local-integration
   DATABRICKS_CONFIG_PROFILE=dev
   DATABRICKS_WAREHOUSE_ID=4efe1f3d3f86e320
   ONE_PAGER_APP_VOLUME_PATH=/Volumes/dev_bia_meta/onepager_app/one_pager_registry
   ```

   In `local-mock` mode the signed-in user is `ONE_PAGER_APP_MOCK_USER` (default `lduadm@becoc001.onmicrosoft.com`, initials `LDU`). It goes through the same username parsing as a real login, so it must match `ONE_PAGER_APP_USER_DOMAINS` and `ONE_PAGER_APP_USERNAME_SUFFIXES`; otherwise the app shows "Access denied". To try the app as a corporate user, set for example `ONE_PAGER_APP_MOCK_USER=x0wadm@becoc001.onmicrosoft.com`.

5. Open your browser and navigate to `http://localhost:8501`. The app opens on the Registry page, and the sidebar shows the environment badge (`ONE_PAGER_APP_ENVIRONMENT`, or derived from the catalog prefix).

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