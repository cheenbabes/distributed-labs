-- URL Shortener Database Schema (Scaled Version)

-- Create sequence for counter-based key generation
CREATE SEQUENCE IF NOT EXISTS url_counter_seq START WITH 1;

-- Main URLs table with instance tracking
CREATE TABLE IF NOT EXISTS urls (
    id SERIAL PRIMARY KEY,
    short_code VARCHAR(20) UNIQUE NOT NULL,
    original_url TEXT NOT NULL,
    strategy VARCHAR(20) NOT NULL DEFAULT 'counter',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    access_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP WITH TIME ZONE,
    created_by_instance VARCHAR(10)
);

-- Index on short_code for fast lookups
CREATE INDEX IF NOT EXISTS idx_urls_short_code ON urls(short_code);

-- Index on created_at for recent URLs queries
CREATE INDEX IF NOT EXISTS idx_urls_created_at ON urls(created_at DESC);

-- Index on original_url for deduplication (hash strategy)
CREATE INDEX IF NOT EXISTS idx_urls_original_url ON urls USING hash(original_url);

-- Index on created_by_instance for analytics
CREATE INDEX IF NOT EXISTS idx_urls_instance ON urls(created_by_instance);

-- Analytics aggregation table
CREATE TABLE IF NOT EXISTS analytics_hourly (
    id SERIAL PRIMARY KEY,
    hour TIMESTAMP WITH TIME ZONE NOT NULL,
    instance_id VARCHAR(10),
    total_shortens INTEGER DEFAULT 0,
    total_redirects INTEGER DEFAULT 0,
    cache_hits INTEGER DEFAULT 0,
    cache_misses INTEGER DEFAULT 0,
    rate_limit_hits INTEGER DEFAULT 0,
    UNIQUE(hour, instance_id)
);

-- Insert some sample data
INSERT INTO urls (short_code, original_url, strategy, access_count, created_by_instance) VALUES
    ('example1', 'https://www.google.com', 'counter', 150, '1'),
    ('example2', 'https://www.github.com', 'counter', 89, '2'),
    ('example3', 'https://www.stackoverflow.com', 'hash', 234, '3')
ON CONFLICT (short_code) DO NOTHING;

-- Update sequence to account for sample data
SELECT setval('url_counter_seq', 100, false);
