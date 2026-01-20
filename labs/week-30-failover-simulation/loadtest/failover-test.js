import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const writeErrors = new Rate('write_errors');
const readErrors = new Rate('read_errors');
const writeLatency = new Trend('write_latency');
const readLatency = new Trend('read_latency');
const failoverDetected = new Counter('failover_detected');

// Test configuration
export const options = {
  scenarios: {
    // Continuous mixed read/write workload
    mixed_workload: {
      executor: 'constant-arrival-rate',
      rate: 20,              // 20 requests per second
      timeUnit: '1s',
      duration: '3m',
      preAllocatedVUs: 10,
      maxVUs: 50,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<5000'],  // Allow high latency during failover
    write_errors: ['rate<0.3'],          // Up to 30% errors during failover is acceptable
    read_errors: ['rate<0.2'],           // Reads should be more reliable
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab30-client-gateway:8000';

// Track write sequence for RPO calculation
let lastWriteSequence = 0;
let writeCounter = 0;

export default function () {
  // 70% reads, 30% writes
  const isWrite = Math.random() < 0.3;

  if (isWrite) {
    performWrite();
  } else {
    performRead();
  }

  // Small pause between requests
  sleep(0.1);
}

function performWrite() {
  writeCounter++;
  const key = `test-key-${writeCounter % 100}`;  // Cycle through 100 keys
  const value = {
    counter: writeCounter,
    timestamp: new Date().toISOString(),
    vu: __VU,
    iter: __ITER,
  };

  const start = Date.now();
  const res = http.post(
    `${BASE_URL}/write`,
    JSON.stringify({ key: key, value: value }),
    {
      headers: { 'Content-Type': 'application/json' },
      timeout: '10s',
    }
  );
  const duration = Date.now() - start;
  writeLatency.add(duration);

  const success = check(res, {
    'write status is 200': (r) => r.status === 200,
    'write has sequence': (r) => {
      try {
        const body = JSON.parse(r.body);
        if (body.result && body.result.sequence) {
          lastWriteSequence = body.result.sequence;
          return true;
        }
        return false;
      } catch (e) {
        return false;
      }
    },
  });

  writeErrors.add(!success);

  if (!success && res.status === 503) {
    // Likely a failover in progress
    failoverDetected.add(1);
    console.log(`Write failed (failover?): ${res.status} - ${res.body}`);
  }
}

function performRead() {
  // Read from a random key that might exist
  const key = `test-key-${Math.floor(Math.random() * 100)}`;

  const start = Date.now();
  const res = http.get(`${BASE_URL}/read/${key}`, {
    timeout: '5s',
  });
  const duration = Date.now() - start;
  readLatency.add(duration);

  const success = check(res, {
    'read status is 200 or 404': (r) => r.status === 200 || r.status === 404,
  });

  readErrors.add(!success);

  if (!success && res.status === 503) {
    failoverDetected.add(1);
    console.log(`Read failed (failover?): ${res.status}`);
  }
}

export function handleSummary(data) {
  const summary = {
    metrics: {
      write_errors: data.metrics.write_errors ? data.metrics.write_errors.values.rate : 0,
      read_errors: data.metrics.read_errors ? data.metrics.read_errors.values.rate : 0,
      write_latency_p95: data.metrics.write_latency ? data.metrics.write_latency.values['p(95)'] : 0,
      read_latency_p95: data.metrics.read_latency ? data.metrics.read_latency.values['p(95)'] : 0,
      failovers_detected: data.metrics.failover_detected ? data.metrics.failover_detected.values.count : 0,
      total_requests: data.metrics.http_reqs ? data.metrics.http_reqs.values.count : 0,
    },
    thresholds: data.root_group ? data.root_group.thresholds : {},
  };

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
