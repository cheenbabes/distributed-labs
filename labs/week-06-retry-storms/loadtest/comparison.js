import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

/**
 * Comparison test: Run the same load pattern with each retry strategy
 * and measure the differences.
 *
 * This test:
 * 1. Sets strategy to naive, runs load, triggers failure
 * 2. Sets strategy to backoff, runs load, triggers failure
 * 3. Sets strategy to jitter, runs load, triggers failure
 * 4. Compares results
 */

// Custom metrics per strategy
const naiveErrors = new Rate('naive_errors');
const backoffErrors = new Rate('backoff_errors');
const jitterErrors = new Rate('jitter_errors');

const naiveLatency = new Trend('naive_latency');
const backoffLatency = new Trend('backoff_latency');
const jitterLatency = new Trend('jitter_latency');

// Test configuration
export const options = {
  scenarios: {
    naive_test: {
      executor: 'constant-vus',
      vus: 5,
      duration: '30s',
      exec: 'naiveTest',
      startTime: '0s',
      tags: { strategy: 'naive' },
    },
    backoff_test: {
      executor: 'constant-vus',
      vus: 5,
      duration: '30s',
      exec: 'backoffTest',
      startTime: '40s', // Start after naive + recovery
      tags: { strategy: 'backoff' },
    },
    jitter_test: {
      executor: 'constant-vus',
      vus: 5,
      duration: '30s',
      exec: 'jitterTest',
      startTime: '80s', // Start after backoff + recovery
      tags: { strategy: 'jitter' },
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<20000'],
  },
};

const CLIENT_URL = __ENV.CLIENT_URL || 'http://client:8000';
const BACKEND_URL = __ENV.BACKEND_URL || 'http://backend:8001';

// Helper to set strategy
function setStrategy(strategy) {
  const res = http.post(
    `${CLIENT_URL}/admin/strategy`,
    JSON.stringify({ strategy: strategy }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  return res.status === 200;
}

// Helper to trigger failure
function triggerFailure(durationSeconds) {
  const res = http.post(
    `${BACKEND_URL}/admin/fail`,
    JSON.stringify({ duration_seconds: durationSeconds }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  return res.status === 200;
}

// Setup - ensure clean state
export function setup() {
  // Reset to naive strategy
  setStrategy('naive');
  console.log('Comparison test starting...');
  console.log('This test will run 3 scenarios:');
  console.log('  1. Naive retries (0-30s)');
  console.log('  2. Backoff retries (40-70s)');
  console.log('  3. Jitter retries (80-110s)');
  console.log('Each scenario triggers a 10s failure during the load.');
  return { startTime: Date.now() };
}

// Naive test scenario
export function naiveTest() {
  // Set strategy on first iteration
  if (__ITER === 0 && __VU === 1) {
    setStrategy('naive');
    // Trigger failure after 5 seconds
    sleep(5);
    triggerFailure(10);
  }

  const res = http.get(`${CLIENT_URL}/process`, { timeout: '30s' });

  naiveLatency.add(res.timings.duration);
  naiveErrors.add(res.status !== 200);

  check(res, {
    'naive: status ok or expected error': (r) => r.status === 200 || r.status === 502,
  });

  sleep(0.5);
}

// Backoff test scenario
export function backoffTest() {
  // Set strategy on first iteration
  if (__ITER === 0 && __VU === 1) {
    setStrategy('backoff');
    // Trigger failure after 5 seconds
    sleep(5);
    triggerFailure(10);
  }

  const res = http.get(`${CLIENT_URL}/process`, { timeout: '30s' });

  backoffLatency.add(res.timings.duration);
  backoffErrors.add(res.status !== 200);

  check(res, {
    'backoff: status ok or expected error': (r) => r.status === 200 || r.status === 502,
  });

  sleep(0.5);
}

// Jitter test scenario
export function jitterTest() {
  // Set strategy on first iteration
  if (__ITER === 0 && __VU === 1) {
    setStrategy('jitter');
    // Trigger failure after 5 seconds
    sleep(5);
    triggerFailure(10);
  }

  const res = http.get(`${CLIENT_URL}/process`, { timeout: '30s' });

  jitterLatency.add(res.timings.duration);
  jitterErrors.add(res.status !== 200);

  check(res, {
    'jitter: status ok or expected error': (r) => r.status === 200 || r.status === 502,
  });

  sleep(0.5);
}

export function teardown(data) {
  // Reset to jitter (best practice)
  setStrategy('jitter');
  const duration = (Date.now() - data.startTime) / 1000;
  console.log(`\nComparison test completed in ${duration.toFixed(1)} seconds`);
}

export function handleSummary(data) {
  const summary = {
    naive: {
      error_rate: ((data.metrics.naive_errors?.values?.rate || 0) * 100).toFixed(2) + '%',
      avg_latency: (data.metrics.naive_latency?.values?.avg || 0).toFixed(0) + 'ms',
      p95_latency: (data.metrics.naive_latency?.values['p(95)'] || 0).toFixed(0) + 'ms',
    },
    backoff: {
      error_rate: ((data.metrics.backoff_errors?.values?.rate || 0) * 100).toFixed(2) + '%',
      avg_latency: (data.metrics.backoff_latency?.values?.avg || 0).toFixed(0) + 'ms',
      p95_latency: (data.metrics.backoff_latency?.values['p(95)'] || 0).toFixed(0) + 'ms',
    },
    jitter: {
      error_rate: ((data.metrics.jitter_errors?.values?.rate || 0) * 100).toFixed(2) + '%',
      avg_latency: (data.metrics.jitter_latency?.values?.avg || 0).toFixed(0) + 'ms',
      p95_latency: (data.metrics.jitter_latency?.values['p(95)'] || 0).toFixed(0) + 'ms',
    },
  };

  console.log('\n' + '='.repeat(60));
  console.log('RETRY STRATEGY COMPARISON RESULTS');
  console.log('='.repeat(60));
  console.log('\nStrategy      | Error Rate | Avg Latency | P95 Latency');
  console.log('-'.repeat(60));
  console.log(`Naive         | ${summary.naive.error_rate.padEnd(10)} | ${summary.naive.avg_latency.padEnd(11)} | ${summary.naive.p95_latency}`);
  console.log(`Backoff       | ${summary.backoff.error_rate.padEnd(10)} | ${summary.backoff.avg_latency.padEnd(11)} | ${summary.backoff.p95_latency}`);
  console.log(`Jitter        | ${summary.jitter.error_rate.padEnd(10)} | ${summary.jitter.avg_latency.padEnd(11)} | ${summary.jitter.p95_latency}`);
  console.log('-'.repeat(60));
  console.log('\nKey observations:');
  console.log('- Naive: Fast failures, but hammers the backend');
  console.log('- Backoff: Better recovery, but synchronized spikes');
  console.log('- Jitter: Best overall - smooth load distribution');

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
