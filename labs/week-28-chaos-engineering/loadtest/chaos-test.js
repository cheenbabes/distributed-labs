import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics for chaos engineering
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const successfulOrders = new Counter('successful_orders');
const failedOrders = new Counter('failed_orders');
const chaosInjections = new Counter('chaos_injections');

// Test configuration - chaos test (longer duration to observe chaos effects)
export const options = {
  stages: [
    { duration: '30s', target: 10 },  // Ramp up to 10 users
    { duration: '3m', target: 10 },   // Stay at 10 users (chaos will be injected during this phase)
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    // More lenient thresholds during chaos
    http_req_duration: ['p(95)<3000'],  // 95% of requests should be < 3s
    errors: ['rate<0.30'],               // Error rate should be < 30% during chaos
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab28-gateway:8000';
const CHAOS_CONTROLLER_URL = __ENV.CHAOS_CONTROLLER_URL || 'http://lab28-chaos-controller:8080';

// Product IDs available in inventory
const PRODUCTS = ['product-001', 'product-002', 'product-003', 'product-004', 'product-005'];

function getRandomProduct() {
  return PRODUCTS[Math.floor(Math.random() * PRODUCTS.length)];
}

function getRandomQuantity() {
  return Math.floor(Math.random() * 3) + 1;
}

export function setup() {
  // Record that we're starting a chaos test
  console.log('Starting chaos test - baseline measurement for first 30s');
  return { startTime: Date.now() };
}

export default function (data) {
  // Create an order
  const orderPayload = JSON.stringify({
    items: [
      { product_id: getRandomProduct(), quantity: getRandomQuantity() },
    ],
    customer_id: `customer-${__VU}-${__ITER}`
  });

  const startTime = Date.now();
  const orderRes = http.post(`${BASE_URL}/api/orders`, orderPayload, {
    headers: { 'Content-Type': 'application/json' },
    timeout: '10s',
  });
  const duration = Date.now() - startTime;

  // Record metrics
  latencyTrend.add(duration);
  errorRate.add(orderRes.status !== 200);

  // Check if this looks like a chaos-induced failure
  if (orderRes.status >= 500) {
    chaosInjections.add(1);
  }

  // Validate response
  const orderSuccess = check(orderRes, {
    'order status is 200': (r) => r.status === 200,
    'response time < 2s': (r) => r.timings.duration < 2000,
    'order has trace_id': (r) => {
      try {
        return r.json().trace_id !== undefined;
      } catch {
        return false;
      }
    },
  });

  if (orderSuccess) {
    successfulOrders.add(1);
  } else {
    failedOrders.add(1);

    // Log failure details for debugging
    if (__ITER % 10 === 0) {
      console.log(`Request failed: status=${orderRes.status}, duration=${duration}ms`);
    }
  }

  // Varied pause between requests
  sleep(Math.random() * 1 + 0.2);
}

export function teardown(data) {
  console.log(`Chaos test completed. Total duration: ${(Date.now() - data.startTime) / 1000}s`);
}

export function handleSummary(data) {
  console.log('\n========== CHAOS TEST RESULTS ==========');
  console.log(`Total requests: ${data.metrics.http_reqs.values.count}`);
  console.log(`Successful orders: ${data.metrics.successful_orders ? data.metrics.successful_orders.values.count : 0}`);
  console.log(`Failed orders: ${data.metrics.failed_orders ? data.metrics.failed_orders.values.count : 0}`);
  console.log(`Chaos-induced failures: ${data.metrics.chaos_injections ? data.metrics.chaos_injections.values.count : 0}`);
  console.log(`Error rate: ${(data.metrics.errors.values.rate * 100).toFixed(2)}%`);
  console.log(`P50 latency: ${data.metrics.http_req_duration.values['p(50)'].toFixed(2)}ms`);
  console.log(`P95 latency: ${data.metrics.http_req_duration.values['p(95)'].toFixed(2)}ms`);
  console.log(`P99 latency: ${data.metrics.http_req_duration.values['p(99)'].toFixed(2)}ms`);
  console.log(`Max latency: ${data.metrics.http_req_duration.values.max.toFixed(2)}ms`);
  console.log('=========================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
