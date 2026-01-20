import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const webhookLatency = new Trend('webhook_queue_latency');
const circuitOpenEvents = new Counter('circuit_open_events');

// Test configuration - hammer the dead endpoint to trigger circuit breaker
export const options = {
  stages: [
    { duration: '10s', target: 3 },   // Ramp up
    { duration: '30s', target: 3 },   // Trigger circuit breaker
    { duration: '40s', target: 3 },   // Watch circuit behavior
    { duration: '10s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<1000'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab33-webhook-sender:8000';
const DEAD_ENDPOINT = 'http://lab33-receiver-dead:8003/webhook';

function generatePayload() {
  return {
    event_id: `evt_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
    timestamp: new Date().toISOString(),
    data: {
      test: 'circuit_breaker',
      iteration: __VU * 1000 + __ITER
    }
  };
}

export default function () {
  const webhookRequest = {
    endpoint: DEAD_ENDPOINT,
    event_type: 'test.circuit_breaker',
    payload: generatePayload()
  };

  const res = http.post(`${BASE_URL}/webhooks`, JSON.stringify(webhookRequest), {
    headers: { 'Content-Type': 'application/json' }
  });

  webhookLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  check(res, {
    'status is 200': (r) => r.status === 200
  });

  // Check circuit breaker status periodically
  if (__ITER % 10 === 0) {
    const cbRes = http.get(`${BASE_URL}/circuit-breakers`);
    if (cbRes.status === 200) {
      try {
        const cbData = cbRes.json();
        const deadCb = cbData.circuit_breakers[DEAD_ENDPOINT];
        if (deadCb && deadCb.state === 'open') {
          circuitOpenEvents.add(1);
          console.log(`Circuit breaker OPEN for dead endpoint!`);
        }
      } catch (e) {
        // Ignore parse errors
      }
    }
  }

  sleep(0.5);
}

export function handleSummary(data) {
  console.log('\n========================================');
  console.log('Circuit Breaker Test Summary');
  console.log('========================================');
  console.log(`Circuit open events detected: ${data.metrics.circuit_open_events ? data.metrics.circuit_open_events.values.count : 0}`);
  console.log('========================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
