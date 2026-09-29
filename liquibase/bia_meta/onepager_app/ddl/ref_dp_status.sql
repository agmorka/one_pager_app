-- liquibase formatted sql

-- changeset onepagerapp:cur_ref_dp_status-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.ref_dp_status (
    status          STRING      NOT NULL PRIMARY KEY,
    display_label   STRING      NOT NULL,
    sort_order      INT         NOT NULL,
    badge_color     STRING,
    is_terminal     BOOLEAN     NOT NULL
);

INSERT INTO ${catalog.name}.onepager_app.ref_dp_status (status, display_label, sort_order, badge_color, is_terminal)
VALUES
    ('In Definition',           'In Definition',           1, '#808080', false),
    ('Ready for Development',   'Ready for Development',   2, '#65B676', false),
    ('In Development',          'In Development',          3, '#3599B8', false),
    ('Active',                  'Active',                  4, '#00975f', false),
    ('In Enhancement',          'In Enhancement',          5, '#F9BD00', false),
    ('Deprecated',              'Deprecated',              6, '#7E57C2', true),
    ('Cancelled',               'Cancelled',               7, '#F34421', true);

--rollback DROP TABLE ${catalog.name}.onepager_app.ref_dp_status;
