INSERT INTO dim_customers
SELECT
    id AS customer_id,
    '客户-' || lpad(id::VARCHAR, 3, '0') AS customer_name,
    CASE
        WHEN id % 10 = 0 THEN NULL
        WHEN id % 5 = 0 THEN '杭州'
        WHEN id % 5 = 1 THEN '上海'
        WHEN id % 5 = 2 THEN '北京'
        WHEN id % 5 = 3 THEN '深圳'
        ELSE '成都'
    END AS city,
    DATE '2023-01-01' + ((id * 11) % 730)::INTEGER AS signup_date,
    CASE id % 4
        WHEN 0 THEN 'new'
        WHEN 1 THEN 'growth'
        WHEN 2 THEN 'core'
        ELSE 'vip'
    END AS segment
FROM range(1, 121) AS t(id);

INSERT INTO dim_products
SELECT
    id AS product_id,
    '商品-' || lpad(id::VARCHAR, 2, '0') AS product_name,
    '品类-' || (floor((id - 1) / 6) + 1)::BIGINT::VARCHAR AS category,
    '品牌-' || (((id - 1) % 9) + 1)::VARCHAR AS brand,
    (20 + id * 7.25)::DECIMAL(14,2) AS list_price
FROM range(1, 37) AS t(id);

INSERT INTO dim_channels VALUES
    (1, '自然流量', 'organic'),
    (2, '搜索广告', 'paid_search'),
    (3, '联盟渠道', 'affiliate'),
    (4, '线下门店', 'offline'),
    (5, '合作伙伴', 'partner');

INSERT INTO fact_orders
SELECT
    order_id,
    (hash('20260829:customer:' || order_id::VARCHAR) % 110 + 1)::BIGINT,
    (hash('20260829:channel:' || order_id::VARCHAR) % 4 + 1)::BIGINT,
    DATE '2025-01-01'
        + (hash('20260829:order-date:' || order_id::VARCHAR) % 546)::INTEGER,
    CASE
        WHEN order_id % 10 = 0 THEN 'cancelled'
        WHEN order_id % 10 = 1 THEN 'pending'
        ELSE 'completed'
    END,
    0::DECIMAL(14,2)
FROM range(1, 601) AS t(order_id);

INSERT INTO fact_order_items
WITH order_lines AS (
    SELECT
        order_id,
        (hash('20260829:line-count:' || order_id::VARCHAR) % 4 + 1)::BIGINT AS line_count
    FROM range(1, 601) AS t(order_id)
), expanded AS (
    SELECT order_id, line_no
    FROM order_lines,
    LATERAL range(1, line_count + 1) AS lines(line_no)
)
SELECT
    e.order_id,
    e.line_no,
    (hash(
        '20260829:product:' || e.order_id::VARCHAR || ':' || e.line_no::VARCHAR
    ) % 36 + 1)::BIGINT AS product_id,
    (hash(
        '20260829:qty:' || e.order_id::VARCHAR || ':' || e.line_no::VARCHAR
    ) % 5 + 1)::BIGINT AS quantity,
    p.list_price AS unit_price,
    (hash(
        '20260829:discount:' || e.order_id::VARCHAR || ':' || e.line_no::VARCHAR
    ) % 5 * 2.50)::DECIMAL(14,2) AS discount_amount
FROM expanded e
JOIN dim_products p
  ON p.product_id = (
      hash('20260829:product:' || e.order_id::VARCHAR || ':' || e.line_no::VARCHAR)
      % 36 + 1
  );

UPDATE fact_orders AS o
SET total_amount = totals.total_amount
FROM (
    SELECT order_id, SUM(quantity * unit_price - discount_amount) AS total_amount
    FROM fact_order_items
    GROUP BY order_id
) AS totals
WHERE totals.order_id = o.order_id;

INSERT INTO fact_payments
SELECT
    o.order_id * 10 + 1 AS payment_id,
    o.order_id,
    o.order_date::TIMESTAMP + INTERVAL 1 DAY AS paid_at,
    CASE o.order_id % 4
        WHEN 0 THEN 'alipay'
        WHEN 1 THEN 'wechat'
        WHEN 2 THEN 'card'
        ELSE 'bank'
    END AS payment_method,
    o.total_amount,
    CASE WHEN o.order_id % 17 = 0 THEN 'refunded' ELSE 'paid' END AS status
FROM fact_orders o
WHERE o.status = 'completed' AND o.order_id % 11 != 0;

INSERT INTO fact_payments
SELECT
    o.order_id * 10 AS payment_id,
    o.order_id,
    o.order_date::TIMESTAMP + INTERVAL 6 HOUR AS paid_at,
    'card' AS payment_method,
    o.total_amount,
    'failed' AS status
FROM fact_orders o
WHERE o.status = 'completed' AND o.order_id % 7 = 0;

INSERT INTO fact_payments
SELECT
    o.order_id * 10 + 2 AS payment_id,
    o.order_id,
    o.order_date::TIMESTAMP + INTERVAL 2 DAY AS paid_at,
    'wechat' AS payment_method,
    o.total_amount,
    'paid' AS status
FROM fact_orders o
WHERE o.status = 'completed' AND o.order_id % 13 = 0;

INSERT INTO fact_returns
SELECT
    i.order_id * 10 + i.line_no AS return_id,
    i.order_id,
    i.line_no,
    o.order_date::TIMESTAMP + INTERVAL 7 DAY AS returned_at,
    greatest(1, i.quantity - 1)::BIGINT AS return_qty,
    round(
        greatest(1, i.quantity - 1)
        * ((i.quantity * i.unit_price - i.discount_amount) / i.quantity),
        2
    )::DECIMAL(14,2) AS refund_amount,
    CASE WHEN (i.order_id * 10 + i.line_no) % 2 = 0 THEN NULL ELSE '不合适' END
FROM fact_order_items i
JOIN fact_orders o ON o.order_id = i.order_id
WHERE o.status = 'completed'
  AND hash(
      '20260829:return:' || i.order_id::VARCHAR || ':' || i.line_no::VARCHAR
  ) % 13 = 0;

INSERT INTO fact_orders VALUES
    (900001, 1, 1, DATE '2026-06-30', 'completed', 0),
    (900002, 1, 1, DATE '2026-06-30', 'completed', 0),
    (900003, 1, 1, DATE '2026-06-30', 'completed', 50.00),
    (900004, 1, 1, DATE '2026-06-30', 'completed', 100.00);

INSERT INTO fact_order_items
WITH base AS (
    SELECT
        SUM(CASE WHEN i.product_id = 1 THEN i.quantity * i.unit_price - i.discount_amount ELSE 0 END) AS base1,
        SUM(CASE WHEN i.product_id = 2 THEN i.quantity * i.unit_price - i.discount_amount ELSE 0 END) AS base2
    FROM fact_order_items i
    JOIN fact_orders o ON o.order_id = i.order_id
    WHERE o.status = 'completed'
), target AS (
    SELECT greatest(base1, base2) + 100.00 AS value, base1, base2
    FROM base
)
SELECT 900001, 1, 1, 1, (value - base1)::DECIMAL(14,2), 0::DECIMAL(14,2)
FROM target
UNION ALL
SELECT 900002, 1, 2, 1, (value - base2)::DECIMAL(14,2), 0::DECIMAL(14,2)
FROM target
UNION ALL
SELECT 900003, 1, 3, 1, 50.00::DECIMAL(14,2), 0::DECIMAL(14,2)
FROM target
UNION ALL
SELECT 900004, 1, 4, 1, 90.00::DECIMAL(14,2), 0::DECIMAL(14,2)
FROM target;

UPDATE fact_orders AS o
SET total_amount = totals.total_amount
FROM (
    SELECT order_id, SUM(quantity * unit_price - discount_amount) AS total_amount
    FROM fact_order_items
    WHERE order_id IN (900001, 900002)
    GROUP BY order_id
) AS totals
WHERE totals.order_id = o.order_id;

INSERT INTO fact_payments VALUES
    (9000031, 900003, TIMESTAMP '2026-07-01 12:00:00', 'card', 25.00, 'paid'),
    (9000032, 900003, TIMESTAMP '2026-07-01 12:00:00', 'wechat', 25.00, 'paid');
UPDATE fact_orders
SET order_date = DATE '2025-04-30'
WHERE status = 'completed'
  AND order_date >= DATE '2025-05-01'
  AND order_date < DATE '2025-07-01';

INSERT INTO dim_customers VALUES
    (121, '边界均值-10.00', '上海', DATE '2024-01-01', 'boundary_avg'),
    (122, '边界均值-10.01-A', '北京', DATE '2024-01-02', 'boundary_avg'),
    (123, '边界均值-10.01-B', '深圳', DATE '2024-01-03', 'boundary_avg');

INSERT INTO dim_products VALUES
    (37, '边界并列商品-A', '品类-不足三件', '边界品牌', 25.00),
    (38, '边界并列商品-B', '品类-不足三件', '边界品牌', 25.00),
    (39, '从未销售商品', '品类-无销量', '边界品牌', 99.00);

INSERT INTO fact_orders VALUES
    (910001, 121, 1, DATE '2026-02-01', 'completed', 10.00),
    (910002, 122, 1, DATE '2026-02-02', 'completed', 10.01),
    (910003, 123, 1, DATE '2026-02-03', 'completed', 10.01),
    (910010, 1, 2, DATE '2026-03-01', 'completed', 50.00),
    (910020, 1, 2, DATE '2025-06-15', 'completed', 0.00),
    (910100, 2, 3, DATE '2026-01-01', 'completed', 999.99),
    (910101, 2, 3, DATE '2026-01-02', 'completed', 1000.00),
    (910102, 2, 3, DATE '2026-01-03', 'completed', 1999.99),
    (910103, 2, 3, DATE '2026-01-04', 'completed', 2000.00),
    (910200, 3, 4, DATE '2026-04-01', 'completed', 30.00),
    (910300, 4, 1, DATE '2026-04-02', 'pending', 77.77),
    (910301, 4, 1, DATE '2026-04-03', 'pending', 33.00),
    (910302, 4, 1, DATE '2026-04-04', 'cancelled', 44.00);

INSERT INTO fact_order_items VALUES
    (910001, 1, 36, 1, 10.00, 0.00),
    (910002, 1, 36, 1, 10.01, 0.00),
    (910003, 1, 36, 1, 10.01, 0.00),
    (910010, 1, 37, 1, 25.00, 0.00),
    (910010, 2, 38, 1, 25.00, 0.00),
    (910020, 1, 37, 1, 0.00, 0.00),
    (910100, 1, 36, 1, 999.99, 0.00),
    (910101, 1, 36, 1, 1000.00, 0.00),
    (910102, 1, 36, 1, 1999.99, 0.00),
    (910103, 1, 36, 1, 2000.00, 0.00),
    (910200, 1, 36, 3, 10.00, 0.00),
    (910300, 1, 36, 1, 77.77, 0.00),
    (910302, 1, 36, 1, 40.00, 0.00);

INSERT INTO fact_payments VALUES
    (9103001, 910300, TIMESTAMP '2026-04-02 12:00:00', 'card', 77.77, 'paid');

INSERT INTO fact_returns VALUES
    (9102001, 910200, 1, TIMESTAMP '2026-04-05 10:00:00', 1, 10.00, '部分退货'),
    (9102002, 910200, 1, TIMESTAMP '2026-04-06 10:00:00', 2, 20.00, '剩余退货');