# Lab 37: Distributed Locks with Redlock

How do you ensure only one process modifies a shared resource at a time in a distributed system? In this lab, you will implement and explore the Redlock algorithm for distributed locking using multiple Redis instances.

## What You Will Learn

- How distributed locks work and why they are challenging
- The Redlock algorithm and its quorum-based approach
- Lock contention patterns and their impact on throughput
- What happens when Redis instances fail during lock operations
- The controversy around Redlock and when to use alternatives

## Architecture

```
                                    ┌─────────────────┐
                                    │   lock-service  │
                                    │   (Redlock API) │
                                    └────────┬────────┘
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    │                        │                        │
                    ▼                        ▼                        ▼
             ┌──────────┐             ┌──────────┐             ┌──────────┐
             │ Redis-1  │             │ Redis-2  │             │ Redis-3  │
             │ (6381)   │             │ (6382)   │             │ (6383)   │
             └──────────┘             └──────────┘             └──────────┘

        ┌──────────┐    ┌──────────┐    ┌──────────┐
        │ Worker-1 │    │ Worker-2 │    │ Worker-3 │
        └──────────┘    └──────────┘    └──────────┘
              │               │               │
              └───────────────┼───────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  lock-service   │
                    │ (compete for    │
                    │  shared lock)   │
                    └─────────────────┘
```

The Redlock algorithm works by:
1. Acquiring locks on N Redis instances (we use 3)
2. Requiring a quorum (N/2 + 1 = 2) to consider the lock acquired
3. Accounting for clock drift and network latency
4. Using atomic operations with unique lock identifiers

## Prerequisites

- Docker and Docker Compose
- curl (for API testing)
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

All services should show as "healthy" or "running".

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Lock Service | http://localhost:8000 | Distributed lock API |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Basic Lock Acquisition

First, understand how the lock service works with a simple acquire/release cycle.

```bash
# Check service health
curl -s http://localhost:8000/health | jq

# Acquire a lock
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "my-resource"}' | jq

# Check lock status
curl -s http://localhost:8000/lock/status/my-resource | jq

# Release the lock (use lock_id from acquire response)
curl -s -X POST http://localhost:8000/lock/release \
  -H "Content-Type: application/json" \
  -d '{"resource": "my-resource", "lock_id": "YOUR_LOCK_ID"}' | jq
```

**Observations:**
- How many Redis nodes was the lock acquired on?
- What is the `valid_until_ms` field telling you?
- What happens if you try to release with a wrong lock_id?

---

### Exercise 2: Lock Contention

Watch multiple workers compete for the same lock.

```bash
# View worker logs (they're competing for "shared-resource")
docker compose logs -f lab37-worker-1 lab37-worker-2 lab37-worker-3
```

**Questions to answer:**
- Which worker wins the lock at any given time?
- How long do workers wait when they cannot acquire the lock?
- What is the effective throughput (successful lock acquisitions per minute)?

Check the metrics in Grafana:
1. Open http://localhost:3001
2. Navigate to "Distributed Locks Dashboard"
3. Observe the lock acquisition rate and contention metrics

---

### Exercise 3: Redis Instance Failure

Test what happens when one Redis instance fails. This is where Redlock shines.

```bash
# First, check current health
curl -s http://localhost:8000/health | jq

# Stop one Redis instance
docker compose stop lab37-redis-1

# Check health again
curl -s http://localhost:8000/health | jq

# Try to acquire a lock - should still work!
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "test-failure", "client_id": "test-client"}' | jq

# Check which nodes have the lock
curl -s http://localhost:8000/lock/status/test-failure | jq
```

**Expected behavior:** Lock acquisition should still succeed because we have 2 out of 3 Redis instances (quorum).

Now stop another Redis instance:

```bash
# Stop a second Redis instance
docker compose stop lab37-redis-2

# Check health
curl -s http://localhost:8000/health | jq

# Try to acquire a new lock - should fail!
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "test-failure-2", "client_id": "test-client"}' | jq
```

**Expected behavior:** Lock acquisition should fail because we no longer have quorum (only 1 of 3 Redis instances).

Restore the Redis instances:

```bash
docker compose start lab37-redis-1 lab37-redis-2
```

---

### Exercise 4: Single Redis vs Redlock

Compare the behavior of using a single Redis instance versus the full Redlock algorithm.

```bash
# Switch to single Redis mode
curl -s -X POST "http://localhost:8000/admin/mode?mode=single" | jq

# Check current mode
curl -s http://localhost:8000/admin/mode | jq

# Acquire a lock in single mode
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "single-test"}' | jq

# Stop the single Redis instance
docker compose stop lab37-redis-1

# Try to acquire - will fail completely
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "single-test-2"}' | jq

# Restore and switch back to Redlock
docker compose start lab37-redis-1
curl -s -X POST "http://localhost:8000/admin/mode?mode=redlock" | jq
```

**Discussion:**
- Single Redis is simpler but has a single point of failure
- Redlock provides fault tolerance but adds complexity
- Consider: Do you really need distributed locks, or would a simpler approach work?

---

### Exercise 5: The Redlock Controversy

The Redlock algorithm has been criticized by distributed systems experts. Let us explore the edge cases.

**Scenario: Lock expiration during processing**

```bash
# Acquire a lock with very short TTL (2 seconds)
RESULT=$(curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "short-lived", "ttl_ms": 2000}')

echo $RESULT | jq

LOCK_ID=$(echo $RESULT | jq -r '.lock_id')

# Wait for TTL to expire
echo "Waiting 3 seconds for lock to expire..."
sleep 3

# Check lock status - should show no lock
curl -s http://localhost:8000/lock/status/short-lived | jq

# Another client can now acquire the same resource!
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "short-lived", "client_id": "new-client"}' | jq

# Original client tries to release their expired lock
curl -s -X POST http://localhost:8000/lock/release \
  -H "Content-Type: application/json" \
  -d '{"resource": "short-lived", "lock_id": "'$LOCK_ID'"}' | jq
```

**Key insight:** If your processing takes longer than the lock TTL, another client can acquire the lock while you are still working. The original Redlock paper does not fully address this.

**Mitigation strategies:**
1. Use fencing tokens (monotonically increasing identifiers)
2. Extend lock TTL periodically while processing
3. Design systems to be idempotent

---

### Exercise 6: Load Testing

Generate sustained load to observe lock behavior under pressure.

```bash
# Run the contention load test
docker compose run --rm lab37-k6 run /scripts/lock-contention.js
```

While the test runs:
1. Watch Grafana dashboards (http://localhost:3001)
2. Observe lock acquisition success rate
3. See contention events increase with more VUs

For testing Redis failure scenarios:

```bash
# Run in one terminal
docker compose run --rm lab37-k6 run /scripts/redis-failure.js

# In another terminal, during the test, stop a Redis node
docker compose stop lab37-redis-2

# Wait 30 seconds, then restart
docker compose start lab37-redis-2
```

---

## Key Takeaways

1. **Distributed locks are hard** - Clock skew, network partitions, and process pauses can all cause issues.

2. **Redlock provides fault tolerance** - With N Redis instances and quorum requirement, you can survive N/2 failures.

3. **TTL is critical** - Too short and locks expire during processing; too long and you have availability issues.

4. **Contention limits throughput** - With many clients competing for the same lock, expect significant contention.

5. **Consider alternatives** -
   - For coordination: Use a proper consensus system (etcd, ZooKeeper, Consul)
   - For idempotency: Use database transactions or optimistic locking
   - For rate limiting: Use token buckets or leaky buckets

6. **The Redlock controversy** - Martin Kleppmann's analysis raises valid concerns about safety guarantees. Understand the trade-offs.

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
# Check Redis status
docker compose exec lab37-redis-1 redis-cli ping
```

### Workers not acquiring locks
```bash
# Check lock service health
curl -s http://localhost:8000/health | jq

# Check Redis node status
curl -s http://localhost:8000/admin/redis/status | jq
```

### Traces not appearing in Jaeger
- Wait 10-15 seconds after making requests
- Check OTEL collector logs: `docker compose logs lab37-otel-collector`

## Further Reading

- [Redlock Algorithm (Redis.io)](https://redis.io/docs/manual/patterns/distributed-locks/)
- [How to do distributed locking (Martin Kleppmann)](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html)
- [Is Redlock Safe? (Antirez response)](http://antirez.com/news/101)
- [Designing Data-Intensive Applications - Chapter 8](https://dataintensive.net/)

## Next Lab

[Lab 38: Distributed Transactions](../week-38-distributed-transactions/) - We will explore saga patterns and two-phase commit for distributed transactions.
