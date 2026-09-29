-- liquibase formatted sql

-- changeset onepagerapp:cur_ref_data_product_types-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.ref_data_product_types (
    type        STRING      NOT NULL PRIMARY KEY,
    sort_order  INT         NOT NULL,
    active      BOOLEAN     NOT NULL
);

INSERT INTO ${catalog.name}.onepager_app.ref_data_product_types (type, sort_order, active)
VALUES
    ('Foundational',  1, true),
    ('Integrated',    2, true),
    ('Augmented',     3, true);

--rollback DROP TABLE ${catalog.name}.onepager_app.ref_data_product_types;
