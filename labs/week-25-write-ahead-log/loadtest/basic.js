import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const walLatency = new Trend('wal_write_latency');
const nowalLatency = new Trend('nowal_write_latency');
const walOps = new Counter('wal_operations');
const nowalOps = new Counter('nowal_operations');

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

const WAL_URL = __ENV.WAL_URL || 'http://lab25-kv-store-wal:8000';
const NOWAL_URL = __ENV.NOWAL_URL || 'http://lab25-kv-store-nowal:8000';

// Generate a unique key for this VU and iteration
function generateKey() {
  return `key-${__VU}-${__ITER}-${Date.now()}`;
}

export default function () {
  const key = generateKey();
  const value = {
    data: `value-${Date.now()}`,
    vu: __VU,
    iter: __ITER,
  };

  // Test WAL-enabled store
  const walSetRes = http.post(
    `${WAL_URL}/kv`,
    JSON.stringify({ key: key, value: value }),
    { headers: { 'Content-Type': 'application/json' } }
  );

  walLatency.add(walSetRes.timings.duration);
  walOps.add(1);

  check(walSetRes, {
    'WAL set status is 200': (r) => r.status === 200,
    'WAL set has key': (r) => r.json().key === key,
  });

  // Test no-WAL store
  const nowalSetRes = http.post(
    `${NOWAL_URL}/kv`,
    JSON.stringify({ key: key, value: value }),
    { headers: { 'Content-Type': 'application/json' } }
  );

  nowalLatency.add(nowalSetRes.timings.duration);
  nowalOps.add(1);

  check(nowalSetRes, {
    'No-WAL set status is 200': (r) => r.status === 200,
    'No-WAL set has key': (r) => r.json().key === key,
  });

  // Record error rate
  errorRate.add(walSetRes.status !== 200 || nowalSetRes.status !== 200);

  // Read back the values
  const walGetRes = http.get(`${WAL_URL}/kv/${key}`);
  const nowalGetRes = http.get(`${NOWAL_URL}/kv/${key}`);

  check(walGetRes, {
    'WAL get status is 200': (r) => r.status === 200,
  });

  check(nowalGetRes, {
    'No-WAL get status is 200': (r) => r.status === 200,
  });

  // Small pause between requests
  sleep(0.1);
}

export function handleSummary(data) {
  // Calculate average latencies
  const walAvg = data.metrics.wal_write_latency ?
    data.metrics.wal_write_latency.values.avg : 0;
  const nowalAvg = data.metrics.nowal_write_latency ?
    data.metrics.nowal_write_latency.values.avg : 0;

  console.log('\n=== WAL vs No-WAL Latency Comparison ===');
  console.log(`WAL-enabled avg latency:  ${walAvg.toFixed(2)}ms`);
  console.log(`No-WAL avg latency:       ${nowalAvg.toFixed(2)}ms`);
  console.log(`Difference:               ${(walAvg - nowalAvg).toFixed(2)}ms`);
  console.log(`WAL overhead:             ${((walAvg / nowalAvg - 1) * 100).toFixed(1)}%`);

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
