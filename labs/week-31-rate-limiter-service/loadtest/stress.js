import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const rateLimitLatency = new Trend('rate_limit_check_latency');
const errorRate = new Rate('errors');
const rateLimitedRate = new Rate('rate_limited');

// Stress test configuration - push the rate limiter to its limits
export const options = {
  scenarios: {
    stress_test: {
      executor: 'ramping-arrival-rate',
      startRate: 10,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 500,
      stages: [
        { duration: '30s', target: 50 },   // Ramp to 50 req/s
        { duration: '1m', target: 100 },   // Increase to 100 req/s
        { duration: '1m', target: 200 },   // Peak at 200 req/s
        { duration: '30s', target: 100 },  // Scale back
        { duration: '30s', target: 0 },    // Wind down
      ],
    },
  },
  thresholds: {
    rate_limit_check_latency: ['p(99)<100'],  // Rate limit checks should be fast
    errors: ['rate<0.01'],                     // Very low error rate expected
  },
};

const API_URL = __ENV.API_URL || 'http://nginx:80';
const RATE_LIMITER_URL = __ENV.RATE_LIMITER_URL || 'http://rate-limiter:8000';

export default function () {
  // Test the rate limiter service directly
  const checkStart = Date.now();
  const checkRes = http.post(`${RATE_LIMITER_URL}/v1/check`, JSON.stringify({
    client_id: `stress-client-${__VU % 10}`,  // 10 unique clients
    resource: '/api/data',
    cost: 1,
  }), {
    headers: { 'Content-Type': 'application/json' },
  });
  const checkDuration = Date.now() - checkStart;

  rateLimitLatency.add(checkDuration);

  // Check response
  const checkOk = check(checkRes, {
    'rate limit check succeeded': (r) => r.status === 200,
    'check returned allowed field': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.allowed !== undefined;
      } catch {
        return false;
      }
    },
  });

  if (!checkOk) {
    errorRate.add(1);
  } else {
    errorRate.add(0);
    const body = JSON.parse(checkRes.body);
    rateLimitedRate.add(body.allowed ? 0 : 1);
  }

  // Minimal sleep to maximize throughput
  sleep(0.01);
}

export function handleSummary(data) {
  const rps = data.metrics.http_reqs?.values?.rate || 0;
  const p50 = data.metrics.rate_limit_check_latency?.values?.['p(50)'] || 0;
  const p95 = data.metrics.rate_limit_check_latency?.values?.['p(95)'] || 0;
  const p99 = data.metrics.rate_limit_check_latency?.values?.['p(99)'] || 0;

  console.log('\n=== Rate Limiter Stress Test Results ===');
  console.log(`Throughput: ${rps.toFixed(2)} req/s`);
  console.log(`Latency p50: ${p50.toFixed(2)}ms`);
  console.log(`Latency p95: ${p95.toFixed(2)}ms`);
  console.log(`Latency p99: ${p99.toFixed(2)}ms`);
  console.log(`Rate limited: ${((data.metrics.rate_limited?.values?.rate || 0) * 100).toFixed(2)}%`);
  console.log(`Errors: ${((data.metrics.errors?.values?.rate || 0) * 100).toFixed(2)}%`);

  return {
    stdout: JSON.stringify({
      throughput_rps: rps.toFixed(2),
      latency_p50_ms: p50.toFixed(2),
      latency_p95_ms: p95.toFixed(2),
      latency_p99_ms: p99.toFixed(2),
      rate_limited_pct: ((data.metrics.rate_limited?.values?.rate || 0) * 100).toFixed(2),
      error_pct: ((data.metrics.errors?.values?.rate || 0) * 100).toFixed(2),
    }, null, 2),
  };
}
