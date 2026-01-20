import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

// Custom metrics
const poolExhaustedRate = new Rate('pool_exhausted');
const successCount = new Counter('successful_requests');
const exhaustedCount = new Counter('exhausted_requests');
const acquireTimeTrend = new Trend('acquire_time_ms');

const BASE_URL = __ENV.BASE_URL || 'http://api:8000';
const CONCURRENT = __ENV.CONCURRENT ? parseInt(__ENV.CONCURRENT) : 50;

// Simple burst test - send exactly N concurrent requests
export const options = {
  scenarios: {
    concurrent_burst: {
      executor: 'shared-iterations',
      vus: CONCURRENT,
      iterations: CONCURRENT,
      maxDuration: '60s',
    },
  },
};

export default function () {
  const vuId = __VU;

  // Slight stagger to ensure true concurrency
  sleep(0.01 * vuId);

  const res = http.get(`${BASE_URL}/api/query`, {
    timeout: '30s',
    tags: { vu: String(vuId) },
  });

  const isSuccess = res.status === 200;
  const isPoolExhausted = res.status === 503;

  if (isSuccess) {
    successCount.add(1);
    try {
      const body = JSON.parse(res.body);
      if (body.timing?.acquire_time_ms) {
        acquireTimeTrend.add(body.timing.acquire_time_ms);
      }
    } catch (e) {}
  }

  if (isPoolExhausted) {
    exhaustedCount.add(1);
    poolExhaustedRate.add(1);
    try {
      const body = JSON.parse(res.body);
      console.log(`VU ${vuId}: Pool exhausted after ${body.detail?.waited_seconds}s`);
    } catch (e) {
      console.log(`VU ${vuId}: Pool exhausted`);
    }
  }

  check(res, {
    'request completed': (r) => r.status === 200 || r.status === 503,
  });
}

export function handleSummary(data) {
  const totalRequests = data.metrics.http_reqs?.values?.count || 0;
  const exhaustedRequests = data.metrics.exhausted_requests?.values?.count || 0;
  const successfulRequests = data.metrics.successful_requests?.values?.count || 0;

  console.log('\n==========================================');
  console.log('     CONCURRENT BURST TEST RESULTS');
  console.log('==========================================');
  console.log(`Concurrent Requests:  ${CONCURRENT}`);
  console.log(`Pool Size:            5 connections`);
  console.log('------------------------------------------');
  console.log(`Successful (200):     ${successfulRequests}`);
  console.log(`Pool Exhausted (503): ${exhaustedRequests}`);
  console.log(`Exhaustion Rate:      ${((exhaustedRequests / totalRequests) * 100).toFixed(1)}%`);
  console.log('==========================================');

  if (exhaustedRequests > 0) {
    console.log('\n[!] Pool exhaustion occurred as expected!');
    console.log('    With 50 concurrent requests and only 5 connections,');
    console.log('    45 requests had to wait or timeout.');
  }

  return {
    stdout: JSON.stringify({
      concurrent_requests: CONCURRENT,
      pool_size: 5,
      successful: successfulRequests,
      exhausted: exhaustedRequests,
      exhaustion_rate: ((exhaustedRequests / totalRequests) * 100).toFixed(1) + '%',
    }, null, 2),
  };
}
