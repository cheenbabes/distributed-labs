# Demo Runbook: Blue-Green Deployments

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

### Make scripts executable

```bash {"name": "make-scripts-executable"}
chmod +x scripts/*.sh
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
echo "=== Blue Environment ==="
curl -s http://localhost:8081/version | jq

echo ""
echo "=== Green Environment ==="
curl -s http://localhost:8082/version | jq

echo ""
echo "=== Router (current active) ==="
curl -s http://localhost:8080/version | jq
```

---

## Part 2: Understand Current State

### Check which environment is active

```bash {"name": "check-active"}
echo "Current active environment:"
curl -s http://localhost:8080/version | jq '{color, version, service}'
```

### Show response headers

```bash {"name": "show-headers"}
echo "Response headers show which environment served the request:"
curl -si http://localhost:8080/api/data 2>/dev/null | grep -E "^X-"
```

### Check health of both environments

```bash {"name": "check-health"}
./scripts/check-health.sh
```

---

## Part 3: Show Both Versions Coexisting

### Make requests to both environments directly

```bash {"name": "compare-versions"}
echo "=== Blue Environment (v1) ==="
curl -s http://localhost:8081/api/data | jq '{version, color, data: .data[0]}'

echo ""
echo "=== Green Environment (v2) ==="
curl -s http://localhost:8082/api/data | jq '{version, color, data: .data[0]}'

echo ""
echo "Note: v2 has additional 'updated_at' and 'version_added' fields"
```

---

## Part 4: Open Observability UIs

### Print UI URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Router:     http://localhost:8080"
echo "  Blue:       http://localhost:8081"
echo "  Green:      http://localhost:8082"
echo "  Jaeger:     http://localhost:16686"
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Prometheus: http://localhost:9090"
```

---

## Part 5: Switch Traffic to Green

### Verify green health before switching

```bash {"name": "verify-green-health"}
echo "Verifying green environment health..."
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8082/health)
if [ "$HEALTH" == "200" ]; then
  echo "Green is healthy (HTTP 200)"
else
  echo "WARNING: Green returned HTTP $HEALTH"
fi
```

### Switch traffic to green

```bash {"name": "switch-to-green"}
echo "Switching traffic to green..."

# Update configuration
cp infra/nginx/conf.d/green.conf infra/nginx/conf.d/active.conf

# Reload nginx (zero-downtime)
docker exec lab10-nginx nginx -s reload

echo "Switch complete!"
```

### Verify traffic is now going to green

```bash {"name": "verify-green-traffic"}
echo "Verifying all traffic goes to green:"
for i in {1..5}; do
  color=$(curl -s http://localhost:8080/version | jq -r '.color')
  version=$(curl -s http://localhost:8080/version | jq -r '.version')
  echo "Request $i: $color ($version)"
done
```

---

## Part 6: Simulate Bug and Rollback

### Inject bug into green environment

```bash {"name": "inject-bug"}
echo "Injecting bug into green environment..."
curl -s -X POST "http://localhost:8082/admin/bug?enabled=true&error_rate=0.5" | jq
echo ""
echo "50% of requests will now fail"
```

### Show requests failing

```bash {"name": "show-failures"}
echo "Making 10 requests to see failures:"
for i in {1..10}; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/data)
  if [ "$status" == "200" ]; then
    echo "Request $i: HTTP $status (success)"
  else
    echo "Request $i: HTTP $status (FAILED)"
  fi
done
```

### Instant rollback to blue

```bash {"name": "rollback-to-blue"}
echo "ROLLBACK: Switching back to blue..."

# Update configuration
cp infra/nginx/conf.d/blue.conf infra/nginx/conf.d/active.conf

# Reload nginx
docker exec lab10-nginx nginx -s reload

echo "Rollback complete!"
```

### Verify rollback success

```bash {"name": "verify-rollback"}
echo "Verifying all requests succeed after rollback:"
for i in {1..10}; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/data)
  color=$(curl -s http://localhost:8080/version | jq -r '.color')
  echo "Request $i: HTTP $status (served by $color)"
done
```

### Fix the green environment

```bash {"name": "fix-green"}
echo "Disabling bug in green environment..."
curl -s -X POST "http://localhost:8082/admin/bug?enabled=false" | jq
```

---

## Part 7: Zero-Downtime Test

### Start load test (run in background)

```bash {"name": "start-load-test", "background": true}
docker compose run --rm k6 run /scripts/zero-downtime.js
```

### Wait for load test to stabilize, then switch

```bash {"name": "switch-during-load"}
echo "Waiting 30 seconds for load to stabilize..."
sleep 30

echo "Switching to green..."
cp infra/nginx/conf.d/green.conf infra/nginx/conf.d/active.conf
docker exec lab10-nginx nginx -s reload

echo "Waiting 30 seconds..."
sleep 30

echo "Switching back to blue..."
cp infra/nginx/conf.d/blue.conf infra/nginx/conf.d/active.conf
docker exec lab10-nginx nginx -s reload

echo "Switches complete - check load test results"
```

---

## Part 8: Generate Traces for Observation

### Generate traffic for Jaeger

```bash {"name": "generate-traces"}
echo "Generating traces for both environments..."

# Send to blue
echo "Sending 20 requests to blue..."
for i in {1..20}; do
  curl -s http://localhost:8081/api/data > /dev/null
  echo -n "."
done
echo ""

# Send to green
echo "Sending 20 requests to green..."
for i in {1..20}; do
  curl -s http://localhost:8082/api/data > /dev/null
  echo -n "."
done
echo ""

echo "Done! Check Jaeger: http://localhost:16686"
echo "Search for services: app-blue and app-green"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up"
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Switch to blue | `cp infra/nginx/conf.d/blue.conf infra/nginx/conf.d/active.conf && docker exec lab10-nginx nginx -s reload` |
| Switch to green | `cp infra/nginx/conf.d/green.conf infra/nginx/conf.d/active.conf && docker exec lab10-nginx nginx -s reload` |
| Check active | `curl -s http://localhost:8080/version \| jq '.color'` |
| Check health | `./scripts/check-health.sh` |
| Inject bug | `curl -X POST "http://localhost:8082/admin/bug?enabled=true"` |
| Fix bug | `curl -X POST "http://localhost:8082/admin/bug?enabled=false"` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |

---

## Troubleshooting Commands

### Check service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check nginx logs

```bash {"name": "logs-nginx"}
docker compose logs nginx --tail=50
```

### Check app logs

```bash {"name": "logs-apps"}
echo "=== Blue Logs ==="
docker compose logs app-blue --tail=20
echo ""
echo "=== Green Logs ==="
docker compose logs app-green --tail=20
```

### Test nginx configuration

```bash {"name": "test-nginx-config"}
docker exec lab10-nginx nginx -t
```

### Check current nginx active config

```bash {"name": "check-active-config"}
cat infra/nginx/conf.d/active.conf
```

### Resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```
