import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomString } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// Custom metrics
const errorRate = new Rate('errors');
const orderLatency = new Trend('order_latency');
const duplicateSent = new Counter('duplicate_orders_sent');
const batchLatency = new Trend('batch_latency');

// Test configuration
export const options = {
  scenarios: {
    // Steady load for baseline
    steady_load: {
      executor: 'constant-rate',
      rate: 10,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 20,
      maxVUs: 50,
      exec: 'singleOrder',
    },
    // Burst load to test exactly-once under pressure
    burst_load: {
      executor: 'ramping-rate',
      startRate: 5,
      timeUnit: '1s',
      stages: [
        { duration: '30s', target: 5 },
        { duration: '30s', target: 50 },  // Spike
        { duration: '30s', target: 5 },
        { duration: '30s', target: 100 }, // Bigger spike
        { duration: '30s', target: 5 },
      ],
      preAllocatedVUs: 100,
      maxVUs: 200,
      exec: 'singleOrder',
      startTime: '2m',
    },
    // Duplicate injection test
    duplicate_test: {
      executor: 'constant-rate',
      rate: 2,
      timeUnit: '1s',
      duration: '1m',
      preAllocatedVUs: 5,
      maxVUs: 10,
      exec: 'duplicateOrder',
      startTime: '5m',
    },
    // Batch transaction test
    batch_test: {
      executor: 'constant-rate',
      rate: 1,
      timeUnit: '1s',
      duration: '1m',
      preAllocatedVUs: 5,
      maxVUs: 10,
      exec: 'batchOrders',
      startTime: '6m',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    errors: ['rate<0.05'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://producer:8000';
const API_URL = __ENV.API_URL || 'http://api:8003';

// Generate a random order
function generateOrder(orderId = null) {
  const items = [
    { name: 'Widget A', quantity: Math.floor(Math.random() * 5) + 1, price: 19.99 },
    { name: 'Gadget B', quantity: Math.floor(Math.random() * 3) + 1, price: 49.99 },
  ];

  const totalAmount = items.reduce((sum, item) => sum + (item.price * item.quantity), 0);

  return {
    order_id: orderId || `order-${randomString(8)}`,
    customer_id: `customer-${Math.floor(Math.random() * 1000)}`,
    items: items,
    total_amount: Math.round(totalAmount * 100) / 100,
  };
}

// Single order test
export function singleOrder() {
  group('Single Order', () => {
    const order = generateOrder();

    const res = http.post(
      `${BASE_URL}/orders`,
      JSON.stringify(order),
      {
        headers: { 'Content-Type': 'application/json' },
        tags: { type: 'single_order' },
      }
    );

    orderLatency.add(res.timings.duration);

    const success = check(res, {
      'status is 200': (r) => r.status === 200,
      'order accepted': (r) => r.json().status === 'accepted',
      'has trace_id': (r) => r.json().trace_id !== undefined,
    });

    errorRate.add(!success);

    if (!success) {
      console.log(`Order failed: ${res.status} - ${res.body}`);
    }
  });

  sleep(0.1);
}

// Duplicate order test - send same order multiple times
export function duplicateOrder() {
  group('Duplicate Order Test', () => {
    const orderId = `dup-order-${randomString(6)}`;
    const order = generateOrder(orderId);

    // Send the same order 3 times
    for (let i = 0; i < 3; i++) {
      const res = http.post(
        `${BASE_URL}/orders`,
        JSON.stringify(order),
        {
          headers: { 'Content-Type': 'application/json' },
          tags: { type: 'duplicate_order', attempt: `${i + 1}` },
        }
      );

      check(res, {
        'duplicate sent successfully': (r) => r.status === 200,
      });

      duplicateSent.add(1);

      sleep(0.05); // Small delay between duplicates
    }
  });

  sleep(0.5);
}

// Batch orders test - send multiple orders in single transaction
export function batchOrders() {
  group('Batch Orders', () => {
    const batchSize = Math.floor(Math.random() * 5) + 3; // 3-7 orders
    const orders = [];

    for (let i = 0; i < batchSize; i++) {
      orders.push(generateOrder());
    }

    const res = http.post(
      `${BASE_URL}/orders/batch`,
      JSON.stringify({ orders: orders }),
      {
        headers: { 'Content-Type': 'application/json' },
        tags: { type: 'batch_order' },
      }
    );

    batchLatency.add(res.timings.duration);

    const success = check(res, {
      'batch status is 200': (r) => r.status === 200,
      'batch committed': (r) => r.json().status === 'committed',
      'correct count': (r) => r.json().count === batchSize,
    });

    errorRate.add(!success);

    if (!success) {
      console.log(`Batch failed: ${res.status} - ${res.body}`);
    }
  });

  sleep(0.5);
}

// Verify stats at the end
export function teardown() {
  console.log('Checking final statistics...');

  const statsRes = http.get(`${API_URL}/stats`);
  if (statsRes.status === 200) {
    const stats = statsRes.json();
    console.log(`Total processed: ${stats.total_processed_messages}`);
    console.log(`Total orders: ${stats.total_unique_orders}`);
  }

  const dupStats = http.get(`${API_URL}/stats/duplicates`);
  if (dupStats.status === 200) {
    const stats = dupStats.json();
    console.log(`Unique messages: ${stats.unique_message_count}`);
    console.log(`Total DB rows: ${stats.total_database_rows}`);
    console.log(`Idempotency working: ${stats.idempotency_working}`);
  }
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      metrics: {
        http_reqs: data.metrics.http_reqs?.values?.count || 0,
        http_req_duration_avg: data.metrics.http_req_duration?.values?.avg || 0,
        http_req_duration_p95: data.metrics.http_req_duration?.values?.['p(95)'] || 0,
        errors: data.metrics.errors?.values?.rate || 0,
        order_latency_avg: data.metrics.order_latency?.values?.avg || 0,
        duplicate_orders_sent: data.metrics.duplicate_orders_sent?.values?.count || 0,
      },
    }, null, 2),
  };
}
