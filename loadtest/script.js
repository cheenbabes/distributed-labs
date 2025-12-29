import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const requestDuration = new Trend('request_duration', true);

// Test configuration
export const options = {
  // Default: 10 virtual users for 30 seconds
  vus: 10,
  duration: '30s',

  // Alternatively, use stages for ramping:
  // stages: [
  //   { duration: '10s', target: 5 },   // Ramp up to 5 users
  //   { duration: '30s', target: 10 },  // Stay at 10 users
  //   { duration: '10s', target: 0 },   // Ramp down to 0
  // ],

  thresholds: {
    http_req_duration: ['p(95)<500'], // 95% of requests should be below 500ms
    errors: ['rate<0.1'],              // Error rate should be below 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  // Make a request to the gateway
  const response = http.get(`${BASE_URL}/api/process`);

  // Track custom metrics
  requestDuration.add(response.timings.duration);

  // Validate response
  const checkResult = check(response, {
    'status is 200': (r) => r.status === 200,
    'response has service field': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.service === 'gateway';
      } catch {
        return false;
      }
    },
    'response has chain': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.chain !== undefined;
      } catch {
        return false;
      }
    },
  });

  // Record errors
  errorRate.add(!checkResult);

  // Small sleep between requests (adjust as needed)
  sleep(0.1);
}

// Lifecycle hooks
export function setup() {
  console.log('Starting load test...');
  console.log(`Target URL: ${BASE_URL}/api/process`);

  // Verify the service is reachable
  const response = http.get(`${BASE_URL}/health`);
  if (response.status !== 200) {
    throw new Error(`Gateway health check failed: ${response.status}`);
  }
  console.log('Gateway is healthy, starting test.');
}

export function teardown(data) {
  console.log('Load test completed.');
}
