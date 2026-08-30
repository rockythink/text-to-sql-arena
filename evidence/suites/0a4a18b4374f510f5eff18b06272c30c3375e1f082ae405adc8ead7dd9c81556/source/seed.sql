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
    (4, '线下门店', 'offline');

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
    (900003, 1, 1, DATE '2026-06-30', 'completed', 50.00);

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
