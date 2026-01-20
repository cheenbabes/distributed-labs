# Distributed Lab Brainstorm

Raw ideas for hands-on distributed systems labs. These bridge the gap between reading DDIA/system design books and actually understanding how systems behave.

---

## What's in DDIA That Nobody Touches Hands-On

### Consistency Models (see it, don't just read about it)
- **Eventual consistency in action** - write to one replica, read from another, see stale data, then see convergence
- **Read-your-writes consistency** - implement session guarantees, show what breaks without them
- **Linearizability vs serializability** - actually demonstrate the difference with concurrent operations
- **Quorum reads/writes** - configure N=3, W=2, R=2, then kill a node and see what still works

### Distributed Primitives
- **Distributed locks** - Redis SETNX, show what happens when lock holder dies, then show Redlock and its controversy
- **Lease-based locking** - TTL-based locks, clock skew problems, fencing tokens
- **Vector clocks / Lamport timestamps** - visualize causality, detect conflicts
- **Two-phase commit** - implement it, then break it (coordinator crash), see why it's problematic
- **Saga pattern** - the alternative to 2PC, compensating transactions, show partial failures

### Storage Internals
- **Write-ahead log** - build one, crash the service, show recovery. This is the foundation of durability.
- **LSM trees** - why Cassandra/RocksDB work this way, memtables, SSTables, compaction
- **B-tree vs LSM tradeoffs** - write amplification, read amplification, space amplification
- **Compaction strategies** - leveled vs size-tiered, see the IO patterns

---

## Data Structures That Power Everything

These are used everywhere but people never implement them:

- **Bloom filters** - probabilistic membership, tune false positive rate, see the tradeoffs
- **Count-Min Sketch** - approximate frequency counting, streaming analytics
- **HyperLogLog** - cardinality estimation (how Redis counts unique items)
- **Skip lists** - how Redis sorted sets actually work
- **Consistent hash ring** - visualize key distribution, add/remove nodes, see rebalancing
- **Merkle trees** - verify data consistency, anti-entropy repair (how Cassandra syncs replicas)
- **Ring buffers** - bounded queues, backpressure, what happens when full

---

## Resilience Patterns Beyond Basics

- **Circuit breakers with state machine** - closed → open → half-open, with metrics dashboard
- **Bulkhead isolation** - separate thread pools, prevent one slow dependency from killing everything
- **Timeout budgets / deadline propagation** - pass remaining time through the call chain
- **Backpressure** - what happens without it (OOM), reactive streams, bounded queues
- **Graceful degradation** - when cache is down, serve stale; when recommendation fails, serve popular
- **Chaos engineering** - random failure injection, see if your resilience patterns actually work
- **Load shedding strategies** - LIFO vs FIFO, priority queues, adaptive shedding

---

## Deployment & Operations

Things every senior engineer should have done at least once:

- **Blue-green deployments** - traffic switching, instant rollback, database compatibility
- **Canary releases** - gradual rollout with metrics, automated rollback on error rate
- **Feature flags** - kill switches, percentage rollouts, user targeting
- **Rolling updates** - one at a time, health checks, connection draining
- **Database migrations without downtime** - expand-contract pattern, dual writes
- **Secret rotation** - rotate credentials without downtime

---

## Streaming & Event-Driven

Beyond basic queues:

- **Change data capture (CDC)** - Debezium pattern, stream database changes to Kafka
- **Exactly-once processing** - Kafka transactions, idempotent producers, deduplication
- **Windowed aggregations** - tumbling, sliding, session windows
- **Stream-table joins** - enrich events with dimension data
- **Dead letter queues** - poison message handling, retry strategies
- **Backpressure in streams** - consumer lag, flow control

---

## Real Systems People Interview On

Classic system design questions made hands-on:

- **Distributed lock service** - like ZooKeeper but simplified, see election, see failures
- **Job scheduler** - delayed execution, recurring jobs, priorities, dead letter handling
- **Leaderboard / ranking system** - real-time updates, sorted sets, sharding strategies
- **Session store** - sticky sessions vs shared store, invalidation, TTL
- **Time-series database** - downsampling, retention, efficient range queries
- **Inverted index / search basics** - tokenization, posting lists, relevance scoring
- **Distributed rate limiter** - sliding window across multiple nodes, eventual consistency tradeoffs
- **Webhook delivery system** - retry with backoff, ordering, exactly-once challenges

---

## Debugging Distributed Systems

Practical skills nobody teaches:

- **Finding the slow service** - trace analysis, percentile breakdown
- **Reproducing race conditions** - inject delays, force interleavings
- **Debugging stale reads** - trace through replicas, find the lag
- **Simulating network issues** - latency injection, packet loss, partitions (tc/netem)
- **Memory leak investigation** - watch metrics degrade, take heap dumps, find the leak
- **Connection pool debugging** - see connections pile up, find the leak, understand pool dynamics
- **Hot partition detection** - uneven load, key distribution analysis

---

## Advanced Patterns

For people going deeper:

- **CRDTs** - conflict-free replicated data types, G-Counter, PN-Counter, LWW-Register
- **Gossip protocols** - epidemic broadcast, failure detection, protocol visualization
- **Anti-entropy repair** - Merkle trees, detect and fix inconsistencies
- **Read repair** - fix stale replicas on read
- **Hinted handoff** - handle temporary failures, replay when node returns
- **Speculative execution** - hedge requests, cancel losers

---

## Multi-Region Simulation

Can simulate with added latency:

- **Geo-replication** - async replication with lag, see consistency tradeoffs
- **Conflict resolution** - last-write-wins, merge functions, CRDTs
- **Global vs local reads** - read from nearest, write to leader
- **Failover simulation** - promote replica, see data loss window
