-- liquibase formatted sql

-- changeset onepagerapp:cur_ref_business_domains-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.ref_business_domains (
    domain      STRING      NOT NULL PRIMARY KEY,
    sort_order  INT         NOT NULL,
    active      BOOLEAN     NOT NULL
);

INSERT INTO ${catalog.name}.onepager_app.ref_business_domains (domain, sort_order, active)
VALUES
    ('Core Banking',        1, true),
    ('Payments',            2, true),
    ('Operations',          3, true),
    ('Risk & Compliance',   4, true),
    ('Customer',            5, true),
    ('Lending',             6, true),
    ('Treasury',            7, true);

--rollback DROP TABLE ${catalog.name}.onepager_app.ref_business_domains;
