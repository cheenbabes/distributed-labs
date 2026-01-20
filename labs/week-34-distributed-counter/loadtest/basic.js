import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const incrementLatency = new Trend('increment_latency');
const readLatency = new Trend('read_latency');
const incrementsPerRegion = new Counter('increments_per_region');

// Test configuration
export const options = {
  stages: [
    { duration: '10s', target: 5 },   // Ramp up
    { duration: '30s', target: 10 },  // Steady state
    { duration: '10s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
    errors: ['rate<0.1'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab34-gateway:8080';

export default function () {
  // Increment counter
  const incrementRes = http.post(
    `${BASE_URL}/api/increment`,
    JSON.stringify({
      counter_name: 'views',
      amount: 1,
      routing: 'random'
    }),
    {
      headers: { 'Content-Type': 'application/json' },
    }
  );

  incrementLatency.add(incrementRes.timings.duration);
  errorRate.add(incrementRes.status !== 200);

  const incrementOk = check(incrementRes, {
    'increment status is 200': (r) => r.status === 200,
    'increment has local_count': (r) => {
      try {
        return r.json().local_count !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  if (incrementOk) {
    try {
      const region = incrementRes.json().region;
      incrementsPerRegion.add(1, { region: region });
    } catch (e) {}
  }

  sleep(0.1);

  // Read global counter
  const readRes = http.get(`${BASE_URL}/api/counter/views`);

  readLatency.add(readRes.timings.duration);
  errorRate.add(readRes.status !== 200);

  check(readRes, {
    'read status is 200': (r) => r.status === 200,
    'read has global_count': (r) => {
      try {
        return r.json().global_count !== undefined;
      } catch (e) {
        return false;
      }
    },
    'read has convergence info': (r) => {
      try {
        return r.json().convergence !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  sleep(0.2);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
