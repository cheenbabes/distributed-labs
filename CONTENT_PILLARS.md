# Distributed Lab: Content Pillars & 52 Video Topics

## Content Pillars

### Pillar 1: Foundations (Building Blocks) — 25%

Core distributed systems patterns with simple, runnable labs. Not "what is a cache" basic, but "here's a write-through cache in front of a database you can trace requests through." The fundamentals that underpin everything else.

### Pillar 2: Breaking Things (Failure Modes) — 20%

What happens when systems fail. Cascading failures, retry storms, network partitions, resource exhaustion. This is the chaos engineering pillar—show people how things break so they understand why resilience patterns exist.

### Pillar 3: Resilience & Operations — 25%

How to build systems that stay up and deploy them safely. Circuit breakers, backpressure, load shedding, blue-green deployments, canary releases. The patterns that keep production running.

### Pillar 4: Real-World Systems (System Design) — 20%

Building simplified versions of real systems—URL shorteners, leaderboards, job schedulers, ticket booking. These are the classic interview patterns but made hands-on with actual running code.

### Pillar 5: Tech Blog Deep Dives (Production at Scale) — 10%

Taking Netflix and other tech blog posts and building simplified, runnable versions. Distributed counters, federated graph search, load shedding. This is the unique authority angle. Netflix-heavy.

---

## 52 Video Topics

### Pillar 1: Foundations (13 topics)

| Week | Topic | Difficulty | Early |
|:----:|-------|:----------:|:-----:|
| 1 | Finding the slow service: distributed tracing basics | Beginner | ✓ |
| 3 | Write-through cache: Redis in front of Postgres | Beginner | ✓ |
| 8 | Cache-aside pattern: when to use it | Beginner | ✓ |
| 42 | Write-behind (write-back) cache pattern | Beginner | |
| 11 | Rate limiting: token bucket and sliding window | Beginner | ✓ |
| 14 | Eventual consistency: see stale reads happen | Intermediate | |
| 22 | Bloom filters: probabilistic membership testing | Intermediate | |
| 25 | Write-ahead log: the foundation of durability | Intermediate | |
| 32 | HyperLogLog: count unique items with 12KB | Intermediate | |
| 43 | Consistent hash ring: data distribution basics | Intermediate | |
| 46 | Quorum reads/writes: N=3, W=2, R=2 in action | Intermediate | |
| 49 | CRDTs: conflict-free replicated data types | Advanced | |
| 41 | Two-phase commit: and why nobody uses it | Advanced | |

---

### Pillar 2: Breaking Things (10 topics)

| Week | Topic | Difficulty | Early |
|:----:|-------|:----------:|:-----:|
| 2 | What happens when your cache dies | Beginner | ✓ |
| 4 | Connection pool exhaustion: the 2am mystery | Intermediate | ✓ |
| 6 | Retry storms: when retries make everything worse | Intermediate | ✓ |
| 9 | Cascading failures: one slow service takes down everything | Intermediate | |
| 12 | Thundering herd: cache stampede in action | Intermediate | |
| 16 | Memory leaks under load: watching a service degrade | Intermediate | |
| 21 | Reproducing race conditions: make bugs happen on demand | Intermediate | |
| 39 | Hot partition detection: find the overloaded shard | Intermediate | |
| 40 | Cold start problems: why your service is slow after deploy | Beginner | ✓ |
| 30 | Failover simulation: what happens when primary dies | Intermediate | |

---

### Pillar 3: Resilience & Operations (14 topics)

| Week | Topic | Difficulty | Early |
|:----:|-------|:----------:|:-----:|
| 7 | Circuit breakers: fail fast, recover gracefully | Intermediate | ✓ |
| 10 | Blue-green deployments: zero-downtime releases | Intermediate | |
| 18 | Backpressure: protect against overload | Intermediate | |
| 19 | Canary releases: gradual rollout with metrics | Intermediate | |
| 23 | Graceful degradation: survive partial failures | Intermediate | |
| 26 | Zuul-style load shedding: Netflix's API gateway pattern | Intermediate | |
| 28 | Chaos engineering: prove your resilience | Intermediate | |
| 35 | Load shedding: intentional rejection under stress | Intermediate | |
| 47 | Bulkhead isolation: separate thread pools per dependency | Intermediate | |
| 51 | Timeout budgets: propagate deadlines through call chains | Intermediate | |
| 52 | Speculative execution: hedge against slow responses | Advanced | |
| 17 | Dead letter queues: handling poison messages | Intermediate | |
| 27 | Change data capture (CDC): stream database changes | Intermediate | |
| 45 | Kafka backpressure: consumer lag and flow control | Intermediate | |

---

### Pillar 4: Real-World Systems (12 topics)

| Week | Topic | Difficulty | Early |
|:----:|-------|:----------:|:-----:|
| 5 | URL shortener Part 1: basic design and key generation | Beginner | ✓ |
| 50 | URL shortener Part 2: scaling with caching and analytics | Intermediate | |
| 13 | Stripe's idempotency keys: exactly-once payments | Intermediate | ✓ |
| 15 | Leaderboard system: real-time rankings with sorted sets | Intermediate | |
| 20 | Distributed rate limiter: coordinate across nodes | Intermediate | |
| 24 | Job scheduler: delayed execution, retries, priorities | Intermediate | |
| 31 | Rate limiter service: a full microservice | Intermediate | ✓ |
| 33 | Webhook delivery: reliable delivery with retries | Intermediate | |
| 36 | Ticket booking: handling concurrent reservations | Intermediate | |
| 37 | Distributed locks: Redis SETNX and its pitfalls | Intermediate | |
| 38 | Exactly-once processing: Kafka transactions | Intermediate | |
| 48 | Unique ID generator: Snowflake IDs explained | Intermediate | |

---

### Pillar 5: Tech Blog Deep Dives (3 topics)

| Week | Topic | Difficulty | Source | Early |
|:----:|-------|:----------:|:------:|:-----:|
| 29 | Saga pattern: distributed transactions without 2PC | Intermediate | General | |
| 34 | Netflix's distributed counter (simplified) | Intermediate | Netflix | |
| 44 | Netflix's federated graph search: making GraphQL searchable | Advanced | Netflix | ✓ |

---

## Topic Distribution

| Pillar | Count | Percentage |
|--------|:-----:|:----------:|
| Foundations | 13 | 25% |
| Breaking Things | 10 | 19% |
| Resilience & Operations | 14 | 27% |
| Real-World Systems | 12 | 23% |
| Tech Blog Deep Dives | 3 | 6% |
| **Total** | **52** | **100%** |

---

## Recommended Starting Sequence (First 12 Videos)

Based on beginner-friendliness, visual appeal, and building production skills:

| Order | Week | Topic | Pillar |
|:-----:|:----:|-------|--------|
| 1 | 1 | Finding the slow service | Foundations |
| 2 | 2 | What happens when your cache dies | Breaking Things |
| 3 | 6 | Retry storms: when retries make everything worse | Breaking Things |
| 4 | 7 | Circuit breakers: fail fast, recover gracefully | Resilience |
| 5 | 5 | URL shortener Part 1 | Real-World Systems |
| 6 | 11 | Rate limiting: token bucket and sliding window | Foundations |
| 7 | 3 | Write-through cache: Redis in front of Postgres | Foundations |
| 8 | 12 | Thundering herd: cache stampede in action | Breaking Things |
| 9 | 13 | Stripe's idempotency keys | Real-World Systems |
| 10 | 9 | Cascading failures | Breaking Things |
| 11 | 23 | Graceful degradation | Resilience |
| 12 | 29 | Saga pattern | Tech Blog Deep Dives |

---

## What Makes This Different

**Every lab is runnable.** Not diagrams. Not pseudocode. Docker Compose up and you're watching it happen.

**Full observability built in.** Every lab has Jaeger traces, Prometheus metrics, and Grafana dashboards. You SEE the distributed system behavior, not just read about it.

**Break things on purpose.** Half the learning is watching systems fail. Kill a cache, exhaust a connection pool, trigger a retry storm—then understand why the fix works.

**Netflix insider perspective.** Real patterns from production systems at scale, simplified to run on a laptop.

---

## The Voice

Someone who questions assumptions and teaches you to do the same—because that's how you move from following patterns to truly understanding systems. When people think of me, they should think: "He actually shows you how this stuff works, you can run it yourself, and he makes it okay to not know everything yet."
