import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const fastLatency = new Trend('fast_backend_latency');
const slowLatency = new Trend('slow_backend_latency');
const flakyLatency = new Trend('flaky_backend_latency');
const rejectedRequests = new Counter('rejected_requests');

// Test configuration
export const options = {
  scenarios: {
    // Scenario 1: Mixed load - demonstrates bulkhead isolation
    mixed_load: {
      executor: 'constant-arrival-rate',
      rate: 50,  // 50 requests per second
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 100,
      maxVUs: 200,
    },
  },
  thresholds: {
    'fast_backend_latency': ['p(95)<500'],   // Fast backend should stay fast
    'errors': ['rate<0.5'],                   // Error rate below 50%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://api-gateway:8000';

// Weighted random selection of endpoints
function selectEndpoint() {
  const rand = Math.random();
  if (rand < 0.4) {
    return '/api/fast';   // 40% fast backend
  } else if (rand < 0.7) {
    return '/api/slow';   // 30% slow backend
  } else {
    return '/api/flaky';  // 30% flaky backend
  }
}

export default function () {
  const endpoint = selectEndpoint();
  const url = `${BASE_URL}${endpoint}`;

  const res = http.get(url, {
    timeout: '10s',
    tags: { endpoint: endpoint },
  });

  // Record metrics based on endpoint
  if (endpoint === '/api/fast') {
    fastLatency.add(res.timings.duration);
  } else if (endpoint === '/api/slow') {
    slowLatency.add(res.timings.duration);
  } else {
    flakyLatency.add(res.timings.duration);
  }

  // Track errors
  const isError = res.status !== 200;
  errorRate.add(isError);

  // Track rejections (503 from bulkhead)
  if (res.status === 503) {
    rejectedRequests.add(1);
  }

  // Validate response
  check(res, {
    'status is 200 or 503': (r) => r.status === 200 || r.status === 503,
    'response has service': (r) => {
      if (r.status === 200) {
        try {
          return r.json().service === 'api-gateway';
        } catch (e) {
          return false;
        }
      }
      return true;
    },
  });

  // Small pause between requests
  sleep(0.1);
}

// Separate test for demonstrating cascade without bulkheads
export function noBulkheadTest() {
  group('No Bulkhead Protection', function () {
    // Disable bulkheads
    http.post(`${BASE_URL}/admin/config`, JSON.stringify({
      mode: 'disabled'
    }), {
      headers: { 'Content-Type': 'application/json' },
    });

    // Send concurrent requests
    for (let i = 0; i < 20; i++) {
      http.get(`${BASE_URL}/api/slow`);
    }
  });
}

// Burst test scenario
export function burstTest() {
  group('Burst Test', function () {
    const responses = [];

    // Send 50 concurrent requests to each backend
    for (let i = 0; i < 50; i++) {
      responses.push(http.get(`${BASE_URL}/api/fast`));
      responses.push(http.get(`${BASE_URL}/api/slow`));
      responses.push(http.get(`${BASE_URL}/api/flaky`));
    }

    // Analyze results
    let successCount = 0;
    let rejectedCount = 0;
    let errorCount = 0;

    responses.forEach((res) => {
      if (res.status === 200) successCount++;
      else if (res.status === 503) rejectedCount++;
      else errorCount++;
    });

    console.log(`Burst test results: success=${successCount}, rejected=${rejectedCount}, errors=${errorCount}`);
  });
}

export function handleSummary(data) {
  return {
    stdout: textSummary(data, { indent: ' ', enableColors: true }),
    'summary.json': JSON.stringify(data, null, 2),
  };
}

function textSummary(data, options) {
  const metrics = data.metrics;
  const lines = [];

  lines.push('\n========== Bulkhead Isolation Test Summary ==========\n');

  // Fast backend latency
  if (metrics.fast_backend_latency) {
    lines.push('Fast Backend Latency:');
    lines.push(`  p50: ${metrics.fast_backend_latency.values['p(50)'].toFixed(2)}ms`);
    lines.push(`  p95: ${metrics.fast_backend_latency.values['p(95)'].toFixed(2)}ms`);
    lines.push(`  p99: ${metrics.fast_backend_latency.values['p(99)'].toFixed(2)}ms`);
    lines.push('');
  }

  // Slow backend latency
  if (metrics.slow_backend_latency) {
    lines.push('Slow Backend Latency:');
    lines.push(`  p50: ${metrics.slow_backend_latency.values['p(50)'].toFixed(2)}ms`);
    lines.push(`  p95: ${metrics.slow_backend_latency.values['p(95)'].toFixed(2)}ms`);
    lines.push(`  p99: ${metrics.slow_backend_latency.values['p(99)'].toFixed(2)}ms`);
    lines.push('');
  }

  // Flaky backend latency
  if (metrics.flaky_backend_latency) {
    lines.push('Flaky Backend Latency:');
    lines.push(`  p50: ${metrics.flaky_backend_latency.values['p(50)'].toFixed(2)}ms`);
    lines.push(`  p95: ${metrics.flaky_backend_latency.values['p(95)'].toFixed(2)}ms`);
    lines.push(`  p99: ${metrics.flaky_backend_latency.values['p(99)'].toFixed(2)}ms`);
    lines.push('');
  }

  // Error and rejection rates
  if (metrics.errors) {
    lines.push(`Error Rate: ${(metrics.errors.values.rate * 100).toFixed(2)}%`);
  }

  if (metrics.rejected_requests) {
    lines.push(`Rejected Requests: ${metrics.rejected_requests.values.count}`);
  }

  lines.push('\n=====================================================\n');

  return lines.join('\n');
}
