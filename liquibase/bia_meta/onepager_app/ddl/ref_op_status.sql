-- liquibase formatted sql

-- changeset onepagerapp:cur_ref_op_status-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.ref_op_status (
    status          STRING      NOT NULL PRIMARY KEY,
    display_label   STRING      NOT NULL,
    sort_order      INT         NOT NULL,
    badge_color     STRING,
    is_terminal     BOOLEAN     NOT NULL
);

INSERT INTO ${catalog.name}.onepager_app.ref_op_status (status, display_label, sort_order, badge_color, is_terminal)
VALUES
    ('Draft',            'Draft',            1, '#808080', false),
    ('Ready for Review', 'Ready for Review', 2, '#F9BD00', false),
    ('In Review',        'In Review',        3, '#FFA500', false),
    ('Approved',         'Approved',         4, '#65B676', false),
    ('Draft Update',     'Draft Update',     5, '#7E57C2', false),
    ('Cancelled',        'Cancelled',        6, '#F34421', true);

--rollback DROP TABLE ${catalog.name}.onepager_app.ref_op_status;