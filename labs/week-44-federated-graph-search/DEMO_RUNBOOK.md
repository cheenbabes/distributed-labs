# Demo Runbook: Federated Graph Search

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
docker system prune -f
```

---

## Part 1: Start the Lab

### Build and start all services

```bash {"name": "start-lab", "background": true}
docker compose up --build -d
```

### Wait for services to be healthy

```bash {"name": "wait-healthy"}
echo "Waiting for services to be healthy..."
sleep 30
docker compose ps
```

### Verify all services are responding

```bash {"name": "verify-services"}
echo "Gateway:"
curl -s http://localhost:4000/health | jq
echo ""
echo "Users Subgraph:"
curl -s http://localhost:4001/health | jq
echo ""
echo "Products Subgraph:"
curl -s http://localhost:4002/health | jq
echo ""
echo "Reviews Subgraph:"
curl -s http://localhost:4003/health | jq
```

---

## Part 2: Understanding Federation

### Query products only (single subgraph)

```bash {"name": "query-products-only"}
curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ products { id name price category } }"}' | jq
```

### Query with federation (crosses subgraph boundaries)

```bash {"name": "query-federated"}
curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ products { id name reviews { rating comment author { name } } averageRating } }"}' | jq
```

### Query users with their reviews

```bash {"name": "query-users"}
curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ users { id name email reviews { rating product { name } } } }"}' | jq
```

### Show observability URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  GraphQL Playground: http://localhost:4000/graphql"
echo "  Jaeger:             http://localhost:16686"
echo "  Grafana:            http://localhost:3001 (admin/admin)"
echo "  Prometheus:         http://localhost:9090"
```

---

## Part 3: N+1 Problem Detection

### Disable DataLoader in all subgraphs

```bash {"name": "disable-dataloader"}
echo "Disabling DataLoader in all subgraphs..."
curl -s -X POST http://localhost:4001/admin/dataloader \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq

curl -s -X POST http://localhost:4002/admin/dataloader \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq

curl -s -X POST http://localhost:4003/admin/dataloader \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq

echo ""
echo "DataLoader is now DISABLED - N+1 queries will occur!"
```

### Run N+1 query (slow)

```bash {"name": "n-plus-one-slow"}
echo "Running query WITHOUT DataLoader batching..."
echo ""
time curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ products { id name reviews { rating author { name } } } }"}' | jq '.data.products | length'
echo ""
echo "Check Jaeger for many individual db.* spans!"
```

### Enable DataLoader in all subgraphs

```bash {"name": "enable-dataloader"}
echo "Enabling DataLoader in all subgraphs..."
curl -s -X POST http://localhost:4001/admin/dataloader \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq

curl -s -X POST http://localhost:4002/admin/dataloader \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq

curl -s -X POST http://localhost:4003/admin/dataloader \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq

echo ""
echo "DataLoader is now ENABLED - queries will be batched!"
```

### Run same query (fast)

```bash {"name": "n-plus-one-fast"}
echo "Running query WITH DataLoader batching..."
echo ""
time curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ products { id name reviews { rating author { name } } } }"}' | jq '.data.products | length'
echo ""
echo "Check Jaeger for batched db.batch* spans!"
```

### Compare side by side

```bash {"name": "compare-n-plus-one"}
echo "=== N+1 COMPARISON ==="
echo ""

# Disable DataLoader
curl -s -X POST http://localhost:4001/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": false}' > /dev/null
curl -s -X POST http://localhost:4002/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": false}' > /dev/null
curl -s -X POST http://localhost:4003/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": false}' > /dev/null

echo "WITHOUT DataLoader (N+1 problem):"
for i in {1..3}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:4000/graphql \
    -H "Content-Type: application/json" \
    -d '{"query": "{ products { id name reviews { rating author { name } } } }"}')
  echo "  Request $i: ${time}s"
done

# Enable DataLoader
curl -s -X POST http://localhost:4001/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": true}' > /dev/null
curl -s -X POST http://localhost:4002/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": true}' > /dev/null
curl -s -X POST http://localhost:4003/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": true}' > /dev/null

echo ""
echo "WITH DataLoader (batched):"
for i in {1..3}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:4000/graphql \
    -H "Content-Type: application/json" \
    -d '{"query": "{ products { id name reviews { rating author { name } } } }"}')
  echo "  Request $i: ${time}s"
done
```

---

## Part 4: Subgraph Failure Handling

### Enable failures in reviews subgraph

```bash {"name": "enable-failures"}
echo "Enabling 50% failure rate in reviews subgraph..."
curl -s -X POST http://localhost:4003/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"rate": 0.5}' | jq
```

### Make requests to see partial failures

```bash {"name": "test-partial-failures"}
echo "Making 5 requests - some will have partial data..."
echo ""
for i in {1..5}; do
  echo "--- Request $i ---"
  result=$(curl -s -X POST http://localhost:4000/graphql \
    -H "Content-Type: application/json" \
    -d '{"query": "{ products { id name reviews { rating } } }"}')

  errors=$(echo "$result" | jq '.errors // empty')
  if [ -n "$errors" ] && [ "$errors" != "null" ]; then
    echo "PARTIAL FAILURE - has errors but may have some data"
    echo "Errors: $(echo "$result" | jq -c '.errors[0].message // "unknown"')"
  else
    echo "SUCCESS - full data returned"
  fi
  echo ""
done
```

### Query without reviews (always succeeds)

```bash {"name": "query-without-reviews"}
echo "Query that doesn't need reviews subgraph:"
curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ products { id name price category } }"}' | jq '.data.products | length'
echo "This always succeeds even when reviews subgraph is failing!"
```

### Disable failures

```bash {"name": "disable-failures"}
curl -s -X POST http://localhost:4003/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"rate": 0}' | jq
echo "Failures disabled - system is healthy again"
```

---

## Part 5: Load Testing

### Run basic load test

```bash {"name": "load-test-basic", "background": true}
docker compose run --rm k6 run /scripts/basic.js
```

### Run N+1 detection load test (without DataLoader)

```bash {"name": "load-test-n-plus-one-slow"}
# Disable DataLoader
curl -s -X POST http://localhost:4001/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": false}' > /dev/null
curl -s -X POST http://localhost:4002/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": false}' > /dev/null
curl -s -X POST http://localhost:4003/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": false}' > /dev/null

echo "Running N+1 test WITHOUT DataLoader..."
docker compose run --rm k6 run /scripts/n-plus-one.js
```

### Run N+1 detection load test (with DataLoader)

```bash {"name": "load-test-n-plus-one-fast"}
# Enable DataLoader
curl -s -X POST http://localhost:4001/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": true}' > /dev/null
curl -s -X POST http://localhost:4002/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": true}' > /dev/null
curl -s -X POST http://localhost:4003/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": true}' > /dev/null

echo "Running N+1 test WITH DataLoader..."
docker compose run --rm k6 run /scripts/n-plus-one.js
```

### Run failure resilience test

```bash {"name": "load-test-failures", "background": true}
# Enable 30% failure rate
curl -s -X POST http://localhost:4003/admin/failure -H "Content-Type: application/json" -d '{"rate": 0.3}' > /dev/null
echo "Running failure resilience test..."
docker compose run --rm k6 run /scripts/failure-test.js
# Reset failures
curl -s -X POST http://localhost:4003/admin/failure -H "Content-Type: application/json" -d '{"rate": 0}' > /dev/null
```

---

## Part 6: Deep Query Analysis

### Execute a complex federated query

```bash {"name": "deep-query"}
curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "query DeepQuery { users { id name reviews { id rating product { name category } } } topProducts(limit: 3) { id name averageRating reviews { comment author { name email } } } }"}' | jq
```

### Generate traces for analysis

```bash {"name": "generate-traces"}
echo "Generating 20 traces for analysis..."
for i in {1..20}; do
  curl -s -X POST http://localhost:4000/graphql \
    -H "Content-Type: application/json" \
    -d '{"query": "{ products { id name reviews { rating author { name } } } }"}' > /dev/null
  echo -n "."
done
echo ""
echo "Done. Check Jaeger: http://localhost:16686"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up"
```

---

## Troubleshooting Commands

### Check service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check gateway logs

```bash {"name": "logs-gateway"}
docker compose logs gateway --tail=50
```

### Check reviews subgraph logs

```bash {"name": "logs-reviews"}
docker compose logs reviews-subgraph --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart all services

```bash {"name": "restart-all"}
docker compose restart
```

### Check DataLoader status

```bash {"name": "check-dataloader-status"}
echo "Users Subgraph:"
curl -s http://localhost:4001/admin/dataloader | jq
echo ""
echo "Products Subgraph:"
curl -s http://localhost:4002/admin/dataloader | jq
echo ""
echo "Reviews Subgraph:"
curl -s http://localhost:4003/admin/dataloader | jq
```

### Check failure rate status

```bash {"name": "check-failure-status"}
curl -s http://localhost:4003/admin/failure | jq
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Disable DataLoader | `curl -X POST localhost:4001/admin/dataloader -d '{"enabled":false}'` |
| Enable DataLoader | `curl -X POST localhost:4001/admin/dataloader -d '{"enabled":true}'` |
| Enable failures | `curl -X POST localhost:4003/admin/failure -d '{"rate":0.5}'` |
| Disable failures | `curl -X POST localhost:4003/admin/failure -d '{"rate":0}'` |
| View traces | http://localhost:16686 |
| View metrics | http://localhost:3001 |
