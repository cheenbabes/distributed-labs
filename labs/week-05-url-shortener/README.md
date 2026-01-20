# Lab 05: URL Shortener - Part 1

The classic system design interview question, actually built. In this lab, you'll implement different key generation strategies, handle collisions, trace the redirect flow, and measure real read vs write patterns.

## What You'll Learn

- How different key generation strategies work (counter, hash, random)
- How to handle collisions in a distributed system
- How to trace a shorten -> redirect flow end-to-end
- How caching affects read-heavy workloads (100:1 read/write ratio)
- Foundation concepts for scaling URL shorteners

## Architecture

```
                                    ┌─────────────┐
                                    │   Redis     │
                                    │  (Cache)    │
                                    └──────▲──────┘
                                           │
┌──────────┐    ┌───────────────────┐      │      ┌───────────────┐
│  Client  │───▶│  URL Shortener    │──────┴─────▶│   Postgres    │
└──────────┘    │       API         │             │   (Storage)   │
                └───────────────────┘             └───────────────┘
                        │
                        ▼
                ┌───────────────┐
                │    OTEL       │──▶ Jaeger (Traces)
                │  Collector    │──▶ Prometheus (Metrics)
                └───────────────┘
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `POST /shorten` | POST | Create a short URL from a long URL |
| `GET /{short_code}` | GET | Redirect to the original URL |
| `GET /stats/{short_code}` | GET | Get statistics for a short URL |
| `POST /admin/strategy` | POST | Change key generation strategy |

### Key Generation Strategies

1. **Counter (Base62)** - Sequential IDs encoded in Base62
2. **Hash (MD5)** - First 7 characters of MD5 hash
3. **Random** - Random 7-character Base62 string

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
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
| Shortener API | http://localhost:8000 | URL shortening service |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Shorten Your First URL

Let's create a short URL and trace the entire flow.

```bash
# Shorten a URL
curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.example.com/very/long/path/that/needs/shortening"}' | jq

# You'll get a response like:
# {
#   "short_code": "abc1234",
#   "short_url": "http://localhost:8000/abc1234",
#   "original_url": "https://www.example.com/very/long/path/that/needs/shortening",
#   "strategy": "counter"
# }
```

Now use the short URL to redirect:

```bash
# Follow the redirect (-L) and show response info (-w)
curl -L -s -o /dev/null -w "Redirected to: %{url_effective}\nStatus: %{http_code}\n" \
  http://localhost:8000/abc1234
```

**Task:** Open Jaeger (http://localhost:16686), find the trace for your redirect request, and observe the cache lookup and database query spans.

---

### Exercise 2: Compare Key Generation Strategies

Let's compare how different strategies generate short codes.

#### Counter Strategy (Default)

```bash
# Switch to counter strategy
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "counter"}' | jq

# Create 5 URLs and observe sequential codes
for i in {1..5}; do
  curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://example.com/page$i\"}" | jq -r '.short_code'
done
```

**Expected:** Codes like `1`, `2`, `3`, `4`, `5` (Base62 encoded)

#### Hash Strategy

```bash
# Switch to hash strategy
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "hash"}' | jq

# Create URLs - same URL should give same code
curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/consistent"}' | jq -r '.short_code'

curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/consistent"}' | jq -r '.short_code'
```

**Expected:** Same code for the same URL (deterministic)

#### Random Strategy

```bash
# Switch to random strategy
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "random"}' | jq

# Create the same URL twice - different codes each time
curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/random-test"}' | jq -r '.short_code'

curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/random-test"}' | jq -r '.short_code'
```

**Expected:** Different codes each time (non-deterministic)

---

### Exercise 3: Observe Collision Handling

With the hash strategy, we can force a collision scenario.

```bash
# Switch to hash strategy
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "hash"}' | jq

# Enable collision simulation (adds suffix when collision detected)
curl -s -X POST http://localhost:8000/admin/collision-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq
```

Now create URLs that will collide:

```bash
# These will demonstrate collision handling
curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://collision-test.com/page1"}' | jq

curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://collision-test.com/page2"}' | jq
```

**Task:** Look at the traces in Jaeger and observe:
- The collision detection span
- How many retry attempts were needed
- The final generated short code

---

### Exercise 4: Measure Read vs Write Patterns

URL shorteners are extremely read-heavy. Let's measure this.

```bash
# First, create some URLs to read
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://example.com/popular-page-$i\"}" > /dev/null
done

# Get the short codes we created
CODES=$(curl -s http://localhost:8000/admin/recent?limit=10 | jq -r '.[].short_code')

# Now simulate read-heavy traffic (100 reads per write)
echo "Simulating 100:1 read/write ratio..."
for i in {1..10}; do
  # 1 write
  curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://example.com/new-page-$i\"}" > /dev/null

  # 100 reads (randomly from existing codes)
  for j in {1..100}; do
    CODE=$(echo "$CODES" | shuf -n 1)
    curl -s -o /dev/null http://localhost:8000/$CODE
  done

  echo "Batch $i complete"
done
```

Now check the metrics:

```bash
# View read vs write metrics
curl -s http://localhost:8000/metrics | grep -E "(shorten|redirect)_total"
```

**Open Grafana** (http://localhost:3001) and observe:
- Request rate by endpoint
- Read vs write ratio visualization
- Response time differences between reads and writes

---

### Exercise 5: Observe Cache Effectiveness

Let's see how caching improves redirect performance.

```bash
# Create a URL for testing
RESULT=$(curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://cache-test.com/popular"}')
CODE=$(echo $RESULT | jq -r '.short_code')
echo "Testing with code: $CODE"

# First access - cache miss (hits database)
echo "First access (cold cache):"
curl -s -o /dev/null -w "Time: %{time_total}s\n" http://localhost:8000/$CODE

# Second access - cache hit (from Redis)
echo "Second access (warm cache):"
curl -s -o /dev/null -w "Time: %{time_total}s\n" http://localhost:8000/$CODE

# Multiple accesses to observe consistent cache performance
echo "Multiple cached accesses:"
for i in {1..5}; do
  curl -s -o /dev/null -w "Access $i: %{time_total}s\n" http://localhost:8000/$CODE
done
```

**Check cache hit rate:**

```bash
curl -s http://localhost:8000/admin/cache-stats | jq
```

**In Jaeger:**
1. Find a redirect trace with a cache miss (you'll see a `db.query` span)
2. Find a redirect trace with a cache hit (no `db.query` span)
3. Compare the total latencies

---

### Exercise 6: Load Test with Mixed Workload

Run a realistic load test with 100:1 read/write ratio:

```bash
# Run the load test
docker compose run --rm k6 run /scripts/url-shortener.js
```

While the test runs:
1. Watch Grafana dashboards
2. Observe cache hit rate increasing over time
3. Monitor p50, p95, p99 latencies for reads vs writes
4. Check database connection pool usage

---

## Key Takeaways

1. **Strategy Trade-offs:**
   - Counter: Predictable but reveals volume; requires coordination for distributed
   - Hash: Deterministic and dedup-friendly but collision-prone with short codes
   - Random: No coordination needed but no deduplication

2. **Read vs Write Ratio:** Real URL shorteners see 100:1 or even 1000:1 ratios - design for reads!

3. **Caching is Critical:** With proper caching, most redirects never touch the database

4. **Collision Handling:** Essential for hash-based strategies; adds complexity and latency

5. **Tracing Reveals Truth:** Distributed tracing shows exactly where time is spent in each request

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
docker compose logs postgres
docker compose exec postgres psql -U shortener -c "SELECT 1"
```

### Redis connection issues
```bash
docker compose exec redis redis-cli ping
```

### Traces not appearing in Jaeger
- Wait 10-15 seconds after making requests
- Check: `docker compose logs otel-collector`

## Next Lab

[Lab 06: URL Shortener - Part 2](../week-06-url-shortener-2/) - We'll scale the shortener horizontally, implement rate limiting, and handle the "thundering herd" problem.
