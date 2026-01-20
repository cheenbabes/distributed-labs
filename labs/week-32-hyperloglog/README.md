# Lab 32: HyperLogLog - Counting Billions with 12KB

How do you count the number of unique visitors to your website when you have billions of page views? Storing every user ID would require terabytes of memory. In this lab, you'll discover HyperLogLog - a probabilistic data structure that can count billions of unique items using only 12KB of memory with approximately 0.81% error.

## What You'll Learn

- How HyperLogLog works conceptually (hashing + bucket counting)
- When to use probabilistic counting vs exact counting
- Redis PFADD/PFCOUNT/PFMERGE commands
- How to measure and visualize the accuracy/memory tradeoff
- Merging HyperLogLogs for time-windowed analytics

## Architecture

```
                                    +-----------------+
                                    |                 |
   Visitors -----> /visit -------> |  Visitor        |
                                    |  Counter        |
                                    |  Service        |
                                    |                 |
                                    +--------+--------+
                                             |
                    +------------------------+------------------------+
                    |                                                 |
                    v                                                 v
          +--------+--------+                               +--------+--------+
          |                 |                               |                 |
          |  Python Set     |                               |  Redis HLL      |
          |  (Exact Count)  |                               |  (Probabilistic)|
          |                 |                               |                 |
          |  Memory: O(n)   |                               |  Memory: 12KB   |
          |  Error: 0%      |                               |  Error: ~0.81%  |
          |                 |                               |                 |
          +-----------------+                               +-----------------+
```

Each page visit is recorded using two methods simultaneously:
1. **Exact counting** with Python sets - memory grows linearly with unique visitors
2. **HyperLogLog** with Redis - constant ~12KB memory regardless of cardinality

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
| Visitor Counter | http://localhost:8000 | API + Swagger docs |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |
| Prometheus | http://localhost:9090 | Metrics |
| Jaeger | http://localhost:16686 | Distributed traces |

## How HyperLogLog Works

HyperLogLog uses a clever trick based on probability:

1. **Hash the input**: Convert each visitor ID to a uniformly distributed hash
2. **Count leading zeros**: The probability of seeing N leading zeros is 1/2^N
3. **Keep the maximum**: Track the maximum number of leading zeros seen
4. **Estimate cardinality**: 2^(max leading zeros) estimates unique items

The "Hyper" in HyperLogLog comes from using multiple buckets (registers) and harmonic mean to improve accuracy.

**Key insight**: You don't need to store the actual items - just the maximum leading zeros seen in each bucket!

## Lab Exercises

### Exercise 1: Basic Counting Comparison

Record a few visitors and observe the difference between exact and HLL counting.

```bash
# Record a single visit
curl -X POST "http://localhost:8000/visit?page_id=homepage"

# Record the same visitor again (should not increase count)
curl -X POST "http://localhost:8000/visit?page_id=homepage&visitor_id=user-123"
curl -X POST "http://localhost:8000/visit?page_id=homepage&visitor_id=user-123"

# Check the stats
curl -s "http://localhost:8000/stats?page_id=homepage" | jq
```

**Expected**: Both exact and HLL counts should be 2 (or very close). Memory usage should be similar at low scale.

**Question**: Why are the counts the same at this scale?

---

### Exercise 2: Watch Memory Grow with Exact Counting

Generate 1,000 unique visitors and observe memory usage.

```bash
# Simulate 1,000 unique visitors
curl -X POST "http://localhost:8000/simulate?page_id=test1&total_visitors=1000&unique_visitors=1000" | jq

# Check memory comparison
curl -s "http://localhost:8000/stats?page_id=test1" | jq
```

**Expected**:
- Exact counting: ~100+ KB memory
- HyperLogLog: ~12 KB memory
- Error rate: typically < 1%

Now try with 10,000 unique visitors:

```bash
curl -X POST "http://localhost:8000/simulate?page_id=test2&total_visitors=10000&unique_visitors=10000" | jq
```

**Question**: How does memory usage scale for each method?

---

### Exercise 3: HyperLogLog Error Distribution

Run multiple simulations to see the error distribution.

```bash
# Reset counters
curl -X DELETE "http://localhost:8000/reset"

# Run 10 simulations with 5,000 unique visitors each
for i in {1..10}; do
  curl -X POST "http://localhost:8000/simulate?page_id=test_$i&total_visitors=5000&unique_visitors=5000" | jq '.results.error_percentage'
done
```

**Expected**: Most error rates should be between 0% and 2%, clustering around 0.81%.

**Question**: Why does HyperLogLog have a standard error of 0.81%?

---

### Exercise 4: Merging HyperLogLogs

A key feature of HyperLogLog is the ability to merge multiple HLLs to count unique items across time windows without double-counting.

```bash
# Reset counters
curl -X DELETE "http://localhost:8000/reset"

# Simulate morning visitors
curl -X POST "http://localhost:8000/simulate?page_id=morning&total_visitors=1000&unique_visitors=800"

# Simulate afternoon visitors (some overlap with morning)
curl -X POST "http://localhost:8000/simulate?page_id=afternoon&total_visitors=1000&unique_visitors=800"

# Some visitors visited both morning and afternoon - simulate overlap
for i in {1..200}; do
  visitor_id="overlap-user-$i"
  curl -s -X POST "http://localhost:8000/visit?page_id=morning&visitor_id=$visitor_id" > /dev/null
  curl -s -X POST "http://localhost:8000/visit?page_id=afternoon&visitor_id=$visitor_id" > /dev/null
done

# Check individual counts
echo "Morning visitors:"
curl -s "http://localhost:8000/stats?page_id=morning" | jq '.exact.count, .hyperloglog.count'

echo "Afternoon visitors:"
curl -s "http://localhost:8000/stats?page_id=afternoon" | jq '.exact.count, .hyperloglog.count'

# Merge to get daily unique visitors (without double-counting!)
curl -X POST "http://localhost:8000/merge?source_pages=morning,afternoon&target_key=daily" | jq
```

**Expected**: The merged count should be less than the sum of individual counts because HyperLogLog automatically handles duplicates during merge.

**Question**: How would you solve this problem without HyperLogLog?

---

### Exercise 5: Visualize in Grafana

Open Grafana (http://localhost:3001, admin/admin) and explore the HyperLogLog dashboard.

1. Start the load test to generate traffic:
   ```bash
   docker compose run --rm lab32-k6 run /scripts/basic.js
   ```

2. While the test runs, observe in Grafana:
   - **Unique Visitors graph**: Exact vs HLL counts (should track closely)
   - **Memory Usage graph**: Exact memory grows, HLL stays flat
   - **Error Rate graph**: Should hover around 0.81%
   - **Memory Savings Ratio**: Watch it increase as cardinality grows

**Key Insight**: The memory savings ratio increases as you count more unique items!

---

### Exercise 6: Scale Test

Run the scale test to push HyperLogLog to its limits.

```bash
# Reset first
curl -X DELETE "http://localhost:8000/reset"

# Run scale test (generates ~12,000 unique visitors)
docker compose run --rm lab32-k6 run /scripts/scale-test.js
```

After the test, check the final stats:

```bash
curl -s "http://localhost:8000/stats?page_id=scale_test" | jq
```

**Expected**:
- Memory savings should be 50x-100x or more
- Error rate should still be around 0.81%

**Question**: At what cardinality does HyperLogLog become worthwhile?

---

## Key Takeaways

1. **HyperLogLog trades accuracy for memory** - 0.81% error for O(1) memory is often an excellent tradeoff

2. **Memory is constant** - Whether counting 1,000 or 1 billion unique items, HLL uses ~12KB

3. **Merging is powerful** - You can union HLLs without re-processing raw data, enabling efficient time-windowed analytics

4. **Error is predictable** - Standard error of 1.04/sqrt(m) where m is number of registers (default 16384 = 0.81%)

5. **Perfect for analytics** - When you need "approximately how many" not "exactly how many"

## When to Use HyperLogLog

**Good Use Cases:**
- Counting unique visitors/users
- Counting unique search queries
- Counting unique events (clicks, views, etc.)
- Any high-cardinality counting where ~1% error is acceptable

**Bad Use Cases:**
- When you need exact counts (billing, voting)
- When you need to list the actual items (HLL only counts, doesn't store)
- Low-cardinality sets (just use a Set)

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Redis connection issues
```bash
docker compose logs lab32-redis
docker compose exec lab32-redis redis-cli ping
```

### Metrics not appearing
- Wait 15-30 seconds for Prometheus to scrape
- Check that the visitor-counter is healthy: `curl http://localhost:8000/health`

## Learn More

- [Redis HyperLogLog Documentation](https://redis.io/docs/data-types/probabilistic/hyperloglogs/)
- [Original HyperLogLog Paper](http://algo.inria.fr/flajolet/Publications/FlFuGaMe07.pdf)
- [Google's HyperLogLog++ Improvements](https://research.google/pubs/pub40671/)

## Next Lab

Continue exploring probabilistic data structures with [Lab 22: Bloom Filters](../week-22-bloom-filters/) - test set membership with O(1) memory.
