import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const timeoutCounter = new Counter('timeout_errors');
const successCounter = new Counter('successful_requests');

// Test configuration - ramp up to find breaking point
export const options = {
  stages: [
    { duration: '30s', target: 10 },   // Warm up
    { duration: '1m', target: 30 },    // Moderate load
    { duration: '1m', target: 50 },    // High load
    { duration: '1m', target: 70 },    // Very high load
    { duration: '1m', target: 100 },   // Extreme load
    { duration: '30s', target: 0 },    // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<10000'], // 95% of requests should be < 10s
    errors: ['rate<0.8'],               // Error rate should be < 80%
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
    'latency under 5s': (r) => r.timings.duration < 5000,
  });

  // Small pause
  sleep(0.2);
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

  console.log('\n========== RAMP LOAD TEST SUMMARY ==========');
  for (const [key, value] of Object.entries(summary)) {
    console.log(`${key}: ${value}`);
  }
  console.log('=============================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
