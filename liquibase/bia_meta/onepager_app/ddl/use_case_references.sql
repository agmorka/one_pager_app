-- liquibase formatted sql

-- changeset onepagerapp:cur_use_case_references-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.use_case_references (
    one_pager_id        STRING      NOT NULL,
    use_case_id         STRING      NOT NULL,
    CONSTRAINT use_case_references_pk PRIMARY KEY (one_pager_id, use_case_id)
);

--rollback DROP TABLE ${catalog.name}.onepager_app.use_case_references;
