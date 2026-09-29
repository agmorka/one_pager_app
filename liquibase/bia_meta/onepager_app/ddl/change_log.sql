-- liquibase formatted sql

-- changeset onepagerapp:cur_change_log-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.change_log (
    id                  BIGINT      NOT NULL PRIMARY KEY GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 1),
    one_pager_id        STRING      NOT NULL,
    version             STRING      NOT NULL,
    event_type          STRING      NOT NULL,
    author_initials     STRING      NOT NULL,
    author_name         STRING      NOT NULL,
    summary             STRING      NOT NULL,
    from_status         STRING,
    to_status           STRING,
    status_field        STRING,
    created_at          TIMESTAMP   NOT NULL
);

--rollback DROP TABLE ${catalog.name}.onepager_app.change_log;
