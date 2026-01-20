import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const tasksPublished = new Counter('tasks_published');

// Burst test configuration - send many messages quickly
export const options = {
  scenarios: {
    burst: {
      executor: 'shared-iterations',
      vus: 10,
      iterations: 500,
      maxDuration: '2m',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<5000'],
    errors: ['rate<0.1'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab17-producer:8000';

export default function () {
  // Use bulk endpoint to send multiple tasks at once
  const bulkPayload = JSON.stringify({
    count: 10,
    task_type: 'burst_test',
    delay_ms: 0
  });

  const res = http.post(`${BASE_URL}/tasks/bulk`, bulkPayload, {
    headers: { 'Content-Type': 'application/json' }
  });

  errorRate.add(res.status !== 200);

  if (res.status === 200) {
    tasksPublished.add(10);
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
    'bulk response has count': (r) => {
      try {
        return r.json().count === 10;
      } catch {
        return false;
      }
    },
  });

  // Very short pause for burst behavior
  sleep(0.05);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
