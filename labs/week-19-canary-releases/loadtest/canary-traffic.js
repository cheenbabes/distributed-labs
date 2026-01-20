import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const v1Requests = new Counter('v1_requests');
const v2Requests = new Counter('v2_requests');
const v1Errors = new Counter('v1_errors');
const v2Errors = new Counter('v2_errors');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 20 },   // Ramp up to 20 users
    { duration: '3m', target: 20 },    // Stay at 20 users
    { duration: '30s', target: 0 },    // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<1000'],   // 95% of requests should be < 1s
    errors: ['rate<0.15'],                // Overall error rate < 15%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab19-nginx';

export default function () {
  const res = http.get(`${BASE_URL}/api/process`);

  // Record general metrics
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  // Try to parse response and track version
  try {
    const body = res.json();
    if (body.version === 'v1') {
      v1Requests.add(1);
      if (res.status !== 200) {
        v1Errors.add(1);
      }
    } else if (body.version === 'v2') {
      v2Requests.add(1);
      if (res.status !== 200) {
        v2Errors.add(1);
      }
    }
  } catch (e) {
    // Error response, try to parse error body
    try {
      const errorBody = res.json();
      if (errorBody.detail && errorBody.detail.version === 'v2') {
        v2Requests.add(1);
        v2Errors.add(1);
      }
    } catch (e2) {
      // Ignore parse errors
    }
  }

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response has version': (r) => {
      try {
        const body = r.json();
        return body.version === 'v1' || body.version === 'v2';
      } catch (e) {
        return false;
      }
    },
  });

  // Small pause between requests
  sleep(0.3);
}

export function handleSummary(data) {
  const v1Total = data.metrics.v1_requests ? data.metrics.v1_requests.values.count : 0;
  const v2Total = data.metrics.v2_requests ? data.metrics.v2_requests.values.count : 0;
  const v1Err = data.metrics.v1_errors ? data.metrics.v1_errors.values.count : 0;
  const v2Err = data.metrics.v2_errors ? data.metrics.v2_errors.values.count : 0;

  const totalRequests = v1Total + v2Total;
  const v1Percent = totalRequests > 0 ? ((v1Total / totalRequests) * 100).toFixed(1) : 0;
  const v2Percent = totalRequests > 0 ? ((v2Total / totalRequests) * 100).toFixed(1) : 0;
  const v1ErrorRate = v1Total > 0 ? ((v1Err / v1Total) * 100).toFixed(2) : 0;
  const v2ErrorRate = v2Total > 0 ? ((v2Err / v2Total) * 100).toFixed(2) : 0;

  console.log('\n========================================');
  console.log('         CANARY TRAFFIC SUMMARY         ');
  console.log('========================================\n');
  console.log(`Total Requests: ${totalRequests}`);
  console.log(`\nStable (v1): ${v1Total} requests (${v1Percent}%)`);
  console.log(`  Error Rate: ${v1ErrorRate}%`);
  console.log(`\nCanary (v2): ${v2Total} requests (${v2Percent}%)`);
  console.log(`  Error Rate: ${v2ErrorRate}%`);
  console.log('\n========================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
