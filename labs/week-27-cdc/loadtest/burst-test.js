import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';
import { randomString, randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// Custom metrics
const errorRate = new Rate('errors');
const writeLatency = new Trend('write_latency');

// Configuration for burst testing
// This creates a sudden spike to observe CDC lag behavior
export const options = {
  scenarios: {
    burst: {
      executor: 'constant-vus',
      vus: 50,
      duration: '30s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    errors: ['rate<0.2'],
  },
};

const WRITER_URL = __ENV.WRITER_URL || 'http://writer-service:8000';

const categories = ['Electronics', 'Office', 'Kitchen', 'Accessories'];

export default function () {
  const product = {
    name: `Burst Product ${randomString(6)}`,
    description: `Burst test product`,
    price: randomIntBetween(10, 100),
    category: categories[randomIntBetween(0, 3)],
    stock_quantity: randomIntBetween(10, 50),
  };

  const start = Date.now();
  const res = http.post(
    `${WRITER_URL}/products`,
    JSON.stringify(product),
    {
      headers: { 'Content-Type': 'application/json' },
    }
  );

  writeLatency.add(Date.now() - start);

  const success = check(res, {
    'status is 200': (r) => r.status === 200,
  });

  if (!success) {
    errorRate.add(1);
  }

  // Minimal pause to maximize throughput
  sleep(0.05);
}

export function handleSummary(data) {
  console.log('\n========== BURST TEST SUMMARY ==========');
  console.log(`Total Requests: ${data.metrics.http_reqs.values.count}`);
  console.log(`Requests/sec: ${data.metrics.http_reqs.values.rate.toFixed(2)}`);
  console.log(`Error Rate: ${(data.metrics.errors.values.rate * 100).toFixed(2)}%`);
  console.log(`Avg Write Latency: ${data.metrics.write_latency.values.avg.toFixed(2)}ms`);
  console.log(`P95 Latency: ${data.metrics.http_req_duration.values['p(95)'].toFixed(2)}ms`);
  console.log('==========================================\n');
  console.log('Now check the CDC consumer lag in Grafana or via API:');
  console.log('  curl http://localhost:8001/events/stats');
  console.log('  curl http://localhost:8003/compare');

  return {};
}
