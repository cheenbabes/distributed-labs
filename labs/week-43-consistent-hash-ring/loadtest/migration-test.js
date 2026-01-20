import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

/**
 * This test generates consistent traffic to observe key distribution
 * before and after node topology changes.
 *
 * Run this test while adding/removing nodes to observe migration behavior.
 */

const errorRate = new Rate('errors');
const keysStored = new Counter('keys_stored');
const keysByNode = {
  'node-1': new Counter('keys_node_1'),
  'node-2': new Counter('keys_node_2'),
  'node-3': new Counter('keys_node_3'),
  'node-4': new Counter('keys_node_4'),
};
const storeLatency = new Trend('store_latency');

export const options = {
  stages: [
    { duration: '2m', target: 5 },   // Steady state - 5 VUs for 2 minutes
    { duration: '1m', target: 5 },   // Continue (topology change window)
    { duration: '1m', target: 5 },   // Observe after change
    { duration: '30s', target: 0 },  // Ramp down
  ],
  thresholds: {
    errors: ['rate<0.1'],
  },
};

const COORDINATOR_URL = __ENV.COORDINATOR_URL || 'http://ring-coordinator:8080';

let keyCounter = 0;

export default function () {
  const key = `migration_test_${keyCounter++}_${__VU}`;
  const value = `value_${Date.now()}`;

  const res = http.post(
    `${COORDINATOR_URL}/keys`,
    JSON.stringify({ key: key, value: value }),
    { headers: { 'Content-Type': 'application/json' } }
  );

  storeLatency.add(res.timings.duration);

  const success = check(res, {
    'status is 200': (r) => r.status === 200,
  });

  if (success) {
    keysStored.add(1);

    try {
      const routedTo = res.json().routed_to;
      if (keysByNode[routedTo]) {
        keysByNode[routedTo].add(1);
      }
    } catch (e) {
      // Ignore JSON parse errors
    }
  }

  errorRate.add(!success);
  sleep(0.2);
}

export function handleSummary(data) {
  const distribution = {};
  for (const [node, counter] of Object.entries(keysByNode)) {
    const metricName = `keys_${node.replace('-', '_')}`;
    if (data.metrics[metricName]) {
      distribution[node] = data.metrics[metricName]?.values?.count || 0;
    }
  }

  const totalKeys = Object.values(distribution).reduce((a, b) => a + b, 0);

  return {
    stdout: JSON.stringify({
      summary: {
        total_keys_stored: totalKeys,
        duration_ms: data.state?.testRunDurationMs,
        error_rate: data.metrics.errors?.values?.rate,
      },
      distribution: distribution,
      percentages: Object.fromEntries(
        Object.entries(distribution).map(([node, count]) => [
          node,
          totalKeys > 0 ? ((count / totalKeys) * 100).toFixed(2) + '%' : '0%'
        ])
      ),
    }, null, 2),
  };
}
