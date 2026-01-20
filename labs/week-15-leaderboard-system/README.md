# Lab 15: Real-Time Leaderboard System

Build a real-time gaming leaderboard that can handle millions of players with O(log N) rank operations using Redis Sorted Sets.

## What You'll Learn

- How Redis Sorted Sets (ZSET) provide O(log N) rank operations
- Why this data structure is perfect for leaderboards
- How to support multiple time-based leaderboards (daily, weekly, all-time)
- Performance characteristics under high load
- Common interview question patterns for ranking systems

## The Problem

Gaming leaderboards have challenging requirements:

1. **High write throughput**: Millions of score updates per second
2. **Fast rank queries**: Players want to see their rank instantly
3. **Top N queries**: Display the leaderboard page efficiently
4. **Around-player queries**: Show players near your rank

A naive approach (sorting all players) would be O(N log N) for every query. With millions of players, this is too slow.

## The Solution: Redis Sorted Sets

Redis Sorted Sets use a skip list data structure that provides:

| Operation | Command | Time Complexity |
|-----------|---------|-----------------|
| Add/update score | ZINCRBY | O(log N) |
| Get player rank | ZREVRANK | O(log N) |
| Get top N players | ZREVRANGE | O(log N + M) |
| Get total players | ZCARD | O(1) |

Where N is the total number of players and M is the number of results returned.

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │           Leaderboard API                │
                    │         (FastAPI + Redis)               │
                    │                                          │
                    │  POST /players/{id}/score  ─────────┐   │
                    │  GET  /leaderboard/top     ─────────┼───│──▶ Redis ZSET
                    │  GET  /players/{id}/rank   ─────────┘   │     (Sorted Set)
                    │  GET  /leaderboard/around/{id}          │
                    └────────────────────────────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────────┐
                    │           PostgreSQL                     │
                    │       (Historical Data)                  │
                    └─────────────────────────────────────────┘
```

**Redis Sorted Set Structure:**
```
Key: leaderboard:all_time
Members:
  player_001 -> 15420
  player_002 -> 12350
  player_003 -> 11200
  ...
```

## Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for running seed script locally)
- curl (for manual testing)

## Quick Start

### 1. Start the Lab

```bash
cd labs/week-15-leaderboard-system
docker compose up --build -d
```

### 2. Verify Services Are Running

```bash
docker compose ps
```

All services should show as "healthy" or "running".

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Leaderboard API | http://localhost:8000 | API endpoints |
| API Docs | http://localhost:8000/docs | Swagger UI |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Add Scores and View the Leaderboard

Let's add some players and see the leaderboard in action.

```bash
# Add score for player1
curl -X POST "http://localhost:8000/players/player1/score" \
  -H "Content-Type: application/json" \
  -d '{"increment": 100}'

# Add more scores
curl -X POST "http://localhost:8000/players/player2/score" -H "Content-Type: application/json" -d '{"increment": 250}'
curl -X POST "http://localhost:8000/players/player3/score" -H "Content-Type: application/json" -d '{"increment": 175}'
curl -X POST "http://localhost:8000/players/player4/score" -H "Content-Type: application/json" -d '{"increment": 300}'
curl -X POST "http://localhost:8000/players/player5/score" -H "Content-Type: application/json" -d '{"increment": 50}'

# View the top 10 leaderboard
curl -s "http://localhost:8000/leaderboard/top?n=10" | jq
```

**Expected output:**
```json
[
  {"rank": 1, "player_id": "player4", "score": 300.0},
  {"rank": 2, "player_id": "player2", "score": 250.0},
  {"rank": 3, "player_id": "player3", "score": 175.0},
  {"rank": 4, "player_id": "player1", "score": 100.0},
  {"rank": 5, "player_id": "player5", "score": 50.0}
]
```

**Key observation:** The leaderboard is automatically sorted - no explicit sorting needed!

---

### Exercise 2: Get a Specific Player's Rank

```bash
# Get player3's rank
curl -s "http://localhost:8000/players/player3/rank" | jq

# Add more points to player3 and check again
curl -X POST "http://localhost:8000/players/player3/score" \
  -H "Content-Type: application/json" \
  -d '{"increment": 200}'

curl -s "http://localhost:8000/players/player3/rank" | jq
```

Notice how player3's rank changes after the score update.

**Get players around player3:**
```bash
curl -s "http://localhost:8000/leaderboard/around/player3?range=2" | jq
```

This shows 2 players above and 2 players below player3's position.

---

### Exercise 3: Observe O(log N) Rank Lookup Performance

Now let's seed the leaderboard with many players to observe that rank lookups remain fast.

```bash
# Seed 50,000 players
docker compose exec lab15-leaderboard-api python -c "
import asyncio
import redis.asyncio as redis
import random

async def seed():
    r = redis.Redis(host='lab15-redis', port=6379, decode_responses=True)
    pipe = r.pipeline()
    for i in range(50000):
        pipe.zadd('leaderboard:all_time', {f'seeded_player_{i:06d}': random.randint(1, 100000)})
        if i % 1000 == 0:
            await pipe.execute()
            pipe = r.pipeline()
    await pipe.execute()
    print(f'Seeded 50,000 players')
    await r.close()

asyncio.run(seed())
"

# Check total players
curl -s "http://localhost:8000/stats" | jq

# Time a rank query
time curl -s "http://localhost:8000/players/seeded_player_025000/rank" | jq

# Time a top 100 query
time curl -s "http://localhost:8000/leaderboard/top?n=100" | jq
```

**Key observation:** Even with 50,000+ players, rank queries complete in < 10ms.

Let's compare with different leaderboard sizes:

```bash
# Check response time with operation_time_ms
curl -s "http://localhost:8000/players/seeded_player_012345/rank" | jq '.operation_time_ms'
```

---

### Exercise 4: High-Volume Score Updates

Run the k6 load test to simulate high-volume traffic:

```bash
# Run load test (4 minutes)
docker compose run --rm lab15-k6 run /scripts/score-updates.js
```

While the test runs:
1. Open Grafana at http://localhost:3001 (admin/admin)
2. Navigate to the "Leaderboard System" dashboard
3. Observe:
   - Score updates per second
   - Latency percentiles remaining low
   - Redis memory usage

**Key metrics to watch:**
- p99 latency should stay under 50ms even under load
- Operations per second should scale with virtual users

---

### Exercise 5: Multiple Leaderboards (Daily Reset)

The system supports multiple leaderboard types that reset at different intervals.

```bash
# Add score to daily leaderboard
curl -X POST "http://localhost:8000/players/daily_player/score?leaderboard=daily" \
  -H "Content-Type: application/json" \
  -d '{"increment": 500}'

# Add score to weekly leaderboard
curl -X POST "http://localhost:8000/players/weekly_player/score?leaderboard=weekly" \
  -H "Content-Type: application/json" \
  -d '{"increment": 1000}'

# View available leaderboard types
curl -s "http://localhost:8000/leaderboard/types" | jq

# View daily leaderboard
curl -s "http://localhost:8000/leaderboard/top?n=10&leaderboard=daily" | jq

# View weekly leaderboard
curl -s "http://localhost:8000/leaderboard/top?n=10&leaderboard=weekly" | jq
```

**How it works:**
- `all_time`: Key = `leaderboard:all_time` (never resets)
- `weekly`: Key = `leaderboard:weekly:2024-W03` (includes ISO week number)
- `daily`: Key = `leaderboard:daily:2024-01-15` (includes date)

When the day/week changes, a new key is automatically used!

---

### Exercise 6: View Traces in Jaeger

1. Open Jaeger at http://localhost:16686
2. Select "leaderboard-api" from the Service dropdown
3. Click "Find Traces"
4. Click on a trace to see:
   - Redis operation timing
   - Span attributes (player_id, operation type, etc.)

Look for the `update_score` and `get_player_rank` spans to understand the internal operations.

---

## Key Takeaways

### 1. Redis Sorted Sets are Perfect for Leaderboards

The skip list data structure provides:
- O(log N) for insertions/updates
- O(log N) for rank queries
- O(log N + M) for range queries

### 2. Time Complexity Matters

| Approach | Rank Query | Why |
|----------|------------|-----|
| Naive (sort array) | O(N log N) | Must sort entire list |
| Hash + Binary Search | O(log N) | But inserts are O(N) |
| Redis ZSET | O(log N) | Skip list magic |

### 3. Memory Efficiency

Redis stores sorted sets efficiently:
- ~50 bytes per member (player ID + score)
- 1 million players = ~50 MB

### 4. Common Interview Variations

- **Leaderboard with ties**: Use lexicographic ordering as tiebreaker
- **Percentile rankings**: Use ZCOUNT for range calculations
- **Friend leaderboards**: Store per-user ZSETs, or use secondary filtering
- **Real-time updates**: Pub/sub for live updates to connected clients

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Redis connection issues
```bash
docker compose exec lab15-redis redis-cli ping
```

### Check leaderboard data directly
```bash
docker compose exec lab15-redis redis-cli
> ZREVRANGE leaderboard:all_time 0 9 WITHSCORES
> ZCARD leaderboard:all_time
```

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/players/{id}/score` | POST | Add to player's score |
| `/leaderboard/top` | GET | Get top N players |
| `/players/{id}/rank` | GET | Get player's rank |
| `/leaderboard/around/{id}` | GET | Get players around a player |
| `/stats` | GET | Get leaderboard statistics |
| `/leaderboard/types` | GET | List available leaderboard types |
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics |

## Next Steps

- Add pagination for leaderboard browsing
- Implement percentile calculations
- Add friend leaderboards
- Set up TTL for daily/weekly leaderboards to auto-expire
