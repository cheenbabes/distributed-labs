# Demo Runbook: CRDTs (Conflict-free Replicated Data Types)

This runbook contains all commands for the CRDT demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose down -v 2>/dev/null || true
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

### Verify all services are responding

```bash {"name": "verify-services"}
echo "Replica 1:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Replica 2:"
curl -s http://localhost:8002/health | jq
echo ""
echo "Replica 3:"
curl -s http://localhost:8003/health | jq
echo ""
echo "Sync Service:"
curl -s http://localhost:8010/health | jq
echo ""
echo "Client:"
curl -s http://localhost:8020/health | jq
```

### Show URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Client API:     http://localhost:8020"
echo "  Replica 1:      http://localhost:8001"
echo "  Replica 2:      http://localhost:8002"
echo "  Replica 3:      http://localhost:8003"
echo "  Sync Service:   http://localhost:8010"
echo "  Jaeger:         http://localhost:16686"
echo "  Grafana:        http://localhost:3001 (admin/admin)"
echo "  Prometheus:     http://localhost:9090"
```

---

## Part 2: G-Counter Demo

### Increment G-Counter on each replica

```bash {"name": "gcounter-increment"}
echo "Incrementing on Replica 1 (+5):"
curl -s -X POST http://localhost:8001/gcounter/demo-visits/increment \
  -H "Content-Type: application/json" \
  -d '{"amount": 5}' | jq

echo ""
echo "Incrementing on Replica 2 (+3):"
curl -s -X POST http://localhost:8002/gcounter/demo-visits/increment \
  -H "Content-Type: application/json" \
  -d '{"amount": 3}' | jq

echo ""
echo "Incrementing on Replica 3 (+7):"
curl -s -X POST http://localhost:8003/gcounter/demo-visits/increment \
  -H "Content-Type: application/json" \
  -d '{"amount": 7}' | jq
```

### Check values before sync

```bash {"name": "gcounter-before-sync"}
echo "=== Values BEFORE sync (each replica only sees its own increments) ==="
echo ""
echo "Replica 1:"
curl -s http://localhost:8001/gcounter/demo-visits | jq
echo ""
echo "Replica 2:"
curl -s http://localhost:8002/gcounter/demo-visits | jq
echo ""
echo "Replica 3:"
curl -s http://localhost:8003/gcounter/demo-visits | jq
```

### Trigger sync

```bash {"name": "gcounter-sync"}
echo "Triggering sync..."
curl -s -X POST http://localhost:8010/sync | jq
sleep 2
echo "Sync complete!"
```

### Check values after sync

```bash {"name": "gcounter-after-sync"}
echo "=== Values AFTER sync (all replicas converged) ==="
echo ""
echo "Replica 1 value:" && curl -s http://localhost:8001/gcounter/demo-visits | jq '.value'
echo "Replica 2 value:" && curl -s http://localhost:8002/gcounter/demo-visits | jq '.value'
echo "Replica 3 value:" && curl -s http://localhost:8003/gcounter/demo-visits | jq '.value'
echo ""
echo "Expected: 15 (5 + 3 + 7)"
```

---

## Part 3: PN-Counter Demo

### Create PN-Counter with mixed operations

```bash {"name": "pncounter-operations"}
echo "Replica 1: +10"
curl -s -X POST http://localhost:8001/pncounter/demo-stock/increment \
  -H "Content-Type: application/json" -d '{"amount": 10}' | jq '.value'

echo "Replica 2: +5"
curl -s -X POST http://localhost:8002/pncounter/demo-stock/increment \
  -H "Content-Type: application/json" -d '{"amount": 5}' | jq '.value'

echo "Replica 2: -3"
curl -s -X POST http://localhost:8002/pncounter/demo-stock/decrement \
  -H "Content-Type: application/json" -d '{"amount": 3}' | jq '.value'

echo "Replica 3: -2"
curl -s -X POST http://localhost:8003/pncounter/demo-stock/decrement \
  -H "Content-Type: application/json" -d '{"amount": 2}' | jq '.value'
```

### Show internal state (P and N counters)

```bash {"name": "pncounter-internal-state"}
echo "Internal state showing P (positive) and N (negative) counters:"
curl -s http://localhost:8002/pncounter/demo-stock | jq '.state'
```

### Sync and verify

```bash {"name": "pncounter-sync-verify"}
curl -s -X POST http://localhost:8010/sync > /dev/null
sleep 2

echo "Final values (should all be 10: +10 +5 -3 -2):"
echo "Replica 1:" && curl -s http://localhost:8001/pncounter/demo-stock | jq '.value'
echo "Replica 2:" && curl -s http://localhost:8002/pncounter/demo-stock | jq '.value'
echo "Replica 3:" && curl -s http://localhost:8003/pncounter/demo-stock | jq '.value'
```

---

## Part 4: LWW-Register Demo

### Write concurrent values

```bash {"name": "lww-writes"}
echo "Writing to Replica 1..."
curl -s -X POST http://localhost:8001/lww/demo-config/set \
  -H "Content-Type: application/json" \
  -d '{"value": "config-from-replica-1"}' | jq '.current'

sleep 0.1

echo "Writing to Replica 2..."
curl -s -X POST http://localhost:8002/lww/demo-config/set \
  -H "Content-Type: application/json" \
  -d '{"value": "config-from-replica-2"}' | jq '.current'

sleep 0.1

echo "Writing to Replica 3..."
curl -s -X POST http://localhost:8003/lww/demo-config/set \
  -H "Content-Type: application/json" \
  -d '{"value": "config-from-replica-3"}' | jq '.current'
```

### Sync and check winner

```bash {"name": "lww-sync-check"}
curl -s -X POST http://localhost:8010/sync > /dev/null
sleep 2

echo "Winner (highest timestamp):"
echo ""
echo "Replica 1:" && curl -s http://localhost:8001/lww/demo-config | jq '.current'
echo "Replica 2:" && curl -s http://localhost:8002/lww/demo-config | jq '.current'
echo "Replica 3:" && curl -s http://localhost:8003/lww/demo-config | jq '.current'
```

---

## Part 5: Network Partition Demo

### Initialize counter for partition test

```bash {"name": "partition-init"}
echo "Initializing counter with value 10 on replica-1..."
curl -s -X POST http://localhost:8001/gcounter/partition-demo/increment \
  -H "Content-Type: application/json" -d '{"amount": 10}' | jq

curl -s -X POST http://localhost:8010/sync > /dev/null
sleep 2

echo "Initial state (all replicas):"
echo "Replica 1:" && curl -s http://localhost:8001/gcounter/partition-demo | jq '.value'
echo "Replica 2:" && curl -s http://localhost:8002/gcounter/partition-demo | jq '.value'
echo "Replica 3:" && curl -s http://localhost:8003/gcounter/partition-demo | jq '.value'
```

### Enable partition on replica-1

```bash {"name": "partition-enable"}
echo "Partitioning replica-1..."
curl -s -X POST http://localhost:8001/admin/partition \
  -H "Content-Type: application/json" -d '{"enabled": true}' | jq

echo ""
echo "Partition status:"
curl -s http://localhost:8001/admin/partition | jq
```

### Make updates during partition

```bash {"name": "partition-updates"}
echo "Updating PARTITIONED replica-1 (+100):"
curl -s -X POST http://localhost:8001/gcounter/partition-demo/increment \
  -H "Content-Type: application/json" -d '{"amount": 100}' | jq '.value'

echo ""
echo "Updating replica-2 (+200):"
curl -s -X POST http://localhost:8002/gcounter/partition-demo/increment \
  -H "Content-Type: application/json" -d '{"amount": 200}' | jq '.value'

echo ""
echo "Updating replica-3 (+300):"
curl -s -X POST http://localhost:8003/gcounter/partition-demo/increment \
  -H "Content-Type: application/json" -d '{"amount": 300}' | jq '.value'

echo ""
echo "Syncing (only replica-2 and replica-3 will participate)..."
curl -s -X POST http://localhost:8010/sync > /dev/null
sleep 2
```

### Show diverged state

```bash {"name": "partition-diverged"}
echo "=== DIVERGED STATE ==="
echo ""
echo "Replica 1 (partitioned, has its own updates only):"
curl -s http://localhost:8001/gcounter/partition-demo | jq

echo ""
echo "Replica 2 (connected, synced with replica-3):"
curl -s http://localhost:8002/gcounter/partition-demo | jq

echo ""
echo "Replica 3 (connected, synced with replica-2):"
curl -s http://localhost:8003/gcounter/partition-demo | jq
```

### Heal partition

```bash {"name": "partition-heal"}
echo "Healing partition..."
curl -s -X POST http://localhost:8001/admin/partition \
  -H "Content-Type: application/json" -d '{"enabled": false}' | jq

echo ""
echo "Triggering sync..."
curl -s -X POST http://localhost:8010/sync > /dev/null
sleep 2

echo ""
echo "=== CONVERGED STATE (all updates merged!) ==="
echo "Expected: 610 (10 + 100 + 200 + 300)"
echo ""
echo "Replica 1:" && curl -s http://localhost:8001/gcounter/partition-demo | jq '.value'
echo "Replica 2:" && curl -s http://localhost:8002/gcounter/partition-demo | jq '.value'
echo "Replica 3:" && curl -s http://localhost:8003/gcounter/partition-demo | jq '.value'
```

---

## Part 6: Concurrent Test via Client API

### Run G-Counter concurrent test

```bash {"name": "test-gcounter-concurrent"}
echo "Running concurrent G-Counter test..."
curl -s -X POST http://localhost:8020/test/concurrent \
  -H "Content-Type: application/json" \
  -d '{
    "crdt_type": "gcounter",
    "name": "client-test-gc",
    "operations_per_replica": 10,
    "operation_delay_ms": 20
  }' | jq '{
    test_type,
    operations_executed,
    is_consistent,
    final_values: [.final_state | to_entries[] | {replica: .key, value: .value.value}]
  }'
```

### Run PN-Counter concurrent test

```bash {"name": "test-pncounter-concurrent"}
echo "Running concurrent PN-Counter test..."
curl -s -X POST http://localhost:8020/test/concurrent \
  -H "Content-Type: application/json" \
  -d '{
    "crdt_type": "pncounter",
    "name": "client-test-pn",
    "operations_per_replica": 10,
    "operation_delay_ms": 20
  }' | jq '{
    test_type,
    operations_executed,
    is_consistent,
    final_values: [.final_state | to_entries[] | {replica: .key, value: .value.value}]
  }'
```

### Run partition test via client

```bash {"name": "test-partition-client"}
echo "Running partition test via client..."
curl -s -X POST http://localhost:8020/test/partition \
  -H "Content-Type: application/json" \
  -d '{
    "crdt_type": "gcounter",
    "name": "client-partition-test",
    "partition_replica": "replica-1",
    "operations_during_partition": 5
  }' | jq '.explanation, {
    phase2_during_partition: [.results.phase2_during_partition | to_entries[] | {replica: .key, value: .value.value}],
    phase4_after_sync: [.results.phase4_after_sync | to_entries[] | {replica: .key, value: .value.value}]
  }'
```

---

## Part 7: Run Full Test Suite

### Run all tests

```bash {"name": "run-test-suite"}
chmod +x scripts/test-concurrent.sh
./scripts/test-concurrent.sh
```

### Run specific test

```bash {"name": "run-stress-test"}
./scripts/test-concurrent.sh stress
```

---

## Part 8: View Traces

### Generate traces

```bash {"name": "generate-traces"}
echo "Generating traces..."
for i in {1..10}; do
  curl -s -X POST http://localhost:8001/gcounter/trace-test/increment \
    -H "Content-Type: application/json" -d '{"amount": 1}' > /dev/null
done
curl -s -X POST http://localhost:8010/sync > /dev/null
echo "Done! View traces at http://localhost:16686"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up"
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Increment G-Counter | `curl -X POST localhost:8001/gcounter/{name}/increment -d '{"amount":1}'` |
| Get G-Counter | `curl localhost:8001/gcounter/{name}` |
| Increment PN-Counter | `curl -X POST localhost:8001/pncounter/{name}/increment -d '{"amount":1}'` |
| Decrement PN-Counter | `curl -X POST localhost:8001/pncounter/{name}/decrement -d '{"amount":1}'` |
| Set LWW-Register | `curl -X POST localhost:8001/lww/{name}/set -d '{"value":"x"}'` |
| Trigger sync | `curl -X POST localhost:8010/sync` |
| Enable partition | `curl -X POST localhost:8001/admin/partition -d '{"enabled":true}'` |
| Disable partition | `curl -X POST localhost:8001/admin/partition -d '{"enabled":false}'` |
| View traces | http://localhost:16686 |
| View Grafana | http://localhost:3001 (admin/admin) |
