import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

/**
 * Multi-Client Test
 *
 * Simulates multiple clients hitting the rate limiter simultaneously.
 * Each client should get their own rate limit quota.
 *
 * Run with: docker compose run --rm k6 run /scripts/multi-client.js
 */

const errorRate = new Rate('errors');
const rateLimitedRate = new Rate('rate_limited');
const allowedCounter = new Counter('requests_allowed');
const rejectedCounter = new Counter('requests_rejected');

export const options = {
  scenarios: {
    clients: {
      executor: 'per-vu-iterations',
      vus: 10,                // 10 different clients
      iterations: 200,        // Each client makes 200 requests
      maxDuration: '2m',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    errors: ['rate<0.05'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://nginx:80';

export default function () {
  // Each VU gets its own API key (simulating different clients)
  const apiKey = `client-${__VU}`;

  const res = http.get(`${BASE_URL}/api/request`, {
    headers: {
      'X-API-Key': apiKey,
    },
  });

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
    'has client info in response': (r) => {
      if (r.status === 200) {
        try {
          const body = JSON.parse(r.body);
          return body.instance !== undefined;
        } catch (e) {
          return false;
        }
      }
      return true;
    },
  });

  // Vary request timing
  sleep(Math.random() * 0.5);
}

export function handleSummary(data) {
  const allowed = data.metrics.requests_allowed ? data.metrics.requests_allowed.values.count : 0;
  const rejected = data.metrics.requests_rejected ? data.metrics.requests_rejected.values.count : 0;
  const total = allowed + rejected;

  console.log('\n=== Multi-Client Test Summary ===');
  console.log(`Total clients: 10`);
  console.log(`Requests per client: 200`);
  console.log(`Total requests: ${total}`);
  console.log(`Allowed: ${allowed}`);
  console.log(`Rejected: ${rejected}`);
  console.log(`Expected allowed per client per minute: 100`);
  console.log('=================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
