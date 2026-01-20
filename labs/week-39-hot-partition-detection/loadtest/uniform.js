import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const keyAccessCounter = new Counter('key_accesses');

// Test configuration - uniform distribution
export const options = {
  scenarios: {
    uniform_load: {
      executor: 'constant-arrival-rate',
      rate: 50,                    // 50 requests per second
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 20,
      maxVUs: 50,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],
    errors: ['rate<0.1'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab39-api-gateway:8000';
const NUM_KEYS = 1000;  // Total number of unique keys

// Generate a random key with uniform distribution
function getUniformKey() {
  const keyId = Math.floor(Math.random() * NUM_KEYS);
  return `user-${keyId}`;
}

export default function () {
  const key = getUniformKey();
  const res = http.get(`${BASE_URL}/data/${key}`);

  // Record metrics
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);
  keyAccessCounter.add(1, { key: key });

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response has key': (r) => r.json().key !== undefined,
    'response has shard_id': (r) => r.json().shard_id !== undefined,
  });
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      scenario: 'uniform_distribution',
      total_requests: data.metrics.http_reqs.values.count,
      avg_latency_ms: data.metrics.http_req_duration.values.avg,
      p95_latency_ms: data.metrics.http_req_duration.values['p(95)'],
      error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
    }, null, 2),
  };
}
