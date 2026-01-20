# Release Schedule

50-week release order designed for variety, narrative arcs, and sustained engagement.

## Design Principles

1. **Start strong** - Tier 1 bangers in weeks 1-4 to build momentum
2. **Problem → Solution arcs** - Show something break, then fix it
3. **Category rotation** - Never two videos from same category back-to-back
4. **Dependency ordering** - Foundations before advanced topics
5. **Save bangers** - Don't blow all Tier 1 in the first month
6. **Netflix moments** - Space out insider content for credibility spikes

---

## Quarter 1: Weeks 1-13 (Foundation + Hooks)

Hook viewers with dramatic failures, establish core concepts.

| Week | # | Topic | Category | Tier | Arc |
|:----:|:-:|-------|----------|:----:|-----|
| 1 | 1 | Finding the slow service | Debugging | 1 | Opener - shows the whole observability stack |
| 2 | 7 | What happens when your cache dies | Breaking | 1 | Drama - sets up why caching matters |
| 3 | 31 | Write-through cache | Foundation | 3 | Solution - first caching pattern |
| 4 | 2 | Connection pool exhaustion | Debugging | 1 | Drama - everyone's hit this at 2am |
| 5 | 35 | URL shortener Part 1 | Real System | 3 | Build - first real system |
| 6 | 8 | Retry storms | Breaking | 1 | Drama - retries make it worse |
| 7 | 5 | Circuit breakers with dashboard | Resilience | 1 | Solution - fix the retry problem |
| 8 | 32 | Cache-aside pattern | Foundation | 3 | Foundation - most common pattern |
| 9 | 9 | Cascading failures | Breaking | 1 | Drama - domino effect |
| 10 | 13 | Blue-green deployments | Ops | 2 | Practical - deploy without fear |
| 11 | 33 | Rate limiting fundamentals | Foundation | 3 | Foundation - enables later videos |
| 12 | 10 | Thundering herd | Breaking | 1 | Drama - cache stampede |
| 13 | 23 | Stripe idempotency keys | Real System | 2 | Build - production pattern |

**Q1 Stats:** 5 Tier 1, 2 Tier 2, 6 Tier 3 | 4 Breaking, 2 Debugging, 2 Real System, 3 Foundation, 1 Resilience, 1 Ops

---

## Quarter 2: Weeks 14-26 (Depth + Systems)

Build real systems, introduce streaming and advanced debugging.

| Week | # | Topic | Category | Tier | Arc |
|:----:|:-:|-------|----------|:----:|-----|
| 14 | 3 | Eventual consistency in action | DDIA | 1 | Eye-opener - see what books describe |
| 15 | 15 | Leaderboard system | Real System | 2 | Build - interview favorite |
| 16 | 4 | Memory leak investigation | Debugging | 1 | Drama - watch a service die slowly |
| 17 | 21 | Dead letter queues | Streaming | 2 | Foundation - intro to queues |
| 18 | 6 | Backpressure | Resilience | 1 | Drama + Solution - OOM then fix |
| 19 | 14 | Canary releases | Ops | 2 | Practical - follows blue-green |
| 20 | 16 | Distributed rate limiter | Real System | 2 | Build - scales week 11 concept |
| 21 | 25 | Reproducing race conditions | Debugging | 2 | Skill - make bugs happen on demand |
| 22 | 12 | Bloom filters | Data Structures | 2 | Deep dive - probabilistic DS |
| 23 | 17 | Graceful degradation | Resilience | 2 | Pattern - survive partial failures |
| 24 | 26 | Job scheduler | Real System | 2 | Build - every system needs this |
| 25 | 11 | Write-ahead log | Storage | 2 | Deep dive - demystify durability |
| 26 | 37 | Zuul load shedding | Netflix | 3 | Netflix moment #1 |

**Q2 Stats:** 3 Tier 1, 9 Tier 2, 1 Tier 3 | 3 Real System, 3 Debugging, 2 Resilience, 2 Ops, 1 DDIA, 1 Streaming, 1 Data Structures, 1 Storage, 1 Netflix

---

## Quarter 3: Weeks 27-39 (Advanced + Netflix)

Complex systems, streaming depth, more Netflix insider content.

| Week | # | Topic | Category | Tier | Arc |
|:----:|:-:|-------|----------|:----:|-----|
| 27 | 20 | Change data capture (CDC) | Streaming | 2 | Build - how systems stay in sync |
| 28 | 18 | Chaos engineering | Resilience | 2 | Fun - break things on purpose |
| 29 | 29 | Saga pattern | DDIA | 2 | Pattern - distributed transactions done right |
| 30 | 22 | Failover simulation | Multi-region | 2 | Drama - kill the primary |
| 31 | 24 | Rate limiter service | Real System | 2 | Build - full production service |
| 32 | 30 | HyperLogLog | Data Structures | 2 | Deep dive - count billions with 12KB |
| 33 | 27 | Webhook delivery system | Real System | 2 | Build - harder than you think |
| 34 | 51 | Netflix distributed counter | Netflix | - | Netflix moment #2 |
| 35 | 19 | Load shedding | Resilience | 2 | Pattern - reject work intentionally |
| 36 | 34 | Ticket booking concurrency | Real System | 3 | Build - race conditions in practice |
| 37 | 28 | Distributed locks | Real System | 2 | Build - Redlock controversy |
| 38 | 38 | Exactly-once processing | Streaming | 3 | Deep dive - the holy grail |
| 39 | 36 | Hot partition detection | Debugging | 3 | Skill - find the hot shard |

**Q3 Stats:** 0 Tier 1, 10 Tier 2, 3 Tier 3 | 4 Real System, 3 Resilience, 2 Streaming, 2 Data Structures, 1 DDIA, 1 Multi-region, 1 Debugging, 1 Netflix

---

## Quarter 4: Weeks 40-52 (Complete + Advanced)

Round out curriculum, advanced topics, strong finish.

| Week | # | Topic | Category | Tier | Arc |
|:----:|:-:|-------|----------|:----:|-----|
| 40 | 50 | Cold start problems | Breaking | 3 | Drama - why deploys are slow |
| 41 | 40 | Two-phase commit | DDIA | 3 | Deep dive - why 2PC is a trap |
| 42 | 42 | Write-behind cache | Foundation | 3 | Pattern - completes cache trilogy |
| 43 | 45 | Consistent hash ring | Data Structures | 3 | Visual - see key distribution |
| 44 | 52 | Netflix federated graph search | Netflix | - | Netflix moment #3 (big finish energy) |
| 45 | 39 | Backpressure in streams | Streaming | 3 | Deep dive - Kafka consumer lag |
| 46 | 41 | Quorum reads/writes | DDIA | 3 | Deep dive - N, R, W explained |
| 47 | 43 | Bulkhead isolation | Resilience | 3 | Pattern - isolate failures |
| 48 | 49 | Unique ID generator | Real System | 3 | Build - Snowflake IDs |
| 49 | 46 | CRDTs | Advanced | 3 | Mind-bender - self-merging data |
| 50 | 48 | URL shortener Part 2 | Real System | 3 | Finale - scale what we built |
| 51 | 44 | Timeout budgets | Resilience | 3 | Pattern - deadline propagation |
| 52 | 47 | Speculative execution | Advanced | 3 | Advanced - hedge your bets |

**Q4 Stats:** 0 Tier 1, 0 Tier 2, 13 Tier 3 | 3 Real System, 3 DDIA, 2 Resilience, 2 Streaming, 2 Data Structures, 2 Advanced, 1 Breaking, 1 Foundation, 1 Netflix

---

## Narrative Arcs

### Arc 1: "Caching Journey" (Weeks 2-3, 8, 12, 42)
- Week 2: Cache dies → disaster
- Week 3: Write-through cache → first solution
- Week 8: Cache-aside → common pattern
- Week 12: Thundering herd → cache failure mode
- Week 42: Write-behind → complete the trilogy

### Arc 2: "Retries and Resilience" (Weeks 6-7, 18)
- Week 6: Retry storms → retries make it worse
- Week 7: Circuit breakers → the solution
- Week 18: Backpressure → another solution

### Arc 3: "Rate Limiting Evolution" (Weeks 11, 20, 31)
- Week 11: Fundamentals → token bucket, sliding window
- Week 20: Distributed rate limiter → across servers
- Week 31: Rate limiter service → full production system

### Arc 4: "URL Shortener Saga" (Weeks 5, 50)
- Week 5: Part 1 → basic build
- Week 50: Part 2 → scale it up (bookend the year)

### Arc 5: "Netflix Insider" (Weeks 26, 34, 44)
- Week 26: Zuul load shedding
- Week 34: Distributed counter
- Week 44: Federated graph search (climax)

---

## Category Distribution by Quarter

| Category | Q1 | Q2 | Q3 | Q4 | Total |
|----------|:--:|:--:|:--:|:--:|:-----:|
| Breaking Things | 4 | 0 | 0 | 1 | 5 |
| Debugging | 2 | 3 | 1 | 0 | 6 |
| Resilience | 1 | 2 | 3 | 2 | 8 |
| Real System | 2 | 3 | 4 | 3 | 12 |
| Foundation | 3 | 0 | 0 | 1 | 4 |
| DDIA | 0 | 1 | 1 | 3 | 5 |
| Data Structures | 0 | 1 | 2 | 1 | 4 |
| Streaming | 0 | 1 | 2 | 1 | 4 |
| Ops | 1 | 2 | 0 | 0 | 3 |
| Netflix | 0 | 1 | 1 | 1 | 3 |
| Multi-region | 0 | 0 | 1 | 0 | 1 |
| Storage | 0 | 1 | 0 | 0 | 1 |
| Advanced | 0 | 0 | 0 | 2 | 2 |

---

## Tier Distribution by Quarter

| Tier | Q1 | Q2 | Q3 | Q4 | Total |
|------|:--:|:--:|:--:|:--:|:-----:|
| Tier 1 (Bangers) | 5 | 3 | 0 | 0 | 8 |
| Tier 2 (Strong) | 2 | 9 | 10 | 0 | 21 |
| Tier 3 (Solid) | 6 | 1 | 3 | 13 | 23 |

**Strategy:** Front-load Tier 1 for growth, Tier 2 for retention, Tier 3 for completeness.

---

## Infrastructure Progression

| Quarter | New Infrastructure Introduced |
|---------|-------------------------------|
| Q1 | Base observability, Redis, Postgres |
| Q2 | RabbitMQ/Kafka basics, multiple service instances |
| Q3 | Debezium, advanced Kafka patterns |
| Q4 | Multi-node clusters, advanced configurations |

---

## Quick Reference: Week → Topic

| Wk | Topic | Wk | Topic |
|:--:|-------|:--:|-------|
| 1 | Finding the slow service | 27 | Change data capture |
| 2 | Cache dies | 28 | Chaos engineering |
| 3 | Write-through cache | 29 | Saga pattern |
| 4 | Connection pool exhaustion | 30 | Failover simulation |
| 5 | URL shortener Pt 1 | 31 | Rate limiter service |
| 6 | Retry storms | 32 | HyperLogLog |
| 7 | Circuit breakers | 33 | Webhook delivery |
| 8 | Cache-aside | 34 | Netflix distributed counter |
| 9 | Cascading failures | 35 | Load shedding |
| 10 | Blue-green deployments | 36 | Ticket booking |
| 11 | Rate limiting fundamentals | 37 | Distributed locks |
| 12 | Thundering herd | 38 | Exactly-once processing |
| 13 | Stripe idempotency | 39 | Hot partition detection |
| 14 | Eventual consistency | 40 | Cold start problems |
| 15 | Leaderboard system | 41 | Two-phase commit |
| 16 | Memory leak investigation | 42 | Write-behind cache |
| 17 | Dead letter queues | 43 | Consistent hash ring |
| 18 | Backpressure | 44 | Netflix graph search |
| 19 | Canary releases | 45 | Backpressure in streams |
| 20 | Distributed rate limiter | 46 | Quorum reads/writes |
| 21 | Reproducing race conditions | 47 | Bulkhead isolation |
| 22 | Bloom filters | 48 | Unique ID generator |
| 23 | Graceful degradation | 49 | CRDTs |
| 24 | Job scheduler | 50 | URL shortener Pt 2 |
| 25 | Write-ahead log | 51 | Timeout budgets |
| 26 | Zuul load shedding | 52 | Speculative execution |
