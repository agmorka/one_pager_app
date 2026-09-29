-- liquibase formatted sql

-- changeset onepagerapp:cur_one_pager_status-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.one_pager_status (
    one_pager_id        STRING      NOT NULL PRIMARY KEY,
    data_product        STRING      NOT NULL UNIQUE,
    product_name        STRING      NOT NULL,
    business_domain     STRING      NOT NULL,
    data_product_type   STRING      NOT NULL,
    one_pager_status    STRING      NOT NULL,
    data_product_status STRING      NOT NULL,
    version             STRING      NOT NULL,
    owner_name          STRING      NOT NULL,
    owner_initials      STRING      NOT NULL,
    owner_email         STRING      NOT NULL,
    owner_team          STRING,
    created_by          STRING      NOT NULL,
    created_at          TIMESTAMP   NOT NULL,
    last_updated_at     TIMESTAMP   NOT NULL,
    last_updated_by     STRING      NOT NULL,
    reviewed_at         TIMESTAMP,
    reviewed_by         STRING,
    structure_definition STRING     NOT NULL,
    pending_pr          BOOLEAN     NOT NULL
);

--rollback DROP TABLE ${catalog.name}.onepager_app.one_pager_status;
