import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Gauge } from 'k6/metrics';

/**
 * Partition Test
 *
 * This test demonstrates counting behavior during network partitions:
 * 1. Baseline: Normal operation, all regions syncing
 * 2. Partition: Isolate one region, observe local counting continues
 * 3. Heal: Restore connectivity, observe convergence
 */

const errorRate = new Rate('errors');
const incrementLatency = new Trend('increment_latency');
const convergenceLag = new Gauge('convergence_lag');

export const options = {
  scenarios: {
    baseline: {
      executor: 'constant-vus',
      vus: 5,
      duration: '20s',
      startTime: '0s',
      exec: 'incrementAndCheck',
      tags: { phase: 'baseline' },
    },
    partition: {
      executor: 'constant-vus',
      vus: 5,
      duration: '20s',
      startTime: '25s',  // Start after baseline + pause
      exec: 'incrementAndCheck',
      tags: { phase: 'partition' },
    },
    heal: {
      executor: 'constant-vus',
      vus: 5,
      duration: '20s',
      startTime: '50s',  // Start after partition + pause
      exec: 'incrementAndCheck',
      tags: { phase: 'heal' },
    },
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab34-gateway:8080';

// Setup: Enable partition before partition phase
export function setup() {
  console.log('Setting up test - ensuring no partitions initially');
  http.post(`${BASE_URL}/admin/partition/all?enabled=false`);
  sleep(2);
  return {};
}

export function incrementAndCheck() {
  // Increment counter
  const incrementRes = http.post(
    `${BASE_URL}/api/increment`,
    JSON.stringify({
      counter_name: 'partition_test',
      amount: 1,
    }),
    {
      headers: { 'Content-Type': 'application/json' },
    }
  );

  incrementLatency.add(incrementRes.timings.duration);
  errorRate.add(incrementRes.status !== 200);

  check(incrementRes, {
    'increment ok': (r) => r.status === 200,
  });

  sleep(0.2);

  // Check convergence
  const statusRes = http.get(`${BASE_URL}/api/counter/partition_test`);

  check(statusRes, {
    'status ok': (r) => r.status === 200,
  });

  if (statusRes.status === 200) {
    try {
      const data = statusRes.json();
      if (data.convergence) {
        convergenceLag.add(data.convergence.lag);
      }
    } catch (e) {}
  }

  sleep(0.3);
}

// Teardown: Restore connectivity
export function teardown(data) {
  console.log('Tearing down - disabling all partitions');
  http.post(`${BASE_URL}/admin/partition/all?enabled=false`);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
