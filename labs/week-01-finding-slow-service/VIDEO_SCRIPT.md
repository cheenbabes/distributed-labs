# Video Script: Finding the Slow Service

**Episode:** Week 1
**Duration:** 15-18 minutes
**Goal:** Hook viewers with a practical debugging scenario, introduce the lab format

---

## Video Flow

### 0:00 - 0:30 | Hook (30 sec)

> "Your distributed system is slow. The gateway is timing out, users are complaining, and your dashboard shows everything is... fine? Which of these 5 services is the problem?"

**Visual:** Show a Grafana dashboard with everything green, but a curl command showing 2-second response times.

**Tension:** The observability gap - you can't debug what you can't see.

---

### 0:30 - 2:00 | Problem Setup (90 sec)

> "This is the scenario every engineer faces eventually. You have multiple services calling each other, latency is high, but where is it coming from?"

**Show architecture diagram:**
- Gateway → Service A → Service B → Service C → Service D
- Explain: "Each service does some work, calls the next one, and returns"

> "Without the right tools, you're reduced to guessing. Add logging everywhere? Check each service manually? That doesn't scale."

**Transition:** "Today we're going to solve this with distributed tracing."

---

### 2:00 - 4:00 | Lab Introduction (2 min)

> "I've built a lab you can run yourself. Five Python services, full observability stack - Jaeger for traces, Prometheus for metrics, Grafana for dashboards."

**Show docker-compose briefly:**
- Point out the service definitions
- Highlight the OTEL configuration
- "All of this runs on your laptop"

**Start the lab:**
```bash
docker compose up --build -d
```

**While building:** "Each service adds random latency between 10-50ms to simulate real work."

---

### 4:00 - 6:00 | Baseline (2 min)

> "First, let's see what 'normal' looks like."

**Make a request:**
```bash
curl -s http://localhost:8000/api/process | jq
```

**Show response:** Total time ~150ms

> "That's our baseline. Now let's look at where that time goes."

**Open Jaeger (http://localhost:16686):**
- Select "gateway" service
- Find trace
- Show the waterfall view

> "This is the magic. Every service, every call, every millisecond - all in one view."

**Walk through the trace:**
- Point to gateway span (top)
- Show it calling service-a
- Follow the chain down to service-d
- "You can see exactly how long each service took"

---

### 6:00 - 8:00 | Inject the Problem (2 min)

> "Now let's break something. I'm going to make one of these services slow, and we'll find it using only the traces."

**Inject latency (don't show which service on screen initially):**
```bash
curl -X POST http://localhost:8004/admin/latency \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "ms": 500}'
```

> "I've added 500ms of latency somewhere in the chain. Let's see the impact."

**Make request:**
```bash
curl -s -o /dev/null -w "Total time: %{time_total}s\n" http://localhost:8000/api/process
```

**Show:** ~650ms now vs ~150ms before

> "Latency more than quadrupled. But where?"

---

### 8:00 - 11:00 | Find the Bottleneck (3 min)

> "Let's find it. This is exactly what you'd do in production."

**Open Jaeger:**
- Find a new trace
- Show the waterfall

> "Look at this. The answer is immediately obvious."

**Point to the long span:**
- "Service D. Look at this bar - it's taking 520ms while everything else is under 50ms."
- "Without distributed tracing, I might have blamed Service A, since it's earlier in the chain."

**Click into the span:**
- Show span details
- Point to duration
- Show any custom attributes

> "This is the power of tracing. In production, this could be a slow database query, a cold cache, an external API timing out."

---

### 11:00 - 13:00 | Deeper Analysis (2 min)

> "But let's go deeper. What can we learn from the data?"

**Compare traces:**
- Pull up a pre-injection trace
- Show side by side

> "Before: total 150ms, evenly distributed. After: 650ms, 80% of that in one service."

**Show Grafana dashboard:**
- Request latency percentiles
- P50 vs P95 vs P99

> "Watch the percentiles. P50 went from 140ms to 620ms. In production, you'd set an alert on this."

---

### 13:00 - 15:00 | The Fix (2 min)

> "Now that we know Service D is the problem, we can fix it."

**Disable the injected latency:**
```bash
curl -X POST http://localhost:8004/admin/latency \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

**Verify:**
```bash
curl -s -o /dev/null -w "Total time: %{time_total}s\n" http://localhost:8000/api/process
```

> "Back to 150ms. Problem solved."

---

### 15:00 - 17:00 | Key Takeaways (2 min)

> "So what did we learn?"

**Takeaway 1:** "Distributed tracing is non-negotiable. Without it, you're debugging blind."

**Takeaway 2:** "Latency compounds. 500ms in a leaf service is 500ms everywhere above it."

**Takeaway 3:** "The waterfall view tells the story instantly. Train yourself to read it."

**Takeaway 4:** "Set up tracing before you need it. When things break, it's too late to add instrumentation."

---

### 17:00 - 18:00 | Call to Action (1 min)

> "The lab is linked below. Clone it, run it, break it. Inject latency into different services and find it."

> "Next week: What happens when your cache dies. We're adding Redis, generating load, and then... pulling the plug. Subscribe so you don't miss it."

**End card:** Lab link, subscribe, next video teaser

---

## B-Roll / Visuals Needed

1. Architecture diagram (can be simple boxes and arrows)
2. Terminal with curl commands
3. Jaeger UI - trace list view
4. Jaeger UI - waterfall view (this is the money shot)
5. Grafana dashboard with latency graphs
6. Split screen: before/after traces

## Commands to Practice

Run these before recording to ensure smooth delivery:

```bash
# Start lab
docker compose up --build -d

# Wait for healthy
docker compose ps

# Baseline request
curl -s http://localhost:8000/api/process | jq

# Inject latency
curl -X POST http://localhost:8004/admin/latency \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "ms": 500}'

# Test slow request
curl -s -o /dev/null -w "%{time_total}s\n" http://localhost:8000/api/process

# Fix it
curl -X POST http://localhost:8004/admin/latency \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Cleanup
docker compose down -v
```

## Notes for Recording

- Keep Jaeger open before recording, pre-load with some traces
- Have terminal font size increased for visibility
- Consider picture-in-picture for face cam during Jaeger walkthrough
- The "reveal" moment (finding service-d) should feel satisfying
