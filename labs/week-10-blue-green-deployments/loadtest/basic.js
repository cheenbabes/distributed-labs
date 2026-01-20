import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const blueRequests = new Counter('blue_requests');
const greenRequests = new Counter('green_requests');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 10 },  // Ramp up to 10 users
    { duration: '2m', target: 10 },   // Stay at 10 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<1000'], // 95% of requests should be < 1s
    errors: ['rate<0.1'],              // Error rate should be < 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://nginx:80';

export default function () {
  // Make request through the load balancer
  const res = http.get(`${BASE_URL}/api/data`);

  // Record metrics
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  // Track which environment served the request
  const appColor = res.headers['X-App-Color'];
  if (appColor === 'blue') {
    blueRequests.add(1);
  } else if (appColor === 'green') {
    greenRequests.add(1);
  }

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'has version header': (r) => r.headers['X-App-Version'] !== undefined,
    'has color header': (r) => r.headers['X-App-Color'] !== undefined,
    'response has data': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.data !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  // Log which environment is serving (for debugging)
  if (__ENV.DEBUG) {
    console.log(`Request served by: ${appColor} (${res.headers['X-App-Version']})`);
  }

  // Small pause between requests
  sleep(0.5);
}

export function handleSummary(data) {
  const blueCount = data.metrics.blue_requests ? data.metrics.blue_requests.values.count : 0;
  const greenCount = data.metrics.green_requests ? data.metrics.green_requests.values.count : 0;
  const total = blueCount + greenCount;

  const summary = {
    'summary': {
      'total_requests': total,
      'blue_requests': blueCount,
      'green_requests': greenCount,
      'blue_percentage': total > 0 ? ((blueCount / total) * 100).toFixed(2) + '%' : '0%',
      'green_percentage': total > 0 ? ((greenCount / total) * 100).toFixed(2) + '%' : '0%',
      'error_rate': data.metrics.errors ? data.metrics.errors.values.rate.toFixed(4) : 0,
      'p95_latency_ms': data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(95)'].toFixed(2) : 0,
    },
    'raw': data
  };

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
