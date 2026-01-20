-- Initialize the products database for Cache-Aside Pattern Lab

CREATE TABLE IF NOT EXISTS products (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_products_created_at ON products(created_at DESC);

-- Insert some sample data
INSERT INTO products (id, name, description, price, created_at, updated_at) VALUES
    ('prod-001', 'Wireless Mouse', 'Ergonomic wireless mouse with 2.4GHz connectivity', 29.99, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('prod-002', 'Mechanical Keyboard', 'RGB mechanical keyboard with Cherry MX switches', 149.99, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('prod-003', 'USB-C Hub', '7-in-1 USB-C hub with HDMI and SD card reader', 49.99, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('prod-004', 'Monitor Stand', 'Adjustable aluminum monitor stand with storage', 79.99, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('prod-005', 'Webcam HD', '1080p HD webcam with built-in microphone', 59.99, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
