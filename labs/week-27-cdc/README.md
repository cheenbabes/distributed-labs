# Lab 27: Change Data Capture (CDC)

How do you keep multiple systems in sync when data changes? Polling is slow and resource-intensive. In this lab, you'll use Change Data Capture (CDC) to stream database changes in real-time to downstream consumers.

## What You'll Learn

- How CDC captures database changes at the transaction log level
- Setting up Debezium to stream PostgreSQL changes to Kafka
- Building read models (search indexes) from change events
- Measuring and comparing CDC latency vs traditional polling
- Handling different change types: inserts, updates, deletes

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Writer Service │────▶│   PostgreSQL    │     │ Polling Service │
│   (writes)      │     │  (WAL enabled)  │◀────│  (comparison)   │
└─────────────────┘     └────────┬────────┘     └─────────────────┘
                                 │
                        ┌────────▼────────┐
                        │    Debezium     │
                        │  (CDC connector)│
                        └────────┬────────┘
                                 │
                        ┌────────▼────────┐
                        │     Kafka       │
                        │ (change events) │
                        └────────┬────────┘
                                 │
                        ┌────────▼────────┐     ┌─────────────────┐
                        │  CDC Consumer   │────▶│ Search Service  │
                        │ (event processor│     │  (read model)   │
                        └─────────────────┘     └─────────────────┘
```

**Key Components:**

- **Writer Service**: REST API that writes to PostgreSQL (products and orders)
- **PostgreSQL**: Source database with logical replication enabled
- **Debezium**: CDC connector that reads the WAL and produces Kafka messages
- **Kafka**: Message broker that streams change events
- **CDC Consumer**: Processes change events and updates downstream systems
- **Search Service**: In-memory search index built from CDC events
- **Polling Service**: Traditional polling approach for comparison

## Prerequisites

- Docker and Docker Compose
- curl (for API calls)
- jq (for JSON parsing)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Wait for Services to be Healthy

```bash
# Wait for all services (this may take 1-2 minutes)
docker compose ps

# Check Debezium is ready
curl -s http://localhost:8083/connectors | jq
```

### 3. Register the Debezium Connector

```bash
# Register the PostgreSQL connector
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d @infra/debezium/register-connector.json | jq

# Verify connector is running
curl -s http://localhost:8083/connectors/products-connector/status | jq
```

### 4. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Writer API | http://localhost:8000 | Create/update products and orders |
| CDC Consumer | http://localhost:8001 | View captured CDC events |
| Search Service | http://localhost:8002 | Query the CDC-built search index |
| Polling Service | http://localhost:8003 | Compare with polling approach |
| Kafka UI | http://localhost:8080 | View Kafka topics and messages |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: See CDC in Action

Create a product and watch it flow through the CDC pipeline.

```bash
# Create a new product
curl -X POST http://localhost:8000/products \
  -H "Content-Type: application/json" \
  -d '{
    "name": "CDC Test Product",
    "description": "Testing change data capture",
    "price": 99.99,
    "category": "Electronics",
    "stock_quantity": 50
  }' | jq

# View the CDC event in the consumer
curl -s http://localhost:8001/events | jq '.events[-1]'

# Check the search index was updated
curl -s http://localhost:8002/search?q=CDC | jq
```

**Questions:**
- How quickly did the change appear in the CDC consumer?
- What information does the CDC event contain?

---

### Exercise 2: Build a Search Index from CDC Events

The search service maintains an in-memory index built entirely from CDC events.

```bash
# Check current index size
curl -s http://localhost:8002/index/status | jq

# Search for products
curl -s "http://localhost:8002/search?q=laptop" | jq

# List categories (aggregated from CDC events)
curl -s http://localhost:8002/categories | jq

# Create more products and watch the index grow
for i in {1..5}; do
  curl -s -X POST http://localhost:8000/products \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"Search Test $i\",
      \"price\": $((i * 10)).99,
      \"category\": \"Test\"
    }" > /dev/null
done

# Check index again
curl -s http://localhost:8002/index/status | jq
```

**Observe:**
- The search index is built without querying the source database
- Each CDC event triggers an index update
- The search service stays in sync automatically

---

### Exercise 3: Observe Consumer Lag During High Write Load

Generate a burst of writes and watch the CDC consumer lag.

```bash
# Check current lag
curl -s http://localhost:8001/events/stats | jq

# Create 20 products quickly
curl -X POST "http://localhost:8000/products/bulk?count=20" | jq

# Watch the lag
watch -n 1 'curl -s http://localhost:8001/events/stats | jq ".avg_lag_ms"'
```

Or run the k6 burst test:

```bash
docker compose run --rm k6 run /scripts/burst-test.js
```

**Questions:**
- What happens to consumer lag during the burst?
- How quickly does it recover?
- Check Grafana dashboard for lag visualization

---

### Exercise 4: Compare Polling vs CDC Latency

The polling service detects changes by querying the database. Compare its detection latency with CDC.

```bash
# Check polling service status
curl -s http://localhost:8003/status | jq

# Check current poll interval
curl -s http://localhost:8003/compare | jq

# Create a product and compare detection times
BEFORE=$(date +%s%3N)
curl -s -X POST http://localhost:8000/products \
  -H "Content-Type: application/json" \
  -d '{"name": "Latency Test", "price": 50}' | jq
AFTER=$(date +%s%3N)

echo "Write took: $((AFTER - BEFORE))ms"

# Wait for both systems to detect
sleep 2

# Check CDC detection
curl -s http://localhost:8001/events | jq '.events[-1]'

# Check polling detection
curl -s http://localhost:8003/changes | jq '.changes[-1]'
```

**Key Differences:**
- CDC: Near real-time (typically <100ms)
- Polling: Up to poll_interval (default 1000ms)

---

### Exercise 5: Handle Schema Changes

What happens when you update a product? CDC captures both before and after states.

```bash
# Get a product ID
PRODUCT_ID=$(curl -s http://localhost:8000/products | jq '.products[0].id')
echo "Testing with product ID: $PRODUCT_ID"

# Update the product
curl -X PUT "http://localhost:8000/products/$PRODUCT_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "price": 199.99,
    "stock_quantity": 25
  }' | jq

# View the CDC event (shows operation type)
curl -s http://localhost:8001/events | jq '.events[-1]'

# Delete a product
curl -X DELETE "http://localhost:8000/products/$PRODUCT_ID" | jq

# View the delete event
curl -s http://localhost:8001/events | jq '.events[-1]'

# Verify it's removed from search index
curl -s "http://localhost:8002/products/$PRODUCT_ID"
# Should return 404
```

**CDC Operation Types:**
- `create` (c): New row inserted
- `update` (u): Existing row modified
- `delete` (d): Row removed
- `read` (r): Snapshot during initial sync

---

### Exercise 6: Explore Kafka Topics

Use Kafka UI to explore the CDC messages directly.

1. Open http://localhost:8080
2. Navigate to Topics
3. Find `cdc.public.products` and `cdc.public.orders`
4. Click on a topic and view Messages

Or use the command line:

```bash
# List topics
docker compose exec kafka kafka-topics --bootstrap-server localhost:29092 --list

# Read from products topic
docker compose exec kafka kafka-console-consumer \
  --bootstrap-server localhost:29092 \
  --topic cdc.public.products \
  --from-beginning \
  --max-messages 5
```

**Observe:**
- Each database change becomes a Kafka message
- Messages are partitioned by primary key
- Consumers can replay from any offset

---

## Key Metrics to Watch

| Metric | Description | What to Look For |
|--------|-------------|------------------|
| `cdc_events_received_total` | Total CDC events received | Should match DB writes |
| `cdc_consumer_lag_ms` | Lag between DB change and processing | Should stay low (<100ms) |
| `cdc_events_processed_total` | Successfully processed events | Should match received |
| `polling_detection_lag_ms` | Polling detection delay | Will be higher than CDC |
| `search_index_size` | Documents in search index | Should match product count |

## Key Takeaways

1. **CDC is Near Real-Time** - Changes are captured from the database transaction log, not by polling

2. **No Additional Database Load** - CDC reads the WAL, which PostgreSQL already maintains

3. **Full History Available** - CDC captures every change, including intermediate states that polling would miss

4. **Before/After States** - Updates include both old and new values, enabling complex event processing

5. **Resilient to Failures** - Events are persisted in Kafka; consumers can catch up after downtime

6. **Decoupled Systems** - Producers and consumers don't need to know about each other

## CDC vs Polling Comparison

| Aspect | CDC | Polling |
|--------|-----|---------|
| Latency | ~10-100ms | Up to poll interval |
| Database Load | Minimal (reads WAL) | Queries on every poll |
| Missed Changes | None | Can miss between polls |
| Before State | Available | Not available |
| Complexity | Higher (Kafka, Debezium) | Simple SQL queries |
| Scalability | Excellent | Limited by DB |

## Troubleshooting

### Debezium Connector Not Starting
```bash
# Check connector status
curl -s http://localhost:8083/connectors/products-connector/status | jq

# Check Debezium logs
docker compose logs debezium
```

### No Events in CDC Consumer
```bash
# Verify Kafka topics exist
docker compose exec kafka kafka-topics --bootstrap-server localhost:29092 --list

# Check consumer logs
docker compose logs cdc-consumer
```

### Search Index Empty
```bash
# Check if CDC consumer is forwarding events
curl -s http://localhost:8001/events | jq '.events | length'

# Check search service health
curl -s http://localhost:8002/health | jq
```

## Cleanup

```bash
docker compose down -v
```

## Further Reading

- [Debezium Documentation](https://debezium.io/documentation/)
- [PostgreSQL Logical Replication](https://www.postgresql.org/docs/current/logical-replication.html)
- [Kafka Connect](https://kafka.apache.org/documentation/#connect)
- [CQRS and Event Sourcing](https://martinfowler.com/bliki/CQRS.html)

## Next Lab

[Lab 28: Event-Driven Sagas](../week-28-event-sagas/) - Coordinate distributed transactions using the saga pattern with compensating actions.
