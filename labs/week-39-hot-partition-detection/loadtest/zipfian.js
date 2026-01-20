import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const keyAccessCounter = new Counter('key_accesses');
const hotKeyAccesses = new Counter('hot_key_accesses');

// Test configuration - Zipfian distribution (creates hot keys)
export const options = {
  scenarios: {
    zipfian_load: {
      executor: 'constant-arrival-rate',
      rate: 50,                    // 50 requests per second
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 20,
      maxVUs: 50,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1000'],  // Higher threshold expected due to hot partition
    errors: ['rate<0.1'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab39-api-gateway:8000';
const NUM_KEYS = 1000;    // Total number of unique keys
const ZIPF_EXPONENT = 1.5; // Higher = more skewed (1.0 is moderate, 2.0 is extreme)

// Pre-calculate Zipfian distribution weights
// Zipf's law: frequency of item ranked k is proportional to 1/k^s
const zipfWeights = [];
let totalWeight = 0;

for (let i = 1; i <= NUM_KEYS; i++) {
  const weight = 1.0 / Math.pow(i, ZIPF_EXPONENT);
  totalWeight += weight;
  zipfWeights.push(totalWeight);
}

// Normalize weights
for (let i = 0; i < zipfWeights.length; i++) {
  zipfWeights[i] /= totalWeight;
}

// Generate a key following Zipfian distribution
// Key 0 (user-0) will be accessed most frequently
function getZipfianKey() {
  const rand = Math.random();

  // Binary search for the key
  let low = 0;
  let high = zipfWeights.length - 1;

  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (zipfWeights[mid] < rand) {
      low = mid + 1;
    } else {
      high = mid;
    }
  }

  return `user-${low}`;
}

// Track access distribution for reporting
const accessCounts = {};

export default function () {
  const key = getZipfianKey();

  // Track access count locally for summary
  accessCounts[key] = (accessCounts[key] || 0) + 1;

  const res = http.get(`${BASE_URL}/data/${key}`);

  // Record metrics
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);
  keyAccessCounter.add(1, { key: key });

  // Track hot key (user-0) specifically
  if (key === 'user-0') {
    hotKeyAccesses.add(1);
  }

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response has key': (r) => r.json().key !== undefined,
    'response has shard_id': (r) => r.json().shard_id !== undefined,
  });
}

export function handleSummary(data) {
  // Sort and get top 10 accessed keys
  const sortedKeys = Object.entries(accessCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10);

  const totalRequests = data.metrics.http_reqs.values.count;

  return {
    stdout: JSON.stringify({
      scenario: 'zipfian_distribution',
      zipf_exponent: ZIPF_EXPONENT,
      total_requests: totalRequests,
      avg_latency_ms: data.metrics.http_req_duration.values.avg,
      p95_latency_ms: data.metrics.http_req_duration.values['p(95)'],
      p99_latency_ms: data.metrics.http_req_duration.values['p(99)'],
      error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
      top_10_keys: sortedKeys.map(([key, count]) => ({
        key,
        count,
        percentage: ((count / totalRequests) * 100).toFixed(2) + '%'
      })),
      analysis: {
        note: 'With Zipfian distribution, a small number of keys receive most of the traffic',
        expected_user0_percentage: '~20-30% depending on exponent',
      }
    }, null, 2),
  };
}
