import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');

// Sustained load configuration - ideal for observing canary behavior over time
export const options = {
  stages: [
    { duration: '1m', target: 15 },    // Ramp up
    { duration: '10m', target: 15 },   // Sustained load for canary observation
    { duration: '30s', target: 0 },    // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    errors: ['rate<0.20'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab19-nginx';

export default function () {
  const res = http.get(`${BASE_URL}/api/process`);

  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  // Consistent request rate
  sleep(0.5);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
