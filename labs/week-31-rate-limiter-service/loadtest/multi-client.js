import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics per client type
const premiumLatency = new Trend('premium_latency');
const standardLatency = new Trend('standard_latency');
const premiumRateLimited = new Rate('premium_rate_limited');
const standardRateLimited = new Rate('standard_rate_limited');

// Test configuration - simulate different client tiers
export const options = {
  scenarios: {
    premium_clients: {
      executor: 'constant-vus',
      vus: 3,
      duration: '2m',
      env: { CLIENT_TYPE: 'premium' },
    },
    standard_clients: {
      executor: 'constant-vus',
      vus: 10,
      duration: '2m',
      env: { CLIENT_TYPE: 'standard' },
    },
    burst_client: {
      executor: 'constant-arrival-rate',
      rate: 50,
      timeUnit: '1s',
      duration: '30s',
      preAllocatedVUs: 20,
      startTime: '30s',
      env: { CLIENT_TYPE: 'burst' },
    },
  },
};

const API_URL = __ENV.API_URL || 'http://nginx:80';

export default function () {
  const clientType = __ENV.CLIENT_TYPE || 'standard';
  const clientId = `${clientType}-client-${__VU}`;

  // Make request
  const res = http.get(`${API_URL}/api/data`, {
    headers: {
      'X-Client-ID': clientId,
    },
  });

  // Record metrics by client type
  const isRateLimited = res.status === 429;

  if (clientType === 'premium') {
    premiumLatency.add(res.timings.duration);
    premiumRateLimited.add(isRateLimited);
  } else {
    standardLatency.add(res.timings.duration);
    standardRateLimited.add(isRateLimited);
  }

  // Validate response
  check(res, {
    'status is 200 or 429': (r) => r.status === 200 || r.status === 429,
    'has rate limit headers': (r) => r.headers['X-Ratelimit-Limit'] !== undefined,
    'has remaining count': (r) => r.headers['X-Ratelimit-Remaining'] !== undefined,
  });

  // Log when rate limited
  if (isRateLimited) {
    const remaining = res.headers['X-Ratelimit-Remaining'] || 0;
    const limit = res.headers['X-Ratelimit-Limit'] || 'unknown';
    console.log(`[${clientType}] Client ${clientId} rate limited. Limit: ${limit}, Remaining: ${remaining}`);
  }

  // Different pause patterns for different client types
  if (clientType === 'premium') {
    sleep(0.5);  // Premium clients make slower, sustained requests
  } else if (clientType === 'burst') {
    // Burst clients don't sleep (handled by arrival rate)
  } else {
    sleep(0.1);  // Standard clients make rapid requests
  }
}

export function handleSummary(data) {
  const summary = {
    premium: {
      p95_latency: (data.metrics.premium_latency?.values?.['p(95)'] || 0).toFixed(2) + 'ms',
      rate_limited_pct: ((data.metrics.premium_rate_limited?.values?.rate || 0) * 100).toFixed(2) + '%',
    },
    standard: {
      p95_latency: (data.metrics.standard_latency?.values?.['p(95)'] || 0).toFixed(2) + 'ms',
      rate_limited_pct: ((data.metrics.standard_rate_limited?.values?.rate || 0) * 100).toFixed(2) + '%',
    },
  };

  console.log('\n=== Multi-Client Test Summary ===');
  console.log(`Premium clients p95 latency: ${summary.premium.p95_latency}`);
  console.log(`Premium clients rate limited: ${summary.premium.rate_limited_pct}`);
  console.log(`Standard clients p95 latency: ${summary.standard.p95_latency}`);
  console.log(`Standard clients rate limited: ${summary.standard.rate_limited_pct}`);

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
