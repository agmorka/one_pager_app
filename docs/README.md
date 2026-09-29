# One Pager Application — Documentation

This folder contains comprehensive documentation for the One Pager Application project, covering architecture, design, requirements, data modeling, testing strategy, and deployment configuration.

## Files Overview

### [Architecture.md](Architecture.md)
Defines the overall system architecture, including component interactions, technology stack decisions, security model (authentication/authorization/audit), API boundaries, error handling strategy, and integration points with Databricks (Unity Catalog, volumes, Git repositories).

### [Backend_Design.md](Backend_Design.md)
Detailed design of the application backend, including service layer architecture, API endpoints, database schema design, caching strategy, error handling patterns, and implementation guidelines for core business logic.

### [Data_Model.md](Data_Model.md)
Complete data model specification for One Pagers, including entity definitions, relationships, field validations, Delta table schemas, audit trail structure, status tracking, and versioning strategy.

### [Project_Structure.md](Project_Structure.md)
Technical project structure and tooling guide, covering repository layout, dependency management with `uv`, environment topology (DEV/INT/UAT/PRD), configuration strategy, CI/CD pipeline, Git branching strategy, Delta table provisioning with Liquibase, and local development setup.

### [Requirements_and_Scope.md](Requirements_and_Scope.md)
Functional and non-functional requirements, business context, scope boundaries, user personas (Creators, Reviewers, Approvers, Admins), core features, and acceptance criteria for the application.

### [Testing_Strategy.md](Testing_Strategy.md)
Comprehensive testing approach covering unit testing strategies, integration test scenarios, end-to-end testing with Streamlit AppTest, performance testing, security testing, test data management, CI/CD integration, and quality gates.

### [UI_Design.md](UI_Design.md)
User interface and user experience design specifications, including page layouts, navigation structure, form design, status indicators, workflow visualization, accessibility requirements, and visual design guidelines aligned with BEC theming.

### [Decision_Log.md](Decision_Log.md)
Record of significant design and development decisions made during the project, including the rationale, alternatives considered, and trade-offs accepted. Serves as institutional knowledge for understanding why the system is built the way it is.

### [Dev_Notes.md](Dev_Notes.md)
Practical notes and gotchas discovered during development. Reference guide for developers maintaining or extending the codebase, documenting non-obvious behavior, configuration quirks, and lessons learned.

## How to Use This Documentation

1. **Getting Started**: Begin with [Requirements_and_Scope.md](Requirements_and_Scope.md) to understand the project goals and user needs.
2. **Architecture Overview**: Read [Architecture.md](Architecture.md) for system design and component interactions.
3. **Understanding Decisions**: Consult [Decision_Log.md](Decision_Log.md) to understand the rationale behind key architectural and technology choices.
4. **Implementation Details**: Refer to [Backend_Design.md](Backend_Design.md) and [Data_Model.md](Data_Model.md) when implementing features. Check [Dev_Notes.md](Dev_Notes.md) for development gotchas and non-obvious patterns.
5. **Setup & Deployment**: Follow [Project_Structure.md](Project_Structure.md) for environment setup and deployment procedures.
6. **UI Development**: Use [UI_Design.md](UI_Design.md) for building frontend pages.
7. **Quality Assurance**: Follow [Testing_Strategy.md](Testing_Strategy.md) for testing guidelines and coverage requirements.

## Document Maintenance

All documentation should be kept in sync with implementation. Update relevant docs when:
- Architecture or design decisions change
- New features are added or requirements evolve
- Testing strategies are refined
- Deployment procedures are updated

Documentation follows Markdown format for easy version control and review.
