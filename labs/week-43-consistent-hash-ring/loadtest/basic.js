import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const lookupLatency = new Trend('lookup_latency');
const storeLatency = new Trend('store_latency');
const keysStored = new Counter('keys_stored');
const keysLookedUp = new Counter('keys_looked_up');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 5 },   // Ramp up to 5 users
    { duration: '1m', target: 10 },   // Stay at 10 users
    { duration: '30s', target: 20 },  // Spike to 20 users
    { duration: '1m', target: 10 },   // Back to 10 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],  // 95% of requests should be < 500ms
    errors: ['rate<0.05'],             // Error rate should be < 5%
  },
};

const COORDINATOR_URL = __ENV.COORDINATOR_URL || 'http://ring-coordinator:8080';

// Store keys for later retrieval
const storedKeys = [];

export default function () {
  const operation = Math.random();

  if (operation < 0.3 || storedKeys.length === 0) {
    // 30% writes (or if no keys stored yet)
    storeKey();
  } else if (operation < 0.7) {
    // 40% reads
    readKey();
  } else {
    // 30% lookups (just check routing, don't fetch value)
    lookupKey();
  }

  sleep(0.1);
}

function storeKey() {
  const key = `k6_key_${Date.now()}_${Math.random().toString(36).substring(7)}`;
  const value = `value_${Math.random().toString(36).substring(7)}`;

  const res = http.post(
    `${COORDINATOR_URL}/keys`,
    JSON.stringify({ key: key, value: value }),
    { headers: { 'Content-Type': 'application/json' } }
  );

  storeLatency.add(res.timings.duration);

  const success = check(res, {
    'store status is 200': (r) => r.status === 200,
    'store response has routed_to': (r) => {
      try {
        return r.json().routed_to !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  if (success) {
    keysStored.add(1);
    // Keep track of stored keys for later reads
    if (storedKeys.length < 1000) {
      storedKeys.push(key);
    }
  }

  errorRate.add(!success);
}

function readKey() {
  if (storedKeys.length === 0) return;

  const key = storedKeys[Math.floor(Math.random() * storedKeys.length)];

  const res = http.get(`${COORDINATOR_URL}/keys/${key}`);

  const success = check(res, {
    'read status is 200 or 404': (r) => r.status === 200 || r.status === 404,
  });

  errorRate.add(!success && res.status !== 404);
}

function lookupKey() {
  const key = storedKeys.length > 0
    ? storedKeys[Math.floor(Math.random() * storedKeys.length)]
    : `random_key_${Math.random().toString(36).substring(7)}`;

  const res = http.get(`${COORDINATOR_URL}/lookup/${key}`);

  lookupLatency.add(res.timings.duration);
  keysLookedUp.add(1);

  const success = check(res, {
    'lookup status is 200': (r) => r.status === 200,
    'lookup response has target_node': (r) => {
      try {
        return r.json().target_node !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  errorRate.add(!success);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      metrics: {
        http_req_duration_p95: data.metrics.http_req_duration?.values?.['p(95)'],
        http_req_duration_avg: data.metrics.http_req_duration?.values?.avg,
        errors_rate: data.metrics.errors?.values?.rate,
        keys_stored: data.metrics.keys_stored?.values?.count,
        keys_looked_up: data.metrics.keys_looked_up?.values?.count,
        lookup_latency_avg: data.metrics.lookup_latency?.values?.avg,
        store_latency_avg: data.metrics.store_latency?.values?.avg,
      },
      summary: {
        total_requests: data.metrics.http_reqs?.values?.count,
        total_duration: data.state?.testRunDurationMs,
      }
    }, null, 2),
  };
}
