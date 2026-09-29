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
