import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const lockAcquireSuccess = new Rate('lock_acquire_success');
const lockAcquireLatency = new Trend('lock_acquire_latency');
const lockReleaseLatency = new Trend('lock_release_latency');
const contentionEvents = new Counter('contention_events');
const errorRate = new Rate('errors');

// Test configuration
export const options = {
  scenarios: {
    // Scenario 1: Gradual ramp-up to show contention
    contention_test: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '30s', target: 5 },   // Ramp up to 5 users
        { duration: '1m', target: 10 },   // Ramp up to 10 users
        { duration: '1m', target: 10 },   // Stay at 10 users
        { duration: '30s', target: 0 },   // Ramp down
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    'lock_acquire_success': ['rate>0.3'],  // At least 30% success (due to contention)
    'lock_acquire_latency': ['p(95)<1000'], // 95% acquire under 1s
    'errors': ['rate<0.1'],                 // Error rate under 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab37-lock-service:8000';
const RESOURCE = __ENV.RESOURCE || 'shared-resource';

export default function () {
  const clientId = `k6-vu-${__VU}-${Date.now()}`;

  // Try to acquire the lock
  const acquireStart = Date.now();
  const acquireRes = http.post(
    `${BASE_URL}/lock/acquire`,
    JSON.stringify({
      resource: RESOURCE,
      client_id: clientId,
      ttl_ms: 5000,  // 5 second TTL
    }),
    {
      headers: { 'Content-Type': 'application/json' },
      timeout: '10s',
    }
  );
  const acquireLatency = Date.now() - acquireStart;

  // Record metrics
  const acquired = acquireRes.status === 200 && acquireRes.json().acquired === true;
  lockAcquireSuccess.add(acquired);
  lockAcquireLatency.add(acquireLatency);
  errorRate.add(acquireRes.status !== 200);

  // Check response
  check(acquireRes, {
    'acquire status is 200': (r) => r.status === 200,
    'response has acquired field': (r) => r.json().acquired !== undefined,
  });

  if (acquired) {
    const lockId = acquireRes.json().lock_id;
    const nodesLocked = acquireRes.json().redis_nodes_locked;

    // Log success
    console.log(`VU ${__VU}: Lock acquired on ${nodesLocked} nodes`);

    // Simulate some work while holding the lock
    const workDuration = Math.random() * 2000 + 500;  // 500ms to 2.5s
    sleep(workDuration / 1000);

    // Release the lock
    const releaseStart = Date.now();
    const releaseRes = http.post(
      `${BASE_URL}/lock/release`,
      JSON.stringify({
        resource: RESOURCE,
        lock_id: lockId,
      }),
      {
        headers: { 'Content-Type': 'application/json' },
        timeout: '10s',
      }
    );
    const releaseLatency = Date.now() - releaseStart;
    lockReleaseLatency.add(releaseLatency);

    check(releaseRes, {
      'release status is 200': (r) => r.status === 200,
      'lock was released': (r) => r.json().released === true,
    });

  } else {
    // Lock not acquired - contention
    contentionEvents.add(1);
    console.log(`VU ${__VU}: Lock contention - ${acquireRes.json().message}`);
  }

  // Small pause between attempts
  sleep(Math.random() * 0.5 + 0.1);  // 100ms to 600ms
}

// Summary handler
export function handleSummary(data) {
  const summary = {
    metrics: {
      lock_acquire_success_rate: data.metrics.lock_acquire_success?.values?.rate || 0,
      lock_acquire_latency_p50: data.metrics.lock_acquire_latency?.values?.['p(50)'] || 0,
      lock_acquire_latency_p95: data.metrics.lock_acquire_latency?.values?.['p(95)'] || 0,
      contention_events: data.metrics.contention_events?.values?.count || 0,
      error_rate: data.metrics.errors?.values?.rate || 0,
    },
    test_info: {
      duration: data.state?.testRunDurationMs || 0,
      vus_max: data.options?.scenarios?.contention_test?.stages?.[1]?.target || 0,
    },
  };

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
