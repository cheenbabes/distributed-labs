import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter } from 'k6/metrics';

/**
 * Burst Test - Tests how each algorithm handles sudden bursts of traffic
 *
 * This test sends bursts of requests to observe:
 * - Fixed Window: allows burst at window boundary
 * - Sliding Window: smooth rejection
 * - Token Bucket: allows burst up to bucket size
 * - Leaky Bucket: smooth, queued processing
 */

const allowedCount = new Counter('allowed_total');
const rejectedCount = new Counter('rejected_total');
const burstAllowed = new Counter('burst_allowed');

export const options = {
  scenarios: {
    burst: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 1,
      maxDuration: '2m',
    },
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://rate-limiter:8000';
const BURST_SIZE = 20;  // Send 20 requests rapidly (2x rate limit)

function sendBurst(label) {
  console.log(`\n=== Sending burst: ${label} ===`);
  let allowed = 0;
  let rejected = 0;

  for (let i = 0; i < BURST_SIZE; i++) {
    const res = http.get(`${BASE_URL}/api/request`);

    if (res.status === 200) {
      allowed++;
      allowedCount.add(1);
      burstAllowed.add(1);
    } else if (res.status === 429) {
      rejected++;
      rejectedCount.add(1);
    }
  }

  console.log(`${label}: ${allowed} allowed, ${rejected} rejected`);
  return { allowed, rejected };
}

export default function () {
  // Test 1: Initial burst (bucket should be full)
  console.log('\n========================================');
  console.log('BURST TEST - Testing rate limit behavior');
  console.log('========================================');

  const burst1 = sendBurst('Burst 1 (bucket full)');

  // Wait for partial refill
  console.log('\nWaiting 0.5 seconds for partial refill...');
  sleep(0.5);

  // Test 2: Burst after partial refill
  const burst2 = sendBurst('Burst 2 (partial refill)');

  // Wait for full refill
  console.log('\nWaiting 2 seconds for full refill...');
  sleep(2);

  // Test 3: Burst after full refill
  const burst3 = sendBurst('Burst 3 (full refill)');

  console.log('\n========================================');
  console.log('BURST TEST COMPLETE');
  console.log(`Total allowed: ${burst1.allowed + burst2.allowed + burst3.allowed}`);
  console.log(`Total rejected: ${burst1.rejected + burst2.rejected + burst3.rejected}`);
  console.log('========================================\n');
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
