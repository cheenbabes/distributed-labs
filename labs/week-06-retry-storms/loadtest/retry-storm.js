import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const successCounter = new Counter('successful_requests');
const failedCounter = new Counter('failed_requests');

// Test configuration - simulate a moderate load
export const options = {
  stages: [
    { duration: '10s', target: 10 },  // Ramp up to 10 users
    { duration: '30s', target: 10 },  // Stay at 10 users (during failure)
    { duration: '20s', target: 10 },  // Recovery period
    { duration: '10s', target: 0 },   // Ramp down
  ],
  thresholds: {
    // We expect errors during failure injection
    http_req_duration: ['p(95)<15000'], // 95% of requests should be < 15s (allows for retries)
  },
};

const CLIENT_URL = __ENV.CLIENT_URL || 'http://client:8000';
const BACKEND_URL = __ENV.BACKEND_URL || 'http://backend:8001';

export function setup() {
  // Log current retry strategy
  const strategyRes = http.get(`${CLIENT_URL}/admin/strategy`);
  console.log(`Current retry strategy: ${strategyRes.body}`);

  // Trigger backend failure after 15 seconds (during the sustained load phase)
  // Note: We'll do this from outside k6 for better control in the demo
  console.log('Load test starting - trigger failure manually or via runbook');

  return { startTime: Date.now() };
}

export default function (data) {
  const res = http.get(`${CLIENT_URL}/process`, {
    timeout: '30s', // Allow time for retries
  });

  // Record metrics
  latencyTrend.add(res.timings.duration);

  const isError = res.status !== 200;
  errorRate.add(isError);

  if (isError) {
    failedCounter.add(1);
  } else {
    successCounter.add(1);
  }

  // Validate response
  check(res, {
    'status is 200 or 502': (r) => r.status === 200 || r.status === 502,
    'response has service': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.service === 'client';
      } catch {
        return false;
      }
    },
  });

  // Small pause between requests (simulates realistic user behavior)
  sleep(0.5);
}

export function teardown(data) {
  const duration = (Date.now() - data.startTime) / 1000;
  console.log(`Test completed in ${duration.toFixed(1)} seconds`);
}

export function handleSummary(data) {
  const summary = {
    total_requests: data.metrics.http_reqs?.values?.count || 0,
    error_rate: ((data.metrics.errors?.values?.rate || 0) * 100).toFixed(2) + '%',
    avg_latency: (data.metrics.request_latency?.values?.avg || 0).toFixed(2) + 'ms',
    p95_latency: (data.metrics.http_req_duration?.values['p(95)'] || 0).toFixed(2) + 'ms',
    successful: data.metrics.successful_requests?.values?.count || 0,
    failed: data.metrics.failed_requests?.values?.count || 0,
  };

  console.log('\n=== RETRY STORM TEST SUMMARY ===');
  console.log(`Total Requests: ${summary.total_requests}`);
  console.log(`Successful: ${summary.successful}`);
  console.log(`Failed: ${summary.failed}`);
  console.log(`Error Rate: ${summary.error_rate}`);
  console.log(`Avg Latency: ${summary.avg_latency}`);
  console.log(`P95 Latency: ${summary.p95_latency}`);

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
