-- liquibase formatted sql

-- Who last changed a reference value on the Admin page (Data_Model.md §3,
-- Decision_Log.md §22). Writes run as the app's service principal, so Delta
-- history cannot show the person. NULL for seeded rows that were never
-- changed in the app.

-- changeset onepagerapp:cur_ref_op_status-002
ALTER TABLE ${catalog.name}.onepager_app.ref_op_status
    ADD COLUMNS (last_updated_by STRING, last_updated_at TIMESTAMP);
--rollback not required

-- changeset onepagerapp:cur_ref_dp_status-002
ALTER TABLE ${catalog.name}.onepager_app.ref_dp_status
    ADD COLUMNS (last_updated_by STRING, last_updated_at TIMESTAMP);
--rollback not required

-- changeset onepagerapp:cur_ref_business_domains-002
ALTER TABLE ${catalog.name}.onepager_app.ref_business_domains
    ADD COLUMNS (last_updated_by STRING, last_updated_at TIMESTAMP);
--rollback not required

-- changeset onepagerapp:cur_ref_data_product_types-002
ALTER TABLE ${catalog.name}.onepager_app.ref_data_product_types
    ADD COLUMNS (last_updated_by STRING, last_updated_at TIMESTAMP);
--rollback not required

-- changeset onepagerapp:cur_ref_source_systems-002
ALTER TABLE ${catalog.name}.onepager_app.ref_source_systems
    ADD COLUMNS (last_updated_by STRING, last_updated_at TIMESTAMP);
--rollback not required
