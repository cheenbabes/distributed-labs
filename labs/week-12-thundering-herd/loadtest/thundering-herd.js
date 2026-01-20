import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter, Gauge } from 'k6/metrics';

// =============================================================================
// Custom Metrics
// =============================================================================

const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency_ms');
const cacheHitRate = new Rate('cache_hit_rate');
const stampedeCaught = new Counter('stampede_events_observed');

// =============================================================================
// Configuration
// =============================================================================

const BASE_URL = __ENV.BASE_URL || 'http://lab12-api:8000';

// Single product ID to create hot key contention
const HOT_KEY = 'popular-product-123';

// Test scenarios - uncomment the one you want to run
export const options = {
  scenarios: {
    // Scenario 1: Steady load to observe cache behavior
    steady_load: {
      executor: 'constant-arrival-rate',
      rate: 20,              // 20 requests per second
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 50,
      maxVUs: 100,
      exec: 'steadyLoad',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<2000'],  // 95% of requests should be < 2s
    errors: ['rate<0.1'],                // Error rate should be < 10%
  },
};

// Alternative scenarios (copy one into 'scenarios' above to use)
export const alternativeScenarios = {
  // Scenario 2: Burst traffic (more likely to trigger stampede)
  burst_traffic: {
    executor: 'ramping-arrival-rate',
    startRate: 5,
    timeUnit: '1s',
    stages: [
      { duration: '10s', target: 5 },    // Warm up
      { duration: '5s', target: 50 },    // Sudden burst
      { duration: '20s', target: 50 },   // Sustained high load
      { duration: '5s', target: 5 },     // Cool down
      { duration: '10s', target: 5 },    // Wait for cache to expire
      { duration: '5s', target: 50 },    // Another burst
      { duration: '20s', target: 50 },   // Sustained
      { duration: '10s', target: 0 },    // Ramp down
    ],
    preAllocatedVUs: 100,
    maxVUs: 200,
    exec: 'steadyLoad',
  },

  // Scenario 3: Gradual ramp up
  gradual_ramp: {
    executor: 'ramping-vus',
    startVUs: 0,
    stages: [
      { duration: '30s', target: 10 },
      { duration: '1m', target: 30 },
      { duration: '1m', target: 30 },
      { duration: '30s', target: 0 },
    ],
    exec: 'steadyLoad',
  },
};

// =============================================================================
// Test Functions
// =============================================================================

export function setup() {
  // Clear cache before test starts
  console.log('Clearing cache before test...');
  const clearRes = http.del(`${BASE_URL}/cache`);
  if (clearRes.status === 200) {
    console.log('Cache cleared successfully');
  }

  // Get current configuration
  const configRes = http.get(`${BASE_URL}/admin/config`);
  if (configRes.status === 200) {
    const config = configRes.json();
    console.log('Current configuration:');
    console.log(`  Cache TTL: ${config.config.cache_ttl_seconds}s`);
    console.log(`  Lock Protection: ${config.config.enable_lock_protection}`);
    console.log(`  Probabilistic Refresh: ${config.config.enable_probabilistic_refresh}`);
  }

  return {
    startTime: Date.now(),
  };
}

export function steadyLoad() {
  // Request the same product (hot key) to create contention
  const res = http.get(`${BASE_URL}/product/${HOT_KEY}`);

  // Track metrics
  errorRate.add(res.status !== 200);
  latencyTrend.add(res.timings.duration);

  // Check response and track cache status
  const checkResult = check(res, {
    'status is 200': (r) => r.status === 200,
    'has data': (r) => r.json().data !== undefined,
    'has cache_status': (r) => r.json().data && r.json().data.cache_status !== undefined,
  });

  if (res.status === 200) {
    const data = res.json();
    const cacheStatus = data.data?.cache_status || '';

    // Track cache hit rate
    const isHit = cacheStatus === 'hit' || cacheStatus === 'early_refresh_attempted';
    cacheHitRate.add(isHit);

    // Log cache misses (potential stampede contributors)
    if (cacheStatus.includes('miss')) {
      // console.log(`Cache miss: ${cacheStatus}, latency: ${res.timings.duration}ms`);
    }

    // Track high latency requests (likely hit during stampede)
    if (res.timings.duration > 500) {
      stampedeCaught.add(1);
    }
  }

  // Small random sleep to simulate realistic traffic patterns
  sleep(Math.random() * 0.1);
}

// Alternative test function for multi-key testing
export function multiKeyLoad() {
  // Rotate between a few keys to see different cache behaviors
  const keys = ['product-1', 'product-2', 'product-3', HOT_KEY, HOT_KEY, HOT_KEY];
  const key = keys[Math.floor(Math.random() * keys.length)];

  const res = http.get(`${BASE_URL}/product/${key}`);

  errorRate.add(res.status !== 200);
  latencyTrend.add(res.timings.duration);

  check(res, {
    'status is 200': (r) => r.status === 200,
    'has data': (r) => r.json().data !== undefined,
  });

  sleep(Math.random() * 0.1);
}

export function teardown(data) {
  const duration = (Date.now() - data.startTime) / 1000;
  console.log(`\nTest completed in ${duration.toFixed(1)}s`);

  // Get final stats
  const statsRes = http.get(`${BASE_URL}/admin/stats`);
  if (statsRes.status === 200) {
    const stats = statsRes.json();
    console.log('Final stats:');
    console.log(`  Cached products: ${stats.cached_products}`);
    console.log(`  Concurrent DB queries: ${stats.concurrent_db_queries}`);
  }
}

// =============================================================================
// Summary Report
// =============================================================================

export function handleSummary(data) {
  const summary = {
    'Test Summary': {
      'Total Requests': data.metrics.http_reqs?.values?.count || 0,
      'Failed Requests': data.metrics.http_req_failed?.values?.passes || 0,
      'Avg Latency (ms)': data.metrics.http_req_duration?.values?.avg?.toFixed(2) || 'N/A',
      'p95 Latency (ms)': data.metrics.http_req_duration?.values['p(95)']?.toFixed(2) || 'N/A',
      'p99 Latency (ms)': data.metrics.http_req_duration?.values['p(99)']?.toFixed(2) || 'N/A',
      'Max Latency (ms)': data.metrics.http_req_duration?.values?.max?.toFixed(2) || 'N/A',
    },
    'Cache Metrics': {
      'Cache Hit Rate': data.metrics.cache_hit_rate?.values?.rate
        ? `${(data.metrics.cache_hit_rate.values.rate * 100).toFixed(1)}%`
        : 'N/A',
      'Stampede Events Observed': data.metrics.stampede_events_observed?.values?.count || 0,
    },
    'Interpretation': {
      'High p99 latency': 'Indicates some requests hit the database (cache miss)',
      'Low cache hit rate': 'Too many cache misses, likely thundering herd',
      'Stampede events': 'Requests that took >500ms (hit during stampede)',
    },
  };

  console.log('\n========================================');
  console.log('THUNDERING HERD TEST RESULTS');
  console.log('========================================\n');

  for (const [section, metrics] of Object.entries(summary)) {
    console.log(`${section}:`);
    for (const [key, value] of Object.entries(metrics)) {
      console.log(`  ${key}: ${value}`);
    }
    console.log('');
  }

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
