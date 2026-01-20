import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter } from 'k6/metrics';
import { randomString } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

/**
 * Data Loss Risk Test
 *
 * This test creates products while we simulate cache failures.
 * Products written to cache but not yet persisted to DB will be LOST.
 *
 * Run with: docker compose run --rm lab42-k6 run /scripts/data-loss-test.js
 *
 * IMPORTANT: Before running, enable DB failure simulation:
 * docker compose stop lab42-worker
 * docker compose up -d lab42-worker -e SIMULATE_DB_FAILURE=true -e FAILURE_RATE=0.5
 */

const errorRate = new Rate('errors');
const productsCreated = new Counter('products_created');
const createdIds = [];

export const options = {
  stages: [
    { duration: '20s', target: 5 },
    { duration: '30s', target: 5 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    errors: ['rate<0.2'],  // Allow higher error rate for this test
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab42-api:8000';

export function setup() {
  console.log('\n=== DATA LOSS RISK TEST ===');
  console.log('This test demonstrates what happens when the cache fails');
  console.log('before writes are persisted to the database.\n');
  console.log('To simulate failures, restart the worker with:');
  console.log('  SIMULATE_DB_FAILURE=true FAILURE_RATE=0.5\n');
  return { startTime: Date.now() };
}

export default function () {
  const product = {
    name: `DataLoss-${randomString(8)}`,
    price: Math.round(Math.random() * 10000) / 100,
    quantity: Math.floor(Math.random() * 100),
  };

  const res = http.post(
    `${BASE_URL}/products`,
    JSON.stringify(product),
    { headers: { 'Content-Type': 'application/json' } }
  );

  errorRate.add(res.status !== 200);

  if (res.status === 200) {
    productsCreated.add(1);
    const id = res.json().id;
    createdIds.push(id);
  }

  check(res, {
    'create status is 200': (r) => r.status === 200,
  });

  sleep(0.2);
}

export function teardown(data) {
  console.log('\n=== VERIFICATION ===');
  console.log(`Products created via API: ${createdIds.length}`);

  // Wait for queue to process
  console.log('Waiting 5 seconds for write queue to process...');
  sleep(5);

  // Now verify which products actually made it to the database
  let foundInCache = 0;
  let foundInDb = 0;
  let missing = 0;

  for (let i = 0; i < Math.min(createdIds.length, 50); i++) {
    const id = createdIds[i];
    const res = http.get(`${BASE_URL}/products/${id}`);

    if (res.status === 200) {
      if (res.json().source === 'cache') {
        foundInCache++;
      } else {
        foundInDb++;
      }
    } else {
      missing++;
    }
  }

  console.log(`\nSample of ${Math.min(createdIds.length, 50)} products:`);
  console.log(`  Found in cache: ${foundInCache}`);
  console.log(`  Found in database: ${foundInDb}`);
  console.log(`  MISSING (DATA LOSS): ${missing}`);

  if (missing > 0) {
    console.log('\n*** DATA LOSS DETECTED ***');
    console.log('These products were acknowledged to the client but never persisted!');
  }
}
