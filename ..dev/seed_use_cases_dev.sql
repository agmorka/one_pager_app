-- DEV seed data for use_cases, use_case_references and id_sequences tables
-- Run manually in Databricks SQL Editor, after seed_one_pager_status_dev.sql
-- Update <catalog> placeholder to match your environment (e.g., onepager_dev)

INSERT INTO <catalog>.onepager_app.use_cases
(
    use_case_id, persona, goal, scenario, decision_enabled, priority, deprecated,
    created_by, created_at, last_updated_by, last_updated_at
)
VALUES
    (
        'UC-001', 'Analytics Manager', 'Understand customer lifetime value trends',
        'Aggregate spending, engagement, and product usage without duplicate records',
        'Identify high-value customer segments for targeted marketing', 'Must Have', false,
        'JD', CURRENT_TIMESTAMP() - INTERVAL 90 DAY, 'JD', CURRENT_TIMESTAMP() - INTERVAL 90 DAY
    ),
    (
        'UC-002', 'Compliance Officer', 'Fulfill GDPR data subject access requests quickly',
        'Query Person dataset with unique ID and get all attributes in one place',
        'Respond to GDPR requests within 30 days', 'Must Have', false,
        'JD', CURRENT_TIMESTAMP() - INTERVAL 90 DAY, 'AB', CURRENT_TIMESTAMP() - INTERVAL 10 DAY
    ),
    (
        'UC-003', 'Finance Director', 'Reconcile revenue across channels and time periods',
        'Query unified order data by date range, channel, product, and customer',
        'Close accounting books on time with full audit trail', 'High', false,
        'RM', CURRENT_TIMESTAMP() - INTERVAL 60 DAY, 'RM', CURRENT_TIMESTAMP() - INTERVAL 60 DAY
    ),
    (
        'UC-004', 'Operations Manager', 'Track fulfillment status and predict delivery dates',
        'See order status, warehouse inventory, and shipping progress in one view',
        'Proactively communicate delivery estimates to customers', 'Medium', false,
        'RM', CURRENT_TIMESTAMP() - INTERVAL 45 DAY, 'RM', CURRENT_TIMESTAMP() - INTERVAL 45 DAY
    ),
    (
        'UC-005', 'Branch Advisor', 'See a customer summary before meetings',
        'Open a printed customer summary prepared by the back office',
        'Prepare advice for scheduled customer meetings', 'Low', true,
        'AB', CURRENT_TIMESTAMP() - INTERVAL 120 DAY, 'AB', CURRENT_TIMESTAMP() - INTERVAL 30 DAY
    );

INSERT INTO <catalog>.onepager_app.use_case_references (one_pager_id, use_case_id)
VALUES
    ('OP-0001', 'UC-001'),
    ('OP-0001', 'UC-002'),
    ('OP-0003', 'UC-002'),
    ('OP-0002', 'UC-003'),
    ('OP-0002', 'UC-004'),
    ('OP-0006', 'UC-001');

-- Keep the UC counter in line with the highest seeded ID so the app continues at UC-006
UPDATE <catalog>.onepager_app.id_sequences SET last_value = 5 WHERE id_type = 'UC';
