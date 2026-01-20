import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const timeoutCounter = new Counter('timeout_errors');
const successCounter = new Counter('successful_requests');

// Test configuration - steady load to observe cascade
export const options = {
  scenarios: {
    steady_load: {
      executor: 'constant-arrival-rate',
      rate: 20,              // 20 requests per second
      timeUnit: '1s',
      duration: '5m',        // Run for 5 minutes
      preAllocatedVUs: 50,   // Pre-allocate 50 VUs
      maxVUs: 100,           // Allow up to 100 VUs
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<5000'], // 95% of requests should be < 5s
    errors: ['rate<0.5'],               // Error rate should be < 50%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://frontend:8000';

export default function () {
  const startTime = new Date();
  const res = http.get(`${BASE_URL}/api/order`, {
    timeout: '15s',  // 15 second timeout
  });
  const duration = new Date() - startTime;

  // Record metrics
  latencyTrend.add(duration);

  if (res.status === 0) {
    // Connection error or timeout
    errorRate.add(true);
    timeoutCounter.add(1);
  } else if (res.status !== 200) {
    errorRate.add(true);
  } else {
    errorRate.add(false);
    successCounter.add(1);
  }

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response has service': (r) => r.status === 200 && r.json().service === 'frontend',
    'response has chain': (r) => r.status === 200 && r.json().chain !== undefined,
    'latency under 2s': (r) => r.timings.duration < 2000,
  });

  // Small pause between requests (handled by constant-arrival-rate)
  sleep(0.1);
}

export function handleSummary(data) {
  const summary = {
    'Total Requests': data.metrics.http_reqs.values.count,
    'Successful Requests': data.metrics.successful_requests ? data.metrics.successful_requests.values.count : 0,
    'Timeout Errors': data.metrics.timeout_errors ? data.metrics.timeout_errors.values.count : 0,
    'Error Rate': `${(data.metrics.errors.values.rate * 100).toFixed(2)}%`,
    'P50 Latency': `${data.metrics.http_req_duration.values['p(50)'].toFixed(0)}ms`,
    'P95 Latency': `${data.metrics.http_req_duration.values['p(95)'].toFixed(0)}ms`,
    'P99 Latency': `${data.metrics.http_req_duration.values['p(99)'].toFixed(0)}ms`,
    'Max Latency': `${data.metrics.http_req_duration.values.max.toFixed(0)}ms`,
  };

  console.log('\n========== LOAD TEST SUMMARY ==========');
  for (const [key, value] of Object.entries(summary)) {
    console.log(`${key}: ${value}`);
  }
  console.log('========================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
