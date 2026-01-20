import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter } from 'k6/metrics';

/**
 * Durability Test Script
 *
 * This test writes data to both WAL and no-WAL stores, then triggers
 * crashes to demonstrate durability differences.
 *
 * Usage:
 *   k6 run durability-test.js
 */

const writeCount = new Counter('writes_total');

export const options = {
  vus: 1,
  iterations: 1,
  thresholds: {},
};

const WAL_URL = __ENV.WAL_URL || 'http://lab25-kv-store-wal:8000';
const NOWAL_URL = __ENV.NOWAL_URL || 'http://lab25-kv-store-nowal:8000';

export default function () {
  const testKeys = [];

  console.log('\n=== Phase 1: Write data to both stores ===\n');

  // Write 50 keys to both stores
  for (let i = 0; i < 50; i++) {
    const key = `durability-test-${i}`;
    const value = { test: 'data', index: i, timestamp: Date.now() };
    testKeys.push(key);

    // Write to WAL store
    const walRes = http.post(
      `${WAL_URL}/kv`,
      JSON.stringify({ key: key, value: value }),
      { headers: { 'Content-Type': 'application/json' } }
    );

    // Write to no-WAL store
    const nowalRes = http.post(
      `${NOWAL_URL}/kv`,
      JSON.stringify({ key: key, value: value }),
      { headers: { 'Content-Type': 'application/json' } }
    );

    check(walRes, { 'WAL write ok': (r) => r.status === 200 });
    check(nowalRes, { 'No-WAL write ok': (r) => r.status === 200 });
    writeCount.add(2);
  }

  console.log(`Wrote ${testKeys.length} keys to each store`);

  // Check stats before crash
  console.log('\n=== Phase 2: Check stats before crash ===\n');

  const walStatsBefore = http.get(`${WAL_URL}/admin/stats`).json();
  const nowalStatsBefore = http.get(`${NOWAL_URL}/admin/stats`).json();

  console.log('WAL store before crash:', JSON.stringify(walStatsBefore, null, 2));
  console.log('No-WAL store before crash:', JSON.stringify(nowalStatsBefore, null, 2));

  console.log('\n=== Phase 3: Trigger crashes ===\n');
  console.log('Triggering crash on both stores...');
  console.log('After containers restart, run:');
  console.log('  curl http://localhost:8001/admin/stats  # WAL store - should have data');
  console.log('  curl http://localhost:8002/admin/stats  # No-WAL store - data lost');

  // Trigger crashes (these will cause containers to restart)
  try {
    http.post(`${WAL_URL}/admin/crash`);
  } catch (e) {
    console.log('WAL crash triggered (expected connection error)');
  }

  sleep(0.5);

  try {
    http.post(`${NOWAL_URL}/admin/crash`);
  } catch (e) {
    console.log('No-WAL crash triggered (expected connection error)');
  }

  console.log('\nWait for containers to restart, then verify recovery.');
}
