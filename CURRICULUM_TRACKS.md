# Curriculum Tracks

Learning paths for focused skill development. Each track groups related labs in recommended order.

---

## Track 1: System Design Interview Prep

**For:** Engineers preparing for FAANG/startup interviews
**Duration:** 16 labs (~4 months if doing one per week)
**Outcome:** Confidently tackle classic system design questions with hands-on experience

| Order | Week | Lab | Why It's Here |
|:-----:|:----:|-----|---------------|
| 1 | 5 | URL Shortener Part 1 | The classic warm-up question |
| 2 | 11 | Rate Limiting | Asked in almost every interview |
| 3 | 3 | Write-Through Cache | First caching pattern to know |
| 4 | 8 | Cache-Aside Pattern | Most common caching pattern |
| 5 | 15 | Leaderboard System | Real-time rankings, sorted sets |
| 6 | 20 | Distributed Rate Limiter | Scaling rate limiting |
| 7 | 31 | Rate Limiter Service | Full production implementation |
| 8 | 48 | Unique ID Generator | Snowflake IDs, always asked |
| 9 | 24 | Job Scheduler | Background processing |
| 10 | 33 | Webhook Delivery | Reliable delivery patterns |
| 11 | 36 | Ticket Booking Concurrency | Handling race conditions |
| 12 | 43 | Consistent Hash Ring | Data distribution fundamentals |
| 13 | 13 | Idempotency Keys | Payment system patterns |
| 14 | 37 | Distributed Locks | Coordination primitives |
| 15 | 50 | URL Shortener Part 2 | Scaling and analytics |
| 16 | 29 | Saga Pattern | Distributed transactions |

**Bonus Labs:** Week 46 (Quorum Reads/Writes), Week 41 (Two-Phase Commit)

---

## Track 2: Resilience Engineering

**For:** Engineers building systems that need to stay up
**Duration:** 12 labs (~3 months)
**Outcome:** Build systems that gracefully handle failures

| Order | Week | Lab | Why It's Here |
|:-----:|:----:|-----|---------------|
| 1 | 9 | Cascading Failures | Understand the problem first |
| 2 | 6 | Retry Storms | Why naive retries fail |
| 3 | 7 | Circuit Breakers | First line of defense |
| 4 | 18 | Backpressure | Protect against overload |
| 5 | 23 | Graceful Degradation | Survive partial failures |
| 6 | 47 | Bulkhead Isolation | Isolate failure domains |
| 7 | 51 | Timeout Budgets | Propagate deadlines |
| 8 | 35 | Load Shedding | Intentional rejection |
| 9 | 26 | Zuul Load Shedding | Netflix's approach |
| 10 | 28 | Chaos Engineering | Prove your resilience |
| 11 | 52 | Speculative Execution | Hedge against slow responses |
| 12 | 30 | Failover Simulation | Disaster recovery |

**Prerequisite:** Basic distributed tracing knowledge (Week 1)

---

## Track 3: Debugging Distributed Systems

**For:** Engineers who get paged at 2am
**Duration:** 8 labs (~2 months)
**Outcome:** Quickly diagnose and fix production issues

| Order | Week | Lab | Why It's Here |
|:-----:|:----:|-----|---------------|
| 1 | 1 | Finding the Slow Service | Start with tracing basics |
| 2 | 4 | Connection Pool Exhaustion | Most common DB issue |
| 3 | 16 | Memory Leak | Watch degradation happen |
| 4 | 21 | Reproducing Race Conditions | Make bugs happen on demand |
| 5 | 39 | Hot Partition Detection | Find uneven load |
| 6 | 40 | Cold Start Problems | Post-deploy slowness |
| 7 | 45 | Kafka Backpressure | Diagnose consumer lag |
| 8 | 2 | Cache Dies | Recognize cache failures |

**Outcome Skills:**
- Read distributed traces effectively
- Identify resource exhaustion patterns
- Reproduce intermittent bugs
- Diagnose performance degradation

---

## Track 4: Caching Mastery

**For:** Engineers who want to deeply understand caching
**Duration:** 8 labs (~2 months)
**Outcome:** Implement and debug any caching pattern

| Order | Week | Lab | Why It's Here |
|:-----:|:----:|-----|---------------|
| 1 | 3 | Write-Through Cache | Simplest pattern, consistency guaranteed |
| 2 | 8 | Cache-Aside Pattern | Most common pattern |
| 3 | 42 | Write-Behind Cache | Performance vs durability tradeoff |
| 4 | 2 | Cache Dies | Failure mode #1 |
| 5 | 12 | Thundering Herd | Failure mode #2 |
| 6 | 22 | Bloom Filters | Optimize cache lookups |
| 7 | 14 | Eventual Consistency | Understand stale reads |
| 8 | 40 | Cold Start Problems | Cache warming strategies |

**Covers:** All three caching patterns, both major failure modes, optimization techniques

---

## Track 5: Event-Driven Architecture

**For:** Engineers building streaming/async systems
**Duration:** 8 labs (~2 months)
**Outcome:** Design and implement event-driven systems

| Order | Week | Lab | Why It's Here |
|:-----:|:----:|-----|---------------|
| 1 | 17 | Dead Letter Queues | Handle poison messages |
| 2 | 18 | Backpressure | Fundamental flow control |
| 3 | 27 | Change Data Capture (CDC) | Stream database changes |
| 4 | 45 | Kafka Backpressure | Kafka-specific patterns |
| 5 | 38 | Exactly-Once Processing | The holy grail |
| 6 | 24 | Job Scheduler | Delayed execution |
| 7 | 33 | Webhook Delivery | Outbound event delivery |
| 8 | 29 | Saga Pattern | Coordinate across services |

**Infrastructure:** RabbitMQ → Kafka progression

---

## Track 6: Breaking Things (Chaos Track)

**For:** Engineers who want to understand failure modes
**Duration:** 10 labs (~2.5 months)
**Outcome:** Predict how systems fail before they do

| Order | Week | Lab | Why It's Here |
|:-----:|:----:|-----|---------------|
| 1 | 2 | Cache Dies | Infrastructure failure |
| 2 | 4 | Connection Pool Exhaustion | Resource exhaustion |
| 3 | 6 | Retry Storms | Amplification failure |
| 4 | 9 | Cascading Failures | Propagation failure |
| 5 | 12 | Thundering Herd | Coordination failure |
| 6 | 16 | Memory Leak | Gradual degradation |
| 7 | 40 | Cold Start Problems | Initialization failure |
| 8 | 41 | Two-Phase Commit | Coordination deadlock |
| 9 | 30 | Failover Simulation | Recovery failure |
| 10 | 28 | Chaos Engineering | Break things on purpose |

**Pattern:** See the failure → understand why → then learn the fix

---

## Track 7: Production Operations

**For:** Engineers responsible for deployments and operations
**Duration:** 7 labs (~2 months)
**Outcome:** Deploy and operate services safely

| Order | Week | Lab | Why It's Here |
|:-----:|:----:|-----|---------------|
| 1 | 10 | Blue-Green Deployments | Zero-downtime deploys |
| 2 | 19 | Canary Releases | Gradual rollout |
| 3 | 40 | Cold Start Problems | Post-deploy issues |
| 4 | 30 | Failover Simulation | Disaster recovery |
| 5 | 28 | Chaos Engineering | Validate operations |
| 6 | 35 | Load Shedding | Handle traffic spikes |
| 7 | 26 | Zuul Load Shedding | Production patterns |

**Bonus:** Combine with Debugging track for full on-call readiness

---

## Track 8: DDIA Companion

**For:** Engineers reading "Designing Data-Intensive Applications"
**Duration:** 10 labs (~2.5 months)
**Outcome:** See DDIA concepts in action

| Order | Week | Lab | DDIA Chapter |
|:-----:|:----:|-----|--------------|
| 1 | 14 | Eventual Consistency | Ch 5: Replication |
| 2 | 46 | Quorum Reads/Writes | Ch 5: Replication |
| 3 | 30 | Failover Simulation | Ch 5: Replication |
| 4 | 43 | Consistent Hash Ring | Ch 6: Partitioning |
| 5 | 39 | Hot Partition Detection | Ch 6: Partitioning |
| 6 | 41 | Two-Phase Commit | Ch 7: Transactions |
| 7 | 29 | Saga Pattern | Ch 7: Transactions |
| 8 | 37 | Distributed Locks | Ch 8: Trouble with Distributed Systems |
| 9 | 25 | Write-Ahead Log | Ch 3: Storage and Retrieval |
| 10 | 49 | CRDTs | Ch 5: Replication (Conflict Resolution) |

**Reading Order:** Follow DDIA chapters, do corresponding lab

---

## Track 9: Data Structures for Scale

**For:** Engineers who want to understand what powers big systems
**Duration:** 5 labs (~1.5 months)
**Outcome:** Implement and use probabilistic and distributed data structures

| Order | Week | Lab | Why It's Here |
|:-----:|:----:|-----|---------------|
| 1 | 22 | Bloom Filters | Probabilistic membership |
| 2 | 32 | HyperLogLog | Probabilistic counting |
| 3 | 43 | Consistent Hash Ring | Distributed key mapping |
| 4 | 49 | CRDTs | Conflict-free replication |
| 5 | 48 | Unique ID Generator | Distributed sequences |

**Math Level:** Light - focus on intuition and implementation, not proofs

---

## Track 10: Netflix Engineering

**For:** Engineers interested in Netflix's approaches
**Duration:** 5 labs (~1.5 months)
**Outcome:** Understand patterns Netflix uses at scale

| Order | Week | Lab | Netflix Connection |
|:-----:|:----:|-----|-------------------|
| 1 | 7 | Circuit Breakers | Hystrix origins |
| 2 | 26 | Zuul Load Shedding | Netflix API gateway |
| 3 | 28 | Chaos Engineering | Chaos Monkey origins |
| 4 | 34 | Distributed Counter | Netflix internal system |
| 5 | 44 | Federated Graph Search | Netflix DGS insider deep dive |

**Credibility:** Insider knowledge on Week 34 and 44

---

## Track Overview Matrix

| Track | Labs | Duration | Difficulty | Best For |
|-------|:----:|:--------:|:----------:|----------|
| System Design Interview | 16 | 4 months | Mixed | Interview prep |
| Resilience Engineering | 12 | 3 months | Intermediate | Building reliable systems |
| Debugging | 8 | 2 months | Intermediate | On-call engineers |
| Caching Mastery | 8 | 2 months | Beginner→Intermediate | Backend engineers |
| Event-Driven | 8 | 2 months | Intermediate | Streaming systems |
| Breaking Things | 10 | 2.5 months | Beginner | Understanding failures |
| Production Ops | 7 | 2 months | Intermediate | DevOps/SRE |
| DDIA Companion | 10 | 2.5 months | Intermediate→Advanced | Book readers |
| Data Structures | 5 | 1.5 months | Intermediate | Algorithm enthusiasts |
| Netflix Engineering | 5 | 1.5 months | Mixed | Netflix curious |

---

## Suggested Track Combinations

### "Full Stack Distributed Systems" (6 months)
1. Caching Mastery (2 months)
2. Event-Driven Architecture (2 months)
3. Resilience Engineering (2 months)

### "Interview Ready" (4 months)
1. System Design Interview (4 months)
*Add Debugging track labs for senior roles*

### "Production Hero" (4 months)
1. Debugging (2 months)
2. Production Ops (2 months)

### "Theory to Practice" (5 months)
1. DDIA Companion (2.5 months)
2. Data Structures for Scale (1.5 months)
3. Breaking Things (selected labs)

---

## Lab Coverage by Track

All 52 labs are covered across tracks. Here's which tracks each lab appears in:

| Week | Lab | Tracks |
|:----:|-----|--------|
| 1 | Finding Slow Service | Debugging |
| 2 | Cache Dies | Caching, Breaking Things, Debugging |
| 3 | Write-Through Cache | System Design, Caching |
| 4 | Connection Pool Exhaustion | Debugging, Breaking Things |
| 5 | URL Shortener Part 1 | System Design |
| 6 | Retry Storms | Resilience, Breaking Things |
| 7 | Circuit Breakers | Resilience, Netflix |
| 8 | Cache-Aside Pattern | System Design, Caching |
| 9 | Cascading Failures | Resilience, Breaking Things |
| 10 | Blue-Green Deployments | Production Ops |
| 11 | Rate Limiting | System Design |
| 12 | Thundering Herd | Caching, Breaking Things |
| 13 | Idempotency Keys | System Design |
| 14 | Eventual Consistency | Caching, DDIA |
| 15 | Leaderboard System | System Design |
| 16 | Memory Leak | Debugging, Breaking Things |
| 17 | Dead Letter Queues | Event-Driven |
| 18 | Backpressure | Resilience, Event-Driven |
| 19 | Canary Releases | Production Ops |
| 20 | Distributed Rate Limiter | System Design |
| 21 | Reproducing Race Conditions | Debugging |
| 22 | Bloom Filters | Caching, Data Structures |
| 23 | Graceful Degradation | Resilience |
| 24 | Job Scheduler | System Design, Event-Driven |
| 25 | Write-Ahead Log | DDIA |
| 26 | Zuul Load Shedding | Resilience, Production Ops, Netflix |
| 27 | Change Data Capture | Event-Driven |
| 28 | Chaos Engineering | Resilience, Breaking Things, Production Ops, Netflix |
| 29 | Saga Pattern | System Design, Event-Driven, DDIA |
| 30 | Failover Simulation | Resilience, Breaking Things, Production Ops, DDIA |
| 31 | Rate Limiter Service | System Design |
| 32 | HyperLogLog | Data Structures |
| 33 | Webhook Delivery | System Design, Event-Driven |
| 34 | Distributed Counter | Netflix |
| 35 | Load Shedding | Resilience, Production Ops |
| 36 | Ticket Booking Concurrency | System Design |
| 37 | Distributed Locks | System Design, DDIA |
| 38 | Exactly-Once Processing | Event-Driven |
| 39 | Hot Partition Detection | Debugging, DDIA |
| 40 | Cold Start Problems | Debugging, Caching, Breaking Things, Production Ops |
| 41 | Two-Phase Commit | Breaking Things, DDIA |
| 42 | Write-Behind Cache | Caching |
| 43 | Consistent Hash Ring | System Design, Data Structures, DDIA |
| 44 | Federated Graph Search | Netflix |
| 45 | Kafka Backpressure | Debugging, Event-Driven |
| 46 | Quorum Reads/Writes | DDIA |
| 47 | Bulkhead Isolation | Resilience |
| 48 | Unique ID Generator | System Design, Data Structures |
| 49 | CRDTs | Data Structures, DDIA |
| 50 | URL Shortener Part 2 | System Design |
| 51 | Timeout Budgets | Resilience |
| 52 | Speculative Execution | Resilience |
