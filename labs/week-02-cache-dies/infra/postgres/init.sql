-- Initialize the items table with sample data
-- This simulates a typical database table that benefits from caching

CREATE TABLE IF NOT EXISTS items (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    value DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Insert 100 sample items
INSERT INTO items (name, description, value) VALUES
    ('Item 1', 'Description for item 1 - A high-quality product with excellent features.', 199.99),
    ('Item 2', 'Description for item 2 - Premium grade materials and craftsmanship.', 249.50),
    ('Item 3', 'Description for item 3 - Best seller in its category.', 89.99),
    ('Item 4', 'Description for item 4 - Award-winning design and functionality.', 549.00),
    ('Item 5', 'Description for item 5 - Customer favorite with outstanding reviews.', 179.99),
    ('Item 6', 'Description for item 6 - Professional-grade equipment for experts.', 899.99),
    ('Item 7', 'Description for item 7 - Budget-friendly option with great value.', 49.99),
    ('Item 8', 'Description for item 8 - Limited edition collector item.', 1299.00),
    ('Item 9', 'Description for item 9 - Everyday essential for modern living.', 34.99),
    ('Item 10', 'Description for item 10 - Innovative solution for common problems.', 159.99);

-- Add an artificial delay to database queries to make the cache benefit more obvious
-- We'll add a function that introduces a small delay
CREATE OR REPLACE FUNCTION add_query_delay() RETURNS TRIGGER AS $$
BEGIN
    -- Add 30-70ms delay to simulate real database latency
    PERFORM pg_sleep(0.03 + random() * 0.04);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create an index to simulate a "normal" query pattern
CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);

-- Grant permissions
GRANT ALL PRIVILEGES ON TABLE items TO app;
GRANT USAGE, SELECT ON SEQUENCE items_id_seq TO app;

-- Log that initialization is complete
DO $$
BEGIN
    RAISE NOTICE 'Database initialized with % items', (SELECT COUNT(*) FROM items);
END $$;
