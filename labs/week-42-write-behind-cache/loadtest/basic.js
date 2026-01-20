import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomString } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// Custom metrics
const errorRate = new Rate('errors');
const createLatency = new Trend('create_latency');
const readLatency = new Trend('read_latency');
const updateLatency = new Trend('update_latency');
const cacheHits = new Counter('cache_hits');
const cacheMisses = new Counter('cache_misses');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 10 },  // Ramp up to 10 users
    { duration: '1m', target: 10 },   // Stay at 10 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],  // 95% of requests should be < 500ms
    errors: ['rate<0.1'],               // Error rate should be < 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab42-api:8000';

// Keep track of created product IDs for updates
const createdProducts = [];

export default function () {
  const scenario = Math.random();

  if (scenario < 0.3) {
    // 30% - Create new product
    createProduct();
  } else if (scenario < 0.6) {
    // 30% - Read product
    readProduct();
  } else {
    // 40% - Update product (to test write coalescing)
    updateProduct();
  }

  sleep(0.1);
}

function createProduct() {
  const product = {
    name: `Product-${randomString(8)}`,
    price: Math.round(Math.random() * 10000) / 100,
    quantity: Math.floor(Math.random() * 100),
  };

  const res = http.post(
    `${BASE_URL}/products`,
    JSON.stringify(product),
    { headers: { 'Content-Type': 'application/json' } }
  );

  createLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200 && res.status !== 201);

  const success = check(res, {
    'create status is 200': (r) => r.status === 200,
    'create has id': (r) => r.json().id !== undefined,
    'create source is cache': (r) => r.json().source === 'cache',
  });

  if (success && res.json().id) {
    createdProducts.push(res.json().id);
    // Keep only last 100 products to avoid memory issues
    if (createdProducts.length > 100) {
      createdProducts.shift();
    }
  }
}

function readProduct() {
  // Try to read a created product, or use a seed product
  let productId;
  if (createdProducts.length > 0 && Math.random() > 0.3) {
    productId = createdProducts[Math.floor(Math.random() * createdProducts.length)];
  } else {
    // Use seed products
    const seedIds = ['seed-001', 'seed-002', 'seed-003', 'seed-004', 'seed-005'];
    productId = seedIds[Math.floor(Math.random() * seedIds.length)];
  }

  const res = http.get(`${BASE_URL}/products/${productId}`);

  readLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200 && res.status !== 404);

  check(res, {
    'read status is 200 or 404': (r) => r.status === 200 || r.status === 404,
  });

  if (res.status === 200) {
    const source = res.json().source;
    if (source === 'cache') {
      cacheHits.add(1);
    } else {
      cacheMisses.add(1);
    }
  }
}

function updateProduct() {
  // Only update existing products
  let productId;
  if (createdProducts.length > 0) {
    productId = createdProducts[Math.floor(Math.random() * createdProducts.length)];
  } else {
    // Use seed products
    const seedIds = ['seed-001', 'seed-002', 'seed-003', 'seed-004', 'seed-005'];
    productId = seedIds[Math.floor(Math.random() * seedIds.length)];
  }

  const update = {
    name: `Updated-${randomString(6)}`,
    price: Math.round(Math.random() * 10000) / 100,
    quantity: Math.floor(Math.random() * 100),
  };

  const res = http.put(
    `${BASE_URL}/products/${productId}`,
    JSON.stringify(update),
    { headers: { 'Content-Type': 'application/json' } }
  );

  updateLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200 && res.status !== 404);

  check(res, {
    'update status is 200 or 404': (r) => r.status === 200 || r.status === 404,
    'update source is cache': (r) => r.status !== 200 || r.json().source === 'cache',
  });
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
