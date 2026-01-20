import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { randomString } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// Custom metrics
const errorRate = new Rate('errors');
const checkLatency = new Trend('check_username_latency');
const bloomHits = new Counter('bloom_filter_hits');
const dbQueries = new Counter('db_queries');

// Test configuration
export const options = {
  scenarios: {
    check_existing: {
      executor: 'constant-vus',
      vus: 5,
      duration: '1m',
      exec: 'checkExistingUsernames',
      tags: { scenario: 'check_existing' },
    },
    check_new: {
      executor: 'constant-vus',
      vus: 5,
      duration: '1m',
      exec: 'checkNewUsernames',
      tags: { scenario: 'check_new' },
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<500'],
    errors: ['rate<0.1'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab22-username-service:8000';

// Pre-generate some usernames to check against
const existingUsernames = [];
const numPreRegistered = parseInt(__ENV.PRE_REGISTERED || '100');

export function setup() {
  console.log(`Pre-registering ${numPreRegistered} usernames...`);

  // Register some usernames first
  for (let i = 0; i < numPreRegistered; i++) {
    const username = `testuser${i}${randomString(4)}`;
    const res = http.post(
      `${BASE_URL}/register-username`,
      JSON.stringify({ username: username }),
      { headers: { 'Content-Type': 'application/json' } }
    );
    if (res.status === 201) {
      existingUsernames.push(username);
    }
  }

  console.log(`Registered ${existingUsernames.length} usernames`);
  return { existingUsernames: existingUsernames };
}

export function checkExistingUsernames(data) {
  // Check usernames that DO exist (tests bloom filter "probably exists" path)
  if (data.existingUsernames.length === 0) {
    sleep(0.1);
    return;
  }

  const username = data.existingUsernames[Math.floor(Math.random() * data.existingUsernames.length)];

  const res = http.post(
    `${BASE_URL}/check-username`,
    JSON.stringify({ username: username }),
    { headers: { 'Content-Type': 'application/json' } }
  );

  checkLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  const body = res.json();

  check(res, {
    'status is 200': (r) => r.status === 200,
    'username not available': () => body.available === false,
  });

  if (body.bloom_result === 'probably_exists') {
    bloomHits.add(1);
  }
  if (body.db_queried) {
    dbQueries.add(1);
  }

  sleep(0.1);
}

export function checkNewUsernames() {
  // Check usernames that DON'T exist (tests bloom filter "definitely not exists" path)
  const username = `newuser${Date.now()}${randomString(8)}`;

  const res = http.post(
    `${BASE_URL}/check-username`,
    JSON.stringify({ username: username }),
    { headers: { 'Content-Type': 'application/json' } }
  );

  checkLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  const body = res.json();

  check(res, {
    'status is 200': (r) => r.status === 200,
    'username is available': () => body.available === true,
  });

  if (body.bloom_result === 'definitely_not_exists') {
    bloomHits.add(1);
  }
  if (body.db_queried) {
    dbQueries.add(1);
  }

  sleep(0.1);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      total_requests: data.metrics.http_reqs.values.count,
      avg_latency_ms: data.metrics.http_req_duration.values.avg,
      p95_latency_ms: data.metrics.http_req_duration.values['p(95)'],
      error_rate: data.metrics.errors.values.rate,
    }, null, 2),
  };
}
