import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter, Gauge } from 'k6/metrics';

// Custom metrics for steady state monitoring
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const latencyP95 = new Gauge('latency_p95');
const healthyServices = new Gauge('healthy_services');

// Test configuration - continuous steady state monitoring
export const options = {
  scenarios: {
    steady_state: {
      executor: 'constant-arrival-rate',
      rate: 2,              // 2 requests per second
      timeUnit: '1s',
      duration: '5m',       // Run for 5 minutes
      preAllocatedVUs: 5,
      maxVUs: 10,
    },
  },
  thresholds: {
    // Steady state thresholds - these define "normal"
    http_req_duration: ['p(95)<300'],   // 95% of requests should be < 300ms
    errors: ['rate<0.02'],               // Error rate should be < 2%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab28-gateway:8000';

// Track latency history for rolling P95 calculation
let latencyHistory = [];
const HISTORY_SIZE = 100;

function updateLatencyP95(latency) {
  latencyHistory.push(latency);
  if (latencyHistory.length > HISTORY_SIZE) {
    latencyHistory.shift();
  }

  if (latencyHistory.length >= 10) {
    const sorted = [...latencyHistory].sort((a, b) => a - b);
    const p95Index = Math.floor(sorted.length * 0.95);
    latencyP95.add(sorted[p95Index]);
  }
}

export default function () {
  // Health check first
  const healthRes = http.get(`${BASE_URL}/api/health-check`);

  const healthSuccess = check(healthRes, {
    'health check status is 200': (r) => r.status === 200,
    'all services healthy': (r) => {
      try {
        const body = r.json();
        return body.status === 'healthy';
      } catch {
        return false;
      }
    },
  });

  if (healthSuccess) {
    try {
      const healthData = healthRes.json();
      const serviceCount = Object.values(healthData.downstream_services || {})
        .filter(s => s === 'healthy').length;
      healthyServices.add(serviceCount);
    } catch {
      healthyServices.add(0);
    }
  }

  // Make an order request
  const orderPayload = JSON.stringify({
    items: [
      { product_id: 'product-001', quantity: 1 },
    ],
    customer_id: `steady-state-${__VU}`
  });

  const startTime = Date.now();
  const orderRes = http.post(`${BASE_URL}/api/orders`, orderPayload, {
    headers: { 'Content-Type': 'application/json' },
    timeout: '5s',
  });
  const duration = Date.now() - startTime;

  // Record metrics
  latencyTrend.add(duration);
  errorRate.add(orderRes.status !== 200);
  updateLatencyP95(duration);

  // Validate steady state
  const steadyStateValid = check(orderRes, {
    'order status is 200': (r) => r.status === 200,
    'latency under 300ms': (r) => r.timings.duration < 300,
    'response has required fields': (r) => {
      try {
        const body = r.json();
        return body.trace_id && body.order;
      } catch {
        return false;
      }
    },
  });

  // Log violations
  if (!steadyStateValid && __ITER % 5 === 0) {
    console.log(`[STEADY STATE VIOLATION] status=${orderRes.status}, latency=${duration}ms`);
  }

  sleep(0.3);
}

export function handleSummary(data) {
  console.log('\n========== STEADY STATE SUMMARY ==========');
  console.log(`Total requests: ${data.metrics.http_reqs.values.count}`);
  console.log(`Error rate: ${(data.metrics.errors.values.rate * 100).toFixed(2)}%`);
  console.log(`P50 latency: ${data.metrics.http_req_duration.values['p(50)'].toFixed(2)}ms`);
  console.log(`P95 latency: ${data.metrics.http_req_duration.values['p(95)'].toFixed(2)}ms`);
  console.log(`P99 latency: ${data.metrics.http_req_duration.values['p(99)'].toFixed(2)}ms`);

  // Determine if steady state was maintained
  const p95 = data.metrics.http_req_duration.values['p(95)'];
  const errRate = data.metrics.errors.values.rate;

  if (p95 < 300 && errRate < 0.02) {
    console.log('\n[OK] Steady state maintained throughout the test');
  } else {
    console.log('\n[WARNING] Steady state violations detected:');
    if (p95 >= 300) console.log(`  - P95 latency ${p95.toFixed(2)}ms exceeds 300ms threshold`);
    if (errRate >= 0.02) console.log(`  - Error rate ${(errRate * 100).toFixed(2)}% exceeds 2% threshold`);
  }
  console.log('==========================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
