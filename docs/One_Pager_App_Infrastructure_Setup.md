# One-Pager-App Infrastructure Setup

## Overview

One-Pager-App is a Databricks application deployed across multiple environments (DEV, INT, TST, UAT, PRD) via Terraform Infrastructure-as-Code. Like all Databricks apps and Unity Catalog objects, it is managed entirely through Terraform configuration, ensuring consistent, version-controlled, and reproducible deployments across environments.

## Application Details

- **Application Name**: `one-pager-app`
- **Description**: Application for creating and managing One Pagers
- **Managed By**: Service Principal `bp-spn-lhx-opa-{env}-001` (environment-specific)
- **Compute Size**: MEDIUM
- **API Scopes**: SQL (enables SQL warehouse access). **Required addition:** `iam.current-user:read` (reads the signed-in user's first name and surname, see "Required changes" below)
- **Deployed To**: DEV, INT, TST, UAT, PRD

## Provisioned Resources

### 1. Serverless SQL Warehouse
- **Name**: `serverless-starter-warehouse`
- **Permission**: `CAN_USE` (Execute queries)

### 2. One Pager Registry Volume
- **Name**: `one-pager-registry-volume`
- **Type**: Unity Catalog Volume
- **Permission**: `WRITE_VOLUME` (Read and write access)

## Access Control

Access is managed through Azure AD groups, configured per environment:

- **DEV/INT/TST**: Base groups (DataPlatEng-Base, Admin-Base) for use; Elevated groups (DataPlatEng-Elev, Admin-Elev) for management
- **UAT/PRD**: All users can use; Elevated groups manage

## Unity Catalog Integration

The application's service principal has **WRITE** access to the `onepager_app` schema, enabling read/write operations on UC resources.

## Data Access Model

Users have no direct privileges on Databricks resources; privileges are granted to groups. The app reads Delta tables **as the signed-in user** (on-behalf-of-user token) and writes **as its service principal** (see [Architecture.md](Architecture.md) §8). This needs the following grants:

| Principal | Catalog / schema | Tables in `onepager_app` | Registry volume | SQL warehouse |
|---|---|---|---|---|
| App service principal (`bp-spn-lhx-opa-{env}-001`) | `USE CATALOG`, `USE SCHEMA` | `SELECT`, `MODIFY` | `READ VOLUME`, `WRITE VOLUME` | `CAN_USE` |
| All employees (`account users`) | `USE CATALOG`, `USE SCHEMA` | `SELECT` only | none | `CAN_USE` |
| Any user group | — | **no** `MODIFY` | **no** `WRITE VOLUME` | — |

Unity Catalog grants must go to an account-level group; the workspace-local `users` group cannot receive them. The role groups below need no Unity Catalog grants, because all writes go through the service principal.

### Grants as SQL (for the platform team)

Terraform is the source of truth; these statements show the same grants for review, or for checking an environment by hand. Replace `<catalog>` (e.g. `dev_bia_meta`) and `<spn-application-id>` (the application ID of `bp-spn-lhx-opa-<env>-001`). Grants on the schema apply to every table in it, including tables added later.

```sql
-- App service principal: reads and writes (all writes run as the app).
GRANT USE CATALOG ON CATALOG <catalog> TO `<spn-application-id>`;
GRANT USE SCHEMA, SELECT, MODIFY ON SCHEMA <catalog>.onepager_app TO `<spn-application-id>`;
GRANT READ VOLUME, WRITE VOLUME
  ON VOLUME <catalog>.onepager_app.one_pager_registry TO `<spn-application-id>`;

-- All employees: read only (reads run as the signed-in user).
GRANT USE CATALOG ON CATALOG <catalog> TO `account users`;
GRANT USE SCHEMA, SELECT ON SCHEMA <catalog>.onepager_app TO `account users`;

-- Check: only the service principal may have MODIFY / WRITE VOLUME.
SHOW GRANTS ON SCHEMA <catalog>.onepager_app;
SHOW GRANTS ON VOLUME <catalog>.onepager_app.one_pager_registry;
```

`CAN_USE` on the SQL warehouse is a workspace permission, not a Unity Catalog grant; it is set in Terraform for the service principal and for all employees.

## Application Roles

Application roles come from Entra ID groups (automatic identity management is enabled, so Entra ID groups are available in Databricks without a sync job). Group names are app settings:

| Role | Setting | Current (interim) group |
|---|---|---|
| Owner/SME | `ONE_PAGER_APP_GROUP_OWNER_SME` | `BEC_BECOC001_LHX_{env}_DataPlatEng` |
| Approver | `ONE_PAGER_APP_GROUP_APPROVER` | `BEC_BECOC001_LHX_{env}_DataPlatEng` |
| Admin | `ONE_PAGER_APP_GROUP_ADMIN` | `BEC_BECOC001_LHX_{env}_DataPlatEng` |
| Viewer | — | Every employee (no group) |

`{env}` is `DEV`, `INT`, `UAT` or `PRD`. Dedicated groups per role will be requested by the Data Platform Engineering team, which also manages membership; switching to them is a change of the three settings. Each environment's `BEC_BECOC001_LHX_{env}_DataPlatEng` group must exist before the app is deployed there, otherwise every user is only a Viewer.

## Required Changes

Changes to the Terraform configuration needed by the identity and access design (`..dev/User_Identity_And_Access_Plan.md`):

1. Add the `iam.current-user:read` user API scope to the app (keep `sql`). Users who opened the app before the change must sign in again (see [Dev_Notes.md](Dev_Notes.md), "User authorization scopes").
2. Grant `account users` `USE CATALOG`, `USE SCHEMA` and `SELECT` on the app tables, and `CAN_USE` on the warehouse.
3. Confirm that no user group has `MODIFY` on the app tables or `WRITE VOLUME` on the registry volume.
4. No change to app access: in UAT/PRD all users can already use the app (all employees are Viewers); DEV/INT/TST stay limited to the base groups.
5. TST: the app's `Environment` setting knows TST (badge, and `BEC_BECOC001_LHX_TST_DataPlatEng` as the interim role group). Check that this group exists in the TST account.
6. Before deploying to an environment, check that `BEC_BECOC001_LHX_<ENV>_DataPlatEng` exists in that environment's account (for example `SELECT is_account_group_member('BEC_BECOC001_LHX_DEV_DataPlatEng')` as a member returns `true`). A missing group means every user is only a Viewer.

## App Settings per Environment

The app's environment variables are in `app/app.yml`, which is the same for every environment:

- `ONE_PAGER_APP_VOLUME_PATH` and `DATABRICKS_WAREHOUSE_ID` come from the app resources (`valueFrom`), so they differ per environment.
- The environment (sidebar badge and the `{env}` in role group names) is derived from the catalog of the registry volume, e.g. `/Volumes/prd_bia_meta/...` → `PRD`. `ONE_PAGER_APP_ENVIRONMENT` overrides it if ever needed.
- The identity settings (`ONE_PAGER_APP_USER_DOMAINS`, `ONE_PAGER_APP_USERNAME_SUFFIXES`, `ONE_PAGER_APP_INITIALS_PATTERN`) are the same everywhere and set explicitly.
- The role group settings stay unset while the interim DataPlatEng group is used; add them once the dedicated groups exist (the `{env}` placeholder keeps one value valid for all environments).

## Promoting the Identity and Access Changes

Order: DEV → INT → TST → UAT → PRD. Move on only when the previous environment has passed the smoke test. Per environment:

1. **Terraform:** the `sql` and `iam.current-user:read` user API scopes on the app; the grants above (service principal: `SELECT`, `MODIFY`, `READ VOLUME`, `WRITE VOLUME`; `account users`: `SELECT`); `CAN_USE` on the warehouse.
2. **Liquibase:** run the pipeline so `ddl/ref_audit_columns.sql` adds `last_updated_by` / `last_updated_at` to the `ref_*` tables **before** the new app version is deployed (Admin changes write these columns).
3. **Groups:** check that `BEC_BECOC001_LHX_<ENV>_DataPlatEng` exists (Required Changes, item 6). Once dedicated role groups exist, add the `ONE_PAGER_APP_GROUP_*` settings to `app/app.yml` (with `{env}`, one value for every environment).
4. **Deploy** the app with the bundle target of the environment. `databricks.yml` has targets `dev`, `int`, `uat` and `prd`; a `tst` target (and its pipeline stage) must be added before deploying to TST.
5. **Check the environment:** the sidebar badge shows the right environment (derived from the registry volume's catalog). A wrong badge means the role groups of the wrong environment are checked.
6. **Smoke test:** run the identity and access smoke test ([Testing_Strategy.md](Testing_Strategy.md) §8) and record the result in the deployment ticket.
7. **Users who used the app before:** if names do not appear, they sign in again so their token gets the new scope ([Dev_Notes.md](Dev_Notes.md), "User authorization scopes").

## Service Principal

The application is managed by environment-specific service principals:

- **DEV**: `bp-spn-lhx-opa-dev-001`
- **INT**: `bp-spn-lhx-opa-int-001`
- **TST**: `bp-spn-lhx-opa-tst-001`
- **UAT**: `bp-spn-lhx-opa-uat-001`
- **PRD**: `bp-spn-lhx-opa-prd-001`

Each SPN:
- Has `CAN_MANAGE` permissions on the app
- Automatically receives WRITE access to the `onepager_app` schema
- Can be used for CI/CD deployments and automated management
