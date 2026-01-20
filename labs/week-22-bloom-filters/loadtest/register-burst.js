import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomString } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// Custom metrics
const errorRate = new Rate('errors');
const registerLatency = new Trend('register_latency');
const registeredCount = new Counter('registered_count');
const conflictCount = new Counter('conflict_count');

// Test configuration - register many usernames to fill the bloom filter
export const options = {
  stages: [
    { duration: '10s', target: 10 },  // Ramp up
    { duration: '30s', target: 10 },  // Stay at 10 VUs
    { duration: '10s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    errors: ['rate<0.2'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab22-username-service:8000';

export default function () {
  // Generate a unique username
  const username = `user${Date.now()}${randomString(6)}`;

  const res = http.post(
    `${BASE_URL}/register-username`,
    JSON.stringify({ username: username }),
    { headers: { 'Content-Type': 'application/json' } }
  );

  registerLatency.add(res.timings.duration);

  if (res.status === 201) {
    registeredCount.add(1);
    check(res, {
      'registration successful': (r) => r.status === 201,
      'username matches': () => res.json().username === username.toLowerCase(),
    });
  } else if (res.status === 409) {
    conflictCount.add(1);
    // Not an error - just a collision
  } else {
    errorRate.add(1);
  }

  sleep(0.05);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      total_requests: data.metrics.http_reqs.values.count,
      registered: data.metrics.registered_count ? data.metrics.registered_count.values.count : 0,
      conflicts: data.metrics.conflict_count ? data.metrics.conflict_count.values.count : 0,
      avg_latency_ms: data.metrics.http_req_duration.values.avg,
      p95_latency_ms: data.metrics.http_req_duration.values['p(95)'],
    }, null, 2),
  };
}
