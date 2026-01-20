# Lab 25: Write-Ahead Log

Your database just crashed during a write. Is your data lost forever? In this lab, you'll implement and explore the Write-Ahead Log (WAL) pattern that makes databases durable and recoverable.

## What You'll Learn

- How write-ahead logging provides durability guarantees
- The relationship between WAL and crash recovery
- How checkpointing works to truncate the WAL
- The performance trade-off between durability and speed
- Why fsync() is critical for true durability

## The Problem

Consider this scenario:

1. You write `{"user": "alice", "balance": 100}` to your key-value store
2. The server acknowledges the write
3. The server crashes before the data reaches disk
4. On restart, the data is gone

Without durability guarantees, acknowledged writes can be lost. This is unacceptable for financial systems, user data, and any application where data loss is costly.

## The Solution: Write-Ahead Logging

Write-Ahead Logging (WAL) solves this by:

1. **Writing to log first**: Before modifying in-memory state, write the operation to a durable log
2. **Syncing to disk**: Use fsync() to ensure the log entry is on disk before acknowledging
3. **Then applying**: Apply the operation to in-memory state
4. **Recovery**: On crash, replay the log to restore state

```
Write Request
     |
     v
+----------------+
| Write to WAL   |  <-- Operation logged to disk first
| (fsync)        |
+----------------+
     |
     v
+----------------+
| Update Memory  |  <-- Then update in-memory state
+----------------+
     |
     v
+----------------+
| Acknowledge    |  <-- Only now is write confirmed
+----------------+
```

## Architecture

This lab runs two instances of the same KV store:

```
                     +----------------------+
                     |   lab25-kv-store-wal |  Port 8001
                     |   (WAL enabled)      |
+--------+           |                      |
| Client |---------->+   /data/wal.log      |  <-- Durable log
+--------+           |   /data/checkpoint   |
                     +----------------------+

                     +----------------------+
                     | lab25-kv-store-nowal |  Port 8002
                     |   (WAL disabled)     |
                     |                      |
                     |   (in-memory only)   |  <-- No durability
                     +----------------------+
```

## Prerequisites

- Docker and Docker Compose
- curl (for API calls)
- jq (optional, for pretty JSON)

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
| KV Store (WAL) | http://localhost:8001 | Durable key-value store |
| KV Store (no WAL) | http://localhost:8002 | Volatile key-value store |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Data Loss Without WAL

First, let's see what happens when we write data to the store without WAL and crash.

#### Step 1: Write data to the no-WAL store

```bash
# Write 10 key-value pairs
for i in {1..10}; do
  curl -s -X POST http://localhost:8002/kv \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"user-$i\", \"value\": {\"name\": \"User $i\", \"balance\": $((i * 100))}}"
  echo ""
done
```

#### Step 2: Verify data exists

```bash
curl -s http://localhost:8002/kv | jq
```

You should see 10 items.

#### Step 3: Crash the service

```bash
curl -s -X POST http://localhost:8002/admin/crash
```

The container will restart automatically.

#### Step 4: Check data after crash

```bash
# Wait for restart
sleep 3

# Check data
curl -s http://localhost:8002/kv | jq
```

**Expected result**: The data is gone! The store shows 0 items because everything was in memory.

---

### Exercise 2: Recovery With WAL

Now let's do the same with WAL enabled and see the difference.

#### Step 1: Write data to the WAL store

```bash
# Write 10 key-value pairs
for i in {1..10}; do
  curl -s -X POST http://localhost:8001/kv \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"user-$i\", \"value\": {\"name\": \"User $i\", \"balance\": $((i * 100))}}"
  echo ""
done
```

#### Step 2: Verify data exists

```bash
curl -s http://localhost:8001/kv | jq
```

#### Step 3: Check the WAL

```bash
curl -s http://localhost:8001/admin/wal | jq
```

You should see 10 WAL entries, one for each write.

#### Step 4: Crash the service

```bash
curl -s -X POST http://localhost:8001/admin/crash
```

#### Step 5: Check data after crash

```bash
# Wait for restart
sleep 3

# Check data
curl -s http://localhost:8001/kv | jq
```

**Expected result**: All 10 items are still there! The WAL was replayed on startup.

#### Step 6: Check recovery stats

```bash
curl -s http://localhost:8001/admin/stats | jq
```

Look for `recovered_entries` - this shows how many entries were replayed from the WAL.

---

### Exercise 3: Understanding the WAL Format

Let's examine what the WAL actually contains.

#### Step 1: Clear and write fresh data

```bash
# Clear the store
curl -s -X POST http://localhost:8001/admin/clear

# Write a few entries
curl -s -X POST http://localhost:8001/kv \
  -H "Content-Type: application/json" \
  -d '{"key": "config", "value": {"version": "1.0"}}'

curl -s -X POST http://localhost:8001/kv \
  -H "Content-Type: application/json" \
  -d '{"key": "settings", "value": {"theme": "dark"}}'

# Delete one
curl -s -X DELETE http://localhost:8001/kv/config
```

#### Step 2: Examine the WAL

```bash
curl -s http://localhost:8001/admin/wal | jq
```

**Things to notice:**
- Each entry has a `sequence` number
- Operations are `SET` or `DELETE`
- The `timestamp` shows when each operation occurred
- `value` is `null` for DELETE operations

**Question:** If you replay this WAL, what will the final state be?

---

### Exercise 4: Checkpoint and WAL Truncation

The WAL grows with every write. Checkpointing saves the current state and truncates the log.

#### Step 1: Write lots of data

```bash
# Write 150 entries (checkpoint threshold is 100)
for i in {1..150}; do
  curl -s -X POST http://localhost:8001/kv \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"item-$i\", \"value\": $i}" > /dev/null
done
echo "Wrote 150 entries"
```

#### Step 2: Check WAL size

```bash
curl -s http://localhost:8001/admin/stats | jq
```

Note the `wal_size_bytes` and `entries_since_checkpoint`.

Since we wrote 150 entries and the threshold is 100, a checkpoint should have occurred automatically.

#### Step 3: Manually trigger checkpoint

```bash
curl -s -X POST http://localhost:8001/admin/checkpoint | jq
```

#### Step 4: Check WAL after checkpoint

```bash
curl -s http://localhost:8001/admin/wal | jq
```

**Expected result**: The WAL is now empty (or very small). The state was saved to the checkpoint file.

#### Step 5: Crash and recover

```bash
curl -s -X POST http://localhost:8001/admin/crash
sleep 3
curl -s http://localhost:8001/admin/stats | jq
```

**Notice**: Recovery is now instant because the checkpoint contains the full state and there's nothing to replay.

---

### Exercise 5: Measuring the Performance Cost

WAL adds latency because every write must hit disk. Let's measure this.

#### Step 1: Measure write latency without WAL

```bash
echo "=== No-WAL Store ==="
for i in {1..10}; do
  start=$(date +%s%N)
  curl -s -X POST http://localhost:8002/kv \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"perf-$i\", \"value\": $i}" > /dev/null
  end=$(date +%s%N)
  echo "Write $i: $(( (end - start) / 1000000 ))ms"
done
```

#### Step 2: Measure write latency with WAL

```bash
echo "=== WAL Store ==="
for i in {1..10}; do
  start=$(date +%s%N)
  curl -s -X POST http://localhost:8001/kv \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"perf-$i\", \"value\": $i}" > /dev/null
  end=$(date +%s%N)
  echo "Write $i: $(( (end - start) / 1000000 ))ms"
done
```

#### Step 3: Run the load test

```bash
docker compose run --rm lab25-k6 run /scripts/basic.js
```

The test will show:
- Average latency for WAL writes
- Average latency for no-WAL writes
- The overhead percentage

**Typical results**: WAL adds 1-5ms per write due to fsync().

---

## Key Concepts

### Write-Ahead Logging Rules

1. **Log before modify**: Never modify state before logging
2. **Sync before acknowledge**: Use fsync() to ensure durability
3. **Idempotent replay**: Operations must be safely replayable
4. **Ordered recovery**: Replay must preserve operation order

### Checkpoint Trade-offs

| Factor | Small Threshold | Large Threshold |
|--------|-----------------|-----------------|
| Recovery time | Faster | Slower |
| Checkpoint frequency | More frequent | Less frequent |
| Checkpoint I/O | More overhead | Less overhead |
| WAL size | Smaller | Larger |

### Real-World Implementations

- **PostgreSQL**: Uses WAL for all changes
- **SQLite**: WAL mode for concurrent reads/writes
- **RocksDB**: Write-ahead log with compaction
- **etcd**: Raft log is a form of WAL

## Cleanup

```bash
docker compose down -v
```

The `-v` flag removes the volumes, deleting the WAL and checkpoint files.

## Troubleshooting

### Container keeps restarting
This is expected after a crash - the `restart: always` policy brings it back.

```bash
docker compose logs lab25-kv-store-wal --tail=50
```

### Data not recovering
Check if the volume exists:
```bash
docker volume ls | grep lab25
```

### Traces not appearing in Jaeger
Wait 10-15 seconds after making requests, then refresh Jaeger.

## Next Steps

- Explore how databases like PostgreSQL implement WAL
- Learn about group commit for batching WAL writes
- Study how distributed systems use WAL for replication
- Investigate LSM trees and their relationship to WAL

## Further Reading

- [PostgreSQL WAL Documentation](https://www.postgresql.org/docs/current/wal-intro.html)
- [SQLite WAL Mode](https://www.sqlite.org/wal.html)
- [Martin Kleppmann - Designing Data-Intensive Applications, Chapter 7](https://dataintensive.net/)
