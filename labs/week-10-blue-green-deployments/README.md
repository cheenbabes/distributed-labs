# Lab 10: Blue-Green Deployments

Achieve zero-downtime deployments using the blue-green pattern. In this lab, you'll run two identical environments side-by-side and learn how to switch traffic between them instantly, enabling safe deployments and instant rollbacks.

## What You'll Learn

- How blue-green deployments enable zero-downtime releases
- How to use nginx as a traffic router between environments
- How to verify new versions before switching traffic
- How to perform instant rollbacks when issues are detected
- How to observe deployment switches in distributed traces

## Architecture

```
                    ┌──────────────┐
     Requests ──────▶    nginx     │
                    │  (router)    │
                    └──────┬───────┘
                           │
              ┌────────────┴────────────┐
              │                         │
       ┌──────┴──────┐          ┌───────┴─────┐
       │  app-blue   │          │  app-green  │
       │    (v1)     │          │    (v2)     │
       └─────────────┘          └─────────────┘
```

Both blue and green environments run identical application code with different version configurations. The nginx router directs all traffic to one environment at a time, and switching is instantaneous via configuration reload.

## Key Concepts

### Blue-Green Deployment Pattern

1. **Two Identical Environments**: Blue and green run the same infrastructure but different versions
2. **Traffic Routing**: A load balancer (nginx) routes all traffic to one environment
3. **Pre-deployment Verification**: Test the inactive environment before switching
4. **Instant Switchover**: Change routing in milliseconds without dropping connections
5. **Instant Rollback**: Switch back immediately if issues are detected

### Why Blue-Green?

- **Zero Downtime**: Users never experience an outage during deployment
- **Safe Deployments**: Verify new version before it receives production traffic
- **Instant Rollback**: Return to previous version in seconds, not minutes
- **Reduced Risk**: Full environment available for rollback at all times

## Prerequisites

- Docker and Docker Compose
- curl (for testing endpoints)
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
| Router (nginx) | http://localhost:8080 | Traffic entry point |
| Blue Direct | http://localhost:8081 | Direct access to blue |
| Green Direct | http://localhost:8082 | Direct access to green |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Observe Current Deployment State

First, understand the current state of the blue-green setup.

```bash
# Check which environment is currently active
curl -s http://localhost:8080/version | jq

# The response shows version, color, and other metadata
```

Check both environments directly:

```bash
# Check blue (direct access)
curl -s http://localhost:8081/version | jq

# Check green (direct access)
curl -s http://localhost:8082/version | jq
```

**Observe in Response Headers:**
```bash
# Make a request and show headers
curl -si http://localhost:8080/api/data | head -20
```

Look for:
- `X-App-Version`: The version running (v1 or v2)
- `X-App-Color`: Which environment served the request (blue or green)
- `X-Served-By`: Combined identifier

**Questions:**
- Which environment is currently receiving traffic?
- What version is running in each environment?

---

### Exercise 2: Verify Health Before Switching

Before switching traffic, always verify the target environment is healthy.

```bash
# Check health of blue
curl -s http://localhost:8081/health | jq

# Check health of green
curl -s http://localhost:8082/health | jq

# Or use the helper script
./scripts/check-health.sh
```

Make some test requests directly to green (the inactive environment):

```bash
# Test green environment directly
for i in {1..5}; do
  echo "Request $i:"
  curl -s http://localhost:8082/api/data | jq '.version, .color'
  echo ""
done
```

**What to verify:**
- Health endpoint returns 200
- API endpoints respond correctly
- Response data looks valid
- Latency is acceptable

---

### Exercise 3: Switch Traffic to Green

Now perform a traffic switch to move all traffic from blue to green.

**Method 1: Using the script**
```bash
./scripts/switch-to-green.sh
```

**Method 2: Manual steps**
```bash
# 1. Update nginx configuration
cp infra/nginx/conf.d/green.conf infra/nginx/conf.d/active.conf

# 2. Reload nginx (no restart needed!)
docker exec lab10-nginx nginx -s reload

# 3. Verify the switch
curl -s http://localhost:8080/version | jq
```

Make several requests to confirm:
```bash
for i in {1..10}; do
  color=$(curl -s http://localhost:8080/version | jq -r '.color')
  echo "Request $i: served by $color"
done
```

**Expected:** All requests should now be served by green.

Open Jaeger (http://localhost:16686) and observe:
1. Select service "app-green"
2. View traces to see the new v2 version serving traffic
3. Notice the `deployment.color` attribute in spans

---

### Exercise 4: Simulate a Bug and Rollback

Let's simulate discovering a bug in the new version and perform an instant rollback.

```bash
# Inject a bug into the green environment
# This will cause 50% of requests to fail
curl -X POST "http://localhost:8082/admin/bug?enabled=true&error_rate=0.5"
```

Now observe the failures:
```bash
# Make 10 requests and watch for errors
for i in {1..10}; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/data)
  echo "Request $i: HTTP $status"
done
```

You should see approximately half the requests failing with HTTP 500.

**Perform Instant Rollback:**
```bash
# Switch back to blue immediately
./scripts/switch-to-blue.sh
```

Or manually:
```bash
cp infra/nginx/conf.d/blue.conf infra/nginx/conf.d/active.conf
docker exec lab10-nginx nginx -s reload
```

Verify the rollback:
```bash
# All requests should succeed now
for i in {1..10}; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/data)
  echo "Request $i: HTTP $status"
done
```

**Key Insight:** The rollback took less than a second. In a traditional deployment, you would need to redeploy the old version, which could take minutes.

Don't forget to fix green for later:
```bash
curl -X POST "http://localhost:8082/admin/bug?enabled=false"
```

---

### Exercise 5: Measure Zero-Downtime

Run a load test while switching traffic to verify zero-downtime.

**Terminal 1: Start load test**
```bash
docker compose run --rm k6 run /scripts/zero-downtime.js
```

**Terminal 2: Switch traffic during the test**
Wait 30 seconds for the load test to stabilize, then:

```bash
# Switch to green
./scripts/switch-to-green.sh

# Wait 30 seconds
sleep 30

# Switch back to blue
./scripts/switch-to-blue.sh
```

**Expected Results:**
- The load test should show <0.1% error rate
- No requests should fail during the switch
- Latency should remain stable

Check the Grafana dashboard (http://localhost:3001) during the test to see:
- Traffic distribution between blue and green
- The moment traffic switches
- Error rates during transition

---

## Advanced Exercises

### Exercise 6: Traffic Split (Canary-like)

While true blue-green is all-or-nothing, you can temporarily test with a traffic split:

```bash
# Access blue and green through nginx proxy paths
# Blue via /blue/
curl -s http://localhost:8080/blue/version | jq

# Green via /green/
curl -s http://localhost:8080/green/version | jq
```

This lets you manually test both versions through the router.

### Exercise 7: Observe in Traces

1. Generate traffic:
```bash
for i in {1..50}; do
  curl -s http://localhost:8080/api/data > /dev/null
done
```

2. Open Jaeger (http://localhost:16686)

3. Search for traces from "app-blue" and "app-green"

4. Compare traces:
   - Look at `app.version` attribute
   - Look at `deployment.color` attribute
   - Notice how version is captured in every span

---

## Key Takeaways

1. **Zero Downtime is Achievable**: nginx reload doesn't drop connections

2. **Pre-verification is Critical**: Always test the inactive environment before switching

3. **Instant Rollback Reduces Risk**: Having the old version ready means quick recovery

4. **Observability Shows Deployment State**: Traces and metrics clearly show which version is serving traffic

5. **Cost of Blue-Green**: You need 2x infrastructure, but the safety benefits often outweigh the cost

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Traffic not switching
```bash
# Verify active.conf was updated
cat infra/nginx/conf.d/active.conf

# Check nginx status
docker exec lab10-nginx nginx -t

# Force reload
docker exec lab10-nginx nginx -s reload
```

### Services not starting
```bash
docker compose logs app-blue
docker compose logs app-green
docker compose logs nginx
```

### Health checks failing
```bash
# Check individual service health
curl -v http://localhost:8081/health
curl -v http://localhost:8082/health
```

## Next Lab

[Lab 11: Rate Limiting](../week-11-rate-limiting/) - Protect your services from overload with distributed rate limiting.
