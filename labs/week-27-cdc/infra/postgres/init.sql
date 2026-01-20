-- Initialize database for CDC lab
-- Enable logical replication for Debezium

-- Create the products table
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price DECIMAL(10, 2) NOT NULL,
    category VARCHAR(100),
    stock_quantity INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create a trigger to auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Create an orders table to demonstrate more complex CDC scenarios
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    product_id INTEGER REFERENCES products(id),
    customer_email VARCHAR(255) NOT NULL,
    quantity INTEGER NOT NULL,
    total_amount DECIMAL(10, 2) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER update_orders_updated_at
    BEFORE UPDATE ON orders
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Create index for common queries
CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_products_updated_at ON products(updated_at);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_product_id ON orders(product_id);

-- Insert some sample data
INSERT INTO products (name, description, price, category, stock_quantity) VALUES
    ('Laptop Pro 15', 'High-performance laptop with 15-inch display', 1299.99, 'Electronics', 50),
    ('Wireless Mouse', 'Ergonomic wireless mouse with long battery life', 29.99, 'Electronics', 200),
    ('USB-C Hub', '7-in-1 USB-C hub with HDMI and Ethernet', 49.99, 'Electronics', 150),
    ('Mechanical Keyboard', 'RGB mechanical keyboard with Cherry MX switches', 129.99, 'Electronics', 75),
    ('Monitor Stand', 'Adjustable monitor stand with USB ports', 79.99, 'Office', 100),
    ('Desk Lamp', 'LED desk lamp with adjustable brightness', 39.99, 'Office', 120),
    ('Notebook Set', 'Premium notebook set for professionals', 24.99, 'Office', 300),
    ('Coffee Mug', 'Insulated coffee mug - 16oz', 19.99, 'Kitchen', 500),
    ('Water Bottle', 'Stainless steel water bottle - 32oz', 29.99, 'Kitchen', 400),
    ('Backpack', 'Laptop backpack with multiple compartments', 89.99, 'Accessories', 80);

-- Grant replication privileges for Debezium
-- Note: The user is already created by POSTGRES_USER env var
ALTER ROLE cdc_user WITH REPLICATION;

-- Create publication for CDC
CREATE PUBLICATION cdc_publication FOR ALL TABLES;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE 'Database initialization completed successfully';
END $$;
