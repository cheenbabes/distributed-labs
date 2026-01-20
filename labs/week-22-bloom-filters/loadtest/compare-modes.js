import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Counter } from 'k6/metrics';
import { randomString } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

/**
 * Compare latency with and without bloom filter.
 *
 * Usage:
 *   # First, run without bloom filter
 *   curl -X POST http://localhost:8000/admin/bloom-config -H "Content-Type: application/json" -d '{"enabled": false}'
 *   k6 run compare-modes.js
 *
 *   # Then, enable bloom filter and run again
 *   curl -X POST http://localhost:8000/admin/bloom-config -H "Content-Type: application/json" -d '{"enabled": true}'
 *   k6 run compare-modes.js
 */

// Custom metrics
const checkNewLatency = new Trend('check_new_username_latency');
const checkExistingLatency = new Trend('check_existing_username_latency');
const dbQueriesCount = new Counter('db_queries');
const bloomSavedCount = new Counter('bloom_saved');

export const options = {
  stages: [
    { duration: '10s', target: 20 },
    { duration: '30s', target: 20 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab22-username-service:8000';

// Store registered usernames for checking
let registeredUsernames = [];

export function setup() {
  // Get bloom filter status
  const statusRes = http.get(`${BASE_URL}/admin/bloom-stats`);
  const status = statusRes.json();
  console.log(`Bloom filter enabled: ${status.enabled}`);
  console.log(`Items in filter: ${status.items_count}`);

  // Register 50 usernames for testing
  for (let i = 0; i < 50; i++) {
    const username = `comparetest${i}${randomString(4)}`;
    const res = http.post(
      `${BASE_URL}/register-username`,
      JSON.stringify({ username: username }),
      { headers: { 'Content-Type': 'application/json' } }
    );
    if (res.status === 201) {
      registeredUsernames.push(username);
    }
  }

  return { registeredUsernames };
}

export default function (data) {
  // 50% check new usernames, 50% check existing
  if (Math.random() < 0.5) {
    // Check a new username (should not exist)
    const username = `newcheck${Date.now()}${randomString(6)}`;
    const res = http.post(
      `${BASE_URL}/check-username`,
      JSON.stringify({ username: username }),
      { headers: { 'Content-Type': 'application/json' } }
    );

    checkNewLatency.add(res.timings.duration);

    const body = res.json();
    check(res, {
      'check new - status 200': (r) => r.status === 200,
      'check new - is available': () => body.available === true,
    });

    if (body.db_queried) {
      dbQueriesCount.add(1);
    } else {
      bloomSavedCount.add(1);
    }
  } else {
    // Check an existing username
    if (data.registeredUsernames.length > 0) {
      const username = data.registeredUsernames[
        Math.floor(Math.random() * data.registeredUsernames.length)
      ];
      const res = http.post(
        `${BASE_URL}/check-username`,
        JSON.stringify({ username: username }),
        { headers: { 'Content-Type': 'application/json' } }
      );

      checkExistingLatency.add(res.timings.duration);

      const body = res.json();
      check(res, {
        'check existing - status 200': (r) => r.status === 200,
        'check existing - not available': () => body.available === false,
      });

      if (body.db_queried) {
        dbQueriesCount.add(1);
      }
    }
  }

  sleep(0.05);
}

export function handleSummary(data) {
  const checkNewP50 = data.metrics.check_new_username_latency
    ? data.metrics.check_new_username_latency.values['p(50)']
    : 0;
  const checkNewP95 = data.metrics.check_new_username_latency
    ? data.metrics.check_new_username_latency.values['p(95)']
    : 0;
  const checkExistingP50 = data.metrics.check_existing_username_latency
    ? data.metrics.check_existing_username_latency.values['p(50)']
    : 0;
  const checkExistingP95 = data.metrics.check_existing_username_latency
    ? data.metrics.check_existing_username_latency.values['p(95)']
    : 0;

  return {
    stdout: JSON.stringify({
      summary: 'Comparison results',
      check_new_username: {
        p50_ms: checkNewP50.toFixed(2),
        p95_ms: checkNewP95.toFixed(2),
      },
      check_existing_username: {
        p50_ms: checkExistingP50.toFixed(2),
        p95_ms: checkExistingP95.toFixed(2),
      },
      db_queries: data.metrics.db_queries ? data.metrics.db_queries.values.count : 0,
      bloom_saved: data.metrics.bloom_saved ? data.metrics.bloom_saved.values.count : 0,
    }, null, 2),
  };
}
