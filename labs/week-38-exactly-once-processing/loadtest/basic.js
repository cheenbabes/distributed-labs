import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');

// Test configuration - simple ramp up/down
export const options = {
  stages: [
    { duration: '30s', target: 10 },  // Ramp up to 10 users
    { duration: '1m', target: 10 },   // Stay at 10 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    errors: ['rate<0.1'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://producer:8000';

function generateOrder() {
  return {
    customer_id: `customer-${Math.floor(Math.random() * 100)}`,
    items: [
      { name: 'Product', quantity: 1, price: 29.99 }
    ],
    total_amount: 29.99,
  };
}

export default function () {
  const order = generateOrder();

  const res = http.post(
    `${BASE_URL}/orders`,
    JSON.stringify(order),
    {
      headers: { 'Content-Type': 'application/json' },
    }
  );

  // Record metrics
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'has order_id': (r) => r.json().order_id !== undefined,
    'has trace_id': (r) => r.json().trace_id !== undefined,
  });

  sleep(0.5);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
