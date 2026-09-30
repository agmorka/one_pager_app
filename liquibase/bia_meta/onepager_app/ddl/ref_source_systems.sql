-- liquibase formatted sql

-- changeset onepagerapp:cur_ref_source_systems-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.ref_source_systems (
    system_name STRING      NOT NULL PRIMARY KEY,
    sort_order  INT         NOT NULL,
    active      BOOLEAN     NOT NULL
);

INSERT INTO ${catalog.name}.onepager_app.ref_source_systems (system_name, sort_order, active)
VALUES
    ('SAP ERP',         1, true),
    ('Salesforce CRM',  2, true),
    ('Workday',         3, true),
    ('Core Banking',    4, true);

--rollback DROP TABLE ${catalog.name}.onepager_app.ref_source_systems;
