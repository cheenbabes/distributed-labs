# Lab 41: Two-Phase Commit (2PC) - The Blocking Protocol Trap

Two-Phase Commit (2PC) is the classic algorithm for distributed transactions. It guarantees atomicity across multiple nodes - either all participants commit or all abort. But this guarantee comes at a steep cost: **2PC is a blocking protocol**. If the coordinator fails at the wrong moment, all participants are stuck holding locks indefinitely.

In this lab, you will experience firsthand why "Designing Data-Intensive Applications" (DDIA) warns that 2PC is a trap, and why modern systems often prefer Sagas or other eventually consistent patterns.

## What You Will Learn

- How 2PC works: the prepare (voting) phase and the commit phase
- Why 2PC is called a "blocking" protocol
- What happens when the coordinator fails after participants vote YES
- How participants get stuck in "uncertain" state, holding locks
- Why timeouts do not help in the commit phase
- Why Sagas are often preferred for distributed transactions

## Architecture

```
┌────────────┐
│   Client   │
└─────┬──────┘
      │
      ▼
┌─────────────────┐
│   Coordinator   │  ◄── Single point of failure!
└─────┬───────────┘
      │
      ├────────────────────┬────────────────────┐
      ▼                    ▼                    ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ Participant A│    │ Participant B│    │ Participant C│
│  (Database)  │    │  (Database)  │    │  (Database)  │
└──────────────┘    └──────────────┘    └──────────────┘
```

### The 2PC Protocol

**Phase 1: Prepare (Voting)**
1. Coordinator sends PREPARE to all participants
2. Each participant:
   - Takes locks on affected resources
   - Writes to durable log that it voted YES
   - Responds with YES (can commit) or NO (cannot commit)

**Phase 2: Commit or Abort**
- If ALL voted YES: Coordinator sends COMMIT to all
- If ANY voted NO or timed out: Coordinator sends ABORT to all

### The Blocking Problem

After voting YES, a participant has:
- Taken locks on resources
- Promised to commit if asked
- **No way to unilaterally decide** - it must wait for the coordinator

If the coordinator crashes between receiving votes and sending the decision:
- All participants are STUCK
- They cannot commit (what if coordinator decides to abort?)
- They cannot abort (what if coordinator decides to commit?)
- They hold locks INDEFINITELY

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- jq (for JSON formatting)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Verify Services Are Running

```bash
docker compose ps
```

All services should show as "healthy".

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Client | http://localhost:8080 | Transaction API |
| Coordinator | http://localhost:8000 | 2PC Coordinator |
| Participant A | http://localhost:8001 | Database simulator |
| Participant B | http://localhost:8002 | Database simulator |
| Participant C | http://localhost:8003 | Database simulator |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Happy Path - Successful 2PC Transaction

First, let us see 2PC working correctly.

```bash
# Perform a transfer
curl -s -X POST http://localhost:8080/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "account-A", "to_account": "account-B", "amount": 100}' | jq

# Check the coordinator's transaction log
curl -s http://localhost:8000/transactions | jq

# Check each participant's state
curl -s http://localhost:8001/transactions | jq
curl -s http://localhost:8002/transactions | jq
curl -s http://localhost:8003/transactions | jq
```

**Expected:** Transaction completes with status "committed". All participants show the transaction as "committed".

**Look at the trace in Jaeger:**
1. Open http://localhost:16686
2. Select "coordinator" from the Service dropdown
3. Find a recent trace
4. Observe the two phases: prepare_phase and decision_phase
5. See how all three participants are called in parallel during prepare

**Question:** How much time is spent in the prepare phase vs the commit phase?

---

### Exercise 2: Participant Failure During Prepare

What happens if one participant cannot commit?

```bash
# Configure participant B to fail prepare requests
curl -s -X POST http://localhost:8002/admin/config \
  -H "Content-Type: application/json" \
  -d '{"fail_prepare_probability": 1.0}' | jq

# Try a transfer
curl -s -X POST http://localhost:8080/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "account-A", "to_account": "account-B", "amount": 100}' | jq

# Check coordinator - should show aborted
curl -s http://localhost:8000/transactions | jq '.transactions[-1]'

# Check participants - none should be in "prepared" state
curl -s http://localhost:8001/blocked | jq
curl -s http://localhost:8002/blocked | jq
curl -s http://localhost:8003/blocked | jq

# Reset participant B
curl -s -X POST http://localhost:8002/admin/reset | jq
```

**Expected:** Transaction is ABORTED because participant B voted NO. No resources are locked.

**Key insight:** When a participant votes NO, the coordinator can safely abort. The other participants that voted YES will be told to abort and release their locks. This is the safe failure mode.

---

### Exercise 3: Coordinator Failure After Prepare - THE BLOCKING SCENARIO

This is the dangerous failure mode that makes 2PC a trap.

```bash
# Configure coordinator to fail AFTER all participants vote YES
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"fail_after_prepare": true}' | jq

# Try a transfer - this will fail!
curl -s -X POST http://localhost:8080/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "account-A", "to_account": "account-B", "amount": 100}' | jq

# Check the participants - they are BLOCKED!
echo "Participant A blocked transactions:"
curl -s http://localhost:8001/blocked | jq

echo "Participant B blocked transactions:"
curl -s http://localhost:8002/blocked | jq

echo "Participant C blocked transactions:"
curl -s http://localhost:8003/blocked | jq
```

**Expected:** The request fails with error "coordinator_failed_after_prepare". Check the blocked transactions - all three participants have transactions in PREPARED state, holding locks!

**This is the 2PC trap:**
- All participants voted YES and are ready to commit
- They wrote to their log that they promised to commit
- The coordinator crashed before telling them to commit
- They CANNOT abort (they promised to commit if asked)
- They CANNOT commit (the coordinator might have decided to abort)
- They are STUCK holding locks

**Check Grafana:**
1. Open http://localhost:3001 (admin/admin)
2. Find the "Two-Phase Commit Dashboard"
3. Look at "Blocked Transactions (Coordinator)" and "Locked Transactions (Participants)"
4. Notice how the blocked count stays high

```bash
# Reset the coordinator to clear the failure mode
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"fail_after_prepare": false}' | jq

# The participants are STILL blocked!
# In real systems, this would require manual intervention or coordinator recovery
curl -s http://localhost:8001/blocked | jq

# Reset all participants to simulate "coordinator recovery resolved it"
curl -s -X POST http://localhost:8001/admin/reset | jq
curl -s -X POST http://localhost:8002/admin/reset | jq
curl -s -X POST http://localhost:8003/admin/reset | jq
curl -s -X POST http://localhost:8000/admin/reset | jq
```

---

### Exercise 4: Timeout Does Not Help in Commit Phase

A common misconception is that timeouts can solve the blocking problem. Let us see why they do not.

Consider this scenario:
1. All participants vote YES
2. Coordinator sends COMMIT to participant A (succeeds)
3. Coordinator crashes
4. Participants B and C never receive the commit decision

**If B and C timeout and abort:**
- Participant A has COMMITTED
- Participants B and C have ABORTED
- The transaction is now INCONSISTENT!

This is why participants CANNOT use timeouts to escape the PREPARED state. They must wait for the coordinator, even if it means waiting forever.

```bash
# Simulate blocking delay (not a crash, just slow coordinator)
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"block_duration_ms": 5000}' | jq

# Start a transfer (will take 5+ seconds due to blocking)
time curl -s -X POST http://localhost:8080/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "account-A", "to_account": "account-B", "amount": 100}' | jq

# During those 5 seconds, participants were holding locks!
# Reset
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"block_duration_ms": 0}' | jq
```

---

### Exercise 5: Why Sagas Are Often Preferred

The fundamental problem with 2PC is that it tries to provide "all or nothing" atomicity across distributed systems. Sagas take a different approach: accept that operations cannot be atomic, and instead provide compensating transactions.

**2PC approach to transfer:**
```
BEGIN DISTRIBUTED TRANSACTION
  UPDATE accounts SET balance = balance - 100 WHERE id = 'A'
  UPDATE accounts SET balance = balance + 100 WHERE id = 'B'
COMMIT
```

**Saga approach to transfer:**
```
Step 1: Debit account A by 100
Step 2: Credit account B by 100

If Step 2 fails:
  Compensate: Credit account A by 100 (undo Step 1)
```

**Key differences:**

| Aspect | 2PC | Saga |
|--------|-----|------|
| Consistency | Strong (ACID) | Eventual |
| Availability | Low (blocking) | High (no blocking) |
| Latency | High (two round trips) | Lower (sequential) |
| Failure handling | Coordinator must recover | Compensating transactions |
| Isolation | Full | Requires careful design |

**When to use which:**

Use 2PC when:
- You absolutely need strong consistency
- All participants are within the same datacenter
- You have reliable coordinator infrastructure
- Transactions are fast (low lock contention)

Use Sagas when:
- Availability is more important than consistency
- Participants are across datacenters or external services
- Transactions might be long-running
- You can design compensating transactions

---

### Exercise 6: Load Testing and Observability

Generate sustained load to see how 2PC behaves under pressure.

```bash
# Run k6 load test
docker compose run --rm lab41-k6 run /scripts/basic.js

# While running, watch Grafana dashboards
# Notice how latency increases when participants hold locks longer
```

With blocking enabled:
```bash
# Enable blocking delay
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"block_duration_ms": 1000}' | jq

# Run load test again - latency will be much higher
docker compose run --rm lab41-k6 run /scripts/basic.js

# Reset
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"block_duration_ms": 0}' | jq
```

---

## Key Takeaways

1. **2PC is a blocking protocol** - If the coordinator fails after prepare, participants are stuck indefinitely

2. **Timeouts do not help in commit phase** - A participant cannot unilaterally decide to abort or commit after voting YES

3. **The coordinator is a single point of failure** - Despite making 2PC work, the coordinator itself can fail

4. **2PC requires coordinator recovery** - In practice, the coordinator must persist its decision and be able to recover

5. **Sagas are often preferred** - For most distributed systems, eventual consistency with compensating transactions is more practical

6. **Lock duration = latency** - In 2PC, locks are held for the entire duration of both phases, which impacts throughput

## Real-World Implications

In production systems:
- **XA transactions** (Java EE, .NET) use 2PC internally - be aware of blocking risks
- **Distributed databases** like Spanner use variations of 2PC with careful coordinator design
- **Microservices** typically avoid 2PC in favor of Sagas (e.g., AWS Step Functions, Temporal)
- **Cross-datacenter transactions** almost never use 2PC due to latency and partition risks

## Cleanup

```bash
docker compose down -v
```

## Further Reading

- DDIA Chapter 9: "Consistency and Consensus" - Distributed Transactions section
- "Sagas" paper by Hector Garcia-Molina and Kenneth Salem (1987)
- Google Spanner paper - How they make 2PC work at scale
- Pat Helland's "Life Beyond Distributed Transactions"

## Next Lab

Lab 42: Implementing Sagas - See how to build reliable distributed workflows without 2PC.
