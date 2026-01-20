import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const requestsTotal = new Counter('requests_total');

// Test configuration - designed to generate steady load for memory growth observation
export const options = {
  scenarios: {
    // Steady load scenario - constant request rate for predictable memory growth
    steady_load: {
      executor: 'constant-arrival-rate',
      rate: 50,                    // 50 requests per second
      timeUnit: '1s',
      duration: '5m',              // Run for 5 minutes to see memory growth
      preAllocatedVUs: 10,
      maxVUs: 50,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],  // 95% of requests should be < 500ms
    errors: ['rate<0.01'],              // Error rate should be < 1%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab16-leaky-service:8080';

export function setup() {
  // Check service is healthy
  const health = http.get(`${BASE_URL}/health`);
  check(health, {
    'service is healthy': (r) => r.status === 200,
  });

  // Get initial memory status
  const status = http.get(`${BASE_URL}/admin/leaks/status`);
  console.log('Initial leak status:', status.body);

  return {
    startTime: new Date().toISOString(),
  };
}

export default function () {
  // Main request - this triggers the memory leaks
  const res = http.get(`${BASE_URL}/api/data`);

  // Record metrics
  latencyTrend.add(res.timings.duration);
  requestsTotal.add(1);
  errorRate.add(res.status !== 200);

  // Validate response
  const success = check(res, {
    'status is 200': (r) => r.status === 200,
    'response has request_id': (r) => {
      try {
        return r.json().request_id !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  if (!success) {
    console.log(`Request failed: ${res.status} - ${res.body}`);
  }

  // Small random pause to simulate realistic traffic
  sleep(Math.random() * 0.1);
}

export function teardown(data) {
  // Get final memory status
  const status = http.get(`${BASE_URL}/admin/leaks/status`);
  console.log('Final leak status:', status.body);

  const memory = http.get(`${BASE_URL}/admin/memory`);
  console.log('Final memory info:', memory.body);

  console.log(`Test started: ${data.startTime}`);
  console.log(`Test ended: ${new Date().toISOString()}`);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      summary: {
        total_requests: data.metrics.http_reqs.values.count,
        avg_latency_ms: data.metrics.http_req_duration.values.avg,
        p95_latency_ms: data.metrics.http_req_duration.values['p(95)'],
        error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
      },
    }, null, 2),
  };
}
