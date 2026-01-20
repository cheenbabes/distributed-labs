# Distributed Lab Curriculum

52 hands-on labs for learning distributed systems patterns through practical exercises.

## Overview

Each lab is designed to:
- Run locally with Docker Compose
- Be fully observable through OpenTelemetry traces, Prometheus metrics, and Grafana dashboards
- Demonstrate real production patterns (not toys)
- Break in interesting, educational ways

---

## Week-by-Week Curriculum

### Foundation & Observability (Weeks 1-5)

| Week | Lab | What You'll Learn | Infrastructure |
|------|-----|-------------------|----------------|
| 1 | Finding Slow Service | Distributed tracing basics, finding latency bottlenecks | Base stack |
| 2 | Cache Dies | What happens when your cache fails, cache dependency patterns | Redis |
| 3 | Write-Through Cache | Write-through caching strategy, consistency guarantees | Redis, Postgres |
| 4 | Connection Pool Exhaustion | Database connection management, pool sizing | Postgres |
| 5 | URL Shortener | Basic distributed system design, hashing | Redis, Postgres |

### Reliability Patterns (Weeks 6-10)

| Week | Lab | What You'll Learn | Infrastructure |
|------|-----|-------------------|----------------|
| 6 | Retry Storms | Why naive retries fail, exponential backoff | Base stack |
| 7 | Circuit Breakers | Fail-fast pattern, circuit breaker states | Base stack |
| 8 | Cache-Aside Pattern | Lazy loading cache, cache invalidation | Redis, Postgres |
| 9 | Cascading Failures | Failure propagation, isolation strategies | Multiple services |
| 10 | Blue-Green Deployments | Zero-downtime deployments, traffic switching | Multiple service versions |

### Rate Limiting & Load Management (Weeks 11-15)

| Week | Lab | What You'll Learn | Infrastructure |
|------|-----|-------------------|----------------|
| 11 | Rate Limiting | Token bucket, sliding window algorithms | Redis |
| 12 | Thundering Herd | Cache stampede prevention, request coalescing | Redis |
| 13 | Idempotency Keys | Safe retries, exactly-once semantics | Postgres |
| 14 | Eventual Consistency | CAP theorem in practice, consistency models | Multiple replicas |
| 15 | Leaderboard System | Redis sorted sets, real-time rankings | Redis |

### Debugging & Queues (Weeks 16-20)

| Week | Lab | What You'll Learn | Infrastructure |
|------|-----|-------------------|----------------|
| 16 | Memory Leak | Detecting and diagnosing memory leaks | Base stack |
| 17 | Dead Letter Queues | Failed message handling, poison pills | RabbitMQ |
| 18 | Backpressure | Flow control, producer-consumer balance | Kafka or RabbitMQ |
| 19 | Canary Releases | Progressive rollouts, traffic splitting | Multiple service versions |
| 20 | Distributed Rate Limiter | Coordinated rate limiting across nodes | Redis |

### Advanced Patterns (Weeks 21-25)

| Week | Lab | What You'll Learn | Infrastructure |
|------|-----|-------------------|----------------|
| 21 | Race Conditions | Identifying and reproducing race conditions | Postgres |
| 22 | Bloom Filters | Probabilistic data structures, membership testing | Redis |
| 23 | Graceful Degradation | Fallback strategies, partial functionality | Multiple services |
| 24 | Job Scheduler | Distributed task scheduling, cron at scale | Redis, Postgres |
| 25 | Write-Ahead Log | Durability guarantees, crash recovery | File-based |

### Production Patterns (Weeks 26-30)

| Week | Lab | What You'll Learn | Infrastructure |
|------|-----|-------------------|----------------|
| 26 | Load Shedding (Zuul) | Netflix-style load shedding at the gateway | Gateway service |
| 27 | Change Data Capture | Database change streaming, event sourcing | Postgres, Debezium |
| 28 | Chaos Engineering | Fault injection, resilience testing | Multiple services |
| 29 | Saga Pattern | Distributed transactions, compensation | Multiple services |
| 30 | Failover Simulation | Primary-replica failover, leader election | Postgres replicas |

### Scaling Patterns (Weeks 31-35)

| Week | Lab | What You'll Learn | Infrastructure |
|------|-----|-------------------|----------------|
| 31 | Rate Limiter Service | Building a rate limiting microservice | Redis |
| 32 | HyperLogLog | Cardinality estimation, counting uniques | Redis |
| 33 | Webhook Delivery | Reliable delivery, retries, circuit breakers | Multiple receivers |
| 34 | Distributed Counter | Netflix-style distributed counting | Redis |
| 35 | Load Shedding | Prioritized request handling, overload protection | Base stack |

### Concurrency & Consistency (Weeks 36-40)

| Week | Lab | What You'll Learn | Infrastructure |
|------|-----|-------------------|----------------|
| 36 | Ticket Booking | Handling concurrent seat reservations | Postgres |
| 37 | Distributed Locks | Redis locks, Redlock algorithm | Redis |
| 38 | Exactly-Once Processing | Kafka exactly-once semantics | Kafka |
| 39 | Hot Partition Detection | Identifying and mitigating hot spots | Multiple partitions |
| 40 | Cold Start Problems | Warming caches, connection pools | Redis, Postgres |

### Distributed Data (Weeks 41-45)

| Week | Lab | What You'll Learn | Infrastructure |
|------|-----|-------------------|----------------|
| 41 | Two-Phase Commit | Distributed transaction coordination | Multiple services |
| 42 | Write-Behind Cache | Async cache writes, eventual persistence | Redis, Postgres |
| 43 | Consistent Hash Ring | Data partitioning, minimal reshuffling | Multiple nodes |
| 44 | Federated Graph Search | Netflix-style DGS federation | GraphQL services |
| 45 | Kafka Backpressure | Consumer lag, flow control | Kafka |

### Advanced Topics (Weeks 46-52)

| Week | Lab | What You'll Learn | Infrastructure |
|------|-----|-------------------|----------------|
| 46 | Quorum Reads/Writes | Configurable consistency levels | Multiple replicas |
| 47 | Bulkhead Isolation | Thread pool isolation, resource limits | Multiple services |
| 48 | Unique ID Generator | Snowflake IDs, distributed ID generation | Redis or Postgres |
| 49 | CRDTs | Conflict-free replicated data types | Multiple replicas |
| 50 | URL Shortener Part 2 | Scaling the URL shortener | Redis, Postgres, Kafka |
| 51 | Timeout Budgets | Deadline propagation, cascading timeouts | Multiple services |
| 52 | Speculative Execution | Hedged requests, latency reduction | Multiple backends |

---

## Infrastructure Summary

Labs maximize infrastructure reuse on top of the base observability stack (OTEL Collector, Prometheus, Grafana, Jaeger):

| Infrastructure | Used By Labs (Weeks) |
|----------------|----------------------|
| Redis | 2-3, 5, 8, 11-12, 15, 20, 22, 24, 31-32, 34, 37, 40, 42, 48 |
| Postgres | 3-5, 8, 13, 21, 24, 27, 30, 36, 40, 42, 48, 50 |
| Kafka | 18, 38, 45, 50 |
| RabbitMQ | 17-18 |
| Multiple Services | 9-10, 19, 23, 28-29, 33, 41, 43, 46-47, 49, 51-52 |

---

## Learning Paths

### Path A: Caching Deep Dive (8 labs)
Week 2 → 3 → 8 → 12 → 15 → 32 → 40 → 42

*Outcome: Master all caching patterns, failure modes, and optimization techniques*

### Path B: Resilience Engineering (9 labs)
Week 6 → 7 → 9 → 16 → 23 → 26 → 28 → 35 → 47

*Outcome: Understand how systems fail and how to build resilient services*

### Path C: Build Real Services (7 labs)
Week 5 → 13 → 31 → 33 → 36 → 48 → 50

*Outcome: Build production-quality services with proper error handling and concurrency*

### Path D: Event-Driven & Streaming (6 labs)
Week 17 → 18 → 27 → 38 → 45 → 50

*Outcome: Master async patterns from queues to CDC to exactly-once processing*

### Path E: Distributed Consistency (7 labs)
Week 14 → 21 → 37 → 41 → 43 → 46 → 49

*Outcome: Deep understanding of consistency models and distributed coordination*

### Path F: Operations & Deployment (5 labs)
Week 10 → 19 → 28 → 30 → 51

*Outcome: Master deployment strategies, chaos testing, and operational resilience*

---

## Suggested Starting Sequence (First 12)

Based on beginner-friendliness, infrastructure simplicity, and building momentum:

1. **Week 1: Finding Slow Service** — Learn to read traces, understand latency
2. **Week 2: Cache Dies** — See what happens when dependencies fail
3. **Week 6: Retry Storms** — Eye-opening failure mode
4. **Week 7: Circuit Breakers** — Essential resilience pattern
5. **Week 5: URL Shortener** — First "real" system design
6. **Week 11: Rate Limiting** — Practical, standalone protection
7. **Week 3: Write-Through Cache** — Foundational caching pattern
8. **Week 12: Thundering Herd** — Dramatic failure, uses cache knowledge
9. **Week 13: Idempotency Keys** — Production-critical pattern
10. **Week 9: Cascading Failures** — See how one service can take down everything
11. **Week 23: Graceful Degradation** — Build systems that fail gracefully
12. **Week 29: Saga Pattern** — Distributed transactions without 2PC
