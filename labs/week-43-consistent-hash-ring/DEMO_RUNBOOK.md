# Demo Runbook: Consistent Hash Ring

This runbook contains all commands for demonstrating consistent hashing concepts. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose --profile scale down -v 2>/dev/null || true
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
echo "Ring Coordinator:"
curl -s http://localhost:8080/health | jq
echo ""
echo "Storage Node 1:"
curl -s http://localhost:8081/health | jq
echo ""
echo "Storage Node 2:"
curl -s http://localhost:8082/health | jq
echo ""
echo "Storage Node 3:"
curl -s http://localhost:8083/health | jq
echo ""
echo "Client Service:"
curl -s http://localhost:8090/health | jq
```

---

## Part 2: Understanding the Ring

### View the hash ring state

```bash {"name": "ring-state"}
curl -s http://localhost:8080/ring | jq
```

### View physical nodes in the ring

```bash {"name": "ring-nodes"}
curl -s http://localhost:8080/ring/nodes | jq
```

### Show URLs for UIs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Ring API:    http://localhost:8080"
echo "  Client API:  http://localhost:8090"
echo "  Jaeger:      http://localhost:16686"
echo "  Grafana:     http://localhost:3001 (admin/admin)"
echo "  Prometheus:  http://localhost:9090"
```

---

## Part 3: Basic Key Distribution

### Look up where keys would be routed

```bash {"name": "lookup-keys"}
echo "Looking up routing for various keys:"
echo ""
echo "user:123 -> "
curl -s http://localhost:8080/lookup/user:123 | jq '.target_node.node_id'
echo ""
echo "user:456 -> "
curl -s http://localhost:8080/lookup/user:456 | jq '.target_node.node_id'
echo ""
echo "session:abc -> "
curl -s http://localhost:8080/lookup/session:abc | jq '.target_node.node_id'
echo ""
echo "order:9999 -> "
curl -s http://localhost:8080/lookup/order:9999 | jq '.target_node.node_id'
```

### Store 100 keys and check distribution

```bash {"name": "store-100-keys"}
echo "Storing 100 keys..."
curl -s -X POST http://localhost:8090/generate-keys \
  -H "Content-Type: application/json" \
  -d '{"count": 100, "prefix": "demo_"}' | jq
```

### Check key distribution

```bash {"name": "check-distribution"}
echo "Key distribution across nodes:"
curl -s http://localhost:8080/ring/distribution | jq
```

---

## Part 4: Analyze Hash Distribution Quality

### Analyze distribution with 1000 sample keys

```bash {"name": "analyze-distribution"}
echo "Analyzing hash distribution quality..."
curl -s "http://localhost:8090/analyze-hash-distribution?sample_size=1000" | jq
```

### Show current virtual nodes setting

```bash {"name": "show-vnodes"}
curl -s http://localhost:8080/ring/nodes | jq '.virtual_nodes_per_physical'
```

---

## Part 5: Adding a Node

### Start the 4th storage node

```bash {"name": "start-node-4"}
docker compose --profile scale up -d storage-node-4
sleep 5
echo "Node 4 started:"
curl -s http://localhost:8084/health | jq
```

### Check distribution BEFORE adding to ring

```bash {"name": "distribution-before-add"}
echo "Distribution BEFORE adding node-4 to ring:"
curl -s http://localhost:8080/ring/distribution | jq
```

### Add node-4 to the ring

```bash {"name": "add-node-4"}
echo "Adding node-4 to the ring..."
curl -s -X POST http://localhost:8080/ring/nodes \
  -H "Content-Type: application/json" \
  -d '{"node_id": "node-4", "address": "http://storage-node-4:8080", "virtual_nodes": 150}' | jq
```

### Check distribution AFTER adding to ring

```bash {"name": "distribution-after-add"}
echo "Distribution AFTER adding node-4 to ring:"
curl -s http://localhost:8080/ring/distribution | jq
```

### Store more keys and observe new distribution

```bash {"name": "store-more-keys"}
echo "Storing 200 more keys..."
curl -s -X POST http://localhost:8090/generate-keys \
  -H "Content-Type: application/json" \
  -d '{"count": 200, "prefix": "after_scale_"}' | jq

echo ""
echo "New distribution:"
curl -s http://localhost:8080/ring/distribution | jq
```

---

## Part 6: Removing a Node

### Check distribution BEFORE removing

```bash {"name": "distribution-before-remove"}
echo "Distribution BEFORE removing node-2:"
curl -s http://localhost:8080/ring/distribution | jq
```

### Remove node-2 from the ring

```bash {"name": "remove-node-2"}
echo "Removing node-2 from the ring..."
curl -s -X DELETE http://localhost:8080/ring/nodes/node-2 | jq
```

### Check distribution AFTER removing

```bash {"name": "distribution-after-remove"}
echo "Distribution AFTER removing node-2:"
curl -s http://localhost:8080/ring/distribution | jq
```

### Add node-2 back

```bash {"name": "add-node-2-back"}
echo "Adding node-2 back to the ring..."
curl -s -X POST http://localhost:8080/ring/nodes \
  -H "Content-Type: application/json" \
  -d '{"node_id": "node-2", "address": "http://storage-node-2:8080", "virtual_nodes": 150}' | jq
```

---

## Part 7: Migration Test

### Run a full migration test

```bash {"name": "migration-test"}
echo "Running migration test (500 keys, then remove node-3)..."
curl -s -X POST http://localhost:8090/test-migration \
  -H "Content-Type: application/json" \
  -d '{
    "key_count": 500,
    "node_to_remove": "node-3"
  }' | jq
```

### Restore node-3 after migration test

```bash {"name": "restore-node-3"}
echo "Restoring node-3 to the ring..."
curl -s -X POST http://localhost:8080/ring/nodes \
  -H "Content-Type: application/json" \
  -d '{"node_id": "node-3", "address": "http://storage-node-3:8080", "virtual_nodes": 150}' | jq
```

---

## Part 8: Virtual Nodes Comparison

### Check current virtual nodes count

```bash {"name": "current-vnodes"}
echo "Current virtual nodes per physical node:"
curl -s http://localhost:8080/ring/nodes | jq '.nodes[] | {node_id, virtual_node_count}'
```

### Set low virtual nodes for new nodes

```bash {"name": "set-low-vnodes"}
echo "Setting default virtual nodes to 10 (low)..."
curl -s -X PUT http://localhost:8080/ring/virtual-nodes \
  -H "Content-Type: application/json" \
  -d '{"count": 10}' | jq
```

### Add a node with few virtual nodes (for comparison)

```bash {"name": "add-low-vnode-node"}
echo "Adding node-5 with only 10 virtual nodes..."
docker compose run -d --name lab43-storage-node-5 \
  -e NODE_ID=node-5 \
  -e PORT=8080 \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317 \
  --network lab43-network \
  $(docker compose config --images | grep storage-node | head -1) || echo "Using existing node"

sleep 3

curl -s -X POST http://localhost:8080/ring/nodes \
  -H "Content-Type: application/json" \
  -d '{"node_id": "node-5", "address": "http://lab43-storage-node-5:8080", "virtual_nodes": 10}' | jq
```

### Compare distribution with mixed virtual nodes

```bash {"name": "compare-vnodes"}
echo "Distribution with mixed virtual node counts:"
curl -s http://localhost:8080/ring/distribution | jq
echo ""
echo "Virtual nodes per node:"
curl -s http://localhost:8080/ring/nodes | jq '.nodes[] | {node_id, virtual_node_count}'
```

### Reset virtual nodes to default

```bash {"name": "reset-vnodes"}
echo "Resetting default virtual nodes to 150..."
curl -s -X PUT http://localhost:8080/ring/virtual-nodes \
  -H "Content-Type: application/json" \
  -d '{"count": 150}' | jq

# Remove node-5 if it was added
curl -s -X DELETE http://localhost:8080/ring/nodes/node-5 2>/dev/null || true
```

---

## Part 9: Load Testing

### Run k6 load test

```bash {"name": "load-test"}
echo "Running k6 load test (3 minutes)..."
docker compose run --rm k6 run /scripts/basic.js
```

### Run migration load test (for observing topology changes)

```bash {"name": "migration-load-test", "background": true}
echo "Starting migration load test in background..."
echo "Add/remove nodes while this runs to observe changes in Grafana"
docker compose run --rm k6 run /scripts/migration-test.js
```

---

## Part 10: Simulate Traffic

### Run sustained traffic simulation

```bash {"name": "simulate-traffic"}
echo "Simulating 30 seconds of mixed traffic..."
curl -s -X POST "http://localhost:8090/simulate-traffic?duration_seconds=30&requests_per_second=10&read_ratio=0.7" | jq
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose --profile scale down -v
docker rm -f lab43-storage-node-5 2>/dev/null || true
echo "Lab cleaned up"
```

---

## Troubleshooting Commands

### View ring coordinator logs

```bash {"name": "logs-coordinator"}
docker compose logs ring-coordinator --tail=50
```

### View storage node logs

```bash {"name": "logs-storage"}
docker compose logs storage-node-1 storage-node-2 storage-node-3 --tail=30
```

### View all service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart ring coordinator

```bash {"name": "restart-coordinator"}
docker compose restart ring-coordinator
sleep 5
curl -s http://localhost:8080/health | jq
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose --profile scale down -v` |
| Ring state | `curl -s localhost:8080/ring \| jq` |
| Key distribution | `curl -s localhost:8080/ring/distribution \| jq` |
| Lookup key | `curl -s localhost:8080/lookup/{key} \| jq` |
| Store key | `curl -X POST localhost:8080/keys -d '{"key":"k","value":"v"}'` |
| Add node | `curl -X POST localhost:8080/ring/nodes -d '{"node_id":"n","address":"addr"}'` |
| Remove node | `curl -X DELETE localhost:8080/ring/nodes/{node_id}` |
| Generate keys | `curl -X POST localhost:8090/generate-keys -d '{"count":100}'` |
| Grafana | http://localhost:3001 (admin/admin) |
| Jaeger | http://localhost:16686 |
