-- URL Shortener Database Schema

-- Create sequence for counter-based key generation
CREATE SEQUENCE IF NOT EXISTS url_counter_seq START WITH 1;

-- Main URLs table
CREATE TABLE IF NOT EXISTS urls (
    id SERIAL PRIMARY KEY,
    short_code VARCHAR(20) UNIQUE NOT NULL,
    original_url TEXT NOT NULL,
    strategy VARCHAR(20) NOT NULL DEFAULT 'counter',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    access_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP WITH TIME ZONE
);

-- Index on short_code for fast lookups
CREATE INDEX IF NOT EXISTS idx_urls_short_code ON urls(short_code);

-- Index on created_at for recent URLs queries
CREATE INDEX IF NOT EXISTS idx_urls_created_at ON urls(created_at DESC);

-- Index on original_url for deduplication (hash strategy)
CREATE INDEX IF NOT EXISTS idx_urls_original_url ON urls USING hash(original_url);

-- Insert some sample data
INSERT INTO urls (short_code, original_url, strategy, access_count) VALUES
    ('example1', 'https://www.google.com', 'counter', 150),
    ('example2', 'https://www.github.com', 'counter', 89),
    ('example3', 'https://www.stackoverflow.com', 'hash', 234)
ON CONFLICT (short_code) DO NOTHING;

-- Update sequence to account for sample data
SELECT setval('url_counter_seq', 100, false);
