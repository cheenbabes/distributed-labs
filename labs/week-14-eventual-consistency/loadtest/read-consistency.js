import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend, Rate } from 'k6/metrics';

// Custom metrics
const staleReads = new Counter('stale_reads');
const freshReads = new Counter('fresh_reads');
const consistencyWindowMs = new Trend('consistency_window_ms');
const writeLatency = new Trend('write_latency_ms');
const readLatency = new Trend('read_latency_ms');
const staleReadRate = new Rate('stale_read_rate');

// Test configuration
export const options = {
  scenarios: {
    // Scenario 1: Continuous writes with immediate reads
    write_then_read: {
      executor: 'constant-arrival-rate',
      rate: 5,  // 5 iterations per second
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      maxVUs: 20,
      exec: 'writeAndRead',
    },
    // Scenario 2: Just reads to measure staleness
    read_only: {
      executor: 'constant-vus',
      vus: 5,
      duration: '2m',
      exec: 'readOnly',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<3000'],  // 95% of requests should be < 3s
    stale_read_rate: ['rate<0.5'],       // Less than 50% stale reads
  },
};

const PRIMARY_URL = __ENV.PRIMARY_URL || 'http://lab14-primary:8000';
const REPLICA1_URL = __ENV.REPLICA1_URL || 'http://lab14-replica1:8001';
const REPLICA2_URL = __ENV.REPLICA2_URL || 'http://lab14-replica2:8002';
const CLIENT_URL = __ENV.CLIENT_URL || 'http://lab14-client:8003';

// Scenario 1: Write to primary, immediately read from replicas
export function writeAndRead() {
  const key = `key-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
  const value = `value-${Date.now()}`;

  // Write to primary
  const writeStart = Date.now();
  const writeRes = http.post(
    `${PRIMARY_URL}/data`,
    JSON.stringify({ key: key, value: value }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  const writeEnd = Date.now();

  check(writeRes, {
    'write status is 200': (r) => r.status === 200,
    'write returns version': (r) => r.json().version !== undefined,
  });

  if (writeRes.status !== 200) {
    return;
  }

  writeLatency.add(writeEnd - writeStart);
  const writeVersion = writeRes.json().version;

  // Immediately read from replicas
  const readStart = Date.now();

  const replica1Res = http.get(`${REPLICA1_URL}/version`);
  const replica2Res = http.get(`${REPLICA2_URL}/version`);

  const readEnd = Date.now();
  readLatency.add(readEnd - readStart);

  // Check for staleness
  let staleCount = 0;

  if (replica1Res.status === 200) {
    const r1Version = replica1Res.json().version;
    if (r1Version < writeVersion) {
      staleCount++;
      staleReads.add(1);
      staleReadRate.add(true);
    } else {
      freshReads.add(1);
      staleReadRate.add(false);
    }
  }

  if (replica2Res.status === 200) {
    const r2Version = replica2Res.json().version;
    if (r2Version < writeVersion) {
      staleCount++;
      staleReads.add(1);
      staleReadRate.add(true);
    } else {
      freshReads.add(1);
      staleReadRate.add(false);
    }
  }

  // If any replica was stale, wait for consistency and measure window
  if (staleCount > 0) {
    const consistencyStart = Date.now();
    const timeout = 5000;  // 5 second timeout

    while (Date.now() - consistencyStart < timeout) {
      const r1 = http.get(`${REPLICA1_URL}/version`);
      const r2 = http.get(`${REPLICA2_URL}/version`);

      const r1Consistent = r1.status === 200 && r1.json().version >= writeVersion;
      const r2Consistent = r2.status === 200 && r2.json().version >= writeVersion;

      if (r1Consistent && r2Consistent) {
        consistencyWindowMs.add(Date.now() - writeEnd);
        break;
      }

      sleep(0.05);  // 50ms between checks
    }
  } else {
    // Already consistent, window is 0
    consistencyWindowMs.add(0);
  }

  sleep(0.1);
}

// Scenario 2: Read-only to measure current staleness
export function readOnly() {
  // Get primary version
  const primaryRes = http.get(`${PRIMARY_URL}/version`);

  if (primaryRes.status !== 200) {
    sleep(0.5);
    return;
  }

  const primaryVersion = primaryRes.json().version;

  // Read from replicas
  const replica1Res = http.get(`${REPLICA1_URL}/version`);
  const replica2Res = http.get(`${REPLICA2_URL}/version`);

  if (replica1Res.status === 200) {
    const version = replica1Res.json().version;
    if (version < primaryVersion) {
      staleReads.add(1);
      staleReadRate.add(true);
    } else {
      freshReads.add(1);
      staleReadRate.add(false);
    }
  }

  if (replica2Res.status === 200) {
    const version = replica2Res.json().version;
    if (version < primaryVersion) {
      staleReads.add(1);
      staleReadRate.add(true);
    } else {
      freshReads.add(1);
      staleReadRate.add(false);
    }
  }

  sleep(0.5);
}

// Default function - use client service for comparison
export default function() {
  // Use the client's write-and-read endpoint
  const res = http.post(
    `${CLIENT_URL}/write-and-read`,
    JSON.stringify({ key: `k6-${Date.now()}`, value: `v-${Math.random()}` }),
    { headers: { 'Content-Type': 'application/json' } }
  );

  check(res, {
    'status is 200': (r) => r.status === 200,
    'response has write_result': (r) => r.json().write_result !== undefined,
    'response has immediate_reads': (r) => r.json().immediate_reads !== undefined,
  });

  if (res.status === 200) {
    const data = res.json();
    const staleCount = data.stale_read_count || 0;

    staleReads.add(staleCount);
    freshReads.add(data.total_replicas - staleCount);

    if (staleCount > 0) {
      staleReadRate.add(true);
    } else {
      staleReadRate.add(false);
    }
  }

  sleep(0.2);
}

export function handleSummary(data) {
  const stale = data.metrics.stale_reads ? data.metrics.stale_reads.values.count : 0;
  const fresh = data.metrics.fresh_reads ? data.metrics.fresh_reads.values.count : 0;
  const total = stale + fresh;
  const stalePercent = total > 0 ? ((stale / total) * 100).toFixed(2) : 0;

  const summary = {
    'Eventual Consistency Test Results': {
      'Total Reads': total,
      'Stale Reads': stale,
      'Fresh Reads': fresh,
      'Stale Read %': `${stalePercent}%`,
    },
  };

  if (data.metrics.consistency_window_ms) {
    summary['Consistency Window'] = {
      'p50': `${data.metrics.consistency_window_ms.values['p(50)']?.toFixed(2) || 'N/A'}ms`,
      'p95': `${data.metrics.consistency_window_ms.values['p(95)']?.toFixed(2) || 'N/A'}ms`,
      'p99': `${data.metrics.consistency_window_ms.values['p(99)']?.toFixed(2) || 'N/A'}ms`,
      'max': `${data.metrics.consistency_window_ms.values['max']?.toFixed(2) || 'N/A'}ms`,
    };
  }

  console.log('\n========================================');
  console.log('EVENTUAL CONSISTENCY TEST SUMMARY');
  console.log('========================================\n');
  console.log(JSON.stringify(summary, null, 2));
  console.log('\n========================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
