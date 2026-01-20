import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter } from 'k6/metrics';

/**
 * Fixed Window Boundary Test
 *
 * Demonstrates the "boundary burst problem" with fixed window rate limiting.
 * By timing requests at window boundaries, we can briefly exceed the rate limit.
 *
 * Strategy:
 * 1. Wait until near end of window
 * 2. Send burst (uses remaining quota)
 * 3. Window resets
 * 4. Send another burst immediately (new quota)
 * Result: 2x rate limit in a short period!
 */

const allowedCount = new Counter('allowed_total');
const rejectedCount = new Counter('rejected_total');

export const options = {
  scenarios: {
    boundary: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 1,
      maxDuration: '2m',
    },
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://rate-limiter:8000';

function setAlgorithm(algorithm) {
  http.post(
    `${BASE_URL}/admin/algorithm`,
    JSON.stringify({ algorithm: algorithm }),
    { headers: { 'Content-Type': 'application/json' } }
  );
}

function resetLimiter() {
  http.post(`${BASE_URL}/admin/reset`);
}

function sendRequests(count) {
  let allowed = 0;
  let rejected = 0;

  for (let i = 0; i < count; i++) {
    const res = http.get(`${BASE_URL}/api/request`);
    if (res.status === 200) {
      allowed++;
      allowedCount.add(1);
    } else {
      rejected++;
      rejectedCount.add(1);
    }
  }

  return { allowed, rejected };
}

export default function () {
  console.log('=========================================');
  console.log('FIXED WINDOW BOUNDARY BURST TEST');
  console.log('=========================================\n');
  console.log('This test demonstrates the boundary burst problem');
  console.log('with fixed window rate limiting.\n');

  // Set to fixed window algorithm
  setAlgorithm('fixed_window');
  resetLimiter();

  // Get current stats to understand timing
  const statsRes = http.get(`${BASE_URL}/admin/stats`);
  console.log('Initial stats:', statsRes.body);

  console.log('\n--- Phase 1: Consume quota near window end ---');

  // Send 10 requests (exactly rate limit)
  const phase1 = sendRequests(10);
  console.log(`Phase 1: ${phase1.allowed} allowed, ${phase1.rejected} rejected`);

  // Wait just a bit less than 1 second (window period)
  console.log('\nWaiting 0.9 seconds (window period is 1s)...');
  sleep(0.9);

  // Send 10 more - some might make it in this window, rest in next
  console.log('\n--- Phase 2: Burst at window boundary ---');
  const phase2 = sendRequests(10);
  console.log(`Phase 2: ${phase2.allowed} allowed, ${phase2.rejected} rejected`);

  // Wait for window to definitely reset
  sleep(0.2);

  // Now send more
  console.log('\n--- Phase 3: After window reset ---');
  const phase3 = sendRequests(10);
  console.log(`Phase 3: ${phase3.allowed} allowed, ${phase3.rejected} rejected`);

  const totalAllowed = phase1.allowed + phase2.allowed + phase3.allowed;
  const totalRejected = phase1.rejected + phase2.rejected + phase3.rejected;

  console.log('\n=========================================');
  console.log('BOUNDARY TEST RESULTS');
  console.log('=========================================');
  console.log(`Total in ~2 second window:`);
  console.log(`  Allowed: ${totalAllowed}`);
  console.log(`  Rejected: ${totalRejected}`);
  console.log(`  Rate Limit: 10 req/s`);
  console.log(`  Expected max in 2s: 20`);
  console.log(`  If > 20: Boundary burst detected!`);
  console.log('=========================================\n');

  // Compare with sliding window
  console.log('\n--- Comparison: Same test with Sliding Window ---\n');

  setAlgorithm('sliding_window');
  resetLimiter();

  const sw1 = sendRequests(10);
  console.log(`Sliding Phase 1: ${sw1.allowed} allowed, ${sw1.rejected} rejected`);

  sleep(0.9);

  const sw2 = sendRequests(10);
  console.log(`Sliding Phase 2: ${sw2.allowed} allowed, ${sw2.rejected} rejected`);

  sleep(0.2);

  const sw3 = sendRequests(10);
  console.log(`Sliding Phase 3: ${sw3.allowed} allowed, ${sw3.rejected} rejected`);

  const swTotal = sw1.allowed + sw2.allowed + sw3.allowed;
  console.log(`\nSliding Window Total Allowed: ${swTotal}`);
  console.log('(Should be closer to 20, more accurate limiting)');
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
