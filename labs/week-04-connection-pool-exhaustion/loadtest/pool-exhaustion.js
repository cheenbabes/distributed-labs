import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics for connection pool analysis
const errorRate = new Rate('errors');
const poolExhaustedRate = new Rate('pool_exhausted');
const latencyTrend = new Trend('request_latency');
const successCount = new Counter('successful_requests');
const exhaustedCount = new Counter('exhausted_requests');

const BASE_URL = __ENV.BASE_URL || 'http://api:8000';

// Test scenarios
export const options = {
  scenarios: {
    // Scenario 1: Ramp up to cause pool exhaustion
    pool_exhaustion: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '10s', target: 10 },   // Ramp to 10 (exceeds pool of 5)
        { duration: '30s', target: 50 },   // Ramp to 50 - severe exhaustion
        { duration: '20s', target: 50 },   // Hold at 50
        { duration: '10s', target: 0 },    // Ramp down
      ],
      gracefulRampDown: '5s',
    },
  },
  thresholds: {
    http_req_duration: ['p(50)<2000'],     // p50 should be < 2s
    errors: ['rate<0.5'],                   // Less than 50% errors (we expect some!)
  },
};

export default function () {
  const startTime = Date.now();

  const res = http.get(`${BASE_URL}/api/query`, {
    timeout: '30s',
  });

  const duration = Date.now() - startTime;
  latencyTrend.add(duration);

  // Check if this was a pool exhaustion (503)
  const isPoolExhausted = res.status === 503;
  const isSuccess = res.status === 200;
  const isError = !isSuccess;

  poolExhaustedRate.add(isPoolExhausted);
  errorRate.add(isError);

  if (isSuccess) {
    successCount.add(1);
  }
  if (isPoolExhausted) {
    exhaustedCount.add(1);
  }

  // Validate response
  check(res, {
    'status is 200 or 503': (r) => r.status === 200 || r.status === 503,
    'response has body': (r) => r.body.length > 0,
  });

  // Log pool exhaustion events
  if (isPoolExhausted) {
    try {
      const body = JSON.parse(res.body);
      console.log(`Pool exhausted! Waited ${body.detail?.waited_seconds}s`);
    } catch (e) {
      console.log('Pool exhausted!');
    }
  }

  // Very short pause - we want to stress the pool
  sleep(0.1);
}

// Burst test - send many requests at once
export function burst() {
  const responses = http.batch([
    ['GET', `${BASE_URL}/api/query`],
    ['GET', `${BASE_URL}/api/query`],
    ['GET', `${BASE_URL}/api/query`],
    ['GET', `${BASE_URL}/api/query`],
    ['GET', `${BASE_URL}/api/query`],
    ['GET', `${BASE_URL}/api/query`],
    ['GET', `${BASE_URL}/api/query`],
    ['GET', `${BASE_URL}/api/query`],
    ['GET', `${BASE_URL}/api/query`],
    ['GET', `${BASE_URL}/api/query`],
  ]);

  responses.forEach((res, i) => {
    check(res, {
      [`request ${i + 1} completed`]: (r) => r.status === 200 || r.status === 503,
    });

    if (res.status === 503) {
      poolExhaustedRate.add(1);
    }
  });
}

export function handleSummary(data) {
  const totalRequests = data.metrics.http_reqs?.values?.count || 0;
  const exhaustedRequests = data.metrics.exhausted_requests?.values?.count || 0;
  const successfulRequests = data.metrics.successful_requests?.values?.count || 0;

  console.log('\n========================================');
  console.log('   CONNECTION POOL EXHAUSTION SUMMARY');
  console.log('========================================');
  console.log(`Total Requests:      ${totalRequests}`);
  console.log(`Successful (200):    ${successfulRequests}`);
  console.log(`Pool Exhausted (503): ${exhaustedRequests}`);
  console.log(`Exhaustion Rate:     ${((exhaustedRequests / totalRequests) * 100).toFixed(1)}%`);
  console.log('========================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
