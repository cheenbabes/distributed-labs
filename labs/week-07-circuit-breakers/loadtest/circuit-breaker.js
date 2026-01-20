import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const rejectedRate = new Rate('rejected');
const latencyTrend = new Trend('request_latency');
const successCount = new Counter('success_count');
const failureCount = new Counter('failure_count');
const rejectedCount = new Counter('rejected_count');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 5 },   // Ramp up to 5 users
    { duration: '2m', target: 5 },    // Stay at 5 users (time to inject failures)
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    // We expect some errors when testing circuit breaker behavior
    http_req_duration: ['p(95)<5000'], // 95% of requests should be < 5s
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://client:8000';

export default function () {
  const res = http.get(`${BASE_URL}/api/data`);

  // Record latency
  latencyTrend.add(res.timings.duration);

  // Categorize response
  if (res.status === 200) {
    successCount.add(1);
    errorRate.add(false);
    rejectedRate.add(false);
  } else if (res.status === 503) {
    // Check if it's a circuit breaker rejection or backend failure
    try {
      const body = res.json();
      if (body.detail && body.detail.error === 'Circuit breaker is OPEN') {
        rejectedCount.add(1);
        rejectedRate.add(true);
        errorRate.add(false);
      } else {
        failureCount.add(1);
        errorRate.add(true);
        rejectedRate.add(false);
      }
    } catch (e) {
      failureCount.add(1);
      errorRate.add(true);
      rejectedRate.add(false);
    }
  } else {
    failureCount.add(1);
    errorRate.add(true);
    rejectedRate.add(false);
  }

  // Validate response structure when successful
  if (res.status === 200) {
    check(res, {
      'status is 200': (r) => r.status === 200,
      'response has service': (r) => r.json().service === 'client',
      'response has circuit_state': (r) => r.json().circuit_state !== undefined,
      'response has backend_response': (r) => r.json().backend_response !== undefined,
    });
  }

  // Small pause between requests
  sleep(0.2);
}

// Teardown function to print summary
export function handleSummary(data) {
  const summary = {
    total_requests: data.metrics.http_reqs.values.count,
    success_count: data.metrics.success_count ? data.metrics.success_count.values.count : 0,
    failure_count: data.metrics.failure_count ? data.metrics.failure_count.values.count : 0,
    rejected_count: data.metrics.rejected_count ? data.metrics.rejected_count.values.count : 0,
    avg_latency_ms: data.metrics.request_latency ? data.metrics.request_latency.values.avg : 0,
    p95_latency_ms: data.metrics.http_req_duration.values['p(95)'],
    error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
    rejected_rate: data.metrics.rejected ? data.metrics.rejected.values.rate : 0,
  };

  console.log('\n=== Circuit Breaker Load Test Summary ===');
  console.log(`Total Requests:   ${summary.total_requests}`);
  console.log(`Successful:       ${summary.success_count}`);
  console.log(`Failed:           ${summary.failure_count}`);
  console.log(`Rejected (CB):    ${summary.rejected_count}`);
  console.log(`Avg Latency:      ${summary.avg_latency_ms.toFixed(2)}ms`);
  console.log(`P95 Latency:      ${summary.p95_latency_ms.toFixed(2)}ms`);
  console.log(`Error Rate:       ${(summary.error_rate * 100).toFixed(2)}%`);
  console.log(`Rejection Rate:   ${(summary.rejected_rate * 100).toFixed(2)}%`);
  console.log('==========================================\n');

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
