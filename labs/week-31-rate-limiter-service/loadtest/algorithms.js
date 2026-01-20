import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Counter } from 'k6/metrics';

// Metrics for comparing algorithms
const tokenBucketLatency = new Trend('token_bucket_latency');
const slidingWindowLatency = new Trend('sliding_window_latency');
const tokenBucketAllowed = new Counter('token_bucket_allowed');
const tokenBucketDenied = new Counter('token_bucket_denied');
const slidingWindowAllowed = new Counter('sliding_window_allowed');
const slidingWindowDenied = new Counter('sliding_window_denied');

// Test configuration
export const options = {
  scenarios: {
    token_bucket_test: {
      executor: 'constant-vus',
      vus: 5,
      duration: '1m',
      env: { ALGORITHM: 'token_bucket' },
    },
    sliding_window_test: {
      executor: 'constant-vus',
      vus: 5,
      duration: '1m',
      startTime: '1m10s',
      env: { ALGORITHM: 'sliding_window' },
    },
  },
};

const RATE_LIMITER_URL = __ENV.RATE_LIMITER_URL || 'http://rate-limiter:8000';

export function setup() {
  // Configure different algorithms for testing
  console.log('Setting up rate limit configurations...');

  // Token bucket configuration
  http.put(`${RATE_LIMITER_URL}/v1/config`, JSON.stringify({
    client_id: 'token-bucket-client',
    resource: '*',
    algorithm: 'token_bucket',
    limit: 50,
    refill_rate: 5.0,
    enabled: true,
  }), { headers: { 'Content-Type': 'application/json' } });

  // Sliding window configuration
  http.put(`${RATE_LIMITER_URL}/v1/config`, JSON.stringify({
    client_id: 'sliding-window-client',
    resource: '*',
    algorithm: 'sliding_window',
    limit: 50,
    window_seconds: 60,
    enabled: true,
  }), { headers: { 'Content-Type': 'application/json' } });

  console.log('Configuration complete.');
}

export default function () {
  const algorithm = __ENV.ALGORITHM || 'token_bucket';
  const clientId = algorithm === 'token_bucket' ? 'token-bucket-client' : 'sliding-window-client';

  const start = Date.now();
  const res = http.post(`${RATE_LIMITER_URL}/v1/check`, JSON.stringify({
    client_id: clientId,
    resource: '/test',
    cost: 1,
  }), {
    headers: { 'Content-Type': 'application/json' },
  });
  const duration = Date.now() - start;

  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  const body = JSON.parse(res.body);

  if (algorithm === 'token_bucket') {
    tokenBucketLatency.add(duration);
    if (body.allowed) {
      tokenBucketAllowed.add(1);
    } else {
      tokenBucketDenied.add(1);
    }
  } else {
    slidingWindowLatency.add(duration);
    if (body.allowed) {
      slidingWindowAllowed.add(1);
    } else {
      slidingWindowDenied.add(1);
    }
  }

  // Varying request rate to see algorithm behavior
  sleep(0.05 + Math.random() * 0.1);
}

export function handleSummary(data) {
  const summary = {
    token_bucket: {
      p50_latency: (data.metrics.token_bucket_latency?.values?.['p(50)'] || 0).toFixed(2) + 'ms',
      p95_latency: (data.metrics.token_bucket_latency?.values?.['p(95)'] || 0).toFixed(2) + 'ms',
      allowed: data.metrics.token_bucket_allowed?.values?.count || 0,
      denied: data.metrics.token_bucket_denied?.values?.count || 0,
    },
    sliding_window: {
      p50_latency: (data.metrics.sliding_window_latency?.values?.['p(50)'] || 0).toFixed(2) + 'ms',
      p95_latency: (data.metrics.sliding_window_latency?.values?.['p(95)'] || 0).toFixed(2) + 'ms',
      allowed: data.metrics.sliding_window_allowed?.values?.count || 0,
      denied: data.metrics.sliding_window_denied?.values?.count || 0,
    },
  };

  console.log('\n=== Algorithm Comparison ===');
  console.log('Token Bucket:');
  console.log(`  Latency p50: ${summary.token_bucket.p50_latency}`);
  console.log(`  Latency p95: ${summary.token_bucket.p95_latency}`);
  console.log(`  Allowed: ${summary.token_bucket.allowed}, Denied: ${summary.token_bucket.denied}`);
  console.log('Sliding Window:');
  console.log(`  Latency p50: ${summary.sliding_window.p50_latency}`);
  console.log(`  Latency p95: ${summary.sliding_window.p95_latency}`);
  console.log(`  Allowed: ${summary.sliding_window.allowed}, Denied: ${summary.sliding_window.denied}`);

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
