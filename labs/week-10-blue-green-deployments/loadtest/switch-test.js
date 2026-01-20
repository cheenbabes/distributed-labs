import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics for tracking deployment switches
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const blueRequests = new Counter('blue_requests');
const greenRequests = new Counter('green_requests');
const switchDetected = new Counter('switch_events');

// Test configuration - longer test for observing traffic switches
export const options = {
  stages: [
    { duration: '10s', target: 5 },   // Ramp up
    { duration: '5m', target: 5 },    // Sustained load - switch traffic during this period
    { duration: '10s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    errors: ['rate<0.2'], // Allow higher error rate during switches
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://nginx:80';
let lastColor = null;

export default function () {
  const res = http.get(`${BASE_URL}/api/data`);

  // Record metrics
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  const appColor = res.headers['X-App-Color'];

  // Track which environment served the request
  if (appColor === 'blue') {
    blueRequests.add(1);
  } else if (appColor === 'green') {
    greenRequests.add(1);
  }

  // Detect traffic switch
  if (lastColor !== null && lastColor !== appColor) {
    switchDetected.add(1);
    console.log(`[SWITCH DETECTED] Traffic moved from ${lastColor} to ${appColor}`);
  }
  lastColor = appColor;

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response is valid': (r) => {
      if (r.status !== 200) return true; // Skip validation for errors
      try {
        const body = JSON.parse(r.body);
        return body.data !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  sleep(0.2);
}

export function handleSummary(data) {
  const blueCount = data.metrics.blue_requests ? data.metrics.blue_requests.values.count : 0;
  const greenCount = data.metrics.green_requests ? data.metrics.green_requests.values.count : 0;
  const total = blueCount + greenCount;
  const switches = data.metrics.switch_events ? data.metrics.switch_events.values.count : 0;

  console.log('\n========================================');
  console.log('       BLUE-GREEN SWITCH TEST SUMMARY');
  console.log('========================================');
  console.log(`Total Requests:     ${total}`);
  console.log(`Blue Requests:      ${blueCount} (${total > 0 ? ((blueCount / total) * 100).toFixed(1) : 0}%)`);
  console.log(`Green Requests:     ${greenCount} (${total > 0 ? ((greenCount / total) * 100).toFixed(1) : 0}%)`);
  console.log(`Switch Events:      ${switches}`);
  console.log(`Error Rate:         ${(data.metrics.errors ? data.metrics.errors.values.rate * 100 : 0).toFixed(2)}%`);
  console.log(`p95 Latency:        ${data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(95)'].toFixed(2) : 0}ms`);
  console.log('========================================\n');

  return {
    stdout: JSON.stringify({
      summary: {
        total_requests: total,
        blue_requests: blueCount,
        green_requests: greenCount,
        switch_events: switches,
        error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
        p95_latency_ms: data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(95)'] : 0,
      }
    }, null, 2),
  };
}
