-- Initialize the ticket booking database

-- Events table
CREATE TABLE events (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    venue VARCHAR(255) NOT NULL,
    event_date TIMESTAMP NOT NULL,
    total_seats INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seats table with version column for optimistic locking
CREATE TABLE seats (
    id SERIAL PRIMARY KEY,
    event_id INTEGER REFERENCES events(id),
    seat_number VARCHAR(10) NOT NULL,
    section VARCHAR(50) NOT NULL,
    row_name VARCHAR(10) NOT NULL,
    is_booked BOOLEAN DEFAULT FALSE,
    booked_by VARCHAR(255),
    booked_at TIMESTAMP,
    version INTEGER DEFAULT 0,
    UNIQUE(event_id, seat_number)
);

-- Bookings table to track all booking attempts
CREATE TABLE bookings (
    id SERIAL PRIMARY KEY,
    event_id INTEGER REFERENCES events(id),
    seat_id INTEGER REFERENCES seats(id),
    customer_id VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL, -- 'confirmed', 'failed', 'double_booked'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    locking_strategy VARCHAR(20)
);

-- Index for faster seat lookups
CREATE INDEX idx_seats_event_booked ON seats(event_id, is_booked);
CREATE INDEX idx_bookings_event ON bookings(event_id);
CREATE INDEX idx_bookings_status ON bookings(status);

-- Insert a sample concert event
INSERT INTO events (name, venue, event_date, total_seats) VALUES
('Rock Festival 2024', 'Madison Square Garden', '2024-12-31 20:00:00', 100);

-- Insert 100 seats for the event (10 rows x 10 seats)
INSERT INTO seats (event_id, seat_number, section, row_name)
SELECT
    1,
    CONCAT(CHR(65 + (n / 10)), (n % 10) + 1),
    CASE
        WHEN n < 30 THEN 'VIP'
        WHEN n < 70 THEN 'Standard'
        ELSE 'Economy'
    END,
    CHR(65 + (n / 10))
FROM generate_series(0, 99) AS n;

-- View to see double bookings
CREATE VIEW double_bookings AS
SELECT
    s.id as seat_id,
    s.seat_number,
    s.event_id,
    COUNT(*) as booking_count,
    array_agg(b.customer_id) as customers
FROM seats s
JOIN bookings b ON s.id = b.seat_id
WHERE b.status = 'confirmed'
GROUP BY s.id, s.seat_number, s.event_id
HAVING COUNT(*) > 1;
