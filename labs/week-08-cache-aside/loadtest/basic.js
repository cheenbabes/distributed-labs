import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const cacheHitRate = new Rate('cache_hits');
const latencyTrend = new Trend('request_latency');
const readRequests = new Counter('read_requests');
const writeRequests = new Counter('write_requests');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 10 },  // Ramp up to 10 users
    { duration: '1m', target: 10 },   // Stay at 10 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'], // 95% of requests should be < 500ms
    errors: ['rate<0.1'],              // Error rate should be < 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab08-product-service:8000';

// Pre-seeded product IDs from init.sql
const PRODUCT_IDS = [
  'prod-001',
  'prod-002',
  'prod-003',
  'prod-004',
  'prod-005',
];

export default function () {
  // 90% reads, 10% writes (typical cache-aside workload)
  const isRead = Math.random() < 0.9;

  if (isRead) {
    // Read a random product
    const productId = PRODUCT_IDS[Math.floor(Math.random() * PRODUCT_IDS.length)];
    const res = http.get(`${BASE_URL}/products/${productId}`);

    readRequests.add(1);
    latencyTrend.add(res.timings.duration);
    errorRate.add(res.status !== 200);

    // Check if it was a cache hit
    if (res.status === 200) {
      const body = res.json();
      cacheHitRate.add(body.cache_status === 'hit');
    }

    check(res, {
      'read status is 200': (r) => r.status === 200,
      'response has id': (r) => r.json().id !== undefined,
      'response has cache_status': (r) => r.json().cache_status !== undefined,
    });
  } else {
    // Update a random product
    const productId = PRODUCT_IDS[Math.floor(Math.random() * PRODUCT_IDS.length)];
    const payload = JSON.stringify({
      name: `Updated Product ${Date.now()}`,
      description: 'Updated via load test',
      price: Math.round(Math.random() * 100 * 100) / 100,
    });

    const res = http.put(`${BASE_URL}/products/${productId}`, payload, {
      headers: { 'Content-Type': 'application/json' },
    });

    writeRequests.add(1);
    latencyTrend.add(res.timings.duration);
    errorRate.add(res.status !== 200);

    check(res, {
      'write status is 200': (r) => r.status === 200,
      'cache was invalidated': (r) => r.json().cache_status === 'invalidated',
    });
  }

  // Small pause between requests
  sleep(0.3);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
