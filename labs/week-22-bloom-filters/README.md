# Lab 22: Bloom Filters

A probabilistic data structure that trades perfect accuracy for massive space and time savings. In this lab, you will use a bloom filter to reduce database queries when checking if a username is already taken.

## What You Will Learn

- How bloom filters provide O(1) membership testing
- The tradeoff between false positives and memory usage
- How to tune filter size and hash functions for your use case
- When bloom filters are the right (and wrong) choice
- How to measure and visualize false positive rates in production

## Architecture

```
                                          +-----------------+
    Client Request                        |                 |
         |                                |   PostgreSQL    |
         v                                |   (usernames)   |
+------------------+                      |                 |
|                  |                      +-----------------+
|  Username        |                              ^
|  Service         |                              |
|                  |                              |
|  +------------+  |     "probably exists"        |
|  |   Bloom    |--+----------------------------->+
|  |   Filter   |  |     (query DB to confirm)
|  +------------+  |
|        |         |
|        |         |
|  "definitely     |
|   not exists"    |
|        |         |
|        v         |
|   SKIP DB QUERY  |  <-- This is the win!
|   Return: available
+------------------+
```

### How Bloom Filters Work

1. **Adding an item**: Hash the item k times, set those k bits to 1
2. **Checking membership**:
   - If ANY bit is 0: Item is **definitely not** in the set
   - If ALL bits are 1: Item is **probably** in the set (might be false positive)

The key insight: False negatives are impossible, but false positives can occur when multiple items hash to overlapping bit positions.

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
cd labs/week-22-bloom-filters
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
| Username Service | http://localhost:8000 | API + Admin endpoints |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Baseline Without Bloom Filter

First, let us see how the system behaves without the bloom filter (default state).

```bash
# Check bloom filter status (should be disabled)
curl -s http://localhost:8000/admin/bloom-stats | jq

# Register some usernames
curl -s -X POST http://localhost:8000/register-username \
  -H "Content-Type: application/json" \
  -d '{"username": "alice"}' | jq

curl -s -X POST http://localhost:8000/register-username \
  -H "Content-Type: application/json" \
  -d '{"username": "bob"}' | jq

# Check if a username is taken (this will hit the database)
curl -s -X POST http://localhost:8000/check-username \
  -H "Content-Type: application/json" \
  -d '{"username": "alice"}' | jq

# Check a username that does not exist (still hits database)
curl -s -X POST http://localhost:8000/check-username \
  -H "Content-Type: application/json" \
  -d '{"username": "newuser123"}' | jq
```

**Observe**: Both checks show `"db_queried": true` - every request hits the database.

---

### Exercise 2: Enable Bloom Filter

Enable the bloom filter and see the difference.

```bash
# Enable bloom filter
curl -s -X POST http://localhost:8000/admin/bloom-config \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq

# Check a username that exists (bloom says "probably exists", queries DB)
curl -s -X POST http://localhost:8000/check-username \
  -H "Content-Type: application/json" \
  -d '{"username": "alice"}' | jq

# Check a username that does NOT exist (bloom says "definitely not", skips DB!)
curl -s -X POST http://localhost:8000/check-username \
  -H "Content-Type: application/json" \
  -d '{"username": "newuser456"}' | jq
```

**Observe**:
- For existing usernames: `"bloom_result": "probably_exists"`, `"db_queried": true`
- For new usernames: `"bloom_result": "definitely_not_exists"`, `"db_queried": false`

The bloom filter saved us a database query for usernames that do not exist.

---

### Exercise 3: Register Many Usernames

Let us fill up the bloom filter and observe the false positive rate increase.

```bash
# Register 500 usernames using a loop
for i in $(seq 1 500); do
  curl -s -X POST http://localhost:8000/register-username \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"user$i\"}" > /dev/null
done

# Check bloom filter stats
curl -s http://localhost:8000/admin/bloom-stats | jq
```

**Observe**:
- `fill_ratio` increases as more items are added
- `theoretical_false_positive_rate` increases with fill ratio

---

### Exercise 4: Observe False Positives

With more items, false positives become more likely.

```bash
# Check many non-existent usernames and look for false positives
for i in $(seq 1 100); do
  result=$(curl -s -X POST http://localhost:8000/check-username \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"randomcheck$RANDOM$RANDOM\"}")

  bloom_result=$(echo $result | jq -r '.bloom_result')
  db_queried=$(echo $result | jq -r '.db_queried')
  available=$(echo $result | jq -r '.available')

  if [ "$bloom_result" = "probably_exists" ] && [ "$available" = "true" ]; then
    echo "FALSE POSITIVE detected! Bloom said exists but username was available."
  fi
done
```

Check the metrics in Grafana (http://localhost:3001) to see:
- `bloom_false_positives_total` counter
- `db_queries_saved_total` counter

---

### Exercise 5: Tune the Bloom Filter

Experiment with different filter sizes and hash counts.

```bash
# Make filter smaller (more false positives)
curl -s -X POST http://localhost:8000/admin/bloom-config \
  -H "Content-Type: application/json" \
  -d '{"size": 1000, "hash_count": 3}' | jq

# Check stats - note the higher fill ratio and FP rate
curl -s http://localhost:8000/admin/bloom-stats | jq

# Make filter larger (fewer false positives)
curl -s -X POST http://localhost:8000/admin/bloom-config \
  -H "Content-Type: application/json" \
  -d '{"size": 100000, "hash_count": 7}' | jq

# Check stats again
curl -s http://localhost:8000/admin/bloom-stats | jq
```

**Optimal Parameters**:
- For n items and desired false positive rate p:
- Optimal size m = -n*ln(p) / (ln(2)^2)
- Optimal hash count k = (m/n) * ln(2)

For 1000 items and 1% FP rate: m = 9586 bits, k = 7 hash functions

---

### Exercise 6: Compare Performance

Run the comparison load test to measure the performance difference.

```bash
# Test WITHOUT bloom filter
curl -s -X POST http://localhost:8000/admin/bloom-config \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Clear data for fair comparison
curl -s -X DELETE http://localhost:8000/admin/clear-all

# Run load test
docker compose run --rm lab22-k6 run /scripts/compare-modes.js

# Now test WITH bloom filter
curl -s -X POST http://localhost:8000/admin/bloom-config \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'

# Clear data again
curl -s -X DELETE http://localhost:8000/admin/clear-all

# Run load test again
docker compose run --rm lab22-k6 run /scripts/compare-modes.js
```

**Compare**:
- p50 and p95 latencies for checking new usernames
- Number of database queries made

---

## Key Metrics

| Metric | Description |
|--------|-------------|
| `bloom_checks_total{result="definitely_not_exists"}` | Bloom filter correctly identified non-members |
| `bloom_checks_total{result="probably_exists"}` | Bloom filter found possible matches (needs DB check) |
| `bloom_false_positives_total` | Times bloom said "exists" but DB said "no" |
| `db_queries_saved_total` | DB queries avoided thanks to bloom filter |
| `db_queries_total{source="direct"}` | DB queries when bloom disabled |
| `db_queries_total{source="bloom_positive"}` | DB queries after bloom positive |
| `bloom_filter_fill_ratio` | Ratio of set bits (higher = more false positives) |
| `bloom_filter_items_count` | Number of items added to filter |

## API Reference

### Core Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/check-username` | Check if username is available |
| POST | `/register-username` | Register a new username |
| GET | `/health` | Health check |
| GET | `/metrics` | Prometheus metrics |

### Admin Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/admin/bloom-stats` | Get bloom filter statistics |
| POST | `/admin/bloom-config` | Configure bloom filter (enable/disable, resize) |
| POST | `/admin/bloom-reset` | Reset and repopulate bloom filter from DB |
| DELETE | `/admin/clear-all` | Clear all data (testing only) |
| GET | `/admin/usernames` | List registered usernames |

## Key Takeaways

1. **Bloom filters excel at negative lookups** - They can definitively say "not in set" but only "probably in set"

2. **Size matters** - Larger filters have lower false positive rates but use more memory

3. **Hash count is a tradeoff** - More hashes reduce false positives but slow down operations

4. **False positives are not errors** - They just mean an extra DB query; false negatives would be errors

5. **Great for high-cardinality lookups** - Usernames, URLs, IP addresses, etc.

6. **Monitor fill ratio** - When it gets too high, consider resizing or using a counting bloom filter

## Real-World Use Cases

- **Username/email availability** - Check before hitting the database
- **Cache lookup** - Avoid cache misses for items that were never cached
- **Duplicate detection** - Quickly filter out definite non-duplicates
- **Spell checkers** - Check if a word is "probably" in the dictionary
- **Network routers** - Check if a packet should be forwarded
- **Databases** - Check if a key exists before disk lookup (LSM trees)

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Database connection issues
```bash
docker compose logs lab22-postgres
```

### Bloom filter not working as expected
```bash
# Check current stats
curl -s http://localhost:8000/admin/bloom-stats | jq

# Reset and repopulate from database
curl -s -X POST http://localhost:8000/admin/bloom-reset | jq
```

## Further Reading

- [Bloom Filters by Example](https://llimllib.github.io/bloomfilter-tutorial/)
- [Wikipedia: Bloom Filter](https://en.wikipedia.org/wiki/Bloom_filter)
- [Counting Bloom Filters](https://en.wikipedia.org/wiki/Counting_Bloom_filter)
- [Cuckoo Filters](https://brilliant.org/wiki/cuckoo-filter/) - A newer alternative
