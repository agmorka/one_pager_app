-- liquibase formatted sql

-- changeset onepagerapp:cur_review_comments-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.review_comments (
    id                  BIGINT      NOT NULL PRIMARY KEY GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 1),
    one_pager_id        STRING      NOT NULL,
    version             STRING      NOT NULL,
    section             STRING,
    reviewer_initials   STRING      NOT NULL,
    reviewer_name       STRING      NOT NULL,
    comment             STRING      NOT NULL,
    resolved            BOOLEAN     NOT NULL,
    resolved_by         STRING,
    created_at          TIMESTAMP   NOT NULL,
    resolved_at         TIMESTAMP
);

--rollback DROP TABLE ${catalog.name}.onepager_app.review_comments;
