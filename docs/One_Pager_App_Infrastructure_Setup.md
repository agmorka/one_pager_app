# One-Pager-App Infrastructure Setup

## Overview

One-Pager-App is a Databricks application deployed across multiple environments (DEV, INT, TST, UAT, PRD) via Terraform Infrastructure-as-Code. Like all Databricks apps and Unity Catalog objects, it is managed entirely through Terraform configuration, ensuring consistent, version-controlled, and reproducible deployments across environments.

## Application Details

- **Application Name**: `one-pager-app`
- **Description**: Application for creating and managing One Pagers
- **Managed By**: Service Principal `bp-spn-lhx-opa-{env}-001` (environment-specific)
- **Compute Size**: MEDIUM
- **API Scopes**: SQL (enables SQL warehouse access)
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
