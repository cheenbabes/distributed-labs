import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomString, randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// Custom metrics
const errorRate = new Rate('errors');
const createLatency = new Trend('create_latency');
const updateLatency = new Trend('update_latency');
const productsCreated = new Counter('products_created');

// Test configuration
export const options = {
  scenarios: {
    // Scenario 1: Steady write load
    steady_writes: {
      executor: 'constant-arrival-rate',
      rate: 5,  // 5 requests per second
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      maxVUs: 20,
    },
    // Scenario 2: Burst writes (to test CDC lag)
    burst_writes: {
      executor: 'ramping-arrival-rate',
      startRate: 1,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 100,
      stages: [
        { duration: '30s', target: 1 },   // Warm up
        { duration: '30s', target: 50 },  // Burst
        { duration: '30s', target: 50 },  // Sustain burst
        { duration: '30s', target: 1 },   // Cool down
      ],
      startTime: '2m',  // Start after steady_writes
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1000'],  // 95% of requests under 1s
    errors: ['rate<0.1'],                // Error rate under 10%
  },
};

const WRITER_URL = __ENV.WRITER_URL || 'http://writer-service:8000';

const categories = ['Electronics', 'Office', 'Kitchen', 'Accessories', 'Sports', 'Books'];

// Generate a random product
function randomProduct() {
  return {
    name: `Product ${randomString(8)}`,
    description: `Auto-generated product for load testing - ${randomString(20)}`,
    price: randomIntBetween(10, 500) + Math.random(),
    category: categories[randomIntBetween(0, categories.length - 1)],
    stock_quantity: randomIntBetween(1, 100),
  };
}

export default function () {
  const product = randomProduct();

  // Create a product
  const createStart = Date.now();
  const createRes = http.post(
    `${WRITER_URL}/products`,
    JSON.stringify(product),
    {
      headers: { 'Content-Type': 'application/json' },
    }
  );

  const createDuration = Date.now() - createStart;
  createLatency.add(createDuration);

  const createSuccess = check(createRes, {
    'create status is 200': (r) => r.status === 200,
    'create returns id': (r) => r.json().id !== undefined,
  });

  if (createSuccess) {
    productsCreated.add(1);
    const productId = createRes.json().id;

    // 50% chance to also update the product
    if (Math.random() > 0.5) {
      sleep(0.1);  // Small delay

      const updateStart = Date.now();
      const updateRes = http.put(
        `${WRITER_URL}/products/${productId}`,
        JSON.stringify({
          price: product.price * 1.1,  // 10% price increase
          stock_quantity: product.stock_quantity + 10,
        }),
        {
          headers: { 'Content-Type': 'application/json' },
        }
      );

      const updateDuration = Date.now() - updateStart;
      updateLatency.add(updateDuration);

      check(updateRes, {
        'update status is 200': (r) => r.status === 200,
      });
    }
  } else {
    errorRate.add(1);
  }

  // Small pause between iterations
  sleep(randomIntBetween(1, 3) / 10);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      summary: {
        total_requests: data.metrics.http_reqs.values.count,
        products_created: data.metrics.products_created ? data.metrics.products_created.values.count : 0,
        error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
        avg_create_latency_ms: data.metrics.create_latency ? data.metrics.create_latency.values.avg : 0,
        avg_update_latency_ms: data.metrics.update_latency ? data.metrics.update_latency.values.avg : 0,
        p95_request_duration_ms: data.metrics.http_req_duration.values['p(95)'],
      },
    }, null, 2),
  };
}
