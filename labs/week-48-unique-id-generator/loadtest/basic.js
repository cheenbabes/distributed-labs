import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const idsGenerated = new Counter('ids_generated');
const collisions = new Counter('collisions_detected');

// Test configuration
export const options = {
  scenarios: {
    // Scenario 1: Steady load for baseline
    steady_load: {
      executor: 'constant-rate',
      rate: 100,           // 100 requests per second
      timeUnit: '1s',
      duration: '1m',
      preAllocatedVUs: 20,
      maxVUs: 50,
      exec: 'singleId',
      startTime: '0s',
    },
    // Scenario 2: Ramp up to stress test
    stress_test: {
      executor: 'ramping-rate',
      startRate: 50,
      timeUnit: '1s',
      stages: [
        { duration: '30s', target: 200 },  // Ramp to 200 RPS
        { duration: '1m', target: 200 },   // Stay at 200 RPS
        { duration: '30s', target: 500 },  // Ramp to 500 RPS
        { duration: '30s', target: 500 },  // Stay at 500 RPS
        { duration: '30s', target: 0 },    // Ramp down
      ],
      preAllocatedVUs: 100,
      maxVUs: 200,
      exec: 'singleId',
      startTime: '1m30s',
    },
    // Scenario 3: Batch requests
    batch_requests: {
      executor: 'constant-rate',
      rate: 10,            // 10 batch requests per second
      timeUnit: '1s',
      duration: '1m',
      preAllocatedVUs: 10,
      maxVUs: 20,
      exec: 'batchIds',
      startTime: '4m30s',
    },
    // Scenario 4: Parallel from all generators
    parallel_burst: {
      executor: 'per-vu-iterations',
      vus: 10,
      iterations: 20,
      exec: 'parallelIds',
      startTime: '5m30s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500', 'p(99)<1000'],
    errors: ['rate<0.05'],
    collisions_detected: ['count==0'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab48-client:8000';

// Generate single ID
export function singleId() {
  const res = http.get(`${BASE_URL}/id`);

  latencyTrend.add(res.timings.duration);

  const success = check(res, {
    'status is 200': (r) => r.status === 200,
    'has id': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.id !== undefined;
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

  sleep(0.01); // 10ms pause
}

// Generate batch of IDs
export function batchIds() {
  const count = 50;
  const res = http.get(`${BASE_URL}/ids/batch?count=${count}`);

  latencyTrend.add(res.timings.duration);

  const success = check(res, {
    'status is 200': (r) => r.status === 200,
    'correct count': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.count_received === count;
      } catch (e) {
        return false;
      }
    },
    'no collisions in batch': (r) => {
      try {
        const body = JSON.parse(r.body);
        if (body.collisions_detected > 0) {
          collisions.add(body.collisions_detected);
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
    idsGenerated.add(count);
  }

  sleep(0.1);
}

// Parallel IDs from all generators
export function parallelIds() {
  const count = 100;
  const res = http.get(`${BASE_URL}/ids/parallel?count=${count}`);

  latencyTrend.add(res.timings.duration);

  const success = check(res, {
    'status is 200': (r) => r.status === 200,
    'has results': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.total_ids > 0;
      } catch (e) {
        return false;
      }
    },
    'no collisions in parallel': (r) => {
      try {
        const body = JSON.parse(r.body);
        if (body.collisions_detected > 0) {
          collisions.add(body.collisions_detected);
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
    try {
      const body = JSON.parse(res.body);
      idsGenerated.add(body.total_ids || 0);
    } catch (e) {}
  }

  sleep(0.5);
}

// Default function (used if no scenario specified)
export default function() {
  singleId();
}

export function handleSummary(data) {
  const summary = {
    total_ids_generated: data.metrics.ids_generated ? data.metrics.ids_generated.values.count : 0,
    collisions_detected: data.metrics.collisions_detected ? data.metrics.collisions_detected.values.count : 0,
    error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
    http_req_duration_p95: data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(95)'] : 0,
    http_req_duration_p99: data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(99)'] : 0,
    total_requests: data.metrics.http_reqs ? data.metrics.http_reqs.values.count : 0,
  };

  console.log('\n============================================');
  console.log('        SNOWFLAKE ID GENERATOR TEST        ');
  console.log('============================================');
  console.log(`Total IDs Generated:    ${summary.total_ids_generated}`);
  console.log(`Collisions Detected:    ${summary.collisions_detected}`);
  console.log(`Error Rate:             ${(summary.error_rate * 100).toFixed(2)}%`);
  console.log(`P95 Latency:            ${summary.http_req_duration_p95.toFixed(2)}ms`);
  console.log(`P99 Latency:            ${summary.http_req_duration_p99.toFixed(2)}ms`);
  console.log(`Total HTTP Requests:    ${summary.total_requests}`);
  console.log('============================================\n');

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
