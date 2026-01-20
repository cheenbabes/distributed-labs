# Distributed Lab

52 hands-on labs for learning distributed systems patterns through practical exercises. Each lab runs locally with Docker Compose and includes full observability (OpenTelemetry traces, Prometheus metrics, Grafana dashboards, Jaeger).

## Quick Start

Each lab is self-contained in the `labs/` directory:

```bash
# Go to a specific lab
cd labs/week-07-circuit-breakers

# Start the lab
docker compose up --build -d

# Follow the README.md for exercises
# Use DEMO_RUNBOOK.md for guided demos (compatible with Runme VS Code extension)

# Clean up
docker compose down -v
```

## Lab Curriculum (52 Weeks)

### Foundation & Observability (Weeks 1-5)
| Week | Lab | What You'll Learn |
|------|-----|-------------------|
| 1 | [Finding Slow Service](labs/week-01-finding-slow-service) | Distributed tracing basics, finding latency bottlenecks |
| 2 | [Cache Dies](labs/week-02-cache-dies) | What happens when your cache fails, cache dependency patterns |
| 3 | [Write-Through Cache](labs/week-03-write-through-cache) | Write-through vs write-behind caching strategies |
| 4 | [Connection Pool Exhaustion](labs/week-04-connection-pool-exhaustion) | Database connection management, pool sizing |
| 5 | [URL Shortener](labs/week-05-url-shortener) | Basic distributed system design, hashing |

### Reliability Patterns (Weeks 6-10)
| Week | Lab | What You'll Learn |
|------|-----|-------------------|
| 6 | [Retry Storms](labs/week-06-retry-storms) | Why naive retries fail, exponential backoff |
| 7 | [Circuit Breakers](labs/week-07-circuit-breakers) | Fail-fast pattern, circuit breaker states |
| 8 | [Cache-Aside Pattern](labs/week-08-cache-aside) | Lazy loading cache, cache invalidation |
| 9 | [Cascading Failures](labs/week-09-cascading-failures) | Failure propagation, isolation strategies |
| 10 | [Blue-Green Deployments](labs/week-10-blue-green-deployments) | Zero-downtime deployments, traffic switching |

### Rate Limiting & Load Management (Weeks 11-15)
| Week | Lab | What You'll Learn |
|------|-----|-------------------|
| 11 | [Rate Limiting](labs/week-11-rate-limiting) | Token bucket, sliding window algorithms |
| 12 | [Thundering Herd](labs/week-12-thundering-herd) | Cache stampede prevention, request coalescing |
| 13 | [Idempotency Keys](labs/week-13-idempotency-keys) | Safe retries, exactly-once semantics |
| 14 | [Eventual Consistency](labs/week-14-eventual-consistency) | CAP theorem in practice, consistency models |
| 15 | [Leaderboard System](labs/week-15-leaderboard-system) | Redis sorted sets, real-time rankings |

### Debugging & Queues (Weeks 16-20)
| Week | Lab | What You'll Learn |
|------|-----|-------------------|
| 16 | [Memory Leak](labs/week-16-memory-leak) | Detecting and diagnosing memory leaks |
| 17 | [Dead Letter Queues](labs/week-17-dead-letter-queues) | Failed message handling, poison pills |
| 18 | [Backpressure](labs/week-18-backpressure) | Flow control, producer-consumer balance |
| 19 | [Canary Releases](labs/week-19-canary-releases) | Progressive rollouts, traffic splitting |
| 20 | [Distributed Rate Limiter](labs/week-20-distributed-rate-limiter) | Coordinated rate limiting across nodes |

### Advanced Patterns (Weeks 21-25)
| Week | Lab | What You'll Learn |
|------|-----|-------------------|
| 21 | [Race Conditions](labs/week-21-reproducing-race-conditions) | Identifying and reproducing race conditions |
| 22 | [Bloom Filters](labs/week-22-bloom-filters) | Probabilistic data structures, membership testing |
| 23 | [Graceful Degradation](labs/week-23-graceful-degradation) | Fallback strategies, partial functionality |
| 24 | [Job Scheduler](labs/week-24-job-scheduler) | Distributed task scheduling, cron at scale |
| 25 | [Write-Ahead Log](labs/week-25-write-ahead-log) | Durability guarantees, crash recovery |

### Production Patterns (Weeks 26-30)
| Week | Lab | What You'll Learn |
|------|-----|-------------------|
| 26 | [Load Shedding (Zuul)](labs/week-26-zuul-load-shedding) | Netflix-style load shedding at the gateway |
| 27 | [Change Data Capture](labs/week-27-cdc) | Database change streaming, event sourcing |
| 28 | [Chaos Engineering](labs/week-28-chaos-engineering) | Fault injection, resilience testing |
| 29 | [Saga Pattern](labs/week-29-saga-pattern) | Distributed transactions, compensation |
| 30 | [Failover Simulation](labs/week-30-failover-simulation) | Primary-replica failover, leader election |

### Scaling Patterns (Weeks 31-35)
| Week | Lab | What You'll Learn |
|------|-----|-------------------|
| 31 | [Rate Limiter Service](labs/week-31-rate-limiter-service) | Building a rate limiting microservice |
| 32 | [HyperLogLog](labs/week-32-hyperloglog) | Cardinality estimation, counting uniques |
| 33 | [Webhook Delivery](labs/week-33-webhook-delivery) | Reliable delivery, retries, circuit breakers |
| 34 | [Distributed Counter](labs/week-34-distributed-counter) | Netflix-style distributed counting |
| 35 | [Load Shedding](labs/week-35-load-shedding) | Prioritized request handling, overload protection |

### Concurrency & Consistency (Weeks 36-40)
| Week | Lab | What You'll Learn |
|------|-----|-------------------|
| 36 | [Ticket Booking](labs/week-36-ticket-booking-concurrency) | Handling concurrent seat reservations |
| 37 | [Distributed Locks](labs/week-37-distributed-locks) | Redis locks, Redlock algorithm |
| 38 | [Exactly-Once Processing](labs/week-38-exactly-once-processing) | Kafka exactly-once semantics |
| 39 | [Hot Partition Detection](labs/week-39-hot-partition-detection) | Identifying and mitigating hot spots |
| 40 | [Cold Start Problems](labs/week-40-cold-start-problems) | Warming caches, connection pools |

### Distributed Data (Weeks 41-45)
| Week | Lab | What You'll Learn |
|------|-----|-------------------|
| 41 | [Two-Phase Commit](labs/week-41-two-phase-commit) | Distributed transaction coordination |
| 42 | [Write-Behind Cache](labs/week-42-write-behind-cache) | Async cache writes, eventual persistence |
| 43 | [Consistent Hash Ring](labs/week-43-consistent-hash-ring) | Data partitioning, minimal reshuffling |
| 44 | [Federated Graph Search](labs/week-44-federated-graph-search) | Netflix-style DGS federation |
| 45 | [Kafka Backpressure](labs/week-45-kafka-backpressure) | Consumer lag, flow control |

### Advanced Topics (Weeks 46-52)
| Week | Lab | What You'll Learn |
|------|-----|-------------------|
| 46 | [Quorum Reads/Writes](labs/week-46-quorum-reads-writes) | Configurable consistency levels |
| 47 | [Bulkhead Isolation](labs/week-47-bulkhead-isolation) | Thread pool isolation, resource limits |
| 48 | [Unique ID Generator](labs/week-48-unique-id-generator) | Snowflake IDs, distributed ID generation |
| 49 | [CRDTs](labs/week-49-crdts) | Conflict-free replicated data types |
| 50 | [URL Shortener Part 2](labs/week-50-url-shortener-part2) | Scaling the URL shortener |
| 51 | [Timeout Budgets](labs/week-51-timeout-budgets) | Deadline propagation, cascading timeouts |
| 52 | [Speculative Execution](labs/week-52-speculative-execution) | Hedged requests, latency reduction |

## Observability Stack

Every lab includes full observability infrastructure:

| Service | URL | Purpose |
|---------|-----|---------|
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics queries |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- k6 (optional, for load testing)
- [Runme VS Code Extension](https://marketplace.visualstudio.com/items?itemName=stateful.runme) (optional, for running DEMO_RUNBOOK.md)

## Base Observability Lab

The root directory contains a base observability stack that can be used as a foundation for your own experiments:

```bash
# Run just the observability infrastructure
docker compose -f docker-compose.base.yml up

# Run the example Echo Chain distributed system
docker compose up --build
```

### Architecture

```
                                    ┌─────────────────┐
                                    │   k6 (load)     │
                                    └────────┬────────┘
                                             │
                                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Docker Network                           │
│                                                                 │
│  ┌──────────┐      ┌──────────┐      ┌──────────┐               │
│  │ Gateway  │─────▶│ Service  │─────▶│ Service  │               │
│  │  :8000   │      │    A     │      │    B     │               │
│  └──────────┘      │  :8001   │      │  :8002   │               │
│       │            └──────────┘      └──────────┘               │
│       │                 │                 │                     │
│       └─────────────────┴─────────────────┘                     │
│                         │                                       │
│                         ▼                                       │
│            ┌─────────────────────────┐                          │
│            │  OpenTelemetry Collector │                         │
│            │         :4317            │                         │
│            └─────────────────────────┘                          │
│                    │         │                                  │
│          ┌─────────┘         └──────────┐                       │
│          ▼                              ▼                       │
│   ┌────────────┐                ┌─────────────┐                 │
│   │   Jaeger   │                │  Prometheus │                 │
│   │   :16686   │                │    :9090    │                 │
│   └────────────┘                └─────────────┘                 │
│                                        │                        │
│   ┌────────────┐                       │                        │
│   │    Loki    │                       │                        │
│   │   :3100    │                       │                        │
│   └────────────┘                       │                        │
│          │                             │                        │
│          └──────────┬──────────────────┘                        │
│                     ▼                                           │
│              ┌────────────┐                                     │
│              │  Grafana   │                                     │
│              │   :3001    │                                     │
│              └────────────┘                                     │
└─────────────────────────────────────────────────────────────────┘
```

## Contributing

Each lab follows a consistent structure:

```
labs/week-XX-topic-name/
├── docker-compose.yml      # Full lab infrastructure
├── README.md               # Exercises and learning objectives
├── DEMO_RUNBOOK.md         # Runme-compatible demo commands
├── services/               # Python services with OTEL instrumentation
├── infra/                  # Config files (Prometheus, Grafana, OTEL)
└── loadtest/               # k6 load test scripts
```

## License

MIT
