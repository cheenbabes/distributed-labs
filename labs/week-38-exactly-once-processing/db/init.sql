-- Initialize database for exactly-once processing lab

-- Table to track processed messages (for idempotency)
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id VARCHAR(255) PRIMARY KEY,
    payload JSONB NOT NULL,
    processed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    consumer_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_processed_messages_consumer
ON processed_messages(consumer_id);

CREATE INDEX IF NOT EXISTS idx_processed_messages_processed_at
ON processed_messages(processed_at);

-- Table to store actual orders (business data)
CREATE TABLE IF NOT EXISTS orders (
    order_id VARCHAR(255) PRIMARY KEY,
    customer_id VARCHAR(255) NOT NULL,
    items JSONB NOT NULL,
    total_amount DECIMAL(10, 2) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for orders table
CREATE INDEX IF NOT EXISTS idx_orders_customer
ON orders(customer_id);

CREATE INDEX IF NOT EXISTS idx_orders_status
ON orders(status);

CREATE INDEX IF NOT EXISTS idx_orders_created_at
ON orders(created_at);

-- Table to track processing attempts (for debugging)
CREATE TABLE IF NOT EXISTS processing_log (
    id SERIAL PRIMARY KEY,
    message_id VARCHAR(255) NOT NULL,
    event_type VARCHAR(50) NOT NULL, -- 'received', 'processed', 'duplicate', 'failed'
    consumer_id VARCHAR(255) NOT NULL,
    details JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_processing_log_message
ON processing_log(message_id);

CREATE INDEX IF NOT EXISTS idx_processing_log_event_type
ON processing_log(event_type);

-- View for easy duplicate detection
CREATE OR REPLACE VIEW v_duplicate_analysis AS
SELECT
    message_id,
    COUNT(*) as total_attempts,
    COUNT(DISTINCT consumer_id) as unique_consumers,
    MIN(created_at) as first_attempt,
    MAX(created_at) as last_attempt,
    EXTRACT(EPOCH FROM (MAX(created_at) - MIN(created_at))) as time_span_seconds
FROM processing_log
WHERE event_type = 'received'
GROUP BY message_id
HAVING COUNT(*) > 1;

-- Summary statistics view
CREATE OR REPLACE VIEW v_processing_stats AS
SELECT
    (SELECT COUNT(*) FROM processed_messages) as total_processed,
    (SELECT COUNT(*) FROM orders) as total_orders,
    (SELECT COUNT(DISTINCT consumer_id) FROM processed_messages) as unique_consumers,
    (SELECT COUNT(*) FROM processing_log WHERE event_type = 'duplicate') as duplicates_caught,
    (SELECT COUNT(*) FROM processing_log WHERE event_type = 'failed') as failures;

-- Function to log processing events
CREATE OR REPLACE FUNCTION log_processing_event(
    p_message_id VARCHAR(255),
    p_event_type VARCHAR(50),
    p_consumer_id VARCHAR(255),
    p_details JSONB DEFAULT NULL
) RETURNS void AS $$
BEGIN
    INSERT INTO processing_log (message_id, event_type, consumer_id, details)
    VALUES (p_message_id, p_event_type, p_consumer_id, p_details);
END;
$$ LANGUAGE plpgsql;

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO lab;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO lab;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO lab;

-- Initial message for verification
INSERT INTO processing_log (message_id, event_type, consumer_id, details)
VALUES ('system', 'init', 'database', '{"message": "Database initialized successfully"}');
