-- liquibase formatted sql

-- changeset onepagerapp:cur_one_pager_authorized_users-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.one_pager_authorized_users (
    one_pager_id    STRING      NOT NULL,
    user_initials   STRING      NOT NULL,
    user_name       STRING      NOT NULL,
    user_email      STRING      NOT NULL,
    user_team       STRING,
    role            STRING      NOT NULL,
    CONSTRAINT pk_one_pager_authorized_users PRIMARY KEY (one_pager_id, user_initials)
);

--rollback DROP TABLE ${catalog.name}.onepager_app.one_pager_authorized_users;
