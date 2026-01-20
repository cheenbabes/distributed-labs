import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const lockAcquireSuccess = new Rate('lock_acquire_success');
const lockAcquireLatency = new Trend('lock_acquire_latency');
const healthyNodes = new Trend('healthy_nodes');

// Test configuration - steady load for observing Redis failures
export const options = {
  scenarios: {
    steady_load: {
      executor: 'constant-vus',
      vus: 5,
      duration: '3m',
    },
  },
  thresholds: {
    'lock_acquire_success': ['rate>0.2'],  // Lower threshold for failure scenarios
    'errors': ['rate<0.3'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab37-lock-service:8000';
const RESOURCE = __ENV.RESOURCE || 'shared-resource';

export function setup() {
  // Check initial health
  const healthRes = http.get(`${BASE_URL}/health`);
  console.log('Initial health:', JSON.stringify(healthRes.json(), null, 2));
  return { startTime: Date.now() };
}

export default function (data) {
  const clientId = `k6-vu-${__VU}-${Date.now()}`;

  // Check health periodically (every 10 iterations)
  if (__ITER % 10 === 0) {
    const healthRes = http.get(`${BASE_URL}/health`);
    if (healthRes.status === 200) {
      const health = healthRes.json();
      healthyNodes.add(health.redis_nodes_healthy);
      console.log(`Health check: ${health.redis_nodes_healthy}/${health.redis_nodes_total} Redis nodes healthy`);
    }
  }

  // Try to acquire the lock
  const acquireStart = Date.now();
  const acquireRes = http.post(
    `${BASE_URL}/lock/acquire`,
    JSON.stringify({
      resource: RESOURCE,
      client_id: clientId,
      ttl_ms: 5000,
    }),
    {
      headers: { 'Content-Type': 'application/json' },
      timeout: '10s',
    }
  );
  const acquireLatency = Date.now() - acquireStart;

  const acquired = acquireRes.status === 200 && acquireRes.json().acquired === true;
  lockAcquireSuccess.add(acquired);
  lockAcquireLatency.add(acquireLatency);

  check(acquireRes, {
    'status is 200': (r) => r.status === 200,
  });

  if (acquired) {
    const lockId = acquireRes.json().lock_id;
    const nodesLocked = acquireRes.json().redis_nodes_locked;

    console.log(`VU ${__VU}: Lock acquired on ${nodesLocked} nodes`);

    // Hold the lock briefly
    sleep(1);

    // Release
    http.post(
      `${BASE_URL}/lock/release`,
      JSON.stringify({
        resource: RESOURCE,
        lock_id: lockId,
      }),
      {
        headers: { 'Content-Type': 'application/json' },
      }
    );
  } else {
    console.log(`VU ${__VU}: Lock failed - ${acquireRes.json()?.message || 'unknown error'}`);
  }

  sleep(0.5);
}

export function teardown(data) {
  const duration = (Date.now() - data.startTime) / 1000;
  console.log(`Test completed in ${duration}s`);

  // Final health check
  const healthRes = http.get(`${BASE_URL}/health`);
  console.log('Final health:', JSON.stringify(healthRes.json(), null, 2));
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      lock_success_rate: data.metrics.lock_acquire_success?.values?.rate || 0,
      avg_healthy_nodes: data.metrics.healthy_nodes?.values?.avg || 0,
      acquire_latency_p95: data.metrics.lock_acquire_latency?.values?.['p(95)'] || 0,
    }, null, 2),
  };
}
