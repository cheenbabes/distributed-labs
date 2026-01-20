import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const shedRate = new Rate('shed_requests');
const latencyTrend = new Trend('request_latency');
const successCounter = new Counter('successful_requests');
const shedCounter = new Counter('shed_requests_total');

// Test configuration - ramp up to overwhelm backend
export const options = {
  stages: [
    { duration: '30s', target: 20 },  // Ramp up to 20 users
    { duration: '1m', target: 50 },   // Increase to 50 users (backend overwhelmed)
    { duration: '1m', target: 50 },   // Stay at 50 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<5000'], // 95% of requests should be < 5s
    errors: ['rate<0.5'],              // Error rate should be < 50%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab26-gateway:8000';

export default function () {
  const res = http.get(`${BASE_URL}/api/process`);

  // Record metrics
  latencyTrend.add(res.timings.duration);

  const isSuccess = res.status === 200;
  const isShed = res.status === 503;
  const isError = res.status >= 500 && res.status !== 503;

  errorRate.add(isError);
  shedRate.add(isShed);

  if (isSuccess) {
    successCounter.add(1);
  }
  if (isShed) {
    shedCounter.add(1);
  }

  // Validate response
  check(res, {
    'status is 200 or 503': (r) => r.status === 200 || r.status === 503,
    'response has service': (r) => r.status !== 200 || r.json().service === 'gateway',
  });

  // Small pause between requests
  sleep(0.1);
}

export function handleSummary(data) {
  return {
    stdout: textSummary(data),
  };
}

function textSummary(data) {
  const summary = [];
  summary.push('\n=== Load Shedding Test Summary ===\n');

  const reqs = data.metrics.http_reqs;
  if (reqs) {
    summary.push(`Total Requests: ${reqs.values.count}`);
    summary.push(`Request Rate: ${reqs.values.rate.toFixed(2)}/s`);
  }

  const duration = data.metrics.http_req_duration;
  if (duration) {
    summary.push(`\nLatency:`);
    summary.push(`  p50: ${duration.values['p(50)'].toFixed(0)}ms`);
    summary.push(`  p95: ${duration.values['p(95)'].toFixed(0)}ms`);
    summary.push(`  p99: ${duration.values['p(99)'].toFixed(0)}ms`);
    summary.push(`  max: ${duration.values.max.toFixed(0)}ms`);
  }

  const errors = data.metrics.errors;
  if (errors) {
    summary.push(`\nError Rate: ${(errors.values.rate * 100).toFixed(2)}%`);
  }

  const shed = data.metrics.shed_requests;
  if (shed) {
    summary.push(`Shed Rate: ${(shed.values.rate * 100).toFixed(2)}%`);
  }

  const success = data.metrics.successful_requests;
  const shedTotal = data.metrics.shed_requests_total;
  if (success && shedTotal) {
    summary.push(`\nSuccessful: ${success.values.count}`);
    summary.push(`Shed: ${shedTotal.values.count}`);
  }

  summary.push('\n================================\n');
  return summary.join('\n');
}
