import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const cacheHitRate = new Rate('cache_hits');

// Test configuration - Celebrity scenario with caching enabled
// Run this AFTER enabling cache to see the mitigation effect
export const options = {
  scenarios: {
    // Enable cache at start
    enable_cache: {
      executor: 'shared-iterations',
      vus: 1,
      iterations: 1,
      exec: 'enableCache',
      startTime: '0s',
    },
    // Normal background traffic
    background_traffic: {
      executor: 'constant-arrival-rate',
      rate: 30,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      maxVUs: 30,
      exec: 'normalTraffic',
      startTime: '5s',  // Start after cache is enabled
    },
    // Celebrity traffic spike
    celebrity_traffic: {
      executor: 'constant-arrival-rate',
      rate: 100,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 30,
      maxVUs: 100,
      exec: 'celebrityTraffic',
      startTime: '5s',  // Start after cache is enabled
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],  // Should be much better with caching
    errors: ['rate<0.1'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab39-api-gateway:8000';

const CELEBRITY_KEY = 'celebrity-user-kardashian';
const NUM_NORMAL_KEYS = 500;

export function enableCache() {
  const res = http.post(
    `${BASE_URL}/admin/cache`,
    JSON.stringify({ enabled: true, ttl_seconds: 60 }),
    { headers: { 'Content-Type': 'application/json' } }
  );

  check(res, {
    'cache enabled': (r) => r.status === 200,
  });

  console.log('Cache enabled:', res.body);
}

function getNormalKey() {
  const keyId = Math.floor(Math.random() * NUM_NORMAL_KEYS);
  return `normal-user-${keyId}`;
}

export function normalTraffic() {
  const key = getNormalKey();
  const res = http.get(`${BASE_URL}/data/${key}`);

  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  if (res.status === 200) {
    const body = res.json();
    cacheHitRate.add(body.cache_hit === true);
  }

  check(res, {
    'normal: status is 200': (r) => r.status === 200,
  });
}

export function celebrityTraffic() {
  const res = http.get(`${BASE_URL}/data/${CELEBRITY_KEY}`);

  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  if (res.status === 200) {
    const body = res.json();
    cacheHitRate.add(body.cache_hit === true);
  }

  check(res, {
    'celebrity: status is 200': (r) => r.status === 200,
  });
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      scenario: 'celebrity_user_with_caching_mitigation',
      celebrity_key: CELEBRITY_KEY,
      total_requests: data.metrics.http_reqs.values.count,
      latency: {
        avg_latency_ms: data.metrics.http_req_duration.values.avg,
        p95_latency_ms: data.metrics.http_req_duration.values['p(95)'],
        p99_latency_ms: data.metrics.http_req_duration.values['p(99)'],
      },
      cache_hit_rate: data.metrics.cache_hits ? data.metrics.cache_hits.values.rate : 0,
      error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
      analysis: {
        note: 'With caching enabled, hot key traffic is served from gateway cache',
        expected_improvement: 'p95 latency should be significantly lower than without caching',
        cache_benefit: 'Cache absorbs hot key traffic, protecting the backend shard',
      }
    }, null, 2),
  };
}
