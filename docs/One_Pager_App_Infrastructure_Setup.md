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
5. TST: the app's `Environment` setting knows DEV, INT, UAT and PRD only. If the app runs in TST, the setting and the `BEC_BECOC001_LHX_TST_DataPlatEng` group must be added.

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
