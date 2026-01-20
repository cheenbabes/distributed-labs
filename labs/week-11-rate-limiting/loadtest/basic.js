import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

// Custom metrics
const allowedRate = new Rate('requests_allowed');
const rejectedRate = new Rate('requests_rejected');
const allowedCount = new Counter('allowed_total');
const rejectedCount = new Counter('rejected_total');
const latencyTrend = new Trend('request_latency');

// Test configuration - sustained load just above rate limit
export const options = {
  stages: [
    { duration: '10s', target: 5 },   // Ramp up
    { duration: '1m', target: 15 },   // Exceed rate limit (10 req/s)
    { duration: '10s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://rate-limiter:8000';

export default function () {
  const res = http.get(`${BASE_URL}/api/request`);

  // Record latency
  latencyTrend.add(res.timings.duration);

  // Track allowed vs rejected
  const allowed = res.status === 200;
  const rejected = res.status === 429;

  allowedRate.add(allowed);
  rejectedRate.add(rejected);

  if (allowed) {
    allowedCount.add(1);
  }
  if (rejected) {
    rejectedCount.add(1);
  }

  // Validate response structure
  check(res, {
    'status is 200 or 429': (r) => r.status === 200 || r.status === 429,
    'has rate limit headers': (r) => r.headers['X-Ratelimit-Limit'] !== undefined,
    'has remaining header': (r) => r.headers['X-Ratelimit-Remaining'] !== undefined,
  });

  // Minimal sleep to generate high request rate
  sleep(0.05);
}

export function handleSummary(data) {
  const allowed = data.metrics.allowed_total ? data.metrics.allowed_total.values.count : 0;
  const rejected = data.metrics.rejected_total ? data.metrics.rejected_total.values.count : 0;
  const total = allowed + rejected;
  const rejectionRate = total > 0 ? (rejected / total * 100).toFixed(1) : 0;

  console.log('\n=== Rate Limiting Summary ===');
  console.log(`Total Requests: ${total}`);
  console.log(`Allowed: ${allowed}`);
  console.log(`Rejected: ${rejected}`);
  console.log(`Rejection Rate: ${rejectionRate}%`);
  console.log('==============================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
