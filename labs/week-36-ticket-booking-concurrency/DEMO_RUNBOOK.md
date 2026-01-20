# Demo Runbook: Ticket Booking Concurrency

This runbook contains all commands for the video demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

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

### Verify booking service is responding

```bash {"name": "verify-service"}
curl -s http://localhost:8000/health | jq
```

### Check initial event and seats

```bash {"name": "check-event"}
curl -s http://localhost:8000/events/1 | jq '{event: .event.name, total_seats: .seats.total, available: .seats.available}'
```

---

## Part 2: Show the Race Condition Bug

### Verify strategy is "none" (no locking)

```bash {"name": "check-strategy"}
curl -s http://localhost:8000/admin/strategy | jq
```

### Reset the event (fresh start)

```bash {"name": "reset-event"}
curl -s -X POST "http://localhost:8000/admin/reset?event_id=1" | jq
```

### Show seat A1 is available

```bash {"name": "check-seat-a1"}
curl -s "http://localhost:8000/events/1/seats?available_only=true" | jq '.seats[] | select(.seat_number == "A1")'
```

### Concurrent booking attack - 10 requests for same seat

```bash {"name": "concurrent-booking-no-lock"}
echo "Sending 10 concurrent booking requests for seat A1..."
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/book \
    -H "Content-Type: application/json" \
    -d "{\"event_id\": 1, \"seat_number\": \"A1\", \"customer_id\": \"customer_$i\"}" &
done
wait
echo "Done!"
```

### Check for double bookings (THE BUG!)

```bash {"name": "check-double-bookings"}
curl -s http://localhost:8000/admin/double-bookings | jq
```

### Show booking statistics

```bash {"name": "show-stats-no-lock"}
curl -s http://localhost:8000/admin/stats | jq
```

---

## Part 3: Fix with Pessimistic Locking

### Switch to pessimistic locking (SELECT FOR UPDATE)

```bash {"name": "set-pessimistic"}
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "pessimistic"}' | jq
```

### Reset the event

```bash {"name": "reset-for-pessimistic"}
curl -s -X POST "http://localhost:8000/admin/reset?event_id=1" | jq
```

### Same concurrent attack - 10 requests for seat A1

```bash {"name": "concurrent-booking-pessimistic"}
echo "Sending 10 concurrent booking requests for seat A1..."
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/book \
    -H "Content-Type: application/json" \
    -d "{\"event_id\": 1, \"seat_number\": \"A1\", \"customer_id\": \"customer_$i\"}" &
done
wait
echo "Done!"
```

### Verify NO double bookings

```bash {"name": "verify-no-double-bookings-pessimistic"}
curl -s http://localhost:8000/admin/double-bookings | jq
```

### Show stats - exactly 1 confirmed, 9 failed

```bash {"name": "show-stats-pessimistic"}
curl -s http://localhost:8000/admin/stats | jq
```

---

## Part 4: Try Optimistic Locking

### Switch to optimistic locking (version column)

```bash {"name": "set-optimistic"}
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "optimistic"}' | jq
```

### Reset the event

```bash {"name": "reset-for-optimistic"}
curl -s -X POST "http://localhost:8000/admin/reset?event_id=1" | jq
```

### Concurrent booking test

```bash {"name": "concurrent-booking-optimistic"}
echo "Sending 10 concurrent booking requests for seat A1..."
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/book \
    -H "Content-Type: application/json" \
    -d "{\"event_id\": 1, \"seat_number\": \"A1\", \"customer_id\": \"customer_$i\"}" &
done
wait
echo "Done!"
```

### Verify NO double bookings

```bash {"name": "verify-no-double-bookings-optimistic"}
curl -s http://localhost:8000/admin/double-bookings | jq
```

### Show stats

```bash {"name": "show-stats-optimistic"}
curl -s http://localhost:8000/admin/stats | jq
```

---

## Part 5: Distributed Locking with Redis

### Switch to distributed locking

```bash {"name": "set-distributed"}
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "distributed"}' | jq
```

### Reset the event

```bash {"name": "reset-for-distributed"}
curl -s -X POST "http://localhost:8000/admin/reset?event_id=1" | jq
```

### Concurrent booking test

```bash {"name": "concurrent-booking-distributed"}
echo "Sending 10 concurrent booking requests for seat A1..."
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/book \
    -H "Content-Type: application/json" \
    -d "{\"event_id\": 1, \"seat_number\": \"A1\", \"customer_id\": \"customer_$i\"}" &
done
wait
echo "Done!"
```

### Verify NO double bookings

```bash {"name": "verify-no-double-bookings-distributed"}
curl -s http://localhost:8000/admin/double-bookings | jq
```

### Show stats

```bash {"name": "show-stats-distributed"}
curl -s http://localhost:8000/admin/stats | jq
```

---

## Part 6: Load Testing

### Show available UIs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Jaeger:     http://localhost:16686"
echo "  Prometheus: http://localhost:9090"
echo "  API:        http://localhost:8000"
```

### Run k6 load test with NO locking (shows bugs at scale)

```bash {"name": "loadtest-no-lock"}
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "none"}'
curl -s -X POST "http://localhost:8000/admin/reset?event_id=1"

docker compose run --rm lab36-k6 run /scripts/concurrent-booking.js
```

### Check double bookings after load test

```bash {"name": "check-double-bookings-after-load"}
curl -s http://localhost:8000/admin/double-bookings | jq
```

### Run k6 load test with PESSIMISTIC locking (should be safe)

```bash {"name": "loadtest-pessimistic"}
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "pessimistic"}'
curl -s -X POST "http://localhost:8000/admin/reset?event_id=1"

docker compose run --rm lab36-k6 run /scripts/concurrent-booking.js
```

### Verify no double bookings with pessimistic locking

```bash {"name": "verify-safe-pessimistic"}
echo "Double bookings:"
curl -s http://localhost:8000/admin/double-bookings | jq '.count'
echo ""
echo "Stats:"
curl -s http://localhost:8000/admin/stats | jq '.totals'
```

---

## Part 7: Strategy Comparison Side-by-Side

### Quick comparison of all strategies

```bash {"name": "quick-comparison"}
echo "=== STRATEGY COMPARISON ==="
echo ""

for strategy in none pessimistic optimistic distributed; do
  echo "--- Testing $strategy ---"

  # Set strategy and reset
  curl -s -X POST http://localhost:8000/admin/strategy \
    -H "Content-Type: application/json" \
    -d "{\"strategy\": \"$strategy\"}" > /dev/null
  curl -s -X POST "http://localhost:8000/admin/reset?event_id=1" > /dev/null
  sleep 1

  # Concurrent booking attack
  for i in {1..20}; do
    curl -s -X POST http://localhost:8000/book \
      -H "Content-Type: application/json" \
      -d "{\"event_id\": 1, \"seat_number\": \"A1\", \"customer_id\": \"user_$i\"}" > /dev/null &
  done
  wait

  # Check results
  double_bookings=$(curl -s http://localhost:8000/admin/double-bookings | jq '.count')
  stats=$(curl -s http://localhost:8000/admin/stats | jq '.totals')

  if [ "$double_bookings" -gt 0 ]; then
    echo "  UNSAFE - $double_bookings double bookings detected"
  else
    echo "  SAFE - No double bookings"
  fi
  echo "  Stats: $stats"
  echo ""
done
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

### Check booking service logs

```bash {"name": "logs-booking"}
docker compose logs lab36-booking-service --tail=50
```

### Check database directly

```bash {"name": "check-db"}
docker compose exec lab36-postgres psql -U booking -d tickets -c "SELECT seat_number, is_booked, booked_by FROM seats WHERE event_id = 1 ORDER BY seat_number LIMIT 20;"
```

### Check Redis

```bash {"name": "check-redis"}
docker compose exec lab36-redis redis-cli KEYS "seat_lock:*"
```

### Restart booking service

```bash {"name": "restart-booking"}
docker compose restart lab36-booking-service
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Set strategy | `curl -X POST localhost:8000/admin/strategy -d '{"strategy":"pessimistic"}'` |
| Reset event | `curl -X POST "localhost:8000/admin/reset?event_id=1"` |
| Book seat | `curl -X POST localhost:8000/book -d '{"event_id":1,"seat_number":"A1","customer_id":"me"}'` |
| Check double bookings | `curl localhost:8000/admin/double-bookings` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
