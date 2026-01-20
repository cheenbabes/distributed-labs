import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const rateLimitedRate = new Rate('rate_limited');
const allowedCounter = new Counter('requests_allowed');
const rejectedCounter = new Counter('requests_rejected');
const latencyTrend = new Trend('request_latency');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 5 },   // Ramp up to 5 users
    { duration: '1m', target: 5 },    // Stay at 5 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    errors: ['rate<0.1'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://nginx:80';
const API_KEY = __ENV.API_KEY || 'test-client-1';

export default function () {
  const res = http.get(`${BASE_URL}/api/request`, {
    headers: {
      'X-API-Key': API_KEY,
    },
  });

  // Record metrics
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status >= 500);

  if (res.status === 200) {
    allowedCounter.add(1);
    rateLimitedRate.add(false);
  } else if (res.status === 429) {
    rejectedCounter.add(1);
    rateLimitedRate.add(true);
  } else {
    errorRate.add(true);
  }

  // Validate response
  check(res, {
    'status is 200 or 429': (r) => r.status === 200 || r.status === 429,
    'has rate limit headers': (r) => r.headers['X-Ratelimit-Limit'] !== undefined,
    'has served-by header': (r) => r.headers['X-Served-By'] !== undefined,
  });

  // Small pause between requests
  sleep(0.1);
}

export function handleSummary(data) {
  const allowed = data.metrics.requests_allowed ? data.metrics.requests_allowed.values.count : 0;
  const rejected = data.metrics.requests_rejected ? data.metrics.requests_rejected.values.count : 0;
  const total = allowed + rejected;

  console.log('\n=== Rate Limit Summary ===');
  console.log(`Total requests: ${total}`);
  console.log(`Allowed: ${allowed} (${((allowed/total)*100).toFixed(1)}%)`);
  console.log(`Rejected: ${rejected} (${((rejected/total)*100).toFixed(1)}%)`);
  console.log('========================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
