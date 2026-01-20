import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Simple quick test for demos

const errorRate = new Rate('errors');
const collisions = new Counter('collisions_detected');
const idsGenerated = new Counter('ids_generated');

export const options = {
  stages: [
    { duration: '10s', target: 20 },   // Ramp up
    { duration: '30s', target: 20 },   // Stay
    { duration: '10s', target: 0 },    // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
    errors: ['rate<0.1'],
    collisions_detected: ['count==0'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab48-client:8000';

export default function() {
  const res = http.get(`${BASE_URL}/id`);

  const success = check(res, {
    'status is 200': (r) => r.status === 200,
    'has id': (r) => {
      try {
        return JSON.parse(r.body).id !== undefined;
      } catch (e) {
        return false;
      }
    },
    'no collision': (r) => {
      try {
        const body = JSON.parse(r.body);
        if (body.collision_detected) {
          collisions.add(1);
          return false;
        }
        return true;
      } catch (e) {
        return false;
      }
    },
  });

  if (!success) {
    errorRate.add(1);
  } else {
    idsGenerated.add(1);
  }

  sleep(0.1);
}

export function handleSummary(data) {
  const summary = {
    total_ids: data.metrics.ids_generated ? data.metrics.ids_generated.values.count : 0,
    collisions: data.metrics.collisions_detected ? data.metrics.collisions_detected.values.count : 0,
    p95_latency_ms: data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(95)'] : 0,
  };

  console.log('\n=== Quick Test Results ===');
  console.log(`IDs Generated: ${summary.total_ids}`);
  console.log(`Collisions:    ${summary.collisions}`);
  console.log(`P95 Latency:   ${summary.p95_latency_ms.toFixed(2)}ms`);
  console.log('==========================\n');

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
