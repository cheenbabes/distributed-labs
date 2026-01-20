import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const readLatency = new Trend('read_latency');
const writeLatency = new Trend('write_latency');
const cacheHits = new Counter('cache_hits');
const cacheMisses = new Counter('cache_misses');

// Test configuration
export const options = {
  scenarios: {
    // Mixed read/write workload
    mixed_workload: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 10 },  // Ramp up
        { duration: '1m', target: 10 },   // Steady state
        { duration: '30s', target: 0 },   // Ramp down
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],  // 95% of requests should be < 500ms
    errors: ['rate<0.1'],               // Error rate should be < 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://api:8000';

// Track created items for subsequent reads/updates
const createdItems = [];

export function setup() {
  // Create a few initial items to ensure we have data to read
  console.log('Setting up test data...');

  for (let i = 0; i < 5; i++) {
    const payload = JSON.stringify({
      name: `Setup Item ${i}`,
      description: `Test item created during setup`,
      price: 10.00 + i,
    });

    const res = http.post(`${BASE_URL}/items`, payload, {
      headers: { 'Content-Type': 'application/json' },
    });

    if (res.status === 200 || res.status === 201) {
      const item = res.json();
      createdItems.push(item.id);
      console.log(`Created item: ${item.id}`);
    }
  }

  return { itemIds: createdItems };
}

export default function (data) {
  const itemIds = data.itemIds || [];

  // Weighted operation selection:
  // - 70% reads (typical read-heavy workload)
  // - 20% writes (new items)
  // - 10% updates (modify existing)
  const operation = Math.random();

  if (operation < 0.70) {
    // READ operation
    doRead(itemIds);
  } else if (operation < 0.90) {
    // WRITE operation (create new item)
    const newId = doWrite();
    if (newId) {
      itemIds.push(newId);
    }
  } else {
    // UPDATE operation
    doUpdate(itemIds);
  }

  // Small pause between requests
  sleep(0.1 + Math.random() * 0.2);
}

function doRead(itemIds) {
  if (itemIds.length === 0) {
    // No items to read, list all items instead
    const res = http.get(`${BASE_URL}/items`);
    readLatency.add(res.timings.duration);
    errorRate.add(res.status !== 200);
    return;
  }

  // Pick a random item to read
  const itemId = itemIds[Math.floor(Math.random() * itemIds.length)];
  const startTime = Date.now();

  const res = http.get(`${BASE_URL}/items/${itemId}`);
  const duration = Date.now() - startTime;

  readLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200 && res.status !== 404);

  check(res, {
    'read status is 200': (r) => r.status === 200,
    'read has item id': (r) => r.status !== 200 || r.json().id === itemId,
  });
}

function doWrite() {
  const payload = JSON.stringify({
    name: `Load Test Item ${Date.now()}`,
    description: `Created by k6 load test at ${new Date().toISOString()}`,
    price: Math.round(Math.random() * 100 * 100) / 100,
  });

  const res = http.post(`${BASE_URL}/items`, payload, {
    headers: { 'Content-Type': 'application/json' },
  });

  writeLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200 && res.status !== 201);

  check(res, {
    'write status is 200/201': (r) => r.status === 200 || r.status === 201,
    'write returns id': (r) => r.status !== 200 && r.status !== 201 || r.json().id !== undefined,
  });

  if (res.status === 200 || res.status === 201) {
    return res.json().id;
  }
  return null;
}

function doUpdate(itemIds) {
  if (itemIds.length === 0) {
    return;
  }

  // Pick a random item to update
  const itemId = itemIds[Math.floor(Math.random() * itemIds.length)];

  const payload = JSON.stringify({
    name: `Updated Item ${Date.now()}`,
    description: `Updated by k6 load test at ${new Date().toISOString()}`,
    price: Math.round(Math.random() * 100 * 100) / 100,
  });

  const res = http.put(`${BASE_URL}/items/${itemId}`, payload, {
    headers: { 'Content-Type': 'application/json' },
  });

  writeLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200 && res.status !== 404);

  check(res, {
    'update status is 200': (r) => r.status === 200 || r.status === 404,
  });
}

export function handleSummary(data) {
  const summary = {
    'Total Requests': data.metrics.http_reqs.values.count,
    'Request Rate (req/s)': data.metrics.http_reqs.values.rate.toFixed(2),
    'Avg Response Time': `${data.metrics.http_req_duration.values.avg.toFixed(2)}ms`,
    'p95 Response Time': `${data.metrics.http_req_duration.values['p(95)'].toFixed(2)}ms`,
    'p99 Response Time': `${data.metrics.http_req_duration.values['p(99)'].toFixed(2)}ms`,
    'Error Rate': `${(data.metrics.errors.values.rate * 100).toFixed(2)}%`,
  };

  if (data.metrics.read_latency) {
    summary['Avg Read Latency'] = `${data.metrics.read_latency.values.avg.toFixed(2)}ms`;
  }
  if (data.metrics.write_latency) {
    summary['Avg Write Latency'] = `${data.metrics.write_latency.values.avg.toFixed(2)}ms`;
  }

  console.log('\n========== LOAD TEST SUMMARY ==========');
  for (const [key, value] of Object.entries(summary)) {
    console.log(`${key}: ${value}`);
  }
  console.log('========================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
