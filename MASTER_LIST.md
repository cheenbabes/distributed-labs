# Distributed Lab Master List

52 hands-on distributed systems labs, scored and ranked for YouTube video production.

## Scoring Criteria

Each topic scored 1-5 on:
- **Feasibility**: Can we build it in docker compose?
- **Scope**: Fits 15-20 min focused video?
- **Visual**: Something satisfying to observe in Grafana/Jaeger?
- **Hook**: Will the title make someone click?
- **Practical**: Will viewers use this at work?

**Total: 25 points max**

---

## Complete Lab List (Ordered by Week)

| Week | Lab Directory | Topic | Score | Category |
|------|---------------|-------|-------|----------|
| 1 | week-01-finding-slow-service | Finding the Slow Service | 25 | Debugging |
| 2 | week-02-cache-dies | What Happens When Your Cache Dies | 24 | Breaking Things |
| 3 | week-03-write-through-cache | Write-Through Cache Pattern | 22 | Foundation |
| 4 | week-04-connection-pool-exhaustion | Connection Pool Exhaustion | 25 | Debugging |
| 5 | week-05-url-shortener | URL Shortener Part 1 | 22 | Real System |
| 6 | week-06-retry-storms | Retry Storms | 24 | Breaking Things |
| 7 | week-07-circuit-breakers | Circuit Breakers | 24 | Resilience |
| 8 | week-08-cache-aside | Cache-Aside Pattern | 22 | Foundation |
| 9 | week-09-cascading-failures | Cascading Failures | 24 | Breaking Things |
| 10 | week-10-blue-green-deployments | Blue-Green Deployments | 23 | Ops |
| 11 | week-11-rate-limiting | Rate Limiting Fundamentals | 22 | Foundation |
| 12 | week-12-thundering-herd | Thundering Herd | 24 | Breaking Things |
| 13 | week-13-idempotency-keys | Stripe Idempotency Keys | 23 | Real System |
| 14 | week-14-eventual-consistency | Eventual Consistency | 25 | DDIA |
| 15 | week-15-leaderboard-system | Leaderboard System | 23 | Real System |
| 16 | week-16-memory-leak | Memory Leak Investigation | 24 | Debugging |
| 17 | week-17-dead-letter-queues | Dead Letter Queues | 23 | Streaming |
| 18 | week-18-backpressure | Backpressure | 24 | Resilience |
| 19 | week-19-canary-releases | Canary Releases | 23 | Ops |
| 20 | week-20-distributed-rate-limiter | Distributed Rate Limiter | 23 | Real System |
| 21 | week-21-reproducing-race-conditions | Reproducing Race Conditions | 23 | Debugging |
| 22 | week-22-bloom-filters | Bloom Filters | 23 | Data Structures |
| 23 | week-23-graceful-degradation | Graceful Degradation | 23 | Resilience |
| 24 | week-24-job-scheduler | Job Scheduler | 22 | Real System |
| 25 | week-25-write-ahead-log | Write-Ahead Log | 23 | Storage |
| 26 | week-26-zuul-load-shedding | Zuul Load Shedding | 22 | Netflix |
| 27 | week-27-cdc | Change Data Capture (CDC) | 23 | Streaming |
| 28 | week-28-chaos-engineering | Chaos Engineering | 23 | Resilience |
| 29 | week-29-saga-pattern | Saga Pattern | 22 | DDIA |
| 30 | week-30-failover-simulation | Failover Simulation | 23 | Multi-region |
| 31 | week-31-rate-limiter-service | Rate Limiter Service | 23 | Real System |
| 32 | week-32-hyperloglog | HyperLogLog | 22 | Data Structures |
| 33 | week-33-webhook-delivery | Webhook Delivery System | 22 | Real System |
| 34 | week-34-distributed-counter | Netflix Distributed Counter | 20 | Netflix |
| 35 | week-35-load-shedding | Load Shedding | 23 | Resilience |
| 36 | week-36-ticket-booking-concurrency | Ticket Booking Concurrency | 22 | Real System |
| 37 | week-37-distributed-locks | Distributed Locks | 22 | Real System |
| 38 | week-38-exactly-once-processing | Exactly-Once Processing | 22 | Streaming |
| 39 | week-39-hot-partition-detection | Hot Partition Detection | 22 | Debugging |
| 40 | week-40-cold-start-problems | Cold Start Problems | 21 | Breaking Things |
| 41 | week-41-two-phase-commit | Two-Phase Commit | 21 | DDIA |
| 42 | week-42-write-behind-cache | Write-Behind Cache | 21 | Foundation |
| 43 | week-43-consistent-hash-ring | Consistent Hash Ring | 21 | Data Structures |
| 44 | week-44-federated-graph-search | Netflix Federated Graph Search | 20 | Netflix |
| 45 | week-45-kafka-backpressure | Kafka Backpressure | 22 | Streaming |
| 46 | week-46-quorum-reads-writes | Quorum Reads/Writes | 21 | DDIA |
| 47 | week-47-bulkhead-isolation | Bulkhead Isolation | 21 | Resilience |
| 48 | week-48-unique-id-generator | Unique ID Generator | 21 | Real System |
| 49 | week-49-crdts | CRDTs | 21 | Advanced |
| 50 | week-50-url-shortener-part2 | URL Shortener Part 2 | 21 | Real System |
| 51 | week-51-timeout-budgets | Timeout Budgets | 21 | Resilience |
| 52 | week-52-speculative-execution | Speculative Execution | 21 | Advanced |

---

## Labs by Score Tier

### Tier 1: Bangers (24-25 points)

| Week | Topic | Category |
|------|-------|----------|
| 1 | Finding the Slow Service | Debugging |
| 4 | Connection Pool Exhaustion | Debugging |
| 14 | Eventual Consistency | DDIA |
| 16 | Memory Leak Investigation | Debugging |
| 7 | Circuit Breakers | Resilience |
| 18 | Backpressure | Resilience |
| 2 | What Happens When Your Cache Dies | Breaking Things |
| 6 | Retry Storms | Breaking Things |
| 9 | Cascading Failures | Breaking Things |
| 12 | Thundering Herd | Breaking Things |

### Tier 2: Strong (22-23 points)

| Week | Topic | Category |
|------|-------|----------|
| 25 | Write-Ahead Log | Storage |
| 22 | Bloom Filters | Data Structures |
| 10 | Blue-Green Deployments | Ops |
| 19 | Canary Releases | Ops |
| 15 | Leaderboard System | Real System |
| 20 | Distributed Rate Limiter | Real System |
| 23 | Graceful Degradation | Resilience |
| 28 | Chaos Engineering | Resilience |
| 35 | Load Shedding | Resilience |
| 27 | Change Data Capture (CDC) | Streaming |
| 17 | Dead Letter Queues | Streaming |
| 30 | Failover Simulation | Multi-region |
| 13 | Idempotency Keys | Real System |
| 31 | Rate Limiter Service | Real System |
| 21 | Reproducing Race Conditions | Debugging |
| 24 | Job Scheduler | Real System |
| 33 | Webhook Delivery System | Real System |
| 37 | Distributed Locks | Real System |
| 29 | Saga Pattern | DDIA |
| 32 | HyperLogLog | Data Structures |
| 3 | Write-Through Cache | Foundation |
| 8 | Cache-Aside Pattern | Foundation |
| 11 | Rate Limiting Fundamentals | Foundation |
| 36 | Ticket Booking Concurrency | Real System |
| 5 | URL Shortener Part 1 | Real System |
| 39 | Hot Partition Detection | Debugging |
| 26 | Zuul Load Shedding | Netflix |
| 38 | Exactly-Once Processing | Streaming |
| 45 | Kafka Backpressure | Streaming |

### Tier 3: Solid (20-21 points)

| Week | Topic | Category |
|------|-------|----------|
| 41 | Two-Phase Commit | DDIA |
| 46 | Quorum Reads/Writes | DDIA |
| 42 | Write-Behind Cache | Foundation |
| 47 | Bulkhead Isolation | Resilience |
| 51 | Timeout Budgets | Resilience |
| 43 | Consistent Hash Ring | Data Structures |
| 49 | CRDTs | Advanced |
| 52 | Speculative Execution | Advanced |
| 50 | URL Shortener Part 2 | Real System |
| 48 | Unique ID Generator | Real System |
| 40 | Cold Start Problems | Breaking Things |
| 34 | Netflix Distributed Counter | Netflix |
| 44 | Netflix Federated Graph Search | Netflix |

---

## Category Distribution

| Category | Count | Weeks |
|----------|:-----:|-------|
| Breaking Things | 5 | 2, 6, 9, 12, 40 |
| Debugging | 5 | 1, 4, 16, 21, 39 |
| Resilience | 8 | 7, 18, 23, 28, 35, 47, 51 |
| Real Systems | 13 | 5, 13, 15, 20, 24, 31, 33, 36, 37, 48, 50 |
| Foundation | 4 | 3, 8, 11, 42 |
| DDIA/Theory | 4 | 14, 29, 41, 46 |
| Data Structures | 3 | 22, 32, 43 |
| Streaming | 5 | 17, 27, 38, 45 |
| Ops/Deployment | 2 | 10, 19 |
| Netflix | 3 | 26, 34, 44 |
| Advanced | 2 | 49, 52 |
| Storage | 1 | 25 |
| Multi-region | 1 | 30 |

---

## Infrastructure Requirements

| Infrastructure | Used By Weeks |
|----------------|---------------|
| Redis | 2-3, 5, 8, 11-12, 15, 20, 22, 24, 31-32, 34, 37, 40, 42, 48 |
| Postgres | 3-5, 8, 13, 21, 24, 27, 30, 36, 40, 42, 48, 50 |
| Kafka | 18, 27, 38, 45, 50 |
| RabbitMQ | 17-18 |
| Multiple service instances | 9-10, 14, 19, 23, 28-30, 33, 41, 43-44, 46-47, 49, 51-52 |

---

## Status

All 52 labs have been built with:
- Full Docker Compose configuration
- README.md with exercises
- DEMO_RUNBOOK.md with Runme-compatible commands
- Python services with OpenTelemetry instrumentation
- Prometheus metrics and Grafana dashboards
- k6 load test scripts
