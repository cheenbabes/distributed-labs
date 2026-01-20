# Demo Runbook: Write-Ahead Log

This runbook contains all commands for the video demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose down -v 2>/dev/null || true
docker volume rm lab25-wal-data lab25-nowal-data 2>/dev/null || true
```

---

## Part 1: Start the Lab

### Build and start all services

```bash {"name": "start-lab"}
docker compose up --build -d
```

### Wait for services to be healthy

```bash {"name": "wait-healthy"}
echo "Waiting for services to be healthy..."
sleep 15
docker compose ps
```

### Verify both KV stores are responding

```bash {"name": "verify-services"}
echo "=== KV Store with WAL (port 8001) ==="
curl -s http://localhost:8001/health | jq
echo ""
echo "=== KV Store without WAL (port 8002) ==="
curl -s http://localhost:8002/health | jq
```

---

## Part 2: Demonstrate Data Loss Without WAL

### Write data to the no-WAL store

```bash {"name": "write-nowal"}
echo "Writing 10 keys to the no-WAL store..."
for i in {1..10}; do
  curl -s -X POST http://localhost:8002/kv \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"user-$i\", \"value\": {\"name\": \"User $i\", \"balance\": $((i * 100))}}"
  echo ""
done
```

### Verify data exists

```bash {"name": "verify-nowal-data"}
echo "Data in no-WAL store:"
curl -s http://localhost:8002/kv | jq
echo ""
echo "Stats:"
curl -s http://localhost:8002/admin/stats | jq
```

### Crash the no-WAL store

```bash {"name": "crash-nowal"}
echo "Crashing the no-WAL store..."
curl -s -X POST http://localhost:8002/admin/crash | jq
echo ""
echo "Container will restart automatically..."
```

### Check data after crash (DATA LOST!)

```bash {"name": "check-nowal-after-crash"}
echo "Waiting for container to restart..."
sleep 5
echo ""
echo "Data in no-WAL store after crash:"
curl -s http://localhost:8002/kv | jq
echo ""
echo "ALL DATA LOST! The store is empty."
```

---

## Part 3: Demonstrate Recovery With WAL

### Write data to the WAL store

```bash {"name": "write-wal"}
echo "Writing 10 keys to the WAL store..."
for i in {1..10}; do
  curl -s -X POST http://localhost:8001/kv \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"user-$i\", \"value\": {\"name\": \"User $i\", \"balance\": $((i * 100))}}"
  echo ""
done
```

### Verify data exists

```bash {"name": "verify-wal-data"}
echo "Data in WAL store:"
curl -s http://localhost:8001/kv | jq
```

### Examine the WAL

```bash {"name": "view-wal"}
echo "WAL contents:"
curl -s http://localhost:8001/admin/wal | jq
```

### Crash the WAL store

```bash {"name": "crash-wal"}
echo "Crashing the WAL store..."
curl -s -X POST http://localhost:8001/admin/crash | jq
echo ""
echo "Container will restart automatically..."
```

### Check data after crash (DATA RECOVERED!)

```bash {"name": "check-wal-after-crash"}
echo "Waiting for container to restart..."
sleep 5
echo ""
echo "Data in WAL store after crash:"
curl -s http://localhost:8001/kv | jq
echo ""
echo "ALL DATA RECOVERED! The WAL was replayed."
```

### Check recovery stats

```bash {"name": "check-recovery-stats"}
echo "Recovery stats:"
curl -s http://localhost:8001/admin/stats | jq
echo ""
echo "Note the 'recovered_entries' field showing how many entries were replayed."
```

---

## Part 4: WAL Format Deep Dive

### Clear and write fresh data

```bash {"name": "fresh-data"}
# Clear the store
curl -s -X POST http://localhost:8001/admin/clear > /dev/null
echo "Store cleared"

# Write some entries
echo "Writing entries..."
curl -s -X POST http://localhost:8001/kv \
  -H "Content-Type: application/json" \
  -d '{"key": "config", "value": {"version": "1.0", "debug": false}}'
echo ""

curl -s -X POST http://localhost:8001/kv \
  -H "Content-Type: application/json" \
  -d '{"key": "settings", "value": {"theme": "dark", "lang": "en"}}'
echo ""

# Update one
curl -s -X POST http://localhost:8001/kv \
  -H "Content-Type: application/json" \
  -d '{"key": "config", "value": {"version": "2.0", "debug": true}}'
echo ""

# Delete one
curl -s -X DELETE http://localhost:8001/kv/settings
echo ""
```

### Examine WAL entries

```bash {"name": "examine-wal"}
echo "WAL entries (notice the operations and sequence numbers):"
curl -s http://localhost:8001/admin/wal | jq
```

---

## Part 5: Checkpointing

### Write lots of data

```bash {"name": "write-bulk"}
echo "Writing 150 entries (checkpoint threshold is 100)..."
for i in {1..150}; do
  curl -s -X POST http://localhost:8001/kv \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"item-$i\", \"value\": $i}" > /dev/null
done
echo "Done writing 150 entries"
```

### Check stats (should have auto-checkpointed)

```bash {"name": "check-checkpoint-stats"}
echo "Stats after bulk write:"
curl -s http://localhost:8001/admin/stats | jq
echo ""
echo "Note: entries_since_checkpoint should be < 100 if auto-checkpoint occurred"
```

### Manual checkpoint

```bash {"name": "manual-checkpoint"}
echo "Triggering manual checkpoint..."
curl -s -X POST http://localhost:8001/admin/checkpoint | jq
```

### Check WAL after checkpoint

```bash {"name": "wal-after-checkpoint"}
echo "WAL after checkpoint (should be empty):"
curl -s http://localhost:8001/admin/wal | jq
echo ""
echo "Stats:"
curl -s http://localhost:8001/admin/stats | jq
```

### Crash and fast recovery

```bash {"name": "fast-recovery"}
echo "Crashing to demonstrate fast checkpoint recovery..."
curl -s -X POST http://localhost:8001/admin/crash > /dev/null
sleep 5

echo "Stats after recovery:"
curl -s http://localhost:8001/admin/stats | jq
echo ""
echo "Notice: recovered_entries should be 0 or very small (checkpoint-based recovery)"
```

---

## Part 6: Performance Comparison

### Compare write latency

```bash {"name": "latency-comparison"}
echo "=== Write Latency Comparison ==="
echo ""
echo "No-WAL Store (memory only):"
for i in {1..5}; do
  start=$(python3 -c "import time; print(int(time.time()*1000))")
  curl -s -X POST http://localhost:8002/kv \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"perf-$i\", \"value\": $i}" > /dev/null
  end=$(python3 -c "import time; print(int(time.time()*1000))")
  echo "  Write $i: $((end - start))ms"
done

echo ""
echo "WAL Store (disk + memory):"
for i in {1..5}; do
  start=$(python3 -c "import time; print(int(time.time()*1000))")
  curl -s -X POST http://localhost:8001/kv \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"perf-$i\", \"value\": $i}" > /dev/null
  end=$(python3 -c "import time; print(int(time.time()*1000))")
  echo "  Write $i: $((end - start))ms"
done
```

### Run k6 load test

```bash {"name": "load-test"}
echo "Running k6 load test (2 minutes)..."
docker compose run --rm lab25-k6 run /scripts/basic.js
```

---

## Part 7: Side-by-Side Demo

### Write same data to both stores

```bash {"name": "side-by-side-write"}
echo "Writing same data to both stores..."
for i in {1..20}; do
  # WAL store
  curl -s -X POST http://localhost:8001/kv \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"demo-$i\", \"value\": {\"data\": \"important-$i\"}}" > /dev/null
  # No-WAL store
  curl -s -X POST http://localhost:8002/kv \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"demo-$i\", \"value\": {\"data\": \"important-$i\"}}" > /dev/null
done
echo "Wrote 20 keys to each store"
```

### Check both stores

```bash {"name": "check-both"}
echo "=== WAL Store ==="
curl -s http://localhost:8001/admin/stats | jq '{item_count, wal_enabled, wal_size_bytes}'
echo ""
echo "=== No-WAL Store ==="
curl -s http://localhost:8002/admin/stats | jq '{item_count, wal_enabled}'
```

### Crash both stores

```bash {"name": "crash-both"}
echo "Crashing both stores simultaneously..."
curl -s -X POST http://localhost:8001/admin/crash > /dev/null &
curl -s -X POST http://localhost:8002/admin/crash > /dev/null &
echo "Crashes triggered, waiting for restart..."
sleep 5
```

### Compare recovery

```bash {"name": "compare-recovery"}
echo "=== After Crash ==="
echo ""
echo "WAL Store (should have all data):"
curl -s http://localhost:8001/admin/stats | jq '{item_count, recovered_entries}'
echo ""
echo "No-WAL Store (should have lost data):"
curl -s http://localhost:8002/admin/stats | jq '{item_count}'
```

---

## Part 8: Show Observability UIs

### Print UI URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  KV Store (WAL):     http://localhost:8001/admin/stats"
echo "  KV Store (no WAL):  http://localhost:8002/admin/stats"
echo "  Jaeger:             http://localhost:16686"
echo "  Grafana:            http://localhost:3001 (admin/admin)"
echo "  Prometheus:         http://localhost:9090"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up (data volumes removed)"
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Write key (WAL) | `curl -X POST localhost:8001/kv -d '{"key":"k","value":"v"}'` |
| Write key (no WAL) | `curl -X POST localhost:8002/kv -d '{"key":"k","value":"v"}'` |
| Get key | `curl localhost:8001/kv/{key}` |
| List all | `curl localhost:8001/kv` |
| View WAL | `curl localhost:8001/admin/wal` |
| View stats | `curl localhost:8001/admin/stats` |
| Trigger checkpoint | `curl -X POST localhost:8001/admin/checkpoint` |
| Crash store | `curl -X POST localhost:8001/admin/crash` |
| Clear store | `curl -X POST localhost:8001/admin/clear` |
