import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics for chaos engineering
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const successfulOrders = new Counter('successful_orders');
const failedOrders = new Counter('failed_orders');

// Test configuration - baseline (no chaos)
export const options = {
  stages: [
    { duration: '30s', target: 5 },   // Ramp up to 5 users
    { duration: '2m', target: 5 },    // Stay at 5 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],  // 95% of requests should be < 500ms
    errors: ['rate<0.05'],              // Error rate should be < 5%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab28-gateway:8000';

// Product IDs available in inventory
const PRODUCTS = ['product-001', 'product-002', 'product-003', 'product-004', 'product-005'];

function getRandomProduct() {
  return PRODUCTS[Math.floor(Math.random() * PRODUCTS.length)];
}

function getRandomQuantity() {
  return Math.floor(Math.random() * 3) + 1; // 1-3 items
}

export default function () {
  // Create an order
  const orderPayload = JSON.stringify({
    items: [
      { product_id: getRandomProduct(), quantity: getRandomQuantity() },
    ],
    customer_id: `customer-${__VU}-${__ITER}`
  });

  const orderRes = http.post(`${BASE_URL}/api/orders`, orderPayload, {
    headers: { 'Content-Type': 'application/json' },
  });

  // Record metrics
  latencyTrend.add(orderRes.timings.duration);
  errorRate.add(orderRes.status !== 200);

  // Validate response
  const orderSuccess = check(orderRes, {
    'order status is 200': (r) => r.status === 200,
    'order has trace_id': (r) => {
      try {
        return r.json().trace_id !== undefined;
      } catch {
        return false;
      }
    },
    'order has order_id': (r) => {
      try {
        return r.json().order && r.json().order.order_id !== undefined;
      } catch {
        return false;
      }
    },
  });

  if (orderSuccess) {
    successfulOrders.add(1);
  } else {
    failedOrders.add(1);
  }

  // Small pause between requests
  sleep(0.5);
}

export function handleSummary(data) {
  console.log('\n========== BASELINE TEST RESULTS ==========');
  console.log(`Total requests: ${data.metrics.http_reqs.values.count}`);
  console.log(`Successful orders: ${data.metrics.successful_orders ? data.metrics.successful_orders.values.count : 0}`);
  console.log(`Failed orders: ${data.metrics.failed_orders ? data.metrics.failed_orders.values.count : 0}`);
  console.log(`Error rate: ${(data.metrics.errors.values.rate * 100).toFixed(2)}%`);
  console.log(`P50 latency: ${data.metrics.http_req_duration.values['p(50)'].toFixed(2)}ms`);
  console.log(`P95 latency: ${data.metrics.http_req_duration.values['p(95)'].toFixed(2)}ms`);
  console.log(`P99 latency: ${data.metrics.http_req_duration.values['p(99)'].toFixed(2)}ms`);
  console.log('============================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
