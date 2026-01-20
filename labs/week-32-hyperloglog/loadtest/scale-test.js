import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Gauge } from 'k6/metrics';
import { randomString } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

/**
 * Scale Test for HyperLogLog
 *
 * This test generates a large number of unique visitors to demonstrate
 * HyperLogLog's memory efficiency at scale.
 */

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const exactCount = new Gauge('exact_count');
const hllCount = new Gauge('hll_count');
const memoryRatio = new Gauge('memory_savings_ratio');

// Test configuration - generates many unique visitors quickly
export const options = {
  scenarios: {
    scale_test: {
      executor: 'constant-arrival-rate',
      rate: 100,           // 100 requests per second
      timeUnit: '1s',
      duration: '2m',      // Run for 2 minutes = ~12,000 visitors
      preAllocatedVUs: 50,
      maxVUs: 100,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    errors: ['rate<0.1'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab32-visitor-counter:8000';

export default function () {
  // Always generate unique visitor IDs to maximize cardinality
  const visitorId = randomString(36);

  const res = http.post(
    `${BASE_URL}/visit?page_id=scale_test&visitor_id=${visitorId}`,
    null,
    {
      headers: {
        'Content-Type': 'application/json',
      },
    }
  );

  // Record metrics
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  // Capture counts for reporting
  if (res.status === 200) {
    const data = res.json();
    exactCount.add(data.exact_count);
    hllCount.add(data.hll_count);
    if (data.memory && data.memory.ratio > 0) {
      memoryRatio.add(data.memory.ratio);
    }
  }

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'has memory savings': (r) => r.json().memory && r.json().memory.ratio > 1,
  });

  // Minimal sleep to maximize throughput
  sleep(0.01);
}

export function teardown() {
  // Get final stats
  const res = http.get(`${BASE_URL}/stats?page_id=scale_test`);
  if (res.status === 200) {
    const stats = res.json();
    console.log('\n=== FINAL SCALE TEST RESULTS ===');
    console.log(`Exact Count: ${stats.exact.count}`);
    console.log(`HyperLogLog Count: ${stats.hyperloglog.count}`);
    console.log(`Error Rate: ${stats.comparison.error_percentage}`);
    console.log(`Exact Memory: ${stats.exact.memory_human}`);
    console.log(`HLL Memory: ${stats.hyperloglog.memory_human}`);
    console.log(`Memory Savings: ${stats.comparison.memory_savings_ratio}x`);
    console.log('================================\n');
  }
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
