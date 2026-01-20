import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const budgetExhaustedCount = new Counter('budget_exhausted');
const successCount = new Counter('success_requests');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 10 },  // Ramp up to 10 users
    { duration: '1m', target: 10 },   // Stay at 10 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000'], // 95% of requests should be < 2s
    errors: ['rate<0.5'],              // Error rate should be < 50% (budgets may exhaust)
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://frontend-api:8000';

export default function () {
  // Use default budget from server config
  const res = http.get(`${BASE_URL}/api/process`);

  // Record metrics
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  // Track budget exhaustion vs success
  if (res.status === 200) {
    successCount.add(1);
  } else if (res.status === 504) {
    budgetExhaustedCount.add(1);
  }

  // Validate response
  check(res, {
    'status is 200 or 504': (r) => r.status === 200 || r.status === 504,
    'response has service': (r) => {
      try {
        const body = r.json();
        return body.service === 'frontend-api' || (body.detail && body.detail.service);
      } catch (e) {
        return false;
      }
    },
  });

  // Small pause between requests
  sleep(0.5);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
