import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter, Gauge } from 'k6/metrics';
import { randomString } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

// Custom metrics
const errorRate = new Rate('errors');
const shortenLatency = new Trend('shorten_latency');
const redirectLatency = new Trend('redirect_latency');
const rateLimitHits = new Counter('rate_limit_hits');
const instanceDistribution = new Counter('instance_requests');

// Test configuration for high traffic simulation
export const options = {
  scenarios: {
    // Scenario 1: Gradual ramp-up to test horizontal scaling
    ramp_up: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 50 },   // Ramp up to 50 VUs
        { duration: '1m', target: 100 },   // Ramp up to 100 VUs
        { duration: '2m', target: 100 },   // Stay at 100 VUs
        { duration: '30s', target: 200 },  // Spike to 200 VUs
        { duration: '1m', target: 200 },   // Stay at 200 VUs
        { duration: '30s', target: 0 },    // Ramp down
      ],
      exec: 'mixedWorkload',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500', 'p(99)<1000'],
    'redirect_latency': ['p(95)<100', 'p(99)<200'],
    'shorten_latency': ['p(95)<300'],
    errors: ['rate<0.05'],
    rate_limit_hits: ['count<1000'],  // Expect some rate limits under high load
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://nginx';

// Store created short codes for reading
const shortCodes = [];
const MAX_CODES = 2000;

// Helper to get a random existing short code
function getRandomCode() {
  if (shortCodes.length === 0) {
    return 'example1';
  }
  return shortCodes[Math.floor(Math.random() * shortCodes.length)];
}

// Track which instance handled the request
function trackInstance(response) {
  const upstream = response.headers['X-Upstream-Server'];
  if (upstream) {
    instanceDistribution.add(1, { instance: upstream });
  }
}

// Write operation: Create new short URLs
function shortenUrl() {
  const url = `https://loadtest.example.com/${randomString(20)}/${randomString(10)}?ts=${Date.now()}`;

  const res = http.post(
    `${BASE_URL}/shorten`,
    JSON.stringify({ url: url }),
    {
      headers: { 'Content-Type': 'application/json' },
      tags: { operation: 'shorten' },
    }
  );

  shortenLatency.add(res.timings.duration);
  trackInstance(res);

  if (res.status === 429) {
    rateLimitHits.add(1);
    return null;
  }

  errorRate.add(res.status !== 200);

  const success = check(res, {
    'shorten status is 200': (r) => r.status === 200,
    'shorten has short_code': (r) => {
      try {
        return r.json().short_code !== undefined;
      } catch (e) {
        return false;
      }
    },
    'shorten has instance': (r) => {
      try {
        return r.json().instance !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  if (success && res.status === 200) {
    try {
      const shortCode = res.json().short_code;
      if (shortCodes.length < MAX_CODES) {
        shortCodes.push(shortCode);
      } else {
        shortCodes[Math.floor(Math.random() * MAX_CODES)] = shortCode;
      }
    } catch (e) {
      // Ignore JSON parse errors
    }
  }

  return res;
}

// Read operation: Follow redirects
function redirectUrl() {
  const code = getRandomCode();

  const res = http.get(`${BASE_URL}/${code}`, {
    redirects: 0,
    tags: { operation: 'redirect' },
  });

  redirectLatency.add(res.timings.duration);
  trackInstance(res);

  if (res.status === 429) {
    rateLimitHits.add(1);
    return null;
  }

  errorRate.add(res.status !== 302 && res.status !== 404);

  check(res, {
    'redirect status is 302 or 404': (r) => r.status === 302 || r.status === 404,
  });

  return res;
}

// Mixed workload: 100:1 read/write ratio
export function mixedWorkload() {
  group('mixed_workload', () => {
    // 1 write
    shortenUrl();

    // 100 reads (simulating real-world ratio)
    for (let i = 0; i < 100; i++) {
      redirectUrl();
      // Small random delay between reads
      if (i % 10 === 0) {
        sleep(0.01);
      }
    }
  });

  sleep(0.1);
}

// Burst test: Many concurrent writes
export function burstWrites() {
  group('burst_writes', () => {
    for (let i = 0; i < 10; i++) {
      shortenUrl();
    }
  });
  sleep(0.5);
}

// Burst test: Many concurrent reads
export function burstReads() {
  group('burst_reads', () => {
    for (let i = 0; i < 50; i++) {
      redirectUrl();
    }
  });
  sleep(0.1);
}

// Rate limit test: Rapid requests from single VU
export function rateLimitTest() {
  group('rate_limit_test', () => {
    // Make rapid requests to trigger rate limiting
    for (let i = 0; i < 150; i++) {
      const res = http.get(`${BASE_URL}/example1`, {
        redirects: 0,
        tags: { operation: 'rate_limit_test' },
      });

      if (res.status === 429) {
        rateLimitHits.add(1);
        check(res, {
          'rate limit returns 429': (r) => r.status === 429,
        });
        break;  // Stop once rate limited
      }
    }
  });
  sleep(60);  // Wait for rate limit window to reset
}

// Setup: Pre-populate URLs
export function setup() {
  console.log('Starting URL Shortener Scaling Load Test');
  console.log(`Target: ${BASE_URL}`);

  // Verify the service is up
  const health = http.get(`${BASE_URL}/health`);
  if (health.status !== 200) {
    throw new Error(`Service not healthy: ${health.status}`);
  }

  // Pre-populate with URLs for read testing
  console.log('Pre-populating URLs...');
  for (let i = 0; i < 100; i++) {
    const res = http.post(
      `${BASE_URL}/shorten`,
      JSON.stringify({ url: `https://setup.example.com/page${i}` }),
      { headers: { 'Content-Type': 'application/json' } }
    );
    if (res.status === 200) {
      try {
        shortCodes.push(res.json().short_code);
      } catch (e) {
        // Ignore
      }
    }
  }
  console.log(`Pre-populated ${shortCodes.length} URLs`);

  return { startTime: Date.now() };
}

// Teardown: Report summary
export function teardown(data) {
  const duration = (Date.now() - data.startTime) / 1000;
  console.log(`\nTest completed in ${duration.toFixed(2)}s`);
}

// Summary handler
export function handleSummary(data) {
  console.log('\n=== URL Shortener Scaling Load Test Summary ===');

  if (data.metrics.shorten_latency) {
    console.log(`\nShorten Latency:`);
    console.log(`  p50: ${data.metrics.shorten_latency.values['p(50)'].toFixed(2)}ms`);
    console.log(`  p95: ${data.metrics.shorten_latency.values['p(95)'].toFixed(2)}ms`);
    console.log(`  p99: ${data.metrics.shorten_latency.values['p(99)'].toFixed(2)}ms`);
  }

  if (data.metrics.redirect_latency) {
    console.log(`\nRedirect Latency:`);
    console.log(`  p50: ${data.metrics.redirect_latency.values['p(50)'].toFixed(2)}ms`);
    console.log(`  p95: ${data.metrics.redirect_latency.values['p(95)'].toFixed(2)}ms`);
    console.log(`  p99: ${data.metrics.redirect_latency.values['p(99)'].toFixed(2)}ms`);
  }

  if (data.metrics.errors) {
    console.log(`\nError Rate: ${(data.metrics.errors.values.rate * 100).toFixed(2)}%`);
  }

  if (data.metrics.rate_limit_hits) {
    console.log(`Rate Limit Hits: ${data.metrics.rate_limit_hits.values.count}`);
  }

  if (data.metrics.http_reqs) {
    const totalReqs = data.metrics.http_reqs.values.count;
    const rps = data.metrics.http_reqs.values.rate;
    console.log(`\nTotal Requests: ${totalReqs}`);
    console.log(`Average RPS: ${rps.toFixed(2)}`);
  }

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}

// Default function for simple testing
export default function () {
  mixedWorkload();
}
