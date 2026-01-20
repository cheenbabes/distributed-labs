# Demo Runbook: Real-Time Leaderboard System

This runbook contains all commands for demonstrating the leaderboard system. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose down -v 2>/dev/null || true
```

---

## Part 1: Start the Lab

### Build and start all services

```bash {"name": "start-lab"}
docker compose up --build -d
```

### Wait for services to be healthy

```bash {"name": "wait-healthy"}
echo "Waiting for services to be healthy..."
sleep 15
docker compose ps
```

### Verify API is responding

```bash {"name": "verify-api"}
curl -s http://localhost:8000/health | jq
```

---

## Part 2: Basic Leaderboard Operations

### Add initial players with scores

```bash {"name": "add-players"}
echo "Adding 5 players with different scores..."

curl -s -X POST "http://localhost:8000/players/alice/score" \
  -H "Content-Type: application/json" \
  -d '{"increment": 1500}' | jq

curl -s -X POST "http://localhost:8000/players/bob/score" \
  -H "Content-Type: application/json" \
  -d '{"increment": 2200}' | jq

curl -s -X POST "http://localhost:8000/players/charlie/score" \
  -H "Content-Type: application/json" \
  -d '{"increment": 1800}' | jq

curl -s -X POST "http://localhost:8000/players/diana/score" \
  -H "Content-Type: application/json" \
  -d '{"increment": 3100}' | jq

curl -s -X POST "http://localhost:8000/players/eve/score" \
  -H "Content-Type: application/json" \
  -d '{"increment": 950}' | jq
```

### View the leaderboard

```bash {"name": "view-leaderboard"}
echo "Top 10 Leaderboard:"
curl -s "http://localhost:8000/leaderboard/top?n=10" | jq
```

### Get a specific player's rank

```bash {"name": "get-rank"}
echo "Charlie's current rank:"
curl -s "http://localhost:8000/players/charlie/rank" | jq
```

### Update a player's score

```bash {"name": "update-score"}
echo "Adding 1500 points to Charlie..."
curl -s -X POST "http://localhost:8000/players/charlie/score" \
  -H "Content-Type: application/json" \
  -d '{"increment": 1500}' | jq

echo ""
echo "Updated leaderboard:"
curl -s "http://localhost:8000/leaderboard/top?n=10" | jq
```

---

## Part 3: Get Players Around a Position

### View players around Charlie

```bash {"name": "around-player"}
echo "Players around Charlie (+/- 2 positions):"
curl -s "http://localhost:8000/leaderboard/around/charlie?range=2" | jq
```

---

## Part 4: Multiple Leaderboard Types

### Add scores to different leaderboards

```bash {"name": "multi-leaderboard"}
echo "Adding to daily leaderboard..."
curl -s -X POST "http://localhost:8000/players/daily_champion/score?leaderboard=daily" \
  -H "Content-Type: application/json" \
  -d '{"increment": 5000}' | jq

echo ""
echo "Adding to weekly leaderboard..."
curl -s -X POST "http://localhost:8000/players/weekly_star/score?leaderboard=weekly" \
  -H "Content-Type: application/json" \
  -d '{"increment": 10000}' | jq
```

### View all leaderboard types

```bash {"name": "list-leaderboards"}
echo "Available leaderboards:"
curl -s "http://localhost:8000/leaderboard/types" | jq
```

### View daily leaderboard

```bash {"name": "daily-leaderboard"}
echo "Daily Leaderboard:"
curl -s "http://localhost:8000/leaderboard/top?n=10&leaderboard=daily" | jq
```

---

## Part 5: Seed Large Dataset

### Seed 50,000 players for performance testing

```bash {"name": "seed-data"}
echo "Seeding 50,000 players (this may take ~30 seconds)..."
docker compose exec lab15-leaderboard-api python -c "
import asyncio
import redis.asyncio as redis
import random

async def seed():
    r = redis.Redis(host='lab15-redis', port=6379, decode_responses=True)
    batch_size = 5000
    total = 50000

    for batch_start in range(0, total, batch_size):
        pipe = r.pipeline()
        for i in range(batch_start, min(batch_start + batch_size, total)):
            pipe.zadd('leaderboard:all_time', {f'player_{i:06d}': random.randint(1, 100000)})
        await pipe.execute()
        print(f'Progress: {min(batch_start + batch_size, total)}/{total}')

    count = await r.zcard('leaderboard:all_time')
    print(f'Total players in leaderboard: {count}')
    await r.close()

asyncio.run(seed())
"
```

### View statistics

```bash {"name": "view-stats"}
curl -s "http://localhost:8000/stats" | jq
```

---

## Part 6: Performance Testing

### Measure rank query performance

```bash {"name": "rank-timing"}
echo "Timing 10 rank queries on a 50,000+ player leaderboard..."
for i in {1..10}; do
  player="player_$(printf '%06d' $((RANDOM % 50000)))"
  time=$(curl -s "http://localhost:8000/players/${player}/rank" | jq -r '.operation_time_ms // "N/A"')
  echo "Query $i (${player}): ${time}ms"
done
```

### Measure top N query performance

```bash {"name": "topn-timing"}
echo "Timing top N queries with different sizes..."
for n in 10 50 100 500; do
  echo -n "Top ${n}: "
  curl -s -o /dev/null -w "%{time_total}s\n" "http://localhost:8000/leaderboard/top?n=${n}"
done
```

### Run k6 load test

```bash {"name": "load-test", "background": true}
docker compose run --rm lab15-k6 run /scripts/score-updates.js
```

---

## Part 7: Observability

### Show observation URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  API Docs:    http://localhost:8000/docs"
echo "  Jaeger:      http://localhost:16686"
echo "  Grafana:     http://localhost:3001 (admin/admin)"
echo "  Prometheus:  http://localhost:9090"
```

### Check Prometheus metrics

```bash {"name": "prometheus-metrics"}
echo "Sample Prometheus metrics:"
curl -s http://localhost:8000/metrics | grep leaderboard_ | head -20
```

---

## Part 8: Direct Redis Access

### View Redis sorted set directly

```bash {"name": "redis-top10"}
echo "Top 10 from Redis directly:"
docker compose exec lab15-redis redis-cli ZREVRANGE leaderboard:all_time 0 9 WITHSCORES
```

### Check total players in Redis

```bash {"name": "redis-count"}
echo "Total players:"
docker compose exec lab15-redis redis-cli ZCARD leaderboard:all_time
```

### Get a player's score directly

```bash {"name": "redis-score"}
echo "Score for player_000001:"
docker compose exec lab15-redis redis-cli ZSCORE leaderboard:all_time player_000001
```

### Check Redis memory usage

```bash {"name": "redis-memory"}
docker compose exec lab15-redis redis-cli INFO memory | grep -E "used_memory_human|used_memory_peak_human"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up"
```

---

## Troubleshooting Commands

### Check all service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check leaderboard API logs

```bash {"name": "logs-api"}
docker compose logs lab15-leaderboard-api --tail=50
```

### Check Redis logs

```bash {"name": "logs-redis"}
docker compose logs lab15-redis --tail=30
```

### Restart the API

```bash {"name": "restart-api"}
docker compose restart lab15-leaderboard-api
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Add score | `curl -X POST localhost:8000/players/{id}/score -d '{"increment": 100}'` |
| Get rank | `curl localhost:8000/players/{id}/rank` |
| Get top 10 | `curl localhost:8000/leaderboard/top?n=10` |
| Get around player | `curl localhost:8000/leaderboard/around/{id}?range=5` |
| Get stats | `curl localhost:8000/stats` |
| API docs | http://localhost:8000/docs |
| View traces | http://localhost:16686 |
| Grafana | http://localhost:3001 (admin/admin) |
