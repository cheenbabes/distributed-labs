import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';
import { uuidv4 } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

// Custom metrics
const duplicateBlockedRate = new Rate('duplicate_requests_blocked');
const conflictRate = new Rate('idempotency_conflicts');
const paymentSuccessRate = new Rate('payment_success');
const retryLatency = new Trend('retry_response_time');
const newRequestLatency = new Trend('new_request_time');
const conflictsTotal = new Counter('conflicts_total');

// Configuration
const BASE_URL = __ENV.BASE_URL || 'http://lab13-payment-api:8000';

export const options = {
  scenarios: {
    // Scenario 1: Normal payments with idempotency keys
    normal_payments: {
      executor: 'constant-vus',
      vus: 5,
      duration: '1m',
      exec: 'normalPaymentWithIdempotency',
      tags: { scenario: 'normal' },
    },
    // Scenario 2: Retry simulation (same key, same body)
    retry_simulation: {
      executor: 'constant-vus',
      vus: 3,
      duration: '1m',
      exec: 'retryPayment',
      startTime: '10s',
      tags: { scenario: 'retry' },
    },
    // Scenario 3: Conflict simulation (same key, different body)
    conflict_simulation: {
      executor: 'constant-vus',
      vus: 2,
      duration: '1m',
      exec: 'conflictPayment',
      startTime: '20s',
      tags: { scenario: 'conflict' },
    },
    // Scenario 4: Concurrent requests with same key
    concurrent_race: {
      executor: 'per-vu-iterations',
      vus: 5,
      iterations: 10,
      exec: 'concurrentRace',
      startTime: '30s',
      tags: { scenario: 'concurrent' },
    },
    // Scenario 5: No idempotency key (dangerous!)
    no_idempotency: {
      executor: 'constant-vus',
      vus: 2,
      duration: '30s',
      exec: 'paymentWithoutIdempotency',
      startTime: '45s',
      tags: { scenario: 'no_key' },
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    payment_success: ['rate>0.9'],
    idempotency_conflicts: ['rate<0.2'],
  },
};

// Helper to create payment payload
function createPaymentPayload(amount = null) {
  return JSON.stringify({
    amount: amount || Math.floor(Math.random() * 10000) + 100, // Random 1-100 dollars
    currency: 'usd',
    description: `Test payment from k6 at ${new Date().toISOString()}`,
    metadata: {
      test_id: uuidv4(),
      vu_id: __VU,
    },
  });
}

// Scenario 1: Normal payment with idempotency key
export function normalPaymentWithIdempotency() {
  const idempotencyKey = `k6-normal-${uuidv4()}`;
  const payload = createPaymentPayload();

  const res = http.post(`${BASE_URL}/payments`, payload, {
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
  });

  const success = check(res, {
    'status is 200': (r) => r.status === 200,
    'has payment id': (r) => r.json() && r.json().id,
    'has idempotency key': (r) => r.json() && r.json().idempotency_key === idempotencyKey,
    'not cached': (r) => r.json() && r.json().cached === false,
  });

  paymentSuccessRate.add(success);
  newRequestLatency.add(res.timings.duration);

  sleep(0.5);
}

// Scenario 2: Retry simulation - send same request twice
export function retryPayment() {
  const idempotencyKey = `k6-retry-${uuidv4()}`;
  const payload = createPaymentPayload(5000); // Fixed amount for consistency

  // First request - should succeed and be processed
  const res1 = http.post(`${BASE_URL}/payments`, payload, {
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
  });

  check(res1, {
    'first request succeeds': (r) => r.status === 200,
    'first request not cached': (r) => r.json() && r.json().cached === false,
  });

  const firstPaymentId = res1.json() && res1.json().id;

  // Small delay to simulate network retry
  sleep(0.1);

  // Second request (retry) - should return cached response
  const res2 = http.post(`${BASE_URL}/payments`, payload, {
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
  });

  const duplicateBlocked = check(res2, {
    'retry request succeeds': (r) => r.status === 200,
    'retry is cached': (r) => r.json() && r.json().cached === true,
    'same payment id returned': (r) => r.json() && r.json().id === firstPaymentId,
  });

  duplicateBlockedRate.add(duplicateBlocked);
  retryLatency.add(res2.timings.duration);

  sleep(1);
}

// Scenario 3: Conflict simulation - same key, different body
export function conflictPayment() {
  const idempotencyKey = `k6-conflict-${uuidv4()}`;

  // First request with amount 1000
  const payload1 = createPaymentPayload(1000);
  const res1 = http.post(`${BASE_URL}/payments`, payload1, {
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
  });

  check(res1, {
    'first request succeeds': (r) => r.status === 200,
  });

  // Second request with DIFFERENT amount using SAME key
  const payload2 = createPaymentPayload(2000); // Different amount!
  const res2 = http.post(`${BASE_URL}/payments`, payload2, {
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
  });

  const isConflict = check(res2, {
    'conflict returns 409': (r) => r.status === 409,
    'conflict has error message': (r) => r.json() && r.json().detail && r.json().detail.error === 'idempotency_key_conflict',
  });

  conflictRate.add(isConflict);
  if (isConflict) {
    conflictsTotal.add(1);
  }

  sleep(1);
}

// Scenario 4: Race condition - concurrent requests with same key
export function concurrentRace() {
  const idempotencyKey = `k6-race-${uuidv4()}`;
  const payload = createPaymentPayload(7500);

  // Send 3 concurrent requests with the same idempotency key
  const responses = http.batch([
    ['POST', `${BASE_URL}/payments`, payload, {
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
    }],
    ['POST', `${BASE_URL}/payments`, payload, {
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
    }],
    ['POST', `${BASE_URL}/payments`, payload, {
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
    }],
  ]);

  // All should succeed with the same payment ID
  const paymentIds = responses
    .filter(r => r.status === 200 && r.json())
    .map(r => r.json().id);

  const uniqueIds = [...new Set(paymentIds)];

  check(responses, {
    'all requests succeed': (r) => r.every(res => res.status === 200),
    'all return same payment ID': () => uniqueIds.length === 1,
    'at least one was cached': (r) => r.some(res => res.json() && res.json().cached === true),
  });

  sleep(2);
}

// Scenario 5: Payment without idempotency key (dangerous!)
export function paymentWithoutIdempotency() {
  const payload = createPaymentPayload(250);

  // Send same request twice WITHOUT idempotency key
  const res1 = http.post(`${BASE_URL}/payments`, payload, {
    headers: {
      'Content-Type': 'application/json',
      // NO Idempotency-Key header!
    },
  });

  const res2 = http.post(`${BASE_URL}/payments`, payload, {
    headers: {
      'Content-Type': 'application/json',
      // NO Idempotency-Key header!
    },
  });

  // Without idempotency key, each request creates a new payment!
  const danger = check([res1, res2], {
    'both requests succeed': (r) => r[0].status === 200 && r[1].status === 200,
    'DANGER: different payment IDs!': (r) => {
      const id1 = r[0].json() && r[0].json().id;
      const id2 = r[1].json() && r[1].json().id;
      return id1 !== id2; // This is the PROBLEM we want to show
    },
    'neither is cached': (r) => {
      return (!r[0].json().cached) && (!r[1].json().cached);
    },
  });

  sleep(1);
}

// Summary handler
export function handleSummary(data) {
  console.log('\n========================================');
  console.log('  IDEMPOTENCY KEY TEST SUMMARY');
  console.log('========================================\n');

  const checks = data.metrics.checks;
  if (checks) {
    console.log(`Total Checks: ${checks.values.passes + checks.values.fails}`);
    console.log(`  Passed: ${checks.values.passes}`);
    console.log(`  Failed: ${checks.values.fails}`);
    console.log(`  Rate: ${(checks.values.rate * 100).toFixed(2)}%\n`);
  }

  if (data.metrics.duplicate_requests_blocked) {
    console.log(`Duplicate Requests Blocked: ${(data.metrics.duplicate_requests_blocked.values.rate * 100).toFixed(2)}%`);
  }

  if (data.metrics.idempotency_conflicts) {
    console.log(`Idempotency Conflicts Detected: ${(data.metrics.idempotency_conflicts.values.rate * 100).toFixed(2)}%`);
  }

  if (data.metrics.conflicts_total) {
    console.log(`Total Conflicts: ${data.metrics.conflicts_total.values.count}`);
  }

  console.log('\n========================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
