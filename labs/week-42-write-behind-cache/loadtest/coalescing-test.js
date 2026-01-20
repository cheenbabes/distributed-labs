import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

/**
 * Write Coalescing Test
 *
 * This test rapidly updates the same product multiple times to demonstrate
 * write coalescing - where multiple updates to the same key are merged
 * into a single database write.
 */

const errorRate = new Rate('errors');
const updateLatency = new Trend('update_latency');
const updatesPerformed = new Counter('updates_performed');

export const options = {
  scenarios: {
    coalescing_test: {
      executor: 'constant-vus',
      vus: 5,
      duration: '30s',
    },
  },
  thresholds: {
    errors: ['rate<0.05'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab42-api:8000';

// All VUs update the SAME product to maximize coalescing
const TARGET_PRODUCT_ID = 'seed-001';

export default function () {
  const update = {
    name: `Coalesce-Test-${Date.now()}`,
    price: Math.round(Math.random() * 10000) / 100,
    quantity: Math.floor(Math.random() * 1000),
  };

  const res = http.put(
    `${BASE_URL}/products/${TARGET_PRODUCT_ID}`,
    JSON.stringify(update),
    { headers: { 'Content-Type': 'application/json' } }
  );

  updateLatency.add(res.timings.duration);
  updatesPerformed.add(1);
  errorRate.add(res.status !== 200);

  check(res, {
    'update status is 200': (r) => r.status === 200,
  });

  // Very short sleep to generate rapid updates
  sleep(0.01);
}

export function handleSummary(data) {
  console.log('\n=== WRITE COALESCING TEST RESULTS ===\n');
  console.log(`Total updates performed: ${data.metrics.updates_performed?.values?.count || 0}`);
  console.log(`Average update latency: ${(data.metrics.update_latency?.values?.avg || 0).toFixed(2)}ms`);
  console.log(`P95 update latency: ${(data.metrics.update_latency?.values?.['p(95)'] || 0).toFixed(2)}ms`);
  console.log('\nCheck Grafana dashboard to see how many DB writes actually occurred.');
  console.log('If coalescing is working, DB writes << API updates performed.\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
