# Lab 48: Unique ID Generator (Snowflake IDs)

How do you generate unique IDs across multiple servers without coordination? In this lab, you'll build and explore Twitter's Snowflake algorithm - a distributed ID generation system that creates unique, time-sortable 64-bit IDs without a central database.

## What You'll Learn

- How Snowflake IDs are structured (timestamp + datacenter + worker + sequence)
- How multiple generators coordinate to avoid collisions
- How clock skew affects distributed ID generation
- How to parse IDs to extract creation time and origin
- Tradeoffs between UUID, auto-increment, and Snowflake approaches

## Architecture

```
                                    ┌─────────────────┐
                                    │   Coordinator   │
                                    │  (Worker IDs)   │
                                    └────────┬────────┘
                                             │ assigns worker IDs
                     ┌───────────────────────┼───────────────────────┐
                     ▼                       ▼                       ▼
            ┌────────────────┐      ┌────────────────┐      ┌────────────────┐
            │  ID Generator  │      │  ID Generator  │      │  ID Generator  │
            │   Worker 0     │      │   Worker 1     │      │   Worker 2     │
            └────────┬───────┘      └────────┬───────┘      └────────┬───────┘
                     │                       │                       │
                     └───────────────────────┼───────────────────────┘
                                             ▼
                                    ┌─────────────────┐
                                    │     Client      │
                                    │ (Load Balancer) │
                                    └─────────────────┘
```

Each ID generator can produce 4,096 unique IDs per millisecond. With 3 generators, that's 12,288 IDs/ms or over 12 million IDs per second!

## Snowflake ID Structure

A 64-bit Snowflake ID is composed of:

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
├─┼─────────────────────────────────────────────────────────────┤
│0│                         Timestamp                           │
├─┼─────────────────────────────────────────────────────────────┤
 ^
 │  41 bits (milliseconds since epoch, ~69 years)

│                               │           │         │         │
├───────────────────────────────┼───────────┼─────────┼─────────┤
│           Timestamp           │ Datacenter│ Worker  │ Sequence│
│           (41 bits)           │ (5 bits)  │ (5 bits)│(12 bits)│
└───────────────────────────────┴───────────┴─────────┴─────────┘

- Unused: 1 bit (always 0, keeps ID positive in signed systems)
- Timestamp: 41 bits = ~69 years of milliseconds
- Datacenter ID: 5 bits = 32 datacenters
- Worker ID: 5 bits = 32 workers per datacenter
- Sequence: 12 bits = 4,096 IDs per millisecond per worker
```

**Total capacity**: 32 datacenters x 32 workers x 4,096 IDs/ms = 4,194,304 IDs/ms

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- jq (for JSON parsing)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Verify Services Are Running

```bash
docker compose ps
```

All services should show as "healthy".

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Client API | http://localhost:8000 | Entry point for ID requests |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |
| Coordinator | http://localhost:8100 | Worker ID management |
| Generator 1 | http://localhost:8001 | Direct generator access |
| Generator 2 | http://localhost:8002 | Direct generator access |
| Generator 3 | http://localhost:8003 | Direct generator access |

## Lab Exercises

### Exercise 1: Generate and Parse Your First Snowflake ID

Generate a single ID and understand its components.

```bash
# Generate a single ID
curl -s http://localhost:8000/id | jq

# Parse the ID to see its components
ID=$(curl -s http://localhost:8000/id | jq -r '.id')
curl -s "http://localhost:8000/parse/$ID" | jq
```

**Expected output:**
```json
{
  "id": 1234567890123456789,
  "id_str": "1234567890123456789",
  "components": {
    "timestamp_ms": 1737331200000,
    "timestamp_utc": "2025-01-20T00:00:00.000Z",
    "datacenter_id": 1,
    "worker_id": 0,
    "sequence": 42
  },
  "age_ms": 150,
  "binary": "0001000100110101..."
}
```

**Questions:**
- What worker generated this ID?
- When was it created?
- What's the sequence number?

---

### Exercise 2: Observe Worker Distribution

Generate multiple IDs and see how they're distributed across workers.

```bash
# Generate 20 IDs and see which workers created them
for i in {1..20}; do
  curl -s http://localhost:8000/id | jq -r '"\(.id) -> worker \(.worker_id)"'
done
```

```bash
# Check which workers are registered with the coordinator
curl -s http://localhost:8100/workers | jq
```

**Question:** Are IDs evenly distributed across workers? Why or why not?

---

### Exercise 3: Understand Time-Sortability

Snowflake IDs are time-sortable - newer IDs are always larger.

```bash
# Generate IDs with delays and compare
echo "Generating IDs with 1-second delays..."
ID1=$(curl -s http://localhost:8000/id | jq -r '.id')
sleep 1
ID2=$(curl -s http://localhost:8000/id | jq -r '.id')
sleep 1
ID3=$(curl -s http://localhost:8000/id | jq -r '.id')

echo "ID1: $ID1"
echo "ID2: $ID2"
echo "ID3: $ID3"

# Verify they're in order
if [ "$ID1" -lt "$ID2" ] && [ "$ID2" -lt "$ID3" ]; then
  echo "IDs are correctly time-ordered!"
fi

# Parse to see timestamps
echo -e "\nTimestamps:"
curl -s "http://localhost:8000/parse/$ID1" | jq '.components.timestamp_utc'
curl -s "http://localhost:8000/parse/$ID2" | jq '.components.timestamp_utc'
curl -s "http://localhost:8000/parse/$ID3" | jq '.components.timestamp_utc'
```

**Key insight:** You can determine when a record was created just by looking at its ID!

---

### Exercise 4: High-Throughput Generation

Generate many IDs quickly and observe sequence numbers.

```bash
# Generate 100 IDs from a single generator as fast as possible
curl -s "http://localhost:8001/generate/batch?count=100" | jq

# Check the sequence numbers used
curl -s "http://localhost:8001/generate/batch?count=100" | jq '.ids[:5]'
```

**Questions:**
- What happens to the sequence number within the same millisecond?
- What's the maximum sequence number before the generator must wait?

---

### Exercise 5: Simulate Clock Skew

Clock skew is a major challenge for distributed ID generation. Let's see what happens.

```bash
# Check current generator status
curl -s http://localhost:8001/info | jq

# Simulate clock moving backwards by 10ms
curl -s -X POST "http://localhost:8001/admin/clock-offset?offset_ms=-10" | jq

# Try to generate an ID - the generator should handle it
curl -s http://localhost:8001/generate | jq

# Check metrics for clock skew events
curl -s http://localhost:8001/metrics | grep clock_skew
```

```bash
# Simulate larger clock skew (100ms backwards)
curl -s -X POST "http://localhost:8001/admin/clock-offset?offset_ms=-100" | jq

# This should fail with an error
curl -s http://localhost:8001/generate | jq
```

```bash
# Reset clock offset
curl -s -X POST "http://localhost:8001/admin/clock-offset?offset_ms=0" | jq
```

**Questions:**
- How does the generator handle small clock skew?
- What happens with large clock skew?
- Why is clock synchronization (NTP) critical for Snowflake?

---

### Exercise 6: Collision Detection

What if two workers got the same worker ID? Let's check the collision detection.

```bash
# Check current collision stats
curl -s http://localhost:8000/collision-stats | jq

# Generate many IDs from all generators in parallel
curl -s "http://localhost:8000/ids/parallel?count=1000" | jq '{
  total_ids: .total_ids,
  collisions: .collisions_detected,
  generators: .generators_used
}'

# Check collision stats again
curl -s http://localhost:8000/collision-stats | jq
```

With properly configured workers, you should see **zero collisions**!

---

### Exercise 7: Load Test and Monitor

Run a sustained load test while watching the Grafana dashboard.

```bash
# Open Grafana in your browser: http://localhost:3001
# Navigate to the "Snowflake ID Generator" dashboard

# Run the quick load test (50 seconds)
docker compose run --rm lab48-k6 run /scripts/quick.js
```

**Watch in Grafana:**
- ID Generation Rate - should be stable across workers
- Sequence numbers - watch them increment
- Clock skew events - should be zero
- Collisions - must stay at zero

---

### Exercise 8: Worker Registration and Failover

What happens when a generator goes down?

```bash
# Check active workers
curl -s http://localhost:8100/workers | jq

# Stop one generator
docker compose stop lab48-id-generator-2

# Check workers again - it should be deregistered
curl -s http://localhost:8100/workers | jq

# Generate some IDs - they should still work
for i in {1..10}; do
  curl -s http://localhost:8000/id | jq -r '.worker_id'
done

# Restart the generator
docker compose start lab48-id-generator-2

# It should get its worker ID back (or a new one)
sleep 5
curl -s http://localhost:8100/workers | jq
```

---

### Exercise 9: Compare ID Strategies

Generate IDs of different types and compare them.

```bash
# Snowflake ID (64-bit, time-sortable)
SNOWFLAKE=$(curl -s http://localhost:8000/id | jq -r '.id')
echo "Snowflake: $SNOWFLAKE (${#SNOWFLAKE} chars)"

# For comparison, a UUID would be:
UUID=$(uuidgen)
echo "UUID:      $UUID (${#UUID} chars)"

# And an auto-increment would be:
echo "Auto-inc:  12345 (5 chars, requires central DB)"

# Snowflake advantages:
# - Roughly time-sortable
# - No central coordination needed
# - Fits in a 64-bit integer (good for databases)
# - Encodes metadata (worker, datacenter)
```

**Comparison Table:**

| Feature | Auto-increment | UUID | Snowflake |
|---------|---------------|------|-----------|
| Size | 32-64 bits | 128 bits | 64 bits |
| Coordination | Central DB | None | Worker ID assignment |
| Time-sortable | Yes | No (v4) | Yes |
| Guessable | Very | No | Somewhat |
| Embeds metadata | No | No | Yes |

---

## Key Takeaways

1. **Snowflake IDs are composable** - Timestamp + Datacenter + Worker + Sequence

2. **Time is embedded in the ID** - You can extract creation time from any ID

3. **Clock synchronization is critical** - NTP must be running on all servers

4. **Worker IDs must be unique** - The coordinator ensures no duplicates

5. **Sequence handles bursts** - 4,096 IDs per millisecond is usually enough

6. **IDs are time-sortable** - Great for database indexing and pagination

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Generators not registering with coordinator
```bash
docker compose logs lab48-coordinator
docker compose logs lab48-id-generator-1
```

### Clock skew errors
```bash
# Reset clock offset on all generators
for port in 8001 8002 8003; do
  curl -s -X POST "http://localhost:$port/admin/clock-offset?offset_ms=0"
done
```

### Collision detected (shouldn't happen!)
```bash
# Check worker IDs - they should all be different
curl -s http://localhost:8001/info | jq '.worker_id'
curl -s http://localhost:8002/info | jq '.worker_id'
curl -s http://localhost:8003/info | jq '.worker_id'
```

## Going Further

- **Epoch selection**: Why did Twitter choose a custom epoch? What happens when it runs out?
- **ID length**: Could you use fewer bits? What are the tradeoffs?
- **Distributed clocks**: How would you handle NTP failures?
- **Shard keys**: How would you use Snowflake IDs for database sharding?

## References

- [Twitter's Snowflake (archived)](https://github.com/twitter-archive/snowflake)
- [Baidu's uid-generator](https://github.com/baidu/uid-generator)
- [Sony's Sonyflake](https://github.com/sony/sonyflake)
- [Instagram's sharding and IDs](https://instagram-engineering.com/sharding-ids-at-instagram-1cf5a71e5a5c)
