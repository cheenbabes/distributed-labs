import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const celebrityLatency = new Trend('celebrity_latency');
const normalLatency = new Trend('normal_latency');

// Test configuration - Celebrity user scenario
// Simulates a viral celebrity post getting massive traffic
export const options = {
  scenarios: {
    // Normal background traffic
    background_traffic: {
      executor: 'constant-arrival-rate',
      rate: 30,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      maxVUs: 30,
      exec: 'normalTraffic',
    },
    // Celebrity traffic spike
    celebrity_traffic: {
      executor: 'constant-arrival-rate',
      rate: 100,                   // 100 requests per second to celebrity key
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 30,
      maxVUs: 100,
      exec: 'celebrityTraffic',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    errors: ['rate<0.2'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab39-api-gateway:8000';

// The celebrity user - all celebrity traffic goes to this single key
// This key will cause a hot partition
const CELEBRITY_KEY = 'celebrity-user-kardashian';

// Normal user keys (uniformly distributed)
const NUM_NORMAL_KEYS = 500;

function getNormalKey() {
  const keyId = Math.floor(Math.random() * NUM_NORMAL_KEYS);
  return `normal-user-${keyId}`;
}

export function normalTraffic() {
  const key = getNormalKey();
  const res = http.get(`${BASE_URL}/data/${key}`);

  normalLatency.add(res.timings.duration);
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  check(res, {
    'normal: status is 200': (r) => r.status === 200,
  });
}

export function celebrityTraffic() {
  const res = http.get(`${BASE_URL}/data/${CELEBRITY_KEY}`);

  celebrityLatency.add(res.timings.duration);
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  check(res, {
    'celebrity: status is 200': (r) => r.status === 200,
  });
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      scenario: 'celebrity_user_hot_key',
      celebrity_key: CELEBRITY_KEY,
      total_requests: data.metrics.http_reqs.values.count,
      overall: {
        avg_latency_ms: data.metrics.http_req_duration.values.avg,
        p95_latency_ms: data.metrics.http_req_duration.values['p(95)'],
        p99_latency_ms: data.metrics.http_req_duration.values['p(99)'],
      },
      celebrity_traffic: {
        avg_latency_ms: data.metrics.celebrity_latency ? data.metrics.celebrity_latency.values.avg : 'N/A',
        p95_latency_ms: data.metrics.celebrity_latency ? data.metrics.celebrity_latency.values['p(95)'] : 'N/A',
      },
      normal_traffic: {
        avg_latency_ms: data.metrics.normal_latency ? data.metrics.normal_latency.values.avg : 'N/A',
        p95_latency_ms: data.metrics.normal_latency ? data.metrics.normal_latency.values['p(95)'] : 'N/A',
      },
      error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
      analysis: {
        note: 'Celebrity key receives 100 rps while normal traffic is 30 rps spread across 500 keys',
        hot_key_impact: 'Check Grafana dashboard to see single shard receiving disproportionate load',
      }
    }, null, 2),
  };
}
