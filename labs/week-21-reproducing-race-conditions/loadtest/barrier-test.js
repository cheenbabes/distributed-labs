import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

/**
 * Barrier Synchronization Test
 *
 * This test demonstrates deterministic race condition reproduction using
 * barrier synchronization. The server-side barrier ensures that exactly N
 * transfers execute at the same instant, guaranteeing the race.
 *
 * Usage:
 * 1. Enable barrier: curl -X POST localhost:8000/admin/barrier -d 'enabled=true&count=2'
 * 2. Run this test with 2 VUs
 * 3. Watch both transfers execute simultaneously and trigger the race
 */

const raceConditions = new Counter('race_conditions_triggered');
const transferLatency = new Trend('transfer_latency');

export const options = {
  // Must match barrier count on server
  vus: 2,
  iterations: 2,
  thresholds: {},
};

const BASE_URL = __ENV.BASE_URL || 'http://lab21-bank-api:8000';

export function setup() {
  console.log('=== BARRIER SYNCHRONIZATION TEST ===\n');

  // Reset database
  http.post(`${BASE_URL}/reset`);
  console.log('Database reset to initial state');

  // Configure barrier
  const barrierRes = http.post(`${BASE_URL}/admin/barrier?enabled=true&count=2`);
  console.log('Barrier configured:', barrierRes.body);

  // Enable debug delay to make the race window wider
  const delayRes = http.post(`${BASE_URL}/admin/delay?enabled=true&ms=200`);
  console.log('Debug delay enabled:', delayRes.body);

  // Get initial state
  const aliceRes = http.get(`${BASE_URL}/accounts/alice`);
  console.log(`\nInitial Alice balance: $${aliceRes.json().balance}`);
  console.log('Each VU will try to transfer $800');
  console.log('Total attempted: $1600 from a $1000 account');
  console.log('\nWaiting for barrier to release both transfers simultaneously...\n');

  return { initial_balance: aliceRes.json().balance };
}

export default function (data) {
  const payload = JSON.stringify({
    from_account: 'alice',
    to_account: 'bob',
    amount: 800,
  });

  const params = {
    headers: { 'Content-Type': 'application/json' },
    timeout: '60s',
  };

  console.log(`[VU ${__VU}] Sending transfer request (will wait at barrier)...`);

  const start = new Date();
  const res = http.post(`${BASE_URL}/transfer`, payload, params);
  const duration = new Date() - start;

  transferLatency.add(duration);

  if (res.status === 200) {
    const body = res.json();
    console.log(`[VU ${__VU}] Transfer completed in ${duration}ms`);
    console.log(`[VU ${__VU}] Balance: $${body.from_balance_before} -> $${body.from_balance_after}`);

    if (body.from_balance_after < 0) {
      raceConditions.add(1);
      console.log(`[VU ${__VU}] *** RACE CONDITION: Negative balance! ***`);
    }
  } else {
    console.log(`[VU ${__VU}] Transfer failed:`, res.status, res.body);
  }

  check(res, {
    'transfer completed': (r) => r.status === 200,
  });
}

export function teardown(data) {
  console.log('\n=== RESULTS ===\n');

  const aliceRes = http.get(`${BASE_URL}/accounts/alice`);
  const bobRes = http.get(`${BASE_URL}/accounts/bob`);

  const aliceBalance = aliceRes.json().balance;
  const bobBalance = bobRes.json().balance;

  console.log(`Alice: $${data.initial_balance} -> $${aliceBalance}`);
  console.log(`Bob: $500 -> $${bobBalance}`);

  if (aliceBalance < 0) {
    console.log('\n*** RACE CONDITION SUCCESSFULLY REPRODUCED ***');
    console.log('The barrier forced both transfers to execute simultaneously.');
    console.log('Both read Alice\'s balance as $1000, both approved $800 transfers.');
    console.log(`Result: Alice has $${aliceBalance} (impossible without the bug!)`);
  }

  // Disable barrier and delay for cleanup
  http.post(`${BASE_URL}/admin/barrier?enabled=false`);
  http.post(`${BASE_URL}/admin/delay?enabled=false`);
  console.log('\nBarrier and delay disabled.');
}
