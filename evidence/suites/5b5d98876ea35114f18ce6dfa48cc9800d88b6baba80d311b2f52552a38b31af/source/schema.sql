CREATE TABLE dim_customers (
    customer_id BIGINT PRIMARY KEY,
    customer_name VARCHAR NOT NULL,
    city VARCHAR,
    signup_date DATE NOT NULL,
    segment VARCHAR NOT NULL
);

CREATE TABLE dim_products (
    product_id BIGINT PRIMARY KEY,
    product_name VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    brand VARCHAR NOT NULL,
    list_price DECIMAL(14,2) NOT NULL
);

CREATE TABLE dim_channels (
    channel_id BIGINT PRIMARY KEY,
    channel_name VARCHAR NOT NULL,
    channel_type VARCHAR NOT NULL
);

CREATE TABLE fact_orders (
    order_id BIGINT PRIMARY KEY,
    customer_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    order_date DATE NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('completed', 'pending', 'cancelled')),
    total_amount DECIMAL(14,2) NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES dim_customers(customer_id),
    FOREIGN KEY (channel_id) REFERENCES dim_channels(channel_id)
);

CREATE TABLE fact_order_items (
    order_id BIGINT NOT NULL,
    line_no BIGINT NOT NULL,
    product_id BIGINT NOT NULL,
    quantity BIGINT NOT NULL,
    unit_price DECIMAL(14,2) NOT NULL,
    discount_amount DECIMAL(14,2) NOT NULL,
    PRIMARY KEY (order_id, line_no),
    FOREIGN KEY (order_id) REFERENCES fact_orders(order_id),
    FOREIGN KEY (product_id) REFERENCES dim_products(product_id)
);

CREATE TABLE fact_payments (
    payment_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    paid_at TIMESTAMP NOT NULL,
    payment_method VARCHAR NOT NULL,
    amount DECIMAL(14,2) NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('paid', 'failed', 'refunded')),
    FOREIGN KEY (order_id) REFERENCES fact_orders(order_id)
);

CREATE TABLE fact_returns (
    return_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    line_no BIGINT NOT NULL,
    returned_at TIMESTAMP NOT NULL,
    return_qty BIGINT NOT NULL,
    refund_amount DECIMAL(14,2) NOT NULL,
    reason VARCHAR,
    FOREIGN KEY (order_id, line_no) REFERENCES fact_order_items(order_id, line_no)
);
