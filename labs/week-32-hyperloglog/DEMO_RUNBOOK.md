# Demo Runbook: HyperLogLog Cardinality Estimation

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

```bash {"name": "start-lab"}
docker compose up --build -d
```

### Wait for services to be healthy

```bash {"name": "wait-healthy"}
echo "Waiting for services to be healthy..."
sleep 15
docker compose ps
```

### Verify the visitor counter service

```bash {"name": "verify-service"}
curl -s http://localhost:8000/health | jq
```

---

## Part 2: The Problem - Counting Unique Visitors

### Make it relatable

```bash {"name": "show-problem"}
echo "=== THE PROBLEM ==="
echo ""
echo "You run a website with 1 billion page views per day."
echo "You want to count unique visitors."
echo ""
echo "Naive approach: Store every user ID in a Set"
echo "  - 1 billion UUIDs x 36 bytes = 36 GB of memory!"
echo ""
echo "HyperLogLog approach: Probabilistic counting"
echo "  - Only 12 KB of memory"
echo "  - ~0.81% error rate"
echo ""
echo "Let's see this in action..."
```

---

## Part 3: Basic Comparison - Small Scale

### Record a few visits

```bash {"name": "basic-visits"}
echo "Recording 5 unique visitors..."
for i in {1..5}; do
  curl -s -X POST "http://localhost:8000/visit?page_id=demo&visitor_id=user-$i" | jq '{exact_count, hll_count, error_rate}'
done
```

### Check the stats

```bash {"name": "basic-stats"}
echo "=== BASIC STATS ==="
curl -s "http://localhost:8000/stats?page_id=demo" | jq
```

**Key point:** At small scale, both methods give the same count and similar memory usage.

---

## Part 4: Watch Memory Grow - Medium Scale

### Simulate 1,000 unique visitors

```bash {"name": "simulate-1k"}
echo "Simulating 1,000 unique visitors..."
curl -s -X POST "http://localhost:8000/simulate?page_id=test_1k&total_visitors=1000&unique_visitors=1000" | jq '{
  simulation: .simulation,
  results: .results,
  memory: .memory,
  insight: .insight
}'
```

### Simulate 10,000 unique visitors

```bash {"name": "simulate-10k"}
echo "Simulating 10,000 unique visitors..."
curl -s -X POST "http://localhost:8000/simulate?page_id=test_10k&total_visitors=10000&unique_visitors=10000" | jq '{
  exact_count: .results.exact_count,
  hll_count: .results.hll_count,
  error_percentage: .results.error_percentage,
  exact_memory: .memory.exact_human,
  hll_memory: .memory.hll_human,
  savings: .memory.savings_ratio
}'
```

### Compare all pages

```bash {"name": "compare-all"}
echo "=== MEMORY COMPARISON ==="
curl -s "http://localhost:8000/stats/all" | jq '{
  pages: .pages | to_entries | map({
    page: .key,
    exact_count: .value.exact_count,
    hll_count: .value.hll_count,
    exact_memory_kb: (.value.exact_memory_bytes / 1024 | floor),
    hll_memory_kb: (.value.hll_memory_bytes / 1024 | floor)
  }),
  totals: .totals
}'
```

---

## Part 5: The Magic - HyperLogLog Merging

### Reset counters

```bash {"name": "reset-for-merge"}
curl -s -X DELETE "http://localhost:8000/reset" | jq
echo "Counters reset"
```

### Create overlapping visitor sets

```bash {"name": "create-overlap"}
echo "Creating morning visitors (800 unique)..."
curl -s -X POST "http://localhost:8000/simulate?page_id=morning&total_visitors=1000&unique_visitors=800" > /dev/null

echo "Creating afternoon visitors (800 unique)..."
curl -s -X POST "http://localhost:8000/simulate?page_id=afternoon&total_visitors=1000&unique_visitors=800" > /dev/null

echo "Adding 200 overlap visitors (visited both morning AND afternoon)..."
for i in {1..200}; do
  curl -s -X POST "http://localhost:8000/visit?page_id=morning&visitor_id=overlap-$i" > /dev/null
  curl -s -X POST "http://localhost:8000/visit?page_id=afternoon&visitor_id=overlap-$i" > /dev/null
done
echo "Done!"
```

### Check individual counts

```bash {"name": "individual-counts"}
echo "=== INDIVIDUAL COUNTS ==="
echo ""
echo "Morning visitors:"
curl -s "http://localhost:8000/stats?page_id=morning" | jq '{exact: .exact.count, hll: .hyperloglog.count}'

echo ""
echo "Afternoon visitors:"
curl -s "http://localhost:8000/stats?page_id=afternoon" | jq '{exact: .exact.count, hll: .hyperloglog.count}'
```

### Merge HyperLogLogs

```bash {"name": "merge-hll"}
echo "=== MERGING HYPERLOGLOGS ==="
echo ""
echo "Without HLL, you'd count: morning + afternoon = ~2000 visitors"
echo "But 200 visitors visited BOTH periods!"
echo ""
curl -s -X POST "http://localhost:8000/merge?source_pages=morning,afternoon&target_key=daily" | jq
```

**Key point:** HyperLogLog merge automatically removes duplicates without storing individual IDs!

---

## Part 6: Error Rate Distribution

### Run multiple simulations

```bash {"name": "error-distribution"}
echo "=== ERROR RATE DISTRIBUTION ==="
echo "Running 10 simulations with 5,000 unique visitors each..."
echo ""

# Reset first
curl -s -X DELETE "http://localhost:8000/reset" > /dev/null

for i in {1..10}; do
  result=$(curl -s -X POST "http://localhost:8000/simulate?page_id=error_test_$i&total_visitors=5000&unique_visitors=5000")
  error=$(echo $result | jq -r '.results.error_percentage')
  echo "Simulation $i: Error = $error"
done

echo ""
echo "Expected: Most errors between 0% and 2%, average ~0.81%"
```

---

## Part 7: Grafana Visualization

### Show dashboard URLs

```bash {"name": "show-urls"}
echo "=== OPEN THESE URLS ==="
echo ""
echo "  Grafana:     http://localhost:3001 (admin/admin)"
echo "  Prometheus:  http://localhost:9090"
echo "  Jaeger:      http://localhost:16686"
echo "  API Docs:    http://localhost:8000/docs"
```

### Generate traffic for visualization

```bash {"name": "generate-traffic"}
echo "Generating traffic for Grafana visualization..."
echo "Watch the dashboard at http://localhost:3001"
echo ""

for i in {1..50}; do
  curl -s -X POST "http://localhost:8000/visit?page_id=homepage" > /dev/null
  echo -n "."
  sleep 0.1
done
echo ""
echo "Done! Check Grafana dashboard."
```

---

## Part 8: Load Test

### Run basic load test

```bash {"name": "load-test-basic", "background": true}
docker compose run --rm lab32-k6 run /scripts/basic.js
```

### Run scale test (more unique visitors)

```bash {"name": "load-test-scale", "background": true}
# Reset first
curl -s -X DELETE "http://localhost:8000/reset" > /dev/null
# Run scale test
docker compose run --rm lab32-k6 run /scripts/scale-test.js
```

### Check final results after scale test

```bash {"name": "final-results"}
echo "=== FINAL SCALE TEST RESULTS ==="
curl -s "http://localhost:8000/stats?page_id=scale_test" | jq '{
  exact_count: .exact.count,
  hll_count: .hyperloglog.count,
  error_rate: .comparison.error_percentage,
  exact_memory: .exact.memory_human,
  hll_memory: .hyperloglog.memory_human,
  memory_savings: "\(.comparison.memory_savings_ratio)x"
}'
```

---

## Part 9: Key Concepts Summary

### Display summary

```bash {"name": "summary"}
echo "========================================"
echo "       HYPERLOGLOG KEY CONCEPTS         "
echo "========================================"
echo ""
echo "1. MEMORY EFFICIENCY"
echo "   - Exact counting: O(n) memory"
echo "   - HyperLogLog: O(1) memory (~12KB)"
echo ""
echo "2. ERROR RATE"
echo "   - Standard error: 1.04 / sqrt(m)"
echo "   - With 16384 registers: ~0.81%"
echo ""
echo "3. OPERATIONS"
echo "   - PFADD: Add element to HLL"
echo "   - PFCOUNT: Get cardinality estimate"
echo "   - PFMERGE: Union multiple HLLs"
echo ""
echo "4. USE CASES"
echo "   - Unique visitors/users"
echo "   - Unique search queries"
echo "   - Any high-cardinality counting"
echo ""
echo "5. TRADE-OFFS"
echo "   - Cannot list actual items"
echo "   - Cannot delete items"
echo "   - ~1% error (usually acceptable)"
echo ""
echo "========================================"
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

### Check visitor counter logs

```bash {"name": "logs-visitor-counter"}
docker compose logs lab32-visitor-counter --tail=50
```

### Check Redis

```bash {"name": "check-redis"}
docker compose exec lab32-redis redis-cli ping
docker compose exec lab32-redis redis-cli info memory | grep used_memory_human
```

### List HyperLogLog keys in Redis

```bash {"name": "list-hll-keys"}
docker compose exec lab32-redis redis-cli keys "hll:*"
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Record visit | `curl -X POST localhost:8000/visit?page_id=test` |
| Get stats | `curl localhost:8000/stats?page_id=test` |
| Simulate visitors | `curl -X POST localhost:8000/simulate?total_visitors=1000&unique_visitors=1000` |
| Merge HLLs | `curl -X POST localhost:8000/merge?source_pages=a,b` |
| Reset counters | `curl -X DELETE localhost:8000/reset` |
| View dashboard | http://localhost:3001 |
