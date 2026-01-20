import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

/**
 * High Concurrency Test
 *
 * This test generates high concurrent load to stress test the rate limiter
 * and demonstrate race conditions in the naive (non-atomic) implementation.
 *
 * Run with: docker compose run --rm k6 run /scripts/high-concurrency.js
 */

const errorRate = new Rate('errors');
const rateLimitedRate = new Rate('rate_limited');
const allowedCounter = new Counter('requests_allowed');
const rejectedCounter = new Counter('requests_rejected');
const latencyTrend = new Trend('request_latency');

export const options = {
  scenarios: {
    burst: {
      executor: 'constant-arrival-rate',
      rate: 200,              // 200 requests per second
      timeUnit: '1s',
      duration: '1m',
      preAllocatedVUs: 50,
      maxVUs: 100,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    errors: ['rate<0.05'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://nginx:80';

export default function () {
  // All requests use the same API key to test per-client limiting
  const apiKey = 'shared-test-client';

  const res = http.get(`${BASE_URL}/api/request`, {
    headers: {
      'X-API-Key': apiKey,
    },
  });

  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status >= 500);

  if (res.status === 200) {
    allowedCounter.add(1);
    rateLimitedRate.add(false);
  } else if (res.status === 429) {
    rejectedCounter.add(1);
    rateLimitedRate.add(true);
  }

  check(res, {
    'status is 200 or 429': (r) => r.status === 200 || r.status === 429,
    'has rate limit headers': (r) => r.headers['X-Ratelimit-Limit'] !== undefined,
  });
}

export function handleSummary(data) {
  const allowed = data.metrics.requests_allowed ? data.metrics.requests_allowed.values.count : 0;
  const rejected = data.metrics.requests_rejected ? data.metrics.requests_rejected.values.count : 0;
  const total = allowed + rejected;

  console.log('\n=== High Concurrency Test Summary ===');
  console.log(`Total requests: ${total}`);
  console.log(`Allowed: ${allowed} (${((allowed/total)*100).toFixed(1)}%)`);
  console.log(`Rejected: ${rejected} (${((rejected/total)*100).toFixed(1)}%)`);
  console.log(`Rate limit: 100 req/60s`);
  console.log(`Expected allowed in 60s: ~100`);
  console.log(`Difference from expected: ${allowed - 100} (over-allowance indicates race conditions)`);
  console.log('====================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
