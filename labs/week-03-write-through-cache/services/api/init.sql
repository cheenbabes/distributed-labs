-- Initialize the items database for write-through cache lab

CREATE TABLE IF NOT EXISTS items (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at DESC);

-- Insert some sample data
INSERT INTO items (id, name, description, price, created_at, updated_at) VALUES
    ('550e8400-e29b-41d4-a716-446655440001', 'Widget A', 'A fantastic widget for all your needs', 29.99, NOW(), NOW()),
    ('550e8400-e29b-41d4-a716-446655440002', 'Gadget B', 'The latest gadget with amazing features', 49.99, NOW(), NOW()),
    ('550e8400-e29b-41d4-a716-446655440003', 'Gizmo C', 'A reliable gizmo that never fails', 19.99, NOW(), NOW());

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO labuser;
