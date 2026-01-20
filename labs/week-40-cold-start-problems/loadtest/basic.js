import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const cacheHitRate = new Rate('cache_hits');
const coldLatency = new Trend('cold_instance_latency');
const warmLatency = new Trend('warm_instance_latency');
const cacheHitCounter = new Counter('cache_hit_total');
const cacheMissCounter = new Counter('cache_miss_total');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 5 },   // Ramp up
    { duration: '1m', target: 10 },   // Steady state
    { duration: '30s', target: 20 },  // Peak load
    { duration: '1m', target: 10 },   // Back to steady
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    errors: ['rate<0.1'],
  },
};

const NGINX_URL = __ENV.NGINX_URL || 'http://nginx:80';
const WARM_URL = __ENV.WARM_URL || 'http://api-warm:8000';
const COLD_URL = __ENV.COLD_URL || 'http://api-cold:8000';

export default function () {
  group('Load Balanced Requests', () => {
    // Request through load balancer
    const productId = Math.floor(Math.random() * 50) + 1;
    const res = http.get(`${NGINX_URL}/api/product/${productId}`);

    const success = check(res, {
      'status is 200': (r) => r.status === 200,
      'has instance info': (r) => r.json() && r.json().instance !== undefined,
    });

    errorRate.add(!success);

    if (res.status === 200) {
      const body = res.json();
      if (body.cache_hit) {
        cacheHitRate.add(1);
        cacheHitCounter.add(1);
      } else {
        cacheHitRate.add(0);
        cacheMissCounter.add(1);
      }
    }
  });

  group('Direct Warm Instance', () => {
    const productId = Math.floor(Math.random() * 50) + 1;
    const res = http.get(`${WARM_URL}/api/product/${productId}`);

    if (res.status === 200) {
      warmLatency.add(res.timings.duration);
    }

    check(res, {
      'warm: status is 200': (r) => r.status === 200,
    });
  });

  group('Direct Cold Instance', () => {
    const productId = Math.floor(Math.random() * 50) + 1;
    const res = http.get(`${COLD_URL}/api/product/${productId}`);

    if (res.status === 200) {
      coldLatency.add(res.timings.duration);
    }

    check(res, {
      'cold: status is 200': (r) => r.status === 200,
    });
  });

  sleep(0.5);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      summary: {
        total_requests: data.metrics.http_reqs.values.count,
        error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
        p95_latency_ms: data.metrics.http_req_duration.values['p(95)'],
        avg_latency_ms: data.metrics.http_req_duration.values.avg,
        warm_avg_latency: data.metrics.warm_instance_latency ? data.metrics.warm_instance_latency.values.avg : null,
        cold_avg_latency: data.metrics.cold_instance_latency ? data.metrics.cold_instance_latency.values.avg : null,
        cache_hit_rate: data.metrics.cache_hits ? data.metrics.cache_hits.values.rate : null,
      }
    }, null, 2),
  };
}
