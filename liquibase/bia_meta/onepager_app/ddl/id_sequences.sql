-- liquibase formatted sql

-- changeset onepagerapp:cur_id_sequences-001

CREATE TABLE IF NOT EXISTS ${catalog.name}.onepager_app.id_sequences (
    id_type     STRING      NOT NULL PRIMARY KEY,
    last_value  INT         NOT NULL
);

INSERT INTO ${catalog.name}.onepager_app.id_sequences (id_type, last_value)
VALUES
    ('OP', 0),
    ('UC', 0),
    ('BR', 0);

--rollback DROP TABLE ${catalog.name}.onepager_app.id_sequences;
