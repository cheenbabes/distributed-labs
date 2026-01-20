import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomString } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

// Custom metrics
const writeErrors = new Rate('write_errors');
const readErrors = new Rate('read_errors');
const quorumFailures = new Rate('quorum_failures');
const readAfterWriteConsistency = new Rate('read_after_write_consistency');
const writeTrend = new Trend('write_latency');
const readTrend = new Trend('read_latency');
const conflictCounter = new Counter('read_conflicts');

// Configuration
const BASE_URL = __ENV.BASE_URL || 'http://lab46-coordinator:8000';

// Test configuration
export const options = {
  scenarios: {
    // Scenario 1: Baseline load
    baseline: {
      executor: 'constant-vus',
      vus: 5,
      duration: '30s',
      startTime: '0s',
      tags: { scenario: 'baseline' },
    },
    // Scenario 2: Ramp up load
    rampup: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 10 },
        { duration: '1m', target: 10 },
        { duration: '30s', target: 0 },
      ],
      startTime: '30s',
      tags: { scenario: 'rampup' },
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    write_errors: ['rate<0.1'],
    read_errors: ['rate<0.1'],
    quorum_failures: ['rate<0.2'],
    read_after_write_consistency: ['rate>0.95'],
  },
};

// Generate a unique key for this VU iteration
function generateKey() {
  return `k6_${__VU}_${__ITER}_${Date.now()}`;
}

// Generate a random value
function generateValue() {
  return randomString(20);
}

export default function () {
  const key = generateKey();
  const value = generateValue();

  group('write_then_read', function () {
    // Write operation
    const writePayload = JSON.stringify({ key: key, value: value });
    const writeRes = http.post(`${BASE_URL}/write`, writePayload, {
      headers: { 'Content-Type': 'application/json' },
      tags: { operation: 'write' },
    });

    writeTrend.add(writeRes.timings.duration);

    const writeSuccess = check(writeRes, {
      'write status is 200': (r) => r.status === 200,
      'write has version': (r) => {
        try {
          const body = JSON.parse(r.body);
          return body.version !== undefined;
        } catch (e) {
          return false;
        }
      },
      'write quorum met': (r) => {
        try {
          const body = JSON.parse(r.body);
          return body.quorum_met === true;
        } catch (e) {
          return false;
        }
      },
    });

    writeErrors.add(!writeSuccess);
    if (!writeSuccess) {
      console.log(`Write failed: ${writeRes.status} - ${writeRes.body}`);
    }

    // Check for quorum failures
    if (writeRes.status === 503) {
      quorumFailures.add(1);
    } else {
      quorumFailures.add(0);
    }

    // Read operation (immediately after write)
    if (writeSuccess) {
      const readRes = http.get(`${BASE_URL}/read/${key}`, {
        tags: { operation: 'read' },
      });

      readTrend.add(readRes.timings.duration);

      const readSuccess = check(readRes, {
        'read status is 200': (r) => r.status === 200,
        'read has value': (r) => {
          try {
            const body = JSON.parse(r.body);
            return body.value !== null && body.value !== undefined;
          } catch (e) {
            return false;
          }
        },
        'read quorum met': (r) => {
          try {
            const body = JSON.parse(r.body);
            return body.quorum_met === true;
          } catch (e) {
            return false;
          }
        },
      });

      readErrors.add(!readSuccess);
      if (!readSuccess) {
        console.log(`Read failed: ${readRes.status} - ${readRes.body}`);
      }

      // Check read-after-write consistency
      if (readSuccess) {
        try {
          const readBody = JSON.parse(readRes.body);
          const isConsistent = readBody.value === value;
          readAfterWriteConsistency.add(isConsistent ? 1 : 0);

          if (!isConsistent) {
            console.log(
              `Consistency violation: wrote '${value}', read '${readBody.value}'`
            );
          }

          // Track conflicts
          if (readBody.had_conflicts) {
            conflictCounter.add(1);
          }
        } catch (e) {
          readAfterWriteConsistency.add(0);
        }
      } else {
        readAfterWriteConsistency.add(0);
      }

      // Check for quorum failures
      if (readRes.status === 503) {
        quorumFailures.add(1);
      } else {
        quorumFailures.add(0);
      }
    }
  });

  // Small pause between iterations
  sleep(0.5);
}

// Handle summary
export function handleSummary(data) {
  const summary = {
    timestamp: new Date().toISOString(),
    metrics: {
      write_latency_p50: data.metrics.write_latency?.values?.['p(50)'],
      write_latency_p95: data.metrics.write_latency?.values?.['p(95)'],
      read_latency_p50: data.metrics.read_latency?.values?.['p(50)'],
      read_latency_p95: data.metrics.read_latency?.values?.['p(95)'],
      write_error_rate: data.metrics.write_errors?.values?.rate,
      read_error_rate: data.metrics.read_errors?.values?.rate,
      quorum_failure_rate: data.metrics.quorum_failures?.values?.rate,
      consistency_rate: data.metrics.read_after_write_consistency?.values?.rate,
      total_requests: data.metrics.http_reqs?.values?.count,
    },
  };

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
