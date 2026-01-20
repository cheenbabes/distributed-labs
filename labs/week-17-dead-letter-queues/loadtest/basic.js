import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const publishLatency = new Trend('publish_latency');
const tasksPublished = new Counter('tasks_published');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 5 },   // Ramp up to 5 users
    { duration: '1m', target: 5 },    // Stay at 5 users
    { duration: '30s', target: 10 },  // Ramp up to 10 users
    { duration: '1m', target: 10 },   // Stay at 10 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000'], // 95% of requests should be < 2s
    errors: ['rate<0.1'],              // Error rate should be < 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab17-producer:8000';

export default function () {
  // Publish a single task
  const taskPayload = JSON.stringify({
    task_type: 'load_test',
    payload: {
      iteration: __ITER,
      vu: __VU,
      timestamp: new Date().toISOString()
    },
    priority: Math.floor(Math.random() * 3)
  });

  const res = http.post(`${BASE_URL}/tasks`, taskPayload, {
    headers: { 'Content-Type': 'application/json' }
  });

  // Record metrics
  publishLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  if (res.status === 200) {
    tasksPublished.add(1);
  }

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response has task_id': (r) => {
      try {
        return r.json().task_id !== undefined;
      } catch {
        return false;
      }
    },
    'response has trace_id': (r) => {
      try {
        return r.json().trace_id !== undefined;
      } catch {
        return false;
      }
    },
  });

  // Small pause between requests
  sleep(0.2 + Math.random() * 0.3);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
