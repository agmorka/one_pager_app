-- liquibase formatted sql

-- changeset onepagerapp:cur_locks-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.locks (
    one_pager_id        STRING      NOT NULL PRIMARY KEY,
    locked_by_initials  STRING      NOT NULL,
    locked_by_name      STRING      NOT NULL,
    session_id          STRING      NOT NULL,
    acquired_at         TIMESTAMP   NOT NULL,
    last_heartbeat      TIMESTAMP   NOT NULL,
    expires_at          TIMESTAMP   NOT NULL
);

--rollback DROP TABLE ${catalog.name}.onepager_app.locks;
