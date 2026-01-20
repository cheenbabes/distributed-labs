import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomString } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

// Custom metrics
const errorRate = new Rate('errors');
const shortenLatency = new Trend('shorten_latency');
const redirectLatency = new Trend('redirect_latency');
const cacheHits = new Counter('cache_hits');
const cacheMisses = new Counter('cache_misses');
const writeCount = new Counter('write_operations');
const readCount = new Counter('read_operations');

// Test configuration - simulates realistic 100:1 read/write ratio
export const options = {
  scenarios: {
    // Scenario 1: Writers (create new short URLs)
    writers: {
      executor: 'constant-arrival-rate',
      rate: 10,              // 10 writes per second
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 5,
      maxVUs: 10,
      exec: 'writeScenario',
    },
    // Scenario 2: Readers (redirect requests) - 100x the rate
    readers: {
      executor: 'constant-arrival-rate',
      rate: 1000,            // 1000 reads per second
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 50,
      maxVUs: 100,
      exec: 'readScenario',
      startTime: '5s',       // Start after some URLs are created
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],     // 95% of requests < 500ms
    'redirect_latency': ['p(95)<100'],    // Redirects should be fast
    'shorten_latency': ['p(95)<200'],     // Shortening can be slower
    errors: ['rate<0.05'],                // Less than 5% errors
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://shortener:8000';

// Store created short codes for reading
const shortCodes = [];
const MAX_CODES = 1000;

// Helper to get a random existing short code
function getRandomCode() {
  if (shortCodes.length === 0) {
    return 'example1'; // Fallback to seeded data
  }
  return shortCodes[Math.floor(Math.random() * shortCodes.length)];
}

// Write scenario: Create new short URLs
export function writeScenario() {
  const url = `https://example.com/${randomString(20)}/${randomString(10)}`;

  const res = http.post(
    `${BASE_URL}/shorten`,
    JSON.stringify({ url: url }),
    {
      headers: { 'Content-Type': 'application/json' },
      tags: { operation: 'shorten' },
    }
  );

  writeCount.add(1);
  shortenLatency.add(res.timings.duration);
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
  });

  if (success && res.status === 200) {
    try {
      const shortCode = res.json().short_code;
      if (shortCodes.length < MAX_CODES) {
        shortCodes.push(shortCode);
      } else {
        // Replace a random entry
        shortCodes[Math.floor(Math.random() * MAX_CODES)] = shortCode;
      }
    } catch (e) {
      // Ignore JSON parse errors
    }
  }

  sleep(0.1);
}

// Read scenario: Follow redirects
export function readScenario() {
  const code = getRandomCode();

  const res = http.get(`${BASE_URL}/${code}`, {
    redirects: 0, // Don't follow redirects, just measure the 302
    tags: { operation: 'redirect' },
  });

  readCount.add(1);
  redirectLatency.add(res.timings.duration);
  errorRate.add(res.status !== 302 && res.status !== 404);

  check(res, {
    'redirect status is 302': (r) => r.status === 302,
    'redirect has location header': (r) => r.headers['Location'] !== undefined,
  });

  // Small random sleep to vary load
  sleep(Math.random() * 0.05);
}

// Default function for simple testing
export default function () {
  group('mixed_workload', () => {
    // 1 write
    writeScenario();

    // 10 reads (simulating 10:1 ratio in default mode)
    for (let i = 0; i < 10; i++) {
      readScenario();
    }
  });

  sleep(0.5);
}

// Lifecycle hooks
export function setup() {
  console.log('Starting URL Shortener load test');
  console.log(`Target: ${BASE_URL}`);

  // Verify the service is up
  const health = http.get(`${BASE_URL}/health`);
  if (health.status !== 200) {
    throw new Error(`Service not healthy: ${health.status}`);
  }

  // Pre-populate with some URLs to ensure reads have data
  console.log('Pre-populating URLs for read testing...');
  for (let i = 0; i < 50; i++) {
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

export function teardown(data) {
  const duration = (Date.now() - data.startTime) / 1000;
  console.log(`Test completed in ${duration.toFixed(2)}s`);
}

export function handleSummary(data) {
  // Calculate read/write ratio
  const writes = data.metrics.write_operations ? data.metrics.write_operations.values.count : 0;
  const reads = data.metrics.read_operations ? data.metrics.read_operations.values.count : 0;
  const ratio = writes > 0 ? (reads / writes).toFixed(1) : 'N/A';

  console.log('\n=== URL Shortener Load Test Summary ===');
  console.log(`Total Writes: ${writes}`);
  console.log(`Total Reads: ${reads}`);
  console.log(`Read/Write Ratio: ${ratio}:1`);

  if (data.metrics.shorten_latency) {
    console.log(`\nShorten Latency (p95): ${data.metrics.shorten_latency.values['p(95)'].toFixed(2)}ms`);
  }
  if (data.metrics.redirect_latency) {
    console.log(`Redirect Latency (p95): ${data.metrics.redirect_latency.values['p(95)'].toFixed(2)}ms`);
  }
  if (data.metrics.errors) {
    console.log(`Error Rate: ${(data.metrics.errors.values.rate * 100).toFixed(2)}%`);
  }

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
