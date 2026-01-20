# Lab 23: Graceful Degradation

When dependencies fail, your system has two choices: fail completely or provide a degraded but still useful response. In this lab, you'll implement and observe graceful degradation patterns that keep your product catalog functional even when recommendations, pricing, or inventory services are unavailable.

## What You'll Learn

- How to implement fallback responses when dependencies fail
- How to use cached data as a safety net
- How to measure and communicate response quality to users
- How feature flags control degradation behavior
- How to monitor and visualize degradation modes in production

## Architecture

```
                              ┌─────────────────────┐
                              │  Recommendation     │
                              │  Service            │
                              │  (port 8001)        │
                              │  Fallback: Popular  │
                              │  Items              │
                              └─────────┬───────────┘
                                        │
┌──────────┐    ┌─────────────────────┐ │
│  Client  │───▶│  Product Catalog    │─┼─────────────────────┐
└──────────┘    │  Service            │ │                     │
                │  (port 8000)        │ │   ┌─────────────────▼───┐
                │                     │ │   │  Pricing Service    │
                │  Aggregates data    │ │   │  (port 8002)        │
                │  with fallbacks     │ │   │  Fallback: Cached   │
                └─────────────────────┘ │   │  Prices             │
                                        │   └─────────────────────┘
                                        │
                              ┌─────────▼───────────┐
                              │  Inventory Service  │
                              │  (port 8003)        │
                              │  Fallback: "Check   │
                              │  in Store"          │
                              └─────────────────────┘
```

### Service Dependencies

| Service | Type | Fallback Behavior |
|---------|------|-------------------|
| Recommendation | Optional | Returns popular items list |
| Pricing | Optional | Returns cached prices (may be stale) |
| Inventory | Required | Returns "Check availability in store" |

### Response Quality Scores

The product catalog tracks response quality:

- **1.0** - All live data (full response)
- **0.67** - One fallback used (partial degradation)
- **0.33** - Two fallbacks used (significant degradation)
- **0.0** - All fallbacks used (maximum degradation)

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- jq (for JSON formatting)
- k6 (optional, for load testing)

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
| Product Catalog | http://localhost:8000 | Main API entry point |
| Recommendation | http://localhost:8001 | Recommendation service |
| Pricing | http://localhost:8002 | Pricing service |
| Inventory | http://localhost:8003 | Inventory service |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Baseline with All Dependencies Healthy

First, establish a baseline with all services running normally.

```bash
# Make a single request
curl -s http://localhost:8000/product/SKU-001 | jq

# Check the quality score (should be 1.0)
curl -s http://localhost:8000/product/SKU-001 | jq '.quality'
```

**Expected Response:**
```json
{
  "quality": {
    "score": 1.0,
    "degradation_level": "none",
    "degraded_components": [],
    "errors": []
  },
  "data": {
    "recommendations": {
      "source": "live",
      "degraded": false
    },
    "pricing": {
      "source": "live",
      "degraded": false
    },
    "inventory": {
      "source": "live",
      "degraded": false
    }
  }
}
```

**Questions:**
- What is the total request latency?
- What data sources are being used?
- Open Jaeger and examine the trace - how many spans do you see?

---

### Exercise 2: Kill Recommendation Service, Observe Fallback

Simulate the recommendation service failing and observe the fallback behavior.

```bash
# Stop the recommendation service
docker compose stop lab23-recommendation

# Make requests and observe fallback
curl -s http://localhost:8000/product/SKU-001 | jq '.quality'
curl -s http://localhost:8000/product/SKU-001 | jq '.data.recommendations'
```

**Expected Response:**
```json
{
  "recommendations": {
    "recommendations": ["SKU-001", "SKU-002", "SKU-003"],
    "source": "fallback_popular_items",
    "degraded": true,
    "fallback_reason": "..."
  }
}
```

**Observations:**
- The quality score drops to ~0.67
- Recommendations come from "popular items" instead of personalized
- The response still succeeds - no error returned to user

```bash
# Restart the service
docker compose start lab23-recommendation
```

---

### Exercise 3: Kill Pricing Service, See Cached Prices

Now test the pricing fallback with cached data.

```bash
# First, note the live prices
curl -s http://localhost:8000/product/SKU-001 | jq '.data.pricing'

# Stop pricing service
docker compose stop lab23-pricing

# Make requests with cached fallback
curl -s http://localhost:8000/product/SKU-001 | jq '.data.pricing'
```

**Expected Response:**
```json
{
  "pricing": {
    "price": 29.99,
    "currency": "USD",
    "source": "fallback_cached",
    "cached_at": "2024-01-01T00:00:00Z",
    "degraded": true
  }
}
```

**Key Insight:** Notice the cached price may differ from the live price (which includes discounts). The response includes `cached_at` to indicate data freshness.

```bash
# Restart the service
docker compose start lab23-pricing
```

---

### Exercise 4: Kill Inventory Service, Compare Degradation

Test what happens when inventory is unavailable.

```bash
# Stop inventory service
docker compose stop lab23-inventory

# Make request
curl -s http://localhost:8000/product/SKU-001 | jq '.data.inventory'
```

**Expected Response:**
```json
{
  "inventory": {
    "in_stock": null,
    "quantity": null,
    "availability_message": "Check availability in store",
    "source": "fallback_check_store",
    "degraded": true
  }
}
```

Now compare with graceful degradation disabled:

```bash
# Disable graceful degradation
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"graceful_degradation_enabled": false, "inventory_fallback_enabled": false}' | jq

# Try to get product - this should fail
curl -s http://localhost:8000/product/SKU-001 | jq
```

With degradation disabled, the entire request fails when a dependency is down.

```bash
# Re-enable graceful degradation
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"graceful_degradation_enabled": true, "inventory_fallback_enabled": true}' | jq

# Restart inventory
docker compose start lab23-inventory
```

---

### Exercise 5: Measure User Experience in Each Mode

Use k6 to measure the user experience under different degradation scenarios.

**Scenario A: All healthy**
```bash
# Run load test with all services up
docker compose run --rm lab23-k6 run /scripts/graceful-degradation.js
```

Note the quality scores and response types.

**Scenario B: Single dependency down**
```bash
# Stop one service and run load test
docker compose stop lab23-pricing
docker compose run --rm lab23-k6 run /scripts/graceful-degradation.js
docker compose start lab23-pricing
```

**Scenario C: Multiple dependencies down**
```bash
# Stop multiple services
docker compose stop lab23-recommendation lab23-pricing

# Run load test
docker compose run --rm lab23-k6 run /scripts/graceful-degradation.js

# Restart services
docker compose start lab23-recommendation lab23-pricing
```

**Compare the Results:**

| Scenario | Error Rate | Avg Quality | Latency p95 |
|----------|------------|-------------|-------------|
| All healthy | ? | 1.0 | ? |
| Pricing down | ? | ~0.67 | ? |
| Both down | ? | ~0.33 | ? |

---

### Exercise 6: Observe Degradation in Grafana

Open Grafana (http://localhost:3001) and explore the "Graceful Degradation Lab" dashboard.

**Key Panels to Watch:**
1. **Median Response Quality** - Gauge showing current quality level
2. **Fallback Usage Rate** - Which fallbacks are being used
3. **Dependency Health** - Real-time status of each dependency
4. **Response Degradation Levels** - Distribution of none/partial/full degradation

**Experiment:**
1. Start with all services healthy
2. Generate load: `docker compose run --rm lab23-k6 run /scripts/graceful-degradation.js`
3. In another terminal, stop services one by one
4. Watch the dashboard update in real-time

---

## Key Takeaways

1. **Graceful degradation preserves availability** - Users get a response even when dependencies fail

2. **Quality indicators matter** - Always communicate when data is degraded so users can make informed decisions

3. **Feature flags give you control** - You can enable/disable degradation per dependency

4. **Fallback data should be meaningful** - Popular items, cached prices, and "check in store" are all useful degraded responses

5. **Monitor your degradation modes** - Track how often you're serving degraded responses

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Fallbacks not working
Check the feature flag configuration:
```bash
curl -s http://localhost:8000/admin/config | jq
```

### Traces not appearing in Jaeger
- Wait 10-15 seconds after making requests
- Check that otel-collector is running: `docker compose logs lab23-otel-collector`

## Related Labs

- [Lab 22: Circuit Breakers](../week-22-circuit-breakers/) - Automatic failure detection
- [Lab 24: Bulkheads](../week-24-bulkheads/) - Isolation between dependencies
