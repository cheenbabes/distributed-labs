import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';

/**
 * Cold Start Comparison Test
 *
 * This test compares latency between:
 * 1. Requests to warm (pre-warmed) instance
 * 2. Requests to cold (fresh) instance
 *
 * It also tests different endpoints to show various cold start impacts:
 * - Product lookup (cache-dependent)
 * - Recommendations (compute-heavy, cache-dependent)
 * - DB query (connection pool-dependent)
 */

// Custom metrics per instance and endpoint
const warmProductLatency = new Trend('warm_product_latency');
const coldProductLatency = new Trend('cold_product_latency');
const warmRecsLatency = new Trend('warm_recommendations_latency');
const coldRecsLatency = new Trend('cold_recommendations_latency');
const warmDbLatency = new Trend('warm_db_latency');
const coldDbLatency = new Trend('cold_db_latency');
const warmCacheHits = new Counter('warm_cache_hits');
const coldCacheHits = new Counter('cold_cache_hits');
const warmCacheMisses = new Counter('warm_cache_misses');
const coldCacheMisses = new Counter('cold_cache_misses');
const errorRate = new Rate('errors');

export const options = {
  scenarios: {
    // Steady load to both instances
    comparison: {
      executor: 'constant-arrival-rate',
      rate: 10,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      maxVUs: 50,
    },
  },
  thresholds: {
    'warm_product_latency': ['p(95)<500'],
    'cold_product_latency': ['p(95)<2000'],  // Allow higher for cold
    errors: ['rate<0.1'],
  },
};

const WARM_URL = __ENV.WARM_URL || 'http://api-warm:8000';
const COLD_URL = __ENV.COLD_URL || 'http://api-cold:8000';

function testEndpoint(url, endpoint, latencyMetric, cacheHitCounter, cacheMissCounter, instanceName) {
  const res = http.get(`${url}${endpoint}`);

  const success = check(res, {
    [`${instanceName}: status is 200`]: (r) => r.status === 200,
  });

  errorRate.add(!success);

  if (res.status === 200) {
    latencyMetric.add(res.timings.duration);

    try {
      const body = res.json();
      if (body.cache_hit === true) {
        cacheHitCounter.add(1);
      } else if (body.cache_hit === false) {
        cacheMissCounter.add(1);
      }
    } catch (e) {
      // JSON parse error, ignore
    }
  }

  return res;
}

export default function () {
  const productId = Math.floor(Math.random() * 100) + 1;
  const userId = Math.floor(Math.random() * 30) + 1;

  group('Warm Instance', () => {
    // Test product endpoint
    testEndpoint(
      WARM_URL,
      `/api/product/${productId}`,
      warmProductLatency,
      warmCacheHits,
      warmCacheMisses,
      'warm'
    );

    // Test recommendations endpoint
    testEndpoint(
      WARM_URL,
      `/api/user/${userId}/recommendations`,
      warmRecsLatency,
      warmCacheHits,
      warmCacheMisses,
      'warm'
    );

    // Test DB query endpoint
    const dbRes = http.get(`${WARM_URL}/api/db-query`);
    if (dbRes.status === 200) {
      warmDbLatency.add(dbRes.timings.duration);
    }
  });

  group('Cold Instance', () => {
    // Test product endpoint
    testEndpoint(
      COLD_URL,
      `/api/product/${productId}`,
      coldProductLatency,
      coldCacheHits,
      coldCacheMisses,
      'cold'
    );

    // Test recommendations endpoint
    testEndpoint(
      COLD_URL,
      `/api/user/${userId}/recommendations`,
      coldRecsLatency,
      coldCacheHits,
      coldCacheMisses,
      'cold'
    );

    // Test DB query endpoint
    const dbRes = http.get(`${COLD_URL}/api/db-query`);
    if (dbRes.status === 200) {
      coldDbLatency.add(dbRes.timings.duration);
    }
  });

  sleep(0.1);
}

export function handleSummary(data) {
  const summary = {
    comparison: {
      product: {
        warm_p50: data.metrics.warm_product_latency ? data.metrics.warm_product_latency.values['p(50)'] : null,
        warm_p95: data.metrics.warm_product_latency ? data.metrics.warm_product_latency.values['p(95)'] : null,
        cold_p50: data.metrics.cold_product_latency ? data.metrics.cold_product_latency.values['p(50)'] : null,
        cold_p95: data.metrics.cold_product_latency ? data.metrics.cold_product_latency.values['p(95)'] : null,
      },
      recommendations: {
        warm_p50: data.metrics.warm_recommendations_latency ? data.metrics.warm_recommendations_latency.values['p(50)'] : null,
        warm_p95: data.metrics.warm_recommendations_latency ? data.metrics.warm_recommendations_latency.values['p(95)'] : null,
        cold_p50: data.metrics.cold_recommendations_latency ? data.metrics.cold_recommendations_latency.values['p(50)'] : null,
        cold_p95: data.metrics.cold_recommendations_latency ? data.metrics.cold_recommendations_latency.values['p(95)'] : null,
      },
      db_query: {
        warm_p50: data.metrics.warm_db_latency ? data.metrics.warm_db_latency.values['p(50)'] : null,
        warm_p95: data.metrics.warm_db_latency ? data.metrics.warm_db_latency.values['p(95)'] : null,
        cold_p50: data.metrics.cold_db_latency ? data.metrics.cold_db_latency.values['p(50)'] : null,
        cold_p95: data.metrics.cold_db_latency ? data.metrics.cold_db_latency.values['p(95)'] : null,
      },
      cache: {
        warm_hits: data.metrics.warm_cache_hits ? data.metrics.warm_cache_hits.values.count : 0,
        warm_misses: data.metrics.warm_cache_misses ? data.metrics.warm_cache_misses.values.count : 0,
        cold_hits: data.metrics.cold_cache_hits ? data.metrics.cold_cache_hits.values.count : 0,
        cold_misses: data.metrics.cold_cache_misses ? data.metrics.cold_cache_misses.values.count : 0,
      },
    },
    error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
  };

  console.log('\n=== Cold Start Comparison Results ===\n');
  console.log('Product Endpoint:');
  console.log(`  Warm: p50=${summary.comparison.product.warm_p50?.toFixed(2)}ms, p95=${summary.comparison.product.warm_p95?.toFixed(2)}ms`);
  console.log(`  Cold: p50=${summary.comparison.product.cold_p50?.toFixed(2)}ms, p95=${summary.comparison.product.cold_p95?.toFixed(2)}ms`);
  console.log('\nRecommendations Endpoint:');
  console.log(`  Warm: p50=${summary.comparison.recommendations.warm_p50?.toFixed(2)}ms, p95=${summary.comparison.recommendations.warm_p95?.toFixed(2)}ms`);
  console.log(`  Cold: p50=${summary.comparison.recommendations.cold_p50?.toFixed(2)}ms, p95=${summary.comparison.recommendations.cold_p95?.toFixed(2)}ms`);
  console.log('\nDB Query Endpoint:');
  console.log(`  Warm: p50=${summary.comparison.db_query.warm_p50?.toFixed(2)}ms, p95=${summary.comparison.db_query.warm_p95?.toFixed(2)}ms`);
  console.log(`  Cold: p50=${summary.comparison.db_query.cold_p50?.toFixed(2)}ms, p95=${summary.comparison.db_query.cold_p95?.toFixed(2)}ms`);
  console.log('\nCache Statistics:');
  console.log(`  Warm: ${summary.comparison.cache.warm_hits} hits, ${summary.comparison.cache.warm_misses} misses`);
  console.log(`  Cold: ${summary.comparison.cache.cold_hits} hits, ${summary.comparison.cache.cold_misses} misses`);
  console.log('');

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
