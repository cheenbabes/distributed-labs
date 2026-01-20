import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter } from 'k6/metrics';

/**
 * Stale Read Test
 *
 * This test demonstrates the cache-aside pattern's potential for stale reads.
 * It creates scenarios where data can become stale in the cache.
 *
 * Pattern:
 * 1. Read a product (populates cache)
 * 2. Update the product (invalidates cache in our implementation)
 * 3. Read again (should see updated data)
 *
 * This shows that with explicit invalidation, stale reads are avoided.
 * But if TTL-based expiration was the only mechanism, stale reads would occur.
 */

const staleReads = new Counter('stale_reads');
const freshReads = new Counter('fresh_reads');

export const options = {
  vus: 1,
  iterations: 10,
};

const BASE_URL = __ENV.BASE_URL || 'http://lab08-product-service:8000';

export default function () {
  const productId = 'prod-001';
  const testPrice = Math.round(Math.random() * 1000 * 100) / 100;

  // Step 1: Read product (this will cache it)
  console.log(`\n--- Iteration ${__ITER + 1} ---`);
  console.log(`Step 1: Reading product ${productId}...`);

  const readRes1 = http.get(`${BASE_URL}/products/${productId}`);
  check(readRes1, { 'initial read ok': (r) => r.status === 200 });

  if (readRes1.status === 200) {
    const data1 = readRes1.json();
    console.log(`  Cache status: ${data1.cache_status}`);
    console.log(`  Current price: ${data1.price}`);
  }

  sleep(0.5);

  // Step 2: Update the product (this invalidates cache in cache-aside)
  console.log(`Step 2: Updating product to price ${testPrice}...`);

  const updatePayload = JSON.stringify({
    name: 'Wireless Mouse',
    description: 'Updated description',
    price: testPrice,
  });

  const updateRes = http.put(`${BASE_URL}/products/${productId}`, updatePayload, {
    headers: { 'Content-Type': 'application/json' },
  });
  check(updateRes, { 'update ok': (r) => r.status === 200 });

  if (updateRes.status === 200) {
    const updateData = updateRes.json();
    console.log(`  Cache status after update: ${updateData.cache_status}`);
  }

  sleep(0.5);

  // Step 3: Read again - should get the updated value
  console.log(`Step 3: Reading product again...`);

  const readRes2 = http.get(`${BASE_URL}/products/${productId}`);
  check(readRes2, { 'second read ok': (r) => r.status === 200 });

  if (readRes2.status === 200) {
    const data2 = readRes2.json();
    console.log(`  Cache status: ${data2.cache_status}`);
    console.log(`  Current price: ${data2.price}`);

    // Check if we got stale data
    if (Math.abs(data2.price - testPrice) < 0.01) {
      console.log(`  Result: FRESH data (prices match)`);
      freshReads.add(1);
    } else {
      console.log(`  Result: STALE data! Expected ${testPrice}, got ${data2.price}`);
      staleReads.add(1);
    }
  }

  sleep(1);
}

export function handleSummary(data) {
  console.log('\n=== Stale Read Test Results ===');
  console.log('With explicit cache invalidation on update, stale reads should be 0.');
  console.log('================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
