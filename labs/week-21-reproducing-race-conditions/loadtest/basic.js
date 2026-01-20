import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const raceConditions = new Counter('race_conditions_triggered');
const transferLatency = new Trend('transfer_latency');

// Test configuration
export const options = {
  stages: [
    { duration: '10s', target: 5 },   // Ramp up
    { duration: '30s', target: 10 },  // Sustained load
    { duration: '10s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<5000'],
    errors: ['rate<0.5'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab21-bank-api:8000';

export function setup() {
  // Reset the database before testing
  const resetRes = http.post(`${BASE_URL}/reset`);
  console.log('Database reset:', resetRes.status);
  return {};
}

export default function () {
  // Perform a transfer from Alice to Bob
  const payload = JSON.stringify({
    from_account: 'alice',
    to_account: 'bob',
    amount: 100,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
  };

  const res = http.post(`${BASE_URL}/transfer`, payload, params);

  // Record metrics
  transferLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  // Check for race condition indicators
  if (res.status === 200) {
    const body = res.json();
    if (body.from_balance_after < 0) {
      raceConditions.add(1);
      console.log('RACE CONDITION: Negative balance detected!', body.from_balance_after);
    }
  }

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'has transfer id': (r) => r.json().id !== undefined,
    'has balances': (r) => r.json().from_balance_after !== undefined,
  });

  // Small pause between requests
  sleep(0.1);
}

export function teardown(data) {
  // Get final account balances
  const accountsRes = http.get(`${BASE_URL}/accounts`);
  console.log('Final account balances:', accountsRes.body);
}
