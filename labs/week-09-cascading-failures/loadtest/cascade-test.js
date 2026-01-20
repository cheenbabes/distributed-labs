import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const timeoutCounter = new Counter('timeout_errors');
const successCounter = new Counter('successful_requests');
const status200 = new Counter('status_200');
const status503 = new Counter('status_503');
const status504 = new Counter('status_504');
const status500 = new Counter('status_500');

// Test configuration - designed to trigger cascade
export const options = {
  scenarios: {
    cascade_trigger: {
      executor: 'constant-arrival-rate',
      rate: 30,              // 30 requests per second - enough to saturate
      timeUnit: '1s',
      duration: '3m',        // Run for 3 minutes
      preAllocatedVUs: 60,
      maxVUs: 150,
    },
  },
  thresholds: {
    http_req_duration: ['p(50)<10000'], // Just track, don't fail
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://frontend:8000';

export default function () {
  const res = http.get(`${BASE_URL}/api/order`, {
    timeout: '20s',  // 20 second timeout
  });

  // Record metrics
  latencyTrend.add(res.timings.duration);

  // Track status codes
  if (res.status === 200) {
    status200.add(1);
    successCounter.add(1);
    errorRate.add(false);
  } else if (res.status === 503) {
    status503.add(1);
    errorRate.add(true);
  } else if (res.status === 504) {
    status504.add(1);
    errorRate.add(true);
  } else if (res.status === 500) {
    status500.add(1);
    errorRate.add(true);
  } else if (res.status === 0) {
    timeoutCounter.add(1);
    errorRate.add(true);
  } else {
    errorRate.add(true);
  }

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'no timeout': (r) => r.status !== 0,
    'not overloaded': (r) => r.status !== 503,
    'not gateway timeout': (r) => r.status !== 504,
  });

  // Very small pause to maintain high load
  sleep(0.05);
}

export function handleSummary(data) {
  console.log('\n========== CASCADE TEST SUMMARY ==========');
  console.log(`Total Requests: ${data.metrics.http_reqs.values.count}`);
  console.log(`Successful (200): ${data.metrics.status_200 ? data.metrics.status_200.values.count : 0}`);
  console.log(`Overloaded (503): ${data.metrics.status_503 ? data.metrics.status_503.values.count : 0}`);
  console.log(`Timeout (504): ${data.metrics.status_504 ? data.metrics.status_504.values.count : 0}`);
  console.log(`Server Error (500): ${data.metrics.status_500 ? data.metrics.status_500.values.count : 0}`);
  console.log(`Connection Timeout: ${data.metrics.timeout_errors ? data.metrics.timeout_errors.values.count : 0}`);
  console.log(`Error Rate: ${(data.metrics.errors.values.rate * 100).toFixed(2)}%`);
  console.log(`P50 Latency: ${data.metrics.http_req_duration.values['p(50)'].toFixed(0)}ms`);
  console.log(`P95 Latency: ${data.metrics.http_req_duration.values['p(95)'].toFixed(0)}ms`);
  console.log(`P99 Latency: ${data.metrics.http_req_duration.values['p(99)'].toFixed(0)}ms`);
  console.log(`Max Latency: ${data.metrics.http_req_duration.values.max.toFixed(0)}ms`);
  console.log('==========================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
