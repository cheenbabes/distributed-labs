-- Initialize database for connection pool lab

-- Create extension for UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create sample table
CREATE TABLE IF NOT EXISTS items (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed with sample data
INSERT INTO items (name, data)
SELECT
    'Item ' || i,
    repeat('Sample data for item ' || i || '. ', 10)
FROM generate_series(1, 100) AS i;

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at);

-- Grant privileges
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO labuser;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO labuser;
