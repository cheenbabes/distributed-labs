import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomString, randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const visitorsGenerated = new Counter('visitors_generated');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 20 },  // Ramp up to 20 users
    { duration: '1m', target: 20 },   // Stay at 20 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'], // 95% of requests should be < 500ms
    errors: ['rate<0.1'],              // Error rate should be < 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab32-visitor-counter:8000';

// Pool of visitor IDs to simulate repeat visitors
// ~20% of visitors will be repeat visitors
const VISITOR_POOL_SIZE = 1000;
const visitorPool = [];

// Initialize visitor pool
export function setup() {
  for (let i = 0; i < VISITOR_POOL_SIZE; i++) {
    visitorPool.push(randomString(36));
  }
  return { visitorPool };
}

export default function (data) {
  // 20% chance to use an existing visitor (repeat visit)
  // 80% chance to be a new unique visitor
  let visitorId;
  if (Math.random() < 0.2 && data.visitorPool.length > 0) {
    // Repeat visitor
    visitorId = data.visitorPool[randomIntBetween(0, data.visitorPool.length - 1)];
  } else {
    // New unique visitor
    visitorId = randomString(36);
  }

  // Choose a random page
  const pages = ['homepage', 'products', 'checkout', 'about'];
  const pageId = pages[randomIntBetween(0, pages.length - 1)];

  const res = http.post(
    `${BASE_URL}/visit?page_id=${pageId}&visitor_id=${visitorId}`,
    null,
    {
      headers: {
        'Content-Type': 'application/json',
      },
    }
  );

  // Record metrics
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);
  visitorsGenerated.add(1);

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response has exact_count': (r) => r.json().exact_count !== undefined,
    'response has hll_count': (r) => r.json().hll_count !== undefined,
    'response has memory info': (r) => r.json().memory !== undefined,
  });

  // Small pause between requests
  sleep(randomIntBetween(1, 3) / 10);  // 0.1 to 0.3 seconds
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
