import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const shedRate = new Rate('shed_rate');
const latencyTrend = new Trend('request_latency');
const successfulRequests = new Counter('successful_requests');
const shedRequests = new Counter('shed_requests');

// Test configuration - ramp up to cause overload
export const options = {
  stages: [
    { duration: '30s', target: 20 },   // Ramp up to 20 users
    { duration: '1m', target: 50 },    // Ramp to 50 users (overload)
    { duration: '1m', target: 50 },    // Stay at 50 users
    { duration: '30s', target: 20 },   // Ramp down
    { duration: '30s', target: 0 },    // Cool down
  ],
  thresholds: {
    http_req_duration: ['p(95)<5000'], // 95% of requests should be < 5s
    errors: ['rate<0.3'],              // Error rate should be < 30%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab35-api-service:8000';

export default function () {
  const res = http.get(`${BASE_URL}/api/process`);

  // Record metrics
  latencyTrend.add(res.timings.duration);

  // Check if request was shed (503)
  const wasShed = res.status === 503;
  shedRate.add(wasShed);

  if (wasShed) {
    shedRequests.add(1);
    // Check for proper shed response
    check(res, {
      'shed response has retry-after': (r) => r.headers['Retry-After'] !== undefined,
      'shed response is 503': (r) => r.status === 503,
    });
  } else {
    errorRate.add(res.status !== 200);
    if (res.status === 200) {
      successfulRequests.add(1);
      check(res, {
        'status is 200': (r) => r.status === 200,
        'response has service': (r) => r.json().service === 'api-service',
      });
    }
  }

  // Small pause between requests
  sleep(0.1);
}

export function handleSummary(data) {
  const shed = data.metrics.shed_requests ? data.metrics.shed_requests.values.count : 0;
  const success = data.metrics.successful_requests ? data.metrics.successful_requests.values.count : 0;
  const total = shed + success;
  const shedPercent = total > 0 ? ((shed / total) * 100).toFixed(2) : 0;

  console.log('\n=== Load Shedding Summary ===');
  console.log(`Total Requests: ${total}`);
  console.log(`Successful: ${success}`);
  console.log(`Shed (503): ${shed} (${shedPercent}%)`);
  console.log('=============================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
