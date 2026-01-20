# Demo Runbook: Bloom Filters

This runbook contains all commands for the video demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
cd /Users/ebaibourine/github/distributed-lab/labs/week-22-bloom-filters
docker compose down -v 2>/dev/null || true
```

---

## Part 1: Start the Lab

### Build and start all services

```bash {"name": "start-lab"}
cd /Users/ebaibourine/github/distributed-lab/labs/week-22-bloom-filters
docker compose up --build -d
```

### Wait for services to be healthy

```bash {"name": "wait-healthy"}
echo "Waiting for services to be healthy..."
sleep 15
cd /Users/ebaibourine/github/distributed-lab/labs/week-22-bloom-filters
docker compose ps
```

### Verify service is responding

```bash {"name": "verify-services"}
curl -s http://localhost:8000/health | jq
```

---

## Part 2: Show URLs

### Print all URLs for the demo

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Username Service: http://localhost:8000"
echo "  Grafana:          http://localhost:3001 (admin/admin)"
echo "  Jaeger:           http://localhost:16686"
echo "  Prometheus:       http://localhost:9090"
```

---

## Part 3: Baseline Without Bloom Filter

### Check bloom filter status

```bash {"name": "check-bloom-status"}
curl -s http://localhost:8000/admin/bloom-stats | jq
```

### Register some usernames

```bash {"name": "register-initial-users"}
echo "Registering alice..."
curl -s -X POST http://localhost:8000/register-username \
  -H "Content-Type: application/json" \
  -d '{"username": "alice"}' | jq

echo ""
echo "Registering bob..."
curl -s -X POST http://localhost:8000/register-username \
  -H "Content-Type: application/json" \
  -d '{"username": "bob"}' | jq

echo ""
echo "Registering charlie..."
curl -s -X POST http://localhost:8000/register-username \
  -H "Content-Type: application/json" \
  -d '{"username": "charlie"}' | jq
```

### Check an existing username (without bloom filter)

```bash {"name": "check-existing-no-bloom"}
echo "Checking 'alice' (exists) - WITHOUT bloom filter:"
curl -s -X POST http://localhost:8000/check-username \
  -H "Content-Type: application/json" \
  -d '{"username": "alice"}' | jq
```

### Check a new username (without bloom filter)

```bash {"name": "check-new-no-bloom"}
echo "Checking 'newuser123' (does not exist) - WITHOUT bloom filter:"
curl -s -X POST http://localhost:8000/check-username \
  -H "Content-Type: application/json" \
  -d '{"username": "newuser123"}' | jq
```

**Key Point**: Notice `"db_queried": true` in both cases. Every request hits the database.

---

## Part 4: Enable Bloom Filter

### Enable the bloom filter

```bash {"name": "enable-bloom"}
curl -s -X POST http://localhost:8000/admin/bloom-config \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq
```

### Check an existing username (with bloom filter)

```bash {"name": "check-existing-with-bloom"}
echo "Checking 'alice' (exists) - WITH bloom filter:"
curl -s -X POST http://localhost:8000/check-username \
  -H "Content-Type: application/json" \
  -d '{"username": "alice"}' | jq
```

**Key Point**: Bloom says "probably_exists", so we still query DB to confirm.

### Check a new username (with bloom filter)

```bash {"name": "check-new-with-bloom"}
echo "Checking 'newuser456' (does not exist) - WITH bloom filter:"
curl -s -X POST http://localhost:8000/check-username \
  -H "Content-Type: application/json" \
  -d '{"username": "newuser456"}' | jq
```

**Key Point**: Bloom says "definitely_not_exists", `"db_queried": false` - We saved a database query!

---

## Part 5: Fill the Bloom Filter

### Register many usernames

```bash {"name": "register-many"}
echo "Registering 200 usernames..."
for i in $(seq 1 200); do
  curl -s -X POST http://localhost:8000/register-username \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"testuser$i\"}" > /dev/null
  if [ $((i % 50)) -eq 0 ]; then
    echo "  Registered $i usernames..."
  fi
done
echo "Done!"
```

### Check bloom filter stats after filling

```bash {"name": "check-stats-after-fill"}
curl -s http://localhost:8000/admin/bloom-stats | jq
```

**Key Point**: Notice the `fill_ratio` and `theoretical_false_positive_rate` have increased.

---

## Part 6: Demonstrate False Positives

### Check for false positives

```bash {"name": "find-false-positives"}
echo "Checking 50 random non-existent usernames for false positives..."
fp_count=0
total=50

for i in $(seq 1 $total); do
  username="randomcheck${RANDOM}${RANDOM}xyz"
  result=$(curl -s -X POST http://localhost:8000/check-username \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"$username\"}")

  bloom_result=$(echo $result | jq -r '.bloom_result')
  available=$(echo $result | jq -r '.available')

  if [ "$bloom_result" = "probably_exists" ] && [ "$available" = "true" ]; then
    fp_count=$((fp_count + 1))
    echo "  FALSE POSITIVE #$fp_count: '$username'"
  fi
done

echo ""
echo "Found $fp_count false positives out of $total checks"
echo "Observed FP rate: $(echo "scale=2; $fp_count * 100 / $total" | bc)%"
```

---

## Part 7: Tune the Bloom Filter

### Make the filter smaller (more false positives)

```bash {"name": "shrink-filter"}
echo "Shrinking bloom filter to 1000 bits with 3 hash functions..."
curl -s -X POST http://localhost:8000/admin/bloom-config \
  -H "Content-Type: application/json" \
  -d '{"size": 1000, "hash_count": 3}' | jq
```

### Check stats with smaller filter

```bash {"name": "check-small-filter-stats"}
curl -s http://localhost:8000/admin/bloom-stats | jq
```

**Key Point**: Much higher `fill_ratio` and `theoretical_false_positive_rate`.

### Make the filter larger (fewer false positives)

```bash {"name": "enlarge-filter"}
echo "Enlarging bloom filter to 50000 bits with 7 hash functions..."
curl -s -X POST http://localhost:8000/admin/bloom-config \
  -H "Content-Type: application/json" \
  -d '{"size": 50000, "hash_count": 7}' | jq
```

### Check stats with larger filter

```bash {"name": "check-large-filter-stats"}
curl -s http://localhost:8000/admin/bloom-stats | jq
```

**Key Point**: Much lower `fill_ratio` and `theoretical_false_positive_rate`.

---

## Part 8: View Metrics in Grafana

### Show metrics endpoint

```bash {"name": "show-metrics"}
echo "Key metrics from the service:"
curl -s http://localhost:8000/metrics | grep -E "^(bloom_|db_queries)" | head -20
```

### Explain the dashboard

```bash {"name": "grafana-instructions"}
echo "Open Grafana at http://localhost:3001"
echo "Login: admin / admin"
echo ""
echo "Go to Dashboards -> Bloom Filter Lab"
echo ""
echo "Key panels to observe:"
echo "  1. DB Queries Saved - Shows how many DB calls bloom filter prevented"
echo "  2. Bloom Filter False Positives - Count of incorrect 'probably exists'"
echo "  3. Fill Ratio - How full the filter is (higher = more FPs)"
echo "  4. Items in Bloom Filter - Number of usernames added"
echo "  5. Bloom Filter Check Rate - probably_exists vs definitely_not_exists"
echo "  6. Database Query Rate - direct vs bloom_positive sources"
```

---

## Part 9: Compare Performance (Optional)

### Run comparison without bloom filter

```bash {"name": "compare-no-bloom"}
echo "Testing WITHOUT bloom filter..."
curl -s -X POST http://localhost:8000/admin/bloom-config \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' > /dev/null

# Make 20 requests and measure timing
total_time=0
for i in $(seq 1 20); do
  time=$(curl -s -o /dev/null -w "%{time_total}" \
    -X POST http://localhost:8000/check-username \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"randomtest${RANDOM}\"}")
  total_time=$(echo "$total_time + $time" | bc)
done

avg=$(echo "scale=4; $total_time / 20" | bc)
echo "Average latency WITHOUT bloom filter: ${avg}s"
```

### Run comparison with bloom filter

```bash {"name": "compare-with-bloom"}
echo "Testing WITH bloom filter..."
curl -s -X POST http://localhost:8000/admin/bloom-config \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' > /dev/null

# Make 20 requests and measure timing
total_time=0
for i in $(seq 1 20); do
  time=$(curl -s -o /dev/null -w "%{time_total}" \
    -X POST http://localhost:8000/check-username \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"randomtest${RANDOM}\"}")
  total_time=$(echo "$total_time + $time" | bc)
done

avg=$(echo "scale=4; $total_time / 20" | bc)
echo "Average latency WITH bloom filter: ${avg}s"
```

---

## Part 10: View Traces

### Generate some traces

```bash {"name": "generate-traces"}
echo "Generating traces..."
for i in $(seq 1 10); do
  curl -s -X POST http://localhost:8000/check-username \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"tracetest$i\"}" > /dev/null
done
echo "Done! Open Jaeger at http://localhost:16686"
echo "Select 'username-service' and click 'Find Traces'"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
cd /Users/ebaibourine/github/distributed-lab/labs/week-22-bloom-filters
docker compose down -v
echo "Lab cleaned up"
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Enable bloom | `curl -X POST localhost:8000/admin/bloom-config -d '{"enabled":true}'` |
| Disable bloom | `curl -X POST localhost:8000/admin/bloom-config -d '{"enabled":false}'` |
| Check stats | `curl localhost:8000/admin/bloom-stats` |
| Resize filter | `curl -X POST localhost:8000/admin/bloom-config -d '{"size":50000,"hash_count":7}'` |
| Check username | `curl -X POST localhost:8000/check-username -d '{"username":"test"}'` |
| Register user | `curl -X POST localhost:8000/register-username -d '{"username":"test"}'` |
| View dashboard | http://localhost:3001 |
| View traces | http://localhost:16686 |

---

## Troubleshooting

### Check service logs

```bash {"name": "logs-service"}
cd /Users/ebaibourine/github/distributed-lab/labs/week-22-bloom-filters
docker compose logs lab22-username-service --tail=50
```

### Check database logs

```bash {"name": "logs-postgres"}
cd /Users/ebaibourine/github/distributed-lab/labs/week-22-bloom-filters
docker compose logs lab22-postgres --tail=50
```

### Restart the service

```bash {"name": "restart-service"}
cd /Users/ebaibourine/github/distributed-lab/labs/week-22-bloom-filters
docker compose restart lab22-username-service
```

### Reset everything

```bash {"name": "reset-all"}
cd /Users/ebaibourine/github/distributed-lab/labs/week-22-bloom-filters
docker compose down -v
docker compose up --build -d
echo "Waiting for services..."
sleep 15
docker compose ps
```
