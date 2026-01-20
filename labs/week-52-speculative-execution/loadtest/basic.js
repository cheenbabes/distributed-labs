import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 10 },  // Ramp up to 10 users
    { duration: '1m', target: 10 },   // Stay at 10 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'], // 95% of requests should be < 500ms with hedging
    errors: ['rate<0.1'],             // Error rate should be < 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab52-frontend:8000';

export default function () {
  // Use hedged mode by default
  const mode = __ENV.MODE || 'hedged';
  const res = http.get(`${BASE_URL}/api/process?mode=${mode}`);

  // Record metrics
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response has service': (r) => {
      try {
        return r.json().service === 'frontend';
      } catch (e) {
        return false;
      }
    },
    'response has downstream result': (r) => {
      try {
        return r.json().downstream_result !== undefined;
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
