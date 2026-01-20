-- Idempotency Keys Lab Database Schema
-- This schema demonstrates the Stripe-style idempotency key pattern

-- ==================================================
-- Idempotency Keys Table
-- ==================================================
-- Stores idempotency keys and their associated request/response data
-- This is the core of the idempotency pattern

CREATE TABLE idempotency_keys (
    -- The idempotency key provided by the client
    key VARCHAR(255) PRIMARY KEY,

    -- Hash of the request body for conflict detection
    -- If the same key is used with a different body, we return 409
    request_hash VARCHAR(64) NOT NULL,

    -- Current status of the request
    -- 'processing' = request is in flight
    -- 'complete' = request completed successfully
    -- 'failed' = request failed
    status VARCHAR(20) NOT NULL DEFAULT 'processing',

    -- The response body to return for retries
    -- Stored as JSON so we can return the exact same response
    response_body JSONB,

    -- When this key was created
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- When this key expires (after which it can be reused)
    -- Stripe uses 24 hours; this is configurable via environment
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Constraint to ensure status is valid
    CONSTRAINT valid_status CHECK (status IN ('processing', 'complete', 'failed'))
);

-- Index for efficient cleanup of expired keys
CREATE INDEX idx_idempotency_keys_expires_at ON idempotency_keys(expires_at);

-- Index for status queries (finding processing requests)
CREATE INDEX idx_idempotency_keys_status ON idempotency_keys(status);

-- ==================================================
-- Payments Table
-- ==================================================
-- Stores actual payment records
-- In a real system, this would have much more detail

CREATE TABLE payments (
    -- Stripe-style payment ID (pay_xxxxx)
    id VARCHAR(50) PRIMARY KEY,

    -- Amount in smallest currency unit (cents for USD)
    amount INTEGER NOT NULL CHECK (amount > 0),

    -- Three-letter ISO currency code
    currency VARCHAR(3) NOT NULL DEFAULT 'usd',

    -- Optional description
    description TEXT,

    -- Payment status
    status VARCHAR(20) NOT NULL DEFAULT 'pending',

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Constraint for valid status
    CONSTRAINT valid_payment_status CHECK (status IN ('pending', 'processing', 'succeeded', 'failed', 'canceled'))
);

-- Index for listing payments by status
CREATE INDEX idx_payments_status ON payments(status);

-- Index for listing payments by creation time
CREATE INDEX idx_payments_created_at ON payments(created_at DESC);

-- ==================================================
-- Helper Functions
-- ==================================================

-- Function to automatically update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger to auto-update payments.updated_at
CREATE TRIGGER update_payments_updated_at
    BEFORE UPDATE ON payments
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ==================================================
-- Useful Views for Debugging
-- ==================================================

-- View to see idempotency key statistics
CREATE VIEW idempotency_stats AS
SELECT
    status,
    COUNT(*) as count,
    MIN(created_at) as oldest,
    MAX(created_at) as newest
FROM idempotency_keys
WHERE expires_at > NOW()
GROUP BY status;

-- View to see payment statistics
CREATE VIEW payment_stats AS
SELECT
    status,
    COUNT(*) as count,
    SUM(amount) as total_amount,
    AVG(amount) as avg_amount
FROM payments
GROUP BY status;

-- ==================================================
-- Sample Data (for testing)
-- ==================================================

-- Insert some sample payments to demonstrate the schema
-- (These will be visible in the initial state of the lab)

-- Uncomment below to add sample data:
-- INSERT INTO payments (id, amount, currency, description, status) VALUES
--     ('pay_sample001', 2500, 'usd', 'Sample payment 1', 'succeeded'),
--     ('pay_sample002', 5000, 'usd', 'Sample payment 2', 'succeeded'),
--     ('pay_sample003', 1000, 'eur', 'Sample payment 3', 'pending');

-- ==================================================
-- Comments
-- ==================================================

COMMENT ON TABLE idempotency_keys IS 'Stores idempotency keys for exactly-once payment processing';
COMMENT ON TABLE payments IS 'Stores payment records';
COMMENT ON COLUMN idempotency_keys.request_hash IS 'SHA-256 hash of the request body for conflict detection';
COMMENT ON COLUMN idempotency_keys.response_body IS 'Cached response to return for retry requests';
COMMENT ON COLUMN idempotency_keys.expires_at IS 'Key expiration time - after this, the key can be reused';
