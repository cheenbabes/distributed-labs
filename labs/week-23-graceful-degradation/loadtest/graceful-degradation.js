import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics for graceful degradation analysis
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const qualityScoreTrend = new Trend('quality_score');
const degradedResponses = new Counter('degraded_responses');
const fullResponses = new Counter('full_responses');
const fallbackRecommendations = new Counter('fallback_recommendations');
const fallbackPricing = new Counter('fallback_pricing');
const fallbackInventory = new Counter('fallback_inventory');

// Test configuration
export const options = {
  scenarios: {
    // Steady load for baseline and degradation testing
    steady_load: {
      executor: 'constant-arrival-rate',
      rate: 10,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 20,
      maxVUs: 50,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<3000'],  // Allow longer latency due to potential degradation
    errors: ['rate<0.05'],               // Less than 5% errors (we expect graceful degradation, not errors)
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab23-product-catalog:8000';

// Products to test with
const PRODUCTS = ['SKU-001', 'SKU-002', 'SKU-003', 'SKU-005', 'SKU-010'];
const USERS = ['user-123', 'user-456', 'user-789', 'user-loadtest'];

export default function () {
  // Pick random product and user
  const productId = PRODUCTS[Math.floor(Math.random() * PRODUCTS.length)];
  const userId = USERS[Math.floor(Math.random() * USERS.length)];

  const res = http.get(`${BASE_URL}/product/${productId}?user_id=${userId}`);

  // Record latency
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  // Validate response structure
  const checkResult = check(res, {
    'status is 200': (r) => r.status === 200,
    'response has product_id': (r) => {
      try {
        return r.json().product_id === productId;
      } catch {
        return false;
      }
    },
    'response has quality info': (r) => {
      try {
        const json = r.json();
        return json.quality !== undefined && json.quality.score !== undefined;
      } catch {
        return false;
      }
    },
  });

  if (res.status === 200) {
    try {
      const data = res.json();
      const quality = data.quality || {};

      // Record quality score
      qualityScoreTrend.add(quality.score || 0);

      // Count response types
      if (quality.degradation_level === 'none') {
        fullResponses.add(1);
      } else {
        degradedResponses.add(1);
      }

      // Count specific fallbacks
      const degradedComponents = quality.degraded_components || [];
      if (degradedComponents.includes('recommendations')) {
        fallbackRecommendations.add(1);
      }
      if (degradedComponents.includes('pricing')) {
        fallbackPricing.add(1);
      }
      if (degradedComponents.includes('inventory')) {
        fallbackInventory.add(1);
      }
    } catch (e) {
      console.error(`Failed to parse response: ${e}`);
    }
  }

  // Small pause between requests
  sleep(0.1);
}

export function handleSummary(data) {
  // Create a custom summary focused on graceful degradation metrics
  const summary = {
    timestamp: new Date().toISOString(),
    test_duration: data.state.testRunDurationMs,
    metrics: {
      total_requests: data.metrics.http_reqs ? data.metrics.http_reqs.values.count : 0,
      error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
      latency: {
        avg: data.metrics.request_latency ? data.metrics.request_latency.values.avg : 0,
        p50: data.metrics.request_latency ? data.metrics.request_latency.values['p(50)'] : 0,
        p95: data.metrics.request_latency ? data.metrics.request_latency.values['p(95)'] : 0,
        p99: data.metrics.request_latency ? data.metrics.request_latency.values['p(99)'] : 0,
      },
      quality_score: {
        avg: data.metrics.quality_score ? data.metrics.quality_score.values.avg : 0,
        min: data.metrics.quality_score ? data.metrics.quality_score.values.min : 0,
        max: data.metrics.quality_score ? data.metrics.quality_score.values.max : 0,
      },
      response_types: {
        full: data.metrics.full_responses ? data.metrics.full_responses.values.count : 0,
        degraded: data.metrics.degraded_responses ? data.metrics.degraded_responses.values.count : 0,
      },
      fallbacks: {
        recommendations: data.metrics.fallback_recommendations ? data.metrics.fallback_recommendations.values.count : 0,
        pricing: data.metrics.fallback_pricing ? data.metrics.fallback_pricing.values.count : 0,
        inventory: data.metrics.fallback_inventory ? data.metrics.fallback_inventory.values.count : 0,
      },
    },
  };

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
