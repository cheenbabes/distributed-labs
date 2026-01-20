-- Initialize database schema for write-behind cache lab

CREATE TABLE IF NOT EXISTS products (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_products_updated_at ON products(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);

-- Insert some seed data
INSERT INTO products (id, name, price, quantity, created_at, updated_at) VALUES
    ('seed-001', 'Widget A', 19.99, 100, NOW(), NOW()),
    ('seed-002', 'Widget B', 29.99, 50, NOW(), NOW()),
    ('seed-003', 'Gadget X', 49.99, 25, NOW(), NOW()),
    ('seed-004', 'Gadget Y', 79.99, 10, NOW(), NOW()),
    ('seed-005', 'Tool Z', 99.99, 5, NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

-- Create a view for monitoring
CREATE OR REPLACE VIEW product_stats AS
SELECT
    COUNT(*) as total_products,
    SUM(quantity) as total_inventory,
    AVG(price) as avg_price,
    MAX(updated_at) as last_update
FROM products;
