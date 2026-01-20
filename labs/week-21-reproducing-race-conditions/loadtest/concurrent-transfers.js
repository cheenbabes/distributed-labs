import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

/**
 * Concurrent Transfers Test
 *
 * This test is designed to reliably trigger race conditions by:
 * 1. Having multiple VUs transfer from the same account simultaneously
 * 2. Each transfer is large relative to the balance
 *
 * With barrier synchronization enabled on the server, transfers will
 * execute at exactly the same time, guaranteeing the race condition.
 */

const errorRate = new Rate('errors');
const raceConditions = new Counter('race_conditions_triggered');
const doubleSpends = new Counter('double_spends');
const transferLatency = new Trend('transfer_latency');

export const options = {
  // Run exactly 2 VUs to match the barrier count
  vus: 2,
  iterations: 2,
  thresholds: {
    errors: ['rate<0.9'], // We expect some "failures" due to race conditions
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab21-bank-api:8000';

export function setup() {
  // Reset and configure for race condition reproduction
  console.log('Setting up race condition test...');

  // Reset database
  http.post(`${BASE_URL}/reset`);

  // Check initial balance
  const aliceRes = http.get(`${BASE_URL}/accounts/alice`);
  console.log('Initial Alice balance:', aliceRes.json().balance);

  return { initial_balance: aliceRes.json().balance };
}

export default function (data) {
  // Both VUs will try to transfer $800 from Alice's $1000 account
  // If both succeed, that's $1600 from a $1000 account = double spend!

  const payload = JSON.stringify({
    from_account: 'alice',
    to_account: 'bob',
    amount: 800,  // Large amount relative to balance
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
    timeout: '60s',  // Barrier may cause delays
  };

  console.log(`VU ${__VU}: Starting transfer of $800 from Alice...`);

  const start = new Date();
  const res = http.post(`${BASE_URL}/transfer`, payload, params);
  const duration = new Date() - start;

  console.log(`VU ${__VU}: Transfer completed in ${duration}ms, status=${res.status}`);

  transferLatency.add(duration);
  errorRate.add(res.status !== 200);

  if (res.status === 200) {
    const body = res.json();
    console.log(`VU ${__VU}: Transfer result:`, JSON.stringify(body, null, 2));

    if (body.from_balance_after < 0) {
      raceConditions.add(1);
      doubleSpends.add(1);
      console.log(`VU ${__VU}: RACE CONDITION DETECTED! Balance went negative: $${body.from_balance_after}`);
    }

    if (body.status === 'success' && body.from_balance_before >= 800) {
      // This transfer succeeded, but did another one also succeed?
      console.log(`VU ${__VU}: Transfer successful. Before: $${body.from_balance_before}, After: $${body.from_balance_after}`);
    }
  }

  check(res, {
    'transfer completed': (r) => r.status === 200,
  });
}

export function teardown(data) {
  console.log('\n=== FINAL RESULTS ===');

  // Get final balances
  const aliceRes = http.get(`${BASE_URL}/accounts/alice`);
  const bobRes = http.get(`${BASE_URL}/accounts/bob`);

  const aliceBalance = aliceRes.json().balance;
  const bobBalance = bobRes.json().balance;

  console.log(`Alice final balance: $${aliceBalance} (started with $${data.initial_balance})`);
  console.log(`Bob final balance: $${bobBalance}`);

  // Check for double-spend
  const totalMoved = data.initial_balance - aliceBalance;
  console.log(`Total moved from Alice: $${totalMoved}`);

  if (aliceBalance < 0) {
    console.log('\n!!! DOUBLE SPEND CONFIRMED !!!');
    console.log(`Alice has a NEGATIVE balance of $${aliceBalance}`);
    console.log('This means the race condition was successfully triggered!');
  } else if (totalMoved > data.initial_balance) {
    console.log('\n!!! DOUBLE SPEND DETECTED !!!');
    console.log(`More money was transferred than Alice had!`);
  } else {
    console.log('\nNo double-spend detected in this run.');
    console.log('Try enabling debug delays or barrier synchronization.');
  }

  // Get transaction log
  const txRes = http.get(`${BASE_URL}/transactions?limit=10`);
  console.log('\nRecent transactions:', txRes.body);
}
