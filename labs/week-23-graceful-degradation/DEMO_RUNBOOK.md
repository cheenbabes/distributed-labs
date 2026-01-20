# Demo Runbook: Graceful Degradation

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
docker system prune -f
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

### Verify all services are responding

```bash {"name": "verify-services"}
echo "Product Catalog:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Recommendation:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Pricing:"
curl -s http://localhost:8002/health | jq
echo ""
echo "Inventory:"
curl -s http://localhost:8003/health | jq
```

---

## Part 2: Show URLs

### Print all URLs for reference

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Product Catalog API: http://localhost:8000"
echo "  Recommendation API:  http://localhost:8001"
echo "  Pricing API:         http://localhost:8002"
echo "  Inventory API:       http://localhost:8003"
echo "  Jaeger:              http://localhost:16686"
echo "  Grafana:             http://localhost:3001 (admin/admin)"
echo "  Prometheus:          http://localhost:9090"
```

---

## Part 3: Baseline - All Services Healthy

### Make a single request and show full response

```bash {"name": "baseline-request"}
echo "=== Full Product Response ==="
curl -s http://localhost:8000/product/SKU-001 | jq
```

### Show quality score

```bash {"name": "baseline-quality"}
echo "=== Quality Score (should be 1.0) ==="
curl -s http://localhost:8000/product/SKU-001 | jq '.quality'
```

### Show data sources

```bash {"name": "baseline-sources"}
echo "=== Data Sources (all should be 'live') ==="
curl -s http://localhost:8000/product/SKU-001 | jq '{
  recommendations: .data.recommendations.source,
  pricing: .data.pricing.source,
  inventory: .data.inventory.source
}'
```

### Show baseline timing

```bash {"name": "baseline-timing"}
echo "Baseline timing (5 requests):"
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/product/SKU-001)
  echo "Request $i: ${time}s"
done
```

---

## Part 4: Kill Recommendation Service

### Stop recommendation service

```bash {"name": "kill-recommendation"}
docker compose stop lab23-recommendation
echo "Recommendation service stopped"
```

### Show fallback in action

```bash {"name": "show-recommendation-fallback"}
echo "=== Quality with recommendation down ==="
curl -s http://localhost:8000/product/SKU-001 | jq '.quality'
echo ""
echo "=== Recommendation data (notice 'fallback_popular_items') ==="
curl -s http://localhost:8000/product/SKU-001 | jq '.data.recommendations'
```

### Compare personalized vs popular items

```bash {"name": "compare-recommendations"}
echo "Fallback returns generic popular items instead of personalized:"
curl -s http://localhost:8000/product/SKU-001 | jq '.data.recommendations.recommendations'
```

### Restart recommendation service

```bash {"name": "restart-recommendation"}
docker compose start lab23-recommendation
echo "Recommendation service restarted"
sleep 3
```

---

## Part 5: Kill Pricing Service

### Stop pricing service

```bash {"name": "kill-pricing"}
docker compose stop lab23-pricing
echo "Pricing service stopped"
```

### Show cached price fallback

```bash {"name": "show-pricing-fallback"}
echo "=== Quality with pricing down ==="
curl -s http://localhost:8000/product/SKU-001 | jq '.quality'
echo ""
echo "=== Pricing data (notice 'fallback_cached' and 'cached_at') ==="
curl -s http://localhost:8000/product/SKU-001 | jq '.data.pricing'
```

### Highlight cached_at timestamp

```bash {"name": "highlight-cache-date"}
echo "Notice the cached_at timestamp - this is stale data:"
curl -s http://localhost:8000/product/SKU-001 | jq '.data.pricing.cached_at'
```

### Restart pricing service

```bash {"name": "restart-pricing"}
docker compose start lab23-pricing
echo "Pricing service restarted"
sleep 3
```

---

## Part 6: Kill Inventory Service

### Stop inventory service

```bash {"name": "kill-inventory"}
docker compose stop lab23-inventory
echo "Inventory service stopped"
```

### Show check-in-store fallback

```bash {"name": "show-inventory-fallback"}
echo "=== Quality with inventory down ==="
curl -s http://localhost:8000/product/SKU-001 | jq '.quality'
echo ""
echo "=== Inventory data (notice 'availability_message') ==="
curl -s http://localhost:8000/product/SKU-001 | jq '.data.inventory'
```

### Show availability message

```bash {"name": "show-availability-message"}
echo "The user sees this helpful message instead of an error:"
curl -s http://localhost:8000/product/SKU-001 | jq '.data.inventory.availability_message'
```

---

## Part 7: Compare With/Without Graceful Degradation

### Disable graceful degradation

```bash {"name": "disable-degradation"}
echo "Disabling graceful degradation..."
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"graceful_degradation_enabled": false, "recommendation_fallback_enabled": false, "pricing_fallback_enabled": false, "inventory_fallback_enabled": false}' | jq
```

### Show request fails without degradation

```bash {"name": "show-failure"}
echo "With inventory still down and degradation disabled, request fails:"
curl -s http://localhost:8000/product/SKU-001 | jq
```

### Re-enable graceful degradation

```bash {"name": "enable-degradation"}
echo "Re-enabling graceful degradation..."
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"graceful_degradation_enabled": true, "recommendation_fallback_enabled": true, "pricing_fallback_enabled": true, "inventory_fallback_enabled": true}' | jq
```

### Show request succeeds with degradation

```bash {"name": "show-success-degraded"}
echo "With degradation enabled, request succeeds with fallback:"
curl -s http://localhost:8000/product/SKU-001 | jq '.quality'
```

### Restart inventory service

```bash {"name": "restart-inventory"}
docker compose start lab23-inventory
echo "Inventory service restarted"
sleep 3
```

---

## Part 8: Multiple Dependencies Down

### Stop multiple services

```bash {"name": "kill-multiple"}
docker compose stop lab23-recommendation lab23-pricing
echo "Recommendation and Pricing services stopped"
```

### Show heavily degraded response

```bash {"name": "show-heavy-degradation"}
echo "=== Quality with 2 services down (score ~0.33) ==="
curl -s http://localhost:8000/product/SKU-001 | jq '.quality'
echo ""
echo "=== All data sources ==="
curl -s http://localhost:8000/product/SKU-001 | jq '{
  recommendations: .data.recommendations.source,
  pricing: .data.pricing.source,
  inventory: .data.inventory.source
}'
```

### Restart all services

```bash {"name": "restart-all"}
docker compose start lab23-recommendation lab23-pricing
echo "All services restarted"
sleep 3
```

---

## Part 9: Failure Injection via API

### Enable failure injection on recommendation service

```bash {"name": "inject-recommendation-failure"}
echo "Enabling failure injection on recommendation service..."
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "failure_rate": 1.0, "failure_type": "error"}' | jq
```

### Show fallback triggers from API failure

```bash {"name": "show-api-failure-fallback"}
echo "=== Request still succeeds with fallback ==="
curl -s http://localhost:8000/product/SKU-001 | jq '.quality'
```

### Disable failure injection

```bash {"name": "disable-failure-injection"}
echo "Disabling failure injection..."
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq
```

---

## Part 10: Load Testing

### Run load test with all healthy

```bash {"name": "load-test-healthy"}
echo "Running load test with all services healthy..."
docker compose run --rm lab23-k6 run --duration 30s /scripts/graceful-degradation.js
```

### Run load test with one service down

```bash {"name": "load-test-degraded"}
echo "Stopping pricing service and running load test..."
docker compose stop lab23-pricing
docker compose run --rm lab23-k6 run --duration 30s /scripts/graceful-degradation.js
docker compose start lab23-pricing
```

---

## Part 11: Generate Traces

### Generate traces for Jaeger analysis

```bash {"name": "generate-traces"}
echo "Generating 20 traces for analysis..."
for i in {1..20}; do
  curl -s http://localhost:8000/product/SKU-00$((i % 3 + 1)) > /dev/null
  echo -n "."
done
echo ""
echo "Done. Check Jaeger: http://localhost:16686"
echo "Select 'product-catalog' service to see traces"
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

### Check service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check specific service logs

```bash {"name": "logs-product-catalog"}
docker compose logs lab23-product-catalog --tail=50
```

### Check recommendation service logs

```bash {"name": "logs-recommendation"}
docker compose logs lab23-recommendation --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Check current degradation config

```bash {"name": "check-config"}
curl -s http://localhost:8000/admin/config | jq
```

### Check failure injection status

```bash {"name": "check-failures"}
echo "Recommendation:"
curl -s http://localhost:8001/admin/failure | jq
echo ""
echo "Pricing:"
curl -s http://localhost:8002/admin/failure | jq
echo ""
echo "Inventory:"
curl -s http://localhost:8003/admin/failure | jq
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Stop a service | `docker compose stop lab23-<service>` |
| Start a service | `docker compose start lab23-<service>` |
| Inject failure | `curl -X POST localhost:800X/admin/failure -d '{"enabled":true}'` |
| Remove failure | `curl -X POST localhost:800X/admin/failure -d '{"enabled":false}'` |
| Check quality | `curl localhost:8000/product/SKU-001 \| jq '.quality'` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
