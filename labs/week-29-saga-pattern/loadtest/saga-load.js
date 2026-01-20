import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const sagaSuccessRate = new Rate('saga_success');
const sagaCompensationRate = new Rate('saga_compensated');
const orderLatency = new Trend('order_latency');
const sagasStarted = new Counter('sagas_started');
const sagasCompleted = new Counter('sagas_completed');
const sagasCompensated = new Counter('sagas_compensated');

// Test configuration
export const options = {
  scenarios: {
    // Baseline: steady load with no failures
    baseline: {
      executor: 'constant-arrival-rate',
      rate: 5,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      maxVUs: 20,
      exec: 'placeOrder',
      tags: { scenario: 'baseline' },
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<5000'], // 95% of requests should be < 5s
    errors: ['rate<0.3'],              // Error rate should be < 30%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab29-order-service:8000';

// Sample products for orders
const products = [
  { product_id: 'PROD-001', name: 'Widget Pro', price: 29.99 },
  { product_id: 'PROD-002', name: 'Gadget Plus', price: 49.99 },
  { product_id: 'PROD-003', name: 'Gizmo Deluxe', price: 79.99 },
  { product_id: 'PROD-004', name: 'Thingamabob', price: 19.99 },
  { product_id: 'PROD-005', name: 'Whatchamacallit', price: 99.99 },
];

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function generateOrder() {
  const numItems = randomInt(1, 3);
  const items = [];
  const usedProducts = new Set();

  for (let i = 0; i < numItems; i++) {
    let product;
    do {
      product = products[randomInt(0, products.length - 1)];
    } while (usedProducts.has(product.product_id));

    usedProducts.add(product.product_id);
    items.push({
      product_id: product.product_id,
      name: product.name,
      quantity: randomInt(1, 5),
      price: product.price,
    });
  }

  return {
    customer_id: `CUST-${randomInt(1000, 9999)}`,
    items: items,
  };
}

export function placeOrder() {
  const order = generateOrder();

  const res = http.post(
    `${BASE_URL}/orders`,
    JSON.stringify(order),
    {
      headers: { 'Content-Type': 'application/json' },
      tags: { name: 'PlaceOrder' },
    }
  );

  sagasStarted.add(1);
  orderLatency.add(res.timings.duration);

  const success = res.status === 200;
  errorRate.add(!success);

  if (success) {
    const result = res.json();
    const sagaSuccess = result.saga_state === 'completed';
    const sagaCompensation = result.saga_state === 'compensated';

    sagaSuccessRate.add(sagaSuccess);
    sagaCompensationRate.add(sagaCompensation);

    if (sagaSuccess) {
      sagasCompleted.add(1);
    } else if (sagaCompensation) {
      sagasCompensated.add(1);
    }

    check(res, {
      'status is 200': (r) => r.status === 200,
      'has saga_id': (r) => r.json().saga_id !== undefined,
      'has order_id': (r) => r.json().order_id !== undefined,
      'has trace_id': (r) => r.json().trace_id !== undefined,
    });
  } else {
    check(res, {
      'status is 200': (r) => r.status === 200,
    });
  }

  sleep(randomInt(1, 3) / 10); // 0.1-0.3s between requests
}

// Demo order for quick testing
export function demoOrder() {
  const res = http.get(`${BASE_URL}/demo/order`, {
    tags: { name: 'DemoOrder' },
  });

  check(res, {
    'demo order status is 200': (r) => r.status === 200,
    'demo order has saga_state': (r) => r.json().saga_state !== undefined,
  });

  console.log(`Demo order result: ${res.body}`);
}

export function handleSummary(data) {
  const summary = {
    'Total Requests': data.metrics.http_reqs.values.count,
    'Request Rate': `${data.metrics.http_reqs.values.rate.toFixed(2)}/s`,
    'Avg Latency': `${data.metrics.http_req_duration.values.avg.toFixed(0)}ms`,
    'P95 Latency': `${data.metrics.http_req_duration.values['p(95)'].toFixed(0)}ms`,
    'Error Rate': `${(data.metrics.errors.values.rate * 100).toFixed(1)}%`,
    'Saga Success Rate': data.metrics.saga_success ?
      `${(data.metrics.saga_success.values.rate * 100).toFixed(1)}%` : 'N/A',
    'Saga Compensation Rate': data.metrics.saga_compensated ?
      `${(data.metrics.saga_compensated.values.rate * 100).toFixed(1)}%` : 'N/A',
  };

  console.log('\n========== SAGA LOAD TEST SUMMARY ==========');
  for (const [key, value] of Object.entries(summary)) {
    console.log(`${key}: ${value}`);
  }
  console.log('=============================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
