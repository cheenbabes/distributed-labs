import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter } from 'k6/metrics';

/**
 * Algorithm Comparison Test
 *
 * Tests each algorithm sequentially under identical load conditions.
 * Switches algorithm, resets state, runs load test, collects metrics.
 */

const allowedFixed = new Counter('allowed_fixed_window');
const rejectedFixed = new Counter('rejected_fixed_window');
const allowedSliding = new Counter('allowed_sliding_window');
const rejectedSliding = new Counter('rejected_sliding_window');
const allowedToken = new Counter('allowed_token_bucket');
const rejectedToken = new Counter('rejected_token_bucket');
const allowedLeaky = new Counter('allowed_leaky_bucket');
const rejectedLeaky = new Counter('rejected_leaky_bucket');

export const options = {
  scenarios: {
    comparison: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 1,
      maxDuration: '5m',
    },
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://rate-limiter:8000';
const TEST_DURATION_MS = 5000;  // 5 seconds per algorithm
const REQUEST_INTERVAL_MS = 50; // 20 requests/second (2x rate limit)

function setAlgorithm(algorithm) {
  const res = http.post(
    `${BASE_URL}/admin/algorithm`,
    JSON.stringify({ algorithm: algorithm }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  check(res, { 'algorithm set': (r) => r.status === 200 });
}

function resetLimiter() {
  const res = http.post(`${BASE_URL}/admin/reset`);
  check(res, { 'limiter reset': (r) => r.status === 200 });
}

function runLoadTest(algorithm, allowedCounter, rejectedCounter) {
  console.log(`\nTesting ${algorithm}...`);

  const startTime = Date.now();
  let allowed = 0;
  let rejected = 0;

  while (Date.now() - startTime < TEST_DURATION_MS) {
    const res = http.get(`${BASE_URL}/api/request`);

    if (res.status === 200) {
      allowed++;
      allowedCounter.add(1);
    } else if (res.status === 429) {
      rejected++;
      rejectedCounter.add(1);
    }

    sleep(REQUEST_INTERVAL_MS / 1000);
  }

  const total = allowed + rejected;
  const allowedPct = (allowed / total * 100).toFixed(1);
  console.log(`${algorithm}: ${allowed}/${total} allowed (${allowedPct}%)`);

  return { allowed, rejected };
}

export default function () {
  console.log('=========================================');
  console.log('ALGORITHM COMPARISON TEST');
  console.log('Rate Limit: 10 req/s | Test Load: ~20 req/s');
  console.log('=========================================\n');

  const results = {};

  // Test Fixed Window
  setAlgorithm('fixed_window');
  resetLimiter();
  sleep(0.5);
  results.fixed_window = runLoadTest('Fixed Window', allowedFixed, rejectedFixed);

  // Test Sliding Window
  setAlgorithm('sliding_window');
  resetLimiter();
  sleep(0.5);
  results.sliding_window = runLoadTest('Sliding Window', allowedSliding, rejectedSliding);

  // Test Token Bucket
  setAlgorithm('token_bucket');
  resetLimiter();
  sleep(0.5);
  results.token_bucket = runLoadTest('Token Bucket', allowedToken, rejectedToken);

  // Test Leaky Bucket
  setAlgorithm('leaky_bucket');
  resetLimiter();
  sleep(0.5);
  results.leaky_bucket = runLoadTest('Leaky Bucket', allowedLeaky, rejectedLeaky);

  console.log('\n=========================================');
  console.log('COMPARISON RESULTS');
  console.log('=========================================');

  for (const [algo, result] of Object.entries(results)) {
    const total = result.allowed + result.rejected;
    const pct = (result.allowed / total * 100).toFixed(1);
    console.log(`${algo.padEnd(20)}: ${result.allowed.toString().padStart(4)} allowed, ${result.rejected.toString().padStart(4)} rejected (${pct}% allowed)`);
  }

  console.log('=========================================\n');
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
