import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const rateLimitedRate = new Rate('rate_limited');
const latencyTrend = new Trend('request_latency');
const allowedRequests = new Counter('allowed_requests');
const deniedRequests = new Counter('denied_requests');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 5 },   // Ramp up to 5 users
    { duration: '1m', target: 10 },   // Increase to 10 users
    { duration: '30s', target: 20 },  // Spike to 20 users (trigger rate limiting)
    { duration: '30s', target: 5 },   // Back to normal
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<1000'], // 95% of requests should be < 1s
    errors: ['rate<0.1'],              // Error rate should be < 10%
  },
};

const API_URL = __ENV.API_URL || 'http://nginx:80';

export default function () {
  // Make request to rate-limited API
  const res = http.get(`${API_URL}/api/data`, {
    headers: {
      'X-Client-ID': `client-${__VU}`,  // Use VU ID as client ID
    },
  });

  // Record latency
  latencyTrend.add(res.timings.duration);

  // Check for errors (non-200/429)
  const isError = res.status !== 200 && res.status !== 429;
  errorRate.add(isError);

  // Track rate limiting
  if (res.status === 429) {
    deniedRequests.add(1);
    rateLimitedRate.add(1);

    // Log rate limit headers
    const retryAfter = res.headers['Retry-After'];
    if (retryAfter) {
      console.log(`Rate limited, retry after ${retryAfter}s`);
    }
  } else if (res.status === 200) {
    allowedRequests.add(1);
    rateLimitedRate.add(0);
  }

  // Validate response
  check(res, {
    'status is 200 or 429': (r) => r.status === 200 || r.status === 429,
    'has rate limit headers': (r) => r.headers['X-Ratelimit-Limit'] !== undefined,
  });

  // Random pause between requests (0.1-0.5s)
  sleep(Math.random() * 0.4 + 0.1);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      allowed: data.metrics.allowed_requests?.values?.count || 0,
      denied: data.metrics.denied_requests?.values?.count || 0,
      rate_limited_pct: ((data.metrics.rate_limited?.values?.rate || 0) * 100).toFixed(2) + '%',
      p95_latency: (data.metrics.http_req_duration?.values?.['p(95)'] || 0).toFixed(2) + 'ms',
    }, null, 2),
  };
}
