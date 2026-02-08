# Distributed Systems Concepts

## CAP Theorem

The CAP theorem, formulated by Eric Brewer in 2000, states that a distributed data store can provide at most two out of three guarantees simultaneously: Consistency, Availability, and Partition Tolerance. Consistency means every read receives the most recent write or an error. Availability means every request receives a non-error response, without guarantee that it contains the most recent write. Partition Tolerance means the system continues to operate despite an arbitrary number of messages being dropped or delayed by the network between nodes.

In practice, since network partitions are unavoidable in distributed systems, the real choice is between consistency and availability during a partition. Systems like traditional relational databases prioritize consistency (CP systems), while systems like Cassandra and DynamoDB prioritize availability (AP systems). Understanding this tradeoff is fundamental to designing distributed architectures.

## Consensus Algorithms

Consensus algorithms allow distributed systems to agree on a single value even when some nodes fail. The two most well-known algorithms are Paxos and Raft.

Paxos, invented by Leslie Lamport in 1989, uses a three-phase protocol: Prepare, Promise, and Accept. A proposer sends a prepare request with a proposal number. Acceptors respond with a promise not to accept lower-numbered proposals. Once a majority of acceptors promise, the proposer sends an accept request. Paxos is provably correct but notoriously difficult to understand and implement.

Raft was designed by Diego Ongaro and John Ousterhout in 2014 as an understandable alternative to Paxos. Raft decomposes consensus into three sub-problems: leader election, log replication, and safety. A cluster elects a single leader that manages log replication to followers. If the leader fails, a new election occurs. Raft uses randomized election timeouts to avoid split votes. Systems like etcd, Consul, and CockroachDB use Raft for consensus.

## Eventual Consistency

Eventual consistency is a consistency model in which, if no new updates are made to a data item, eventually all accesses to that item will return the last updated value. This is weaker than strong consistency but allows for higher availability and lower latency. DNS is a classic example of an eventually consistent system: when you update a DNS record, it takes time to propagate across all DNS servers worldwide, but eventually all servers will have the new value.

Amazon's Dynamo paper popularized eventual consistency in database design. The key insight is that many applications can tolerate temporary inconsistency if the system always remains available. Conflict resolution strategies include last-writer-wins (using timestamps), version vectors, and application-level merge functions.

## Distributed Transactions and Two-Phase Commit

A distributed transaction spans multiple nodes or databases. The Two-Phase Commit (2PC) protocol coordinates these transactions. In the prepare phase, a coordinator asks all participants if they can commit. Each participant performs the work and responds yes or no. In the commit phase, if all participants voted yes, the coordinator tells them to commit. If any voted no, the coordinator tells all to abort.

Two-Phase Commit guarantees atomicity but has a critical weakness: if the coordinator crashes after sending prepare but before sending commit/abort, all participants are blocked waiting. They cannot safely commit or abort because they do not know the coordinator's decision. This is called the blocking problem. Three-Phase Commit (3PC) adds an extra phase to address this, but it is rarely used in practice due to its complexity and the availability of better alternatives like Saga patterns.

## Leader Election

Leader election ensures exactly one node in a cluster acts as the coordinator at any given time. The Bully algorithm is simple: when a node detects the leader has failed, it sends election messages to all higher-numbered nodes. If none responds, it becomes leader. If a higher-numbered node responds, that node takes over the election.

In practice, leader election is often implemented using consensus algorithms (Raft includes built-in leader election) or external coordination services like ZooKeeper or etcd. The key challenges are detecting leader failure (using heartbeats and timeouts), avoiding split-brain scenarios (two nodes both believing they are leader), and handling network partitions gracefully.

## Gossip Protocols

Gossip protocols (also called epidemic protocols) spread information through a distributed system the way rumors spread in social networks. Each node periodically selects a random peer and exchanges state information. Over time, all nodes converge on the same state. Gossip protocols are highly scalable and fault-tolerant because they have no single point of failure and can tolerate arbitrary node failures.

Apache Cassandra uses gossip for cluster membership and failure detection. Each node gossips its state (including heartbeat counters) to random peers every second. If a node's heartbeat counter stops increasing, other nodes mark it as potentially down. Gossip protocols converge in O(log N) rounds for N nodes, making them practical even for very large clusters.

## Consistent Hashing

Consistent hashing maps both data items and nodes to positions on a virtual ring. Each data item is assigned to the first node encountered moving clockwise from the item's position on the ring. When a node joins or leaves, only the items mapped to that node need to be redistributed, minimizing data movement.

Amazon's DynamoDB and Apache Cassandra both use consistent hashing for data partitioning. Virtual nodes (vnodes) improve load distribution: each physical node is assigned multiple positions on the ring, spreading the load more evenly. Without virtual nodes, node failures can cause uneven load distribution on the remaining nodes.

## Vector Clocks

Vector clocks track causality in distributed systems. Each node maintains a vector of logical timestamps, one per node. When a node performs a local event, it increments its own counter. When sending a message, a node attaches its current vector. When receiving a message, a node updates its vector to the element-wise maximum of its own vector and the received vector, then increments its own counter.

Vector clocks can determine if two events are causally related or concurrent. Event A happened before event B if A's vector is less than or equal to B's vector in all components and strictly less in at least one. If neither vector dominates the other, the events are concurrent and may represent a conflict that needs resolution. Amazon's original Dynamo paper used vector clocks for conflict detection.

## CRDTs

Conflict-free Replicated Data Types (CRDTs) are data structures that can be replicated across multiple nodes and updated independently without coordination. They are mathematically guaranteed to converge to the same state regardless of the order in which updates are applied. There are two types: state-based CRDTs (convergent) that merge by exchanging full state, and operation-based CRDTs (commutative) that merge by exchanging operations.

Common CRDTs include G-Counters (grow-only counters), PN-Counters (increment and decrement), G-Sets (grow-only sets), OR-Sets (observed-remove sets), and LWW-Registers (last-writer-wins registers). Redis uses CRDTs in its Active-Active Geo-Distribution feature. Riak uses CRDTs for conflict-free data types. CRDTs are particularly useful in systems that prioritize availability and can tolerate eventual consistency, such as collaborative editing applications.
