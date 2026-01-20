Content Pillars

Pillar 1: Foundations (Building Blocks) — 30%
Core distributed systems patterns with simple, runnable labs. Not "what is a cache" basic, but "here's a write-through cache in front of a database you can trace requests through." The fundamentals that underpin everything else.

Pillar 2: Breaking Things (Failure Modes) — 20%
What happens when systems fail. Cascading failures, retry storms, network partitions, resource exhaustion. This is the chaos engineering pillar—show people how things break so they understand why resilience patterns exist.

Pillar 3: Real-World Systems (System Design) — 35%
Building simplified versions of real systems—URL shorteners, newsfeeds, ride-sharing backends. These are the classic interview patterns but made hands-on with actual running code.

Pillar 4: Tech Blog Deep Dives (Production at Scale) — 15%
Taking Netflix and other tech blog posts and building simplified, runnable versions. Distributed graph search, distributed counters, load shedding, chaos engineering tools. This is the unique authority angle. Netflix-heavy (30-40% of this pillar).

51 Video Topics

Pillar 1: Foundations (15 topics)

#
Topic
Difficulty
Early
1
Write-through cache: Redis in front of Postgres
Beginner
✓
2
Write-behind (write-back) cache pattern
Beginner
✓
3
Cache-aside pattern: when to use it
Beginner
✓
4
Consistent hashing: distributing data across nodes
Intermediate


5
Leader election with simple consensus
Intermediate


6
Event sourcing: storing state as a sequence of events
Intermediate


7
CQRS: separating reads from writes
Intermediate


8
Database replication: primary-replica setup
Beginner
✓
9
Sharding a database: horizontal partitioning
Intermediate


10
Message queues: async communication with RabbitMQ
Beginner
✓
11
Pub/sub messaging with Kafka basics
Intermediate


12
API gateway pattern: routing and aggregation
Intermediate


13
Service discovery: how services find each other
Intermediate


14
Distributed configuration management
Intermediate


15
Rate limiting: token bucket and sliding window
Beginner
✓


Pillar 2: Breaking Things (10 topics)

#
Topic
Difficulty
Early
16
What happens when your cache dies
Beginner
✓
17
Retry storms: when retries make everything worse
Intermediate
✓
18
Cascading failures: one slow service takes down everything
Intermediate


19
Split-brain: when your cluster disagrees
Advanced


20
Thundering herd: cache stampede in action
Intermediate


21
Database connection pool exhaustion
Intermediate
✓
22
Memory leaks under load: watching a service degrade
Intermediate


23
Network partitions: what CAP theorem actually looks like
Intermediate


24
Deadlocks in distributed transactions
Advanced


25
Cold start problems: why your service is slow after deploy
Beginner
✓


Pillar 3: Real-World Systems (17 topics)

#
Topic
Difficulty
Early
26
URL shortener Part 1: basic design and key generation
Beginner
✓
27
URL shortener Part 2: scaling with caching and analytics
Intermediate


28
Pastebin clone: storing and retrieving text at scale
Beginner
✓
29
Rate limiter service: protecting your APIs
Intermediate
✓
30
Unique ID generator: distributed sequence generation
Intermediate


31
News feed Part 1: fan-out on write vs fan-out on read
Intermediate


32
News feed Part 2: ranking and real-time updates
Advanced


33
Chat system Part 1: real-time messaging basics
Intermediate


34
Chat system Part 2: presence and delivery guarantees
Advanced


35
Notification service: push notifications at scale
Intermediate


36
File storage service: chunking and deduplication
Intermediate


37
Search autocomplete: typeahead suggestions
Intermediate


38
Nearby friends / location-based service
Intermediate


39
Ride-sharing backend Part 1: matching riders and drivers
Advanced


40
Ticket booking system: handling concurrent reservations
Intermediate


41
Web crawler: distributed crawling basics
Intermediate


42
Key-value store: building a simple distributed KV
Advanced




Pillar 4: Tech Blog Deep Dives (9 topics)

#
Topic
Difficulty
Source
Early
43
Netflix's distributed counter (simplified)
Intermediate
Netflix


44
Zuul-style API gateway with load shedding
Intermediate
Netflix


45
Real-time distributed graph (simplified)
Advanced
Netflix


46
Netflix's federated graph search: making GraphQL searchable
Advanced
Netflix
✓
47
Discord's message storage: billions of messages
Intermediate
Discord


48
Stripe's idempotency keys: exactly-once payments
Intermediate
Stripe


49
Uber's geospatial indexing: H3 and location matching
Advanced
Uber


50
Slack's real-time messaging architecture
Intermediate
Slack


51
GitHub's merge queue: handling concurrent PRs
Intermediate
GitHub




Recommended Starting Sequence (First 10 Videos)

Based on beginner-friendly topics that will let you build production skills:

1. Write-through cache: Redis in front of Postgres (Foundations)
2. What happens when your cache dies (Breaking Things)
3. URL shortener Part 1 (Real-World Systems)
4. Cache-aside pattern (Foundations)
5. Rate limiting: token bucket and sliding window (Foundations)
6. Rate limiter service: protecting your APIs (Real-World Systems)
7. Netflix's federated graph search: making GraphQL searchable (Tech Blog Deep Dives)
8. Message queues: async communication with RabbitMQ (Foundations)
9. Retry storms: when retries make everything worse (Breaking Things)
10. Pastebin clone (Real-World Systems)
