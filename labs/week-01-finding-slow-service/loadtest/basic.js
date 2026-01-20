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
    http_req_duration: ['p(95)<2000'], // 95% of requests should be < 2s
    errors: ['rate<0.1'],              // Error rate should be < 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://gateway:8000';

export default function () {
  const res = http.get(`${BASE_URL}/api/process`);

  // Record metrics
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response has service': (r) => r.json().service === 'gateway',
    'response has chain': (r) => r.json().chain !== undefined,
  });

  // Small pause between requests
  sleep(0.5);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
