-- DEV seed data for one_pager_status table
-- Run manually in Databricks SQL Editor
-- Update <catalog> and <schema> placeholders to match your environment (e.g., onepager_dev, onepager_app)

INSERT INTO <catalog>.onepager_app.one_pager_status 
(
    one_pager_id, data_product, product_name, business_domain, data_product_type, 
    one_pager_status, data_product_status, version, owner_name, owner_initials, 
    owner_email, owner_team, created_by, created_at, last_updated_at, last_updated_by, 
    reviewed_at, reviewed_by, structure_definition, pending_pr
)
VALUES
    (
        'OP-0001', 'customer_master', 'Customer Master', 'Core Banking', 'Foundational',
        'Approved', 'Active', '1.0.0', 'Jane Doe', 'JD', 'jane.doe@bank.com', 'Data Engineering',
        'JD', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), 'JD',
        CURRENT_TIMESTAMP(), 'AB', 'structure_one_pager/structure_one_pager_v_1.json', false
    ),
    (
        'OP-0002', 'account_master', 'Account Master', 'Core Banking', 'Foundational',
        'In Review', 'In Development', '0.5.0', 'John Smith', 'JS', 'john.smith@bank.com', 'Data Engineering',
        'JS', CURRENT_TIMESTAMP() - INTERVAL 7 DAY, CURRENT_TIMESTAMP() - INTERVAL 2 DAY, 'JS',
        NULL, NULL, 'structure_one_pager/structure_one_pager_v_1.json', false
    ),
    (
        'OP-0003', 'transaction_detail', 'Transaction Detail', 'Payments', 'Integrated',
        'Draft', 'In Definition', '0.1.0', 'Alice Brown', 'AB', 'alice.brown@bank.com', 'Analytics',
        'AB', CURRENT_TIMESTAMP() - INTERVAL 1 DAY, CURRENT_TIMESTAMP() - INTERVAL 1 DAY, 'AB',
        NULL, NULL, 'structure_one_pager/structure_one_pager_v_1.json', false
    ),
    (
        'OP-0004', 'payment_events', 'Payment Events Stream', 'Payments', 'Augmented',
        'Ready for Review', 'Ready for Development', '0.3.0', 'Michael Chen', 'MC', 'michael.chen@bank.com', 'Data Engineering',
        'MC', CURRENT_TIMESTAMP() - INTERVAL 5 DAY, CURRENT_TIMESTAMP() - INTERVAL 1 DAY, 'MC',
        NULL, NULL, 'structure_one_pager/structure_one_pager_v_1.json', false
    ),
    (
        'OP-0005', 'risk_indicators', 'Risk Indicators', 'Risk & Compliance', 'Integrated',
        'Approved', 'Active', '2.1.0', 'Sarah Wilson', 'SW', 'sarah.wilson@bank.com', 'Risk Analytics',
        'SW', CURRENT_TIMESTAMP() - INTERVAL 30 DAY, CURRENT_TIMESTAMP() - INTERVAL 10 DAY, 'SW',
        CURRENT_TIMESTAMP() - INTERVAL 10 DAY, 'AB', 'structure_one_pager/structure_one_pager_v_1.json', false
    ),
    (
        'OP-0006', 'customer_behavior', 'Customer Behavior Analytics', 'Customer', 'Augmented',
        'Draft Update', 'In Enhancement', '1.2.1', 'Robert Martinez', 'RM', 'robert.martinez@bank.com', 'Analytics',
        'RM', CURRENT_TIMESTAMP() - INTERVAL 60 DAY, CURRENT_TIMESTAMP() - INTERVAL 3 DAY, 'RM',
        CURRENT_TIMESTAMP() - INTERVAL 5 DAY, 'JD', 'structure_one_pager/structure_one_pager_v_1.json', false
    );
