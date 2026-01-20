import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Counter } from 'k6/metrics';

/**
 * Consistency Comparison Test
 *
 * Compares eventual consistency (CRDT) vs strong consistency counters.
 * Demonstrates the latency trade-off Netflix makes for high-volume counting.
 */

const eventualLatency = new Trend('eventual_latency');
const strongLatency = new Trend('strong_latency');
const eventualCount = new Counter('eventual_increments');
const strongCount = new Counter('strong_increments');

export const options = {
  scenarios: {
    eventual: {
      executor: 'constant-vus',
      vus: 10,
      duration: '30s',
      exec: 'testEventual',
      tags: { consistency: 'eventual' },
    },
    strong: {
      executor: 'constant-vus',
      vus: 10,
      duration: '30s',
      exec: 'testStrong',
      tags: { consistency: 'strong' },
    },
  },
  thresholds: {
    'eventual_latency{consistency:eventual}': ['p(95)<100'],
    'strong_latency{consistency:strong}': ['p(95)<500'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab34-gateway:8080';
const CONSISTENT_URL = __ENV.CONSISTENT_URL || 'http://lab34-counter-consistent:8004';

export function testEventual() {
  const res = http.post(
    `${BASE_URL}/api/increment`,
    JSON.stringify({
      counter_name: 'comparison_eventual',
      amount: 1,
    }),
    {
      headers: { 'Content-Type': 'application/json' },
    }
  );

  eventualLatency.add(res.timings.duration);
  eventualCount.add(1);

  check(res, {
    'eventual increment ok': (r) => r.status === 200,
  });

  sleep(0.1);
}

export function testStrong() {
  const res = http.post(
    `${CONSISTENT_URL}/increment`,
    JSON.stringify({
      counter_name: 'comparison_strong',
      amount: 1,
    }),
    {
      headers: { 'Content-Type': 'application/json' },
    }
  );

  strongLatency.add(res.timings.duration);
  strongCount.add(1);

  check(res, {
    'strong increment ok': (r) => r.status === 200,
  });

  sleep(0.1);
}

export function handleSummary(data) {
  const eventualP50 = data.metrics.eventual_latency?.values?.['p(50)'] || 0;
  const eventualP95 = data.metrics.eventual_latency?.values?.['p(95)'] || 0;
  const strongP50 = data.metrics.strong_latency?.values?.['p(50)'] || 0;
  const strongP95 = data.metrics.strong_latency?.values?.['p(95)'] || 0;

  console.log('\n========== CONSISTENCY COMPARISON ==========');
  console.log(`\nEventual Consistency (CRDT):`);
  console.log(`  p50 latency: ${eventualP50.toFixed(2)}ms`);
  console.log(`  p95 latency: ${eventualP95.toFixed(2)}ms`);
  console.log(`\nStrong Consistency:`);
  console.log(`  p50 latency: ${strongP50.toFixed(2)}ms`);
  console.log(`  p95 latency: ${strongP95.toFixed(2)}ms`);
  console.log(`\nSpeedup (p50): ${(strongP50 / eventualP50).toFixed(1)}x`);
  console.log(`Speedup (p95): ${(strongP95 / eventualP95).toFixed(1)}x`);
  console.log('\n=============================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
