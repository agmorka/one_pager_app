# Databricks Development Notes

Practical notes and gotchas discovered during development. Intended as a reference for anyone maintaining or extending this codebase.

## Table of Contents

- [DAB Configuration](#dab-configuration)
  - [Why apps require an explicit name but jobs don't](#why-apps-require-an-explicit-name-but-jobs-dont)
  - [Relationship between databricks.yml, one-pager-app.yml, and app.yml](#relationship-between-databricksyml-one-pager-appyml-and-appyml)
  - [Where to place app.yml](#where-to-place-appyml)
  - [User authorization scopes and stale OAuth tokens](#user-authorization-scopes-and-stale-oauth-tokens)
- [Local Development (`run-local`)](#local-development-run-local)
  - [What `--prepare-environment` does](#what---prepare-environment-does)
  - [Where the app source comes from, depending on how you run it](#where-the-app-source-comes-from-depending-on-how-you-run-it)
  - [How dependencies are installed for the deployed app (CI/CD)](#how-dependencies-are-installed-for-the-deployed-app-cicd)
- [Deployment and Monitoring](#deployment-and-monitoring)
  - [Checking app logs and status](#checking-app-logs-and-status)
  - [Troubleshooting deployment errors](#troubleshooting-deployment-errors)
- [Streamlit](#streamlit)
  - [st.set_page_config() must be called exactly once as the first Streamlit command](#stset_page_config-must-be-called-exactly-once-as-the-first-streamlit-command)
  - [Choosing between pages and app_pages](#choosing-between-pages-and-app_pages)

---

## DAB Configuration

### Why apps require an explicit `name` but jobs don't

In jobs, the `name` defaults to the resource key (e.g., `resources.jobs.my_job` → name is `"my_job"`). Apps work differently — the `name` field is a required API parameter for `POST /api/2.0/apps` and serves as the app's unique URL slug (e.g., `https://<workspace>.databricksapps.com/<name>`). There is no automatic fallback from the resource key, so it must be specified explicitly.

### Relationship between `databricks.yml`, `one-pager-app.yml`, and `app.yml`

These three files form a chain: `databricks.yml` → `one-pager-app.yml` → `app.yml`.

- **`databricks.yml`** — DAB root config. Defines the bundle name, variables, and targets. Uses `include: resources/apps/*.yml` to pull in resource definitions.
- **`resources/apps/one-pager-app.yml`** — App resource definition. Declares the app under `resources.apps.one-pager-app`, sets `source_code_path` pointing to the source directory, and provides the app name/description for the `POST /api/2.0/apps` API call.
- **`app.yml`** (project root) — Databricks Apps runtime manifest. Tells the platform **how to run** the app inside its container: the `command` (`streamlit run src/onepagerapp/app.py`) and runtime environment variables.

**How Databricks Apps runs the application:**

1. `databricks bundle deploy` reads `databricks.yml`, resolves includes, finds the app resource, and uploads everything under `source_code_path` plus the root `app.yml` to the workspace.
2. DAB calls `POST /api/2.0/apps` with the app `name`, creating a managed app entry.
3. Databricks spins up a serverless container. Source code is mounted at `/app/python/source_code` (hence the `PYTHONPATH` env var in `app.yml`).
4. The platform reads `app.yml` and executes the `command` array. Environment variables from `env` are injected into the container.
5. The app is exposed at `https://<workspace>.databricksapps.com/<name>` — Databricks handles TLS, authentication (via Unity Catalog identity), and HTTP proxying to the Streamlit process.

### `source_code_path` must use the `/Workspace` prefix

The `source_code_path` field in the app resource definition must be an absolute workspace path starting with `/Workspace`. The Databricks Apps API (`POST /api/2.0/apps/<name>/deployments`) validates this and returns `INVALID_PARAMETER_VALUE: "Source code path must be a valid workspace path."` if the prefix is missing.

DAB syncs bundle files to `{root_path}/files/` in the workspace. Because `root_path` resolves to `/LakehouseX/OnePagerApp` (from the `directory_path` variable), the correct `source_code_path` is:

```yaml
source_code_path: /Workspace${var.directory_path}/files/src
# resolves to: /Workspace/LakehouseX/OnePagerApp/files/src
```

**Why relative paths don't work:** DAB's `TranslatePaths` mutator converts relative paths (e.g., `../../src`) to workspace paths, but as of DAB CLI 1.2.1 it does not prepend the `/Workspace` prefix for the app `source_code_path` field. The resulting path (e.g., `/LakehouseX/OnePagerApp/files/src`) is rejected by the API.

### Where to place `app.yml`

`app.yml` must be at the **root of the directory specified by `source_code_path`** in the app resource definition. The Databricks Apps platform looks for it at the top level of the uploaded source — not in the project root or any arbitrary location.

In this project, `source_code_path` points to `src/onepagerapp`, so `app.yml` belongs in `src/onepagerapp/app.yml`. Placing it in the project root only works if `source_code_path` is set to `.` (the project root), which would upload the entire repository — including tests, docs, and CI configs — to the workspace unnecessarily.

---

## Streamlit

### `st.set_page_config()` must be called exactly once as the first Streamlit command

`st.set_page_config()` must be the very first Streamlit command in the app — before `st.sidebar`, `st.navigation`, or any other `st.*` call. It can only be called once per app page; calling it again (e.g., in a page view loaded via `st.navigation`) raises `StreamlitAPIException`.

In a multi-page app using `st.navigation`/`st.Page`, call `set_page_config()` at the top of the entry point (`app.py`) and **do not** call it in individual page files (`views/Home.py`, `views/Registry.py`, etc.).

### Choosing between `pages` and `app_pages`

Use Streamlit's reserved `pages/` directory for a simple, automatically discovered multipage app. Place it next to the entry point, add one top-level `.py` file for each page, and Streamlit creates the sidebar navigation. Page filenames determine the generated labels, URLs, and ordering. This is a good fit when every user should see the same fixed set of pages and filename-based navigation is sufficient.

Do not use `pages/` when the application needs explicit navigation control: custom titles or icons, page groups, a custom navigation position, conditional pages based on a user's role or state, or pages implemented as functions. In that model, define pages with `st.Page()` and register them once through `st.navigation()` in the entry point. Calling `st.navigation()` makes Streamlit ignore `pages/`, so the two models cannot be used together.

This project uses the explicit model. Its page modules are kept in `app_pages/` to avoid Streamlit's reserved automatic-discovery name. In `app.py`, the router registers each module by its path:

```python
pg = st.navigation(
  [
    st.Page("app_pages/home.py", title="Home"),
    st.Page("app_pages/registry.py", title="Registry"),
  ]
)
pg.run()
```

Adding a page therefore requires both a module under `app_pages/` and an explicit `st.Page(...)` registration in `app.py`. Rename `app_pages/` to `pages/` only when removing this router and deliberately switching to Streamlit's directory-based model.

---

## DAB Configuration

### User authorization scopes and stale OAuth tokens

`home.py` queries the SQL warehouse on behalf of the signed-in user, using the token Databricks forwards in the `x-forwarded-access-token` header. For this to work, the app resource must request the `sql` scope via `user_api_scopes` in `resources/apps/one-pager-app.yml`:

```yaml
user_api_scopes:
  - sql
```

The identity design (Architecture §4) also needs `iam.current-user:read`, used to read the signed-in user's first name and surname from the SCIM `Me` endpoint:

```yaml
user_api_scopes:
  - sql
  - iam.current-user:read
```

Only reads use the user's token; writes use the app's service principal (Architecture §8). The same re-authentication gotcha below applies when a scope is added.

If the `sql` scope is missing (or not yet granted), `sql.connect()` fails with:

```
RequestError: Error during request to server: : Provided OAuth token does not have required scopes: sql.
```

**Gotcha:** adding/changing `user_api_scopes` does not retroactively upgrade tokens already issued to a user's existing browser session. Even after the app resource is redeployed and the new scope shows up correctly in the app's Authorization settings, a user who opened the app *before* the scope was added will keep hitting this error — their cached session token was minted under the old scope grant.

**Fix:** the affected user must fully re-authenticate — sign out of the workspace and back in, or open the app in a new incognito/private window. A plain page refresh is not enough, since the browser session/token is independent of the app deployment.

If a fresh incognito session still fails, check:
- The app's **Deployments** tab to confirm a deployment happened *after* the scope change (in this repo's CD pipeline, an app is only redeployed/restarted for environments listed in the `AppsToStart` parameter passed to `templates/deploy-dab.yml` — see [CD.yml](../azure-pipelines/CD.yml)).
- Workspace **Settings → Development → Restrict OAuth scopes for apps to selected values** — if scopes are restricted to an allowlist, confirm `sql` is included.

---

## Local Development (`run-local`)

`databricks apps run-local` (run from `app/`, so it picks up `app/app.yml` by default) starts the app the same way Databricks Apps does in production — reading the `command` and `env` from `app.yml`.

### What `--prepare-environment` does

- Creates an **isolated virtual environment inside the app directory**, at `app/.venv` (uv-managed — the flag requires `uv` to be installed, and fails without it).
- Populates that venv with the same baseline package set the Databricks Apps runtime provides by default (`streamlit`, `pandas`, `plotly`, `mlflow`, `databricks-sql-connector`, `gradio`, `dash`, `flask`, etc.), then installs `app/requirements.txt` on top of it — which is what actually pulls in `onepagerapp` (resolved from the local wheel in `app/dist` via the `--find-links ./dist` line in that file).
- Without this flag, `run-local` skips environment setup entirely and just runs the `command` from `app.yml` using whatever Python environment is already active in your shell — it does **not** read `app/requirements.txt`.
- `app/.venv` ships its own `.gitignore`, so it never needs to be added to the repo's ignore rules manually.
- Rebuilding is on you: rerun with `--prepare-environment` any time `app/requirements.txt` changes or a new wheel is built into `app/dist` (e.g. after `uv build --wheel --out-dir app/dist`) — it is not automatically detected.

**Gotcha:** installing `app/requirements.txt` still needs network access to the private index configured in `pyproject.toml` (`[[tool.uv.index]]`), because `onepagerapp`'s own dependencies (`databricks-sdk`, `pydantic`, `streamlit`, ...) must resolve from there even though the `onepagerapp` wheel itself is local. In an offline/sandboxed environment, the baseline packages still install and Streamlit still starts, but `onepagerapp` can silently fail to install — this only surfaces later as `ModuleNotFoundError: No module named 'onepagerapp'` when a page actually loads.

### Where the app source comes from, depending on how you run it

| How you run it | Environment used | Source of the `onepagerapp` package |
|---|---|---|
| `streamlit run app.py` directly, or `run-local` **without** `--prepare-environment` | Whatever Python/venv is already active on `$PATH` | In the devcontainer, that's `/home/vscode/.venv`, set up by `.devcontainer/postStartCommand.sh` (`uv sync --link-mode=copy`), which installs `onepagerapp` **editable**, pointing straight at `src/onepagerapp`. Source edits are picked up immediately — no rebuild needed. |
| `run-local --prepare-environment` | Fresh venv at `app/.venv` | Installed from `app/requirements.txt`, which resolves `onepagerapp` from the wheel in `app/dist`. Source changes require rebuilding the wheel and rerunning with `--prepare-environment`. |
| Deployed to Databricks Apps | Managed container built from `source_code_path` | Installed from `app/requirements.txt`, generated per-deploy by the CD pipeline to reference the wheel in a Unity Catalog volume (not `app/dist` — see below). |

The `app.py`/`views/`/`adapters/` files themselves are never packaged — they're read directly from the source directory in every case (`app/` locally, or whatever `source_code_path` uploads in production). Only the `onepagerapp` package (`src/onepagerapp`: config, data access) is installed as a dependency, and *which copy* of it you get (editable source vs. built wheel) depends on the run method above.

### Environment variables with `valueFrom` need explicit `--env` overrides during `run-local`

In `app.yml`, environment variables can be defined in two ways:

- **`value`** — hardcoded value, used directly when running locally or deployed.
- **`valueFrom`** — reference to a Databricks resource (e.g., `one-pager-registry-volume` or `serverless-starter-warehouse`).

During `run-local`, `valueFrom` references **do not resolve** — the platform is not managing the resources, so there is nothing to look them up from. That's why the README examples override these variables with explicit `--env` flags:

```bash
databricks apps run-local --profile dev \
   --env APP_MODE=local-integration \
   --env ONE_PAGER_APP_VOLUME_PATH=/Volumes/dev_bia_meta/onepager_app/one_pager_registry \
   --env DATABRICKS_WAREHOUSE_ID=4efe1f3d3f86e320
```

When deployed to Databricks Apps, the `valueFrom` references are resolved at runtime by the platform.

### How dependencies are installed for the deployed app (CI/CD)

The deployed app never uses `app/dist` — that's only for local `--prepare-environment` runs. Instead, the CD pipeline ([deploy-dab.yml](../azure-pipelines/templates/deploy-dab.yml)) wires up `onepagerapp` as follows:

1. `templates/build.yml` builds the wheel (`uv build`) and publishes it as a pipeline artifact.
2. The "Copy wheel to shared volume" step downloads the wheel from the workspace path DAB uploaded it to (`databricks workspace export-dir` — `databricks fs` can't read `/Workspace` paths directly) and pushes it into a Unity Catalog volume (`databricks fs cp`), replacing any previously deployed `.whl` files there.
3. The "Generate app/requirements.txt" step reads the current `VERSION` from `__version.py` and writes the exact wheel path (e.g. `/Volumes/dev_bia_meta/onepager_app/one_pager_registry/wheels/onepagerapp-0.1.6-py3-none-any.whl`) into `app/requirements.txt`.
4. `databricks apps deploy` triggers the platform's own `BUILD` phase, which runs `pip install -r requirements.txt` inside an isolated container and installs straight from that volume path.

**Gotchas:**

- The `BUILD` container can only reach a Unity Catalog volume if it's bound as an app `resource` (see [Decision_Log.md](Decision_Log.md), #4) — it cannot install from arbitrary `/Workspace` paths, and pointing `--find-links`/`--index-url` at a volume directory is silently ignored (`WARNING: Location '...' is ignored: it is neither a file nor a directory.`).
- `requirements.txt` cannot reference environment variables — the wheel path must be a literal, hard-coded string, which is why the pipeline generates the file fresh on every deploy instead of committing it.
- The app's `.venv` persists across deployments. Pip resolves `onepagerapp` by name+version, not by wheel content, so if `VERSION` in `__version.py` doesn't change, pip reports "already satisfied" and silently skips installing the replaced wheel. **Every code change requires a version bump** for a redeploy to actually take effect (see [Decision_Log.md](Decision_Log.md), #5).

---

## Deployment and Monitoring

### Checking app logs and status

After deploying or running the app, use Databricks CLI to monitor it:

**View app logs in real-time:**
```bash
databricks apps logs one-pager-app --follow --timeout 5m -p dev
```

**Check app and deployment state:**
```bash
databricks apps get one-pager-app -p dev --output json
```

The response has no top-level `status` field. The relevant fields are:

| Field | Meaning | Healthy value |
|---|---|---|
| `compute_status.state` | Whether the app's container is running | `ACTIVE` |
| `app_status.state` | Whether the process inside is serving | `RUNNING` |
| `active_deployment.status.state` | Outcome of the last completed deployment | `SUCCEEDED` |
| `pending_deployment.status.state` | Deployment currently in flight | absent, or `SUCCEEDED`/`FAILED`/`CANCELLED` |

```bash
databricks apps get one-pager-app -p dev --output json \
  | jq '{app_status, compute_status, active_deployment, pending_deployment}'
```

### Troubleshooting deployment errors

#### Error: "Cannot deploy app as there is an active deployment in progress"

Databricks Apps allows only one deployment per app at a time. `POST /api/2.0/apps/<name>/deployments` is rejected while `pending_deployment.status.state` is `IN_PROGRESS`.

**Why the CD pipeline hit this:** `databricks apps start` does not only boot the container — it also **re-deploys the app's last-known source code**. So the deployment triggered by `start` was still `IN_PROGRESS` when the very next command, `databricks apps deploy`, fired. Stopping the app first makes this *worse*, not better: a stopped app always has to go through the start-triggered redeploy.

**How the pipeline handles it** (see [deploy-dab.yml](../azure-pipelines/templates/deploy-dab.yml)):

1. Only call `databricks apps start` when `compute_status.state` is not already `ACTIVE`.
2. Poll `pending_deployment.status.state` until no deployment is in flight (hard timeout, so the stage can never hang forever).
3. Then run `databricks apps deploy` with the explicit `--source-code-path`.
4. A separate verification step asserts `active_deployment.status.state == SUCCEEDED` and `app_status.state == RUNNING`, dumping app logs on failure.

**Manual recovery:** wait for `pending_deployment` to clear, then redeploy:

```bash
databricks apps get one-pager-app -p dev --output json | jq '.pending_deployment.status'
databricks apps deploy one-pager-app --source-code-path /Workspace/LakehouseX/OnePagerApp/files/app -p dev
```

---

## Streamlit