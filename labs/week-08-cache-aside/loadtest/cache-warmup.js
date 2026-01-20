import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

/**
 * Cache Warmup Test
 *
 * This test demonstrates the difference between cold cache and warm cache performance.
 * It reads all products multiple times to populate the cache, then measures hit rate.
 */

const hitRate = new Rate('cache_hit_rate');
const missRate = new Rate('cache_miss_rate');
const latencyTrend = new Trend('read_latency');

export const options = {
  stages: [
    { duration: '10s', target: 5 },   // Warm up phase - populate cache
    { duration: '30s', target: 10 },  // Steady state - should see high hit rate
    { duration: '10s', target: 0 },   // Ramp down
  ],
  thresholds: {
    cache_hit_rate: ['rate>0.7'],     // After warmup, hit rate should be >70%
    http_req_duration: ['p(95)<200'], // With cache, p95 should be fast
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab08-product-service:8000';

const PRODUCT_IDS = [
  'prod-001',
  'prod-002',
  'prod-003',
  'prod-004',
  'prod-005',
];

export default function () {
  // Read all products in sequence to ensure cache population
  const productId = PRODUCT_IDS[__VU % PRODUCT_IDS.length];
  const res = http.get(`${BASE_URL}/products/${productId}`);

  latencyTrend.add(res.timings.duration);

  if (res.status === 200) {
    const body = res.json();
    hitRate.add(body.cache_status === 'hit');
    missRate.add(body.cache_status === 'miss');
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  sleep(0.1);
}

export function handleSummary(data) {
  const hitRateValue = data.metrics.cache_hit_rate ?
    (data.metrics.cache_hit_rate.values.rate * 100).toFixed(2) : 'N/A';

  console.log(`\n=== Cache Warmup Results ===`);
  console.log(`Cache Hit Rate: ${hitRateValue}%`);
  console.log(`============================\n`);

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
