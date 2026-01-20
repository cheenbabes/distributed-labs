import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const messagesProduced = new Counter('messages_produced');
const productionLatency = new Trend('production_latency');

// Test configuration - steady state production
export const options = {
  stages: [
    { duration: '30s', target: 5 },   // Ramp up to 5 VUs
    { duration: '3m', target: 5 },    // Stay at 5 VUs
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
    errors: ['rate<0.05'],
  },
};

const PRODUCER_URL = __ENV.PRODUCER_URL || 'http://producer:8000';

export default function () {
  const res = http.post(`${PRODUCER_URL}/produce`);

  productionLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  if (res.status === 200) {
    messagesProduced.add(1);
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
    'message produced': (r) => {
      try {
        return r.json().success === true;
      } catch {
        return false;
      }
    },
  });

  // Small delay between requests - produces ~10 msg/sec per VU
  sleep(0.1);
}

export function handleSummary(data) {
  const summary = {
    total_messages: data.metrics.messages_produced ? data.metrics.messages_produced.values.count : 0,
    avg_latency_ms: data.metrics.production_latency ? data.metrics.production_latency.values.avg : 0,
    p95_latency_ms: data.metrics.production_latency ? data.metrics.production_latency.values['p(95)'] : 0,
    error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
  };

  console.log('\n=== Production Summary ===');
  console.log(`Total messages produced: ${summary.total_messages}`);
  console.log(`Avg latency: ${summary.avg_latency_ms.toFixed(2)}ms`);
  console.log(`P95 latency: ${summary.p95_latency_ms.toFixed(2)}ms`);
  console.log(`Error rate: ${(summary.error_rate * 100).toFixed(2)}%`);

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
