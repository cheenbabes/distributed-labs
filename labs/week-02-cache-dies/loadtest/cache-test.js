import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const cacheHitRate = new Rate('cache_hits');
const latencyTrend = new Trend('request_latency_ms');
const cacheHitLatency = new Trend('cache_hit_latency_ms');
const cacheMissLatency = new Trend('cache_miss_latency_ms');

// Test configuration
export const options = {
  scenarios: {
    // Sustained load to observe cache behavior
    sustained_load: {
      executor: 'constant-arrival-rate',
      rate: 20,           // 20 requests per second
      timeUnit: '1s',
      duration: '5m',     // Run for 5 minutes
      preAllocatedVUs: 50,
      maxVUs: 100,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1000'],  // 95% of requests should be < 1s
    errors: ['rate<0.1'],                // Error rate should be < 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://gateway:8000';

export default function () {
  const startTime = Date.now();
  const res = http.get(`${BASE_URL}/api/data`);
  const duration = Date.now() - startTime;

  // Record overall metrics
  latencyTrend.add(duration);
  errorRate.add(res.status !== 200);

  // Parse response and record cache-specific metrics
  if (res.status === 200) {
    try {
      const body = res.json();

      // Track cache hits vs misses
      const isCacheHit = body.cache_hit === true;
      cacheHitRate.add(isCacheHit);

      // Track latency by cache status
      if (isCacheHit) {
        cacheHitLatency.add(duration);
      } else {
        cacheMissLatency.add(duration);
      }
    } catch (e) {
      // JSON parse error - still count as success
    }
  }

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response has data': (r) => {
      try {
        return r.json().data !== undefined;
      } catch {
        return false;
      }
    },
  });

  // Small pause between requests
  sleep(0.1);
}

// Summary handler for end-of-test reporting
export function handleSummary(data) {
  const summary = {
    'Total Requests': data.metrics.http_reqs.values.count,
    'Error Rate': `${(data.metrics.errors.values.rate * 100).toFixed(2)}%`,
    'Cache Hit Rate': data.metrics.cache_hits
      ? `${(data.metrics.cache_hits.values.rate * 100).toFixed(2)}%`
      : 'N/A',
    'Latency P50': `${data.metrics.http_req_duration.values['p(50)'].toFixed(2)}ms`,
    'Latency P95': `${data.metrics.http_req_duration.values['p(95)'].toFixed(2)}ms`,
    'Latency P99': `${data.metrics.http_req_duration.values['p(99)'].toFixed(2)}ms`,
  };

  if (data.metrics.cache_hit_latency_ms) {
    summary['Cache Hit Latency P95'] = `${data.metrics.cache_hit_latency_ms.values['p(95)'].toFixed(2)}ms`;
  }
  if (data.metrics.cache_miss_latency_ms) {
    summary['Cache Miss Latency P95'] = `${data.metrics.cache_miss_latency_ms.values['p(95)'].toFixed(2)}ms`;
  }

  console.log('\n========== CACHE PERFORMANCE SUMMARY ==========');
  for (const [key, value] of Object.entries(summary)) {
    console.log(`${key}: ${value}`);
  }
  console.log('================================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
