import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 5 },   // Ramp up to 5 users
    { duration: '1m', target: 5 },    // Stay at 5 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000'],  // 95% of requests should be < 2s
    errors: ['rate<0.1'],               // Error rate should be < 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab30-client-gateway:8000';

export default function () {
  // Mix of reads and writes
  if (Math.random() < 0.3) {
    // Write operation
    const key = `key-${Math.floor(Math.random() * 50)}`;
    const res = http.post(
      `${BASE_URL}/write`,
      JSON.stringify({
        key: key,
        value: { data: `value-${Date.now()}`, vu: __VU }
      }),
      { headers: { 'Content-Type': 'application/json' } }
    );

    latencyTrend.add(res.timings.duration);
    errorRate.add(res.status !== 200);

    check(res, {
      'write status is 200': (r) => r.status === 200,
      'write has result': (r) => {
        try {
          return JSON.parse(r.body).result !== undefined;
        } catch (e) {
          return false;
        }
      },
    });
  } else {
    // Read operation
    const key = `key-${Math.floor(Math.random() * 50)}`;
    const res = http.get(`${BASE_URL}/read/${key}`);

    latencyTrend.add(res.timings.duration);
    // 404 is acceptable for reads (key might not exist)
    errorRate.add(res.status !== 200 && res.status !== 404);

    check(res, {
      'read status is 200 or 404': (r) => r.status === 200 || r.status === 404,
    });
  }

  sleep(0.5);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
