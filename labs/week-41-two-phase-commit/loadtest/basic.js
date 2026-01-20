import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const blockedRate = new Rate('blocked');
const latencyTrend = new Trend('transaction_latency');
const committedCounter = new Counter('committed_transactions');
const abortedCounter = new Counter('aborted_transactions');
const blockedCounter = new Counter('blocked_transactions');

// Test configuration
export const options = {
  scenarios: {
    // Normal load scenario
    normal_load: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 5 },   // Ramp up to 5 users
        { duration: '1m', target: 5 },    // Stay at 5 users
        { duration: '30s', target: 0 },   // Ramp down
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<5000'],  // 95% of requests should be < 5s
    errors: ['rate<0.3'],                // Error rate should be < 30%
  },
};

const CLIENT_URL = __ENV.CLIENT_URL || 'http://lab41-client:8080';

// List of account pairs for transfers
const accounts = ['account-A', 'account-B', 'account-C', 'account-D', 'account-E'];

function getRandomAccount(exclude) {
  const available = accounts.filter(a => a !== exclude);
  return available[Math.floor(Math.random() * available.length)];
}

export default function () {
  const fromAccount = accounts[Math.floor(Math.random() * accounts.length)];
  const toAccount = getRandomAccount(fromAccount);
  const amount = Math.round(Math.random() * 1000 * 100) / 100;

  const payload = JSON.stringify({
    from_account: fromAccount,
    to_account: toAccount,
    amount: amount,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
    timeout: '30s',
  };

  const res = http.post(`${CLIENT_URL}/transfer`, payload, params);

  // Record latency
  latencyTrend.add(res.timings.duration);

  // Parse response
  let status = 'unknown';
  try {
    const body = JSON.parse(res.body);
    status = body.status || 'unknown';
  } catch (e) {
    status = 'error';
  }

  // Update metrics based on status
  if (status === 'committed') {
    committedCounter.add(1);
    errorRate.add(false);
    blockedRate.add(false);
  } else if (status === 'aborted') {
    abortedCounter.add(1);
    errorRate.add(false);  // Aborted is a valid outcome, not an error
    blockedRate.add(false);
  } else if (status === 'blocked' || status === 'timeout') {
    blockedCounter.add(1);
    errorRate.add(true);
    blockedRate.add(true);
  } else {
    errorRate.add(true);
    blockedRate.add(false);
  }

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'transaction completed': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.status === 'committed' || body.status === 'aborted';
      } catch (e) {
        return false;
      }
    },
    'not blocked': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.status !== 'blocked' && body.status !== 'timeout';
      } catch (e) {
        return true;
      }
    },
  });

  // Pause between transactions
  sleep(0.5 + Math.random() * 0.5);
}

// Scenario for testing coordinator failure
export function coordinatorFailure() {
  const fromAccount = accounts[Math.floor(Math.random() * accounts.length)];
  const toAccount = getRandomAccount(fromAccount);
  const amount = Math.round(Math.random() * 1000 * 100) / 100;

  const payload = JSON.stringify({
    from_account: fromAccount,
    to_account: toAccount,
    amount: amount,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
    timeout: '60s',  // Longer timeout for blocked scenarios
  };

  const res = http.post(`${CLIENT_URL}/transfer`, payload, params);

  latencyTrend.add(res.timings.duration);

  // In failure scenario, we expect some blocked transactions
  let status = 'unknown';
  try {
    const body = JSON.parse(res.body);
    status = body.status || 'unknown';

    if (status === 'blocked' || status === 'timeout') {
      console.log(`BLOCKED: Transaction took ${res.timings.duration}ms`);
      blockedCounter.add(1);
    }
  } catch (e) {
    console.log(`Error parsing response: ${e}`);
  }

  sleep(1);
}

export function handleSummary(data) {
  const summary = {
    'Total Requests': data.metrics.http_reqs.values.count,
    'Committed': data.metrics.committed_transactions ? data.metrics.committed_transactions.values.count : 0,
    'Aborted': data.metrics.aborted_transactions ? data.metrics.aborted_transactions.values.count : 0,
    'Blocked': data.metrics.blocked_transactions ? data.metrics.blocked_transactions.values.count : 0,
    'Error Rate': data.metrics.errors ? `${(data.metrics.errors.values.rate * 100).toFixed(2)}%` : 'N/A',
    'P50 Latency': `${data.metrics.transaction_latency.values['p(50)'].toFixed(0)}ms`,
    'P95 Latency': `${data.metrics.transaction_latency.values['p(95)'].toFixed(0)}ms`,
    'P99 Latency': `${data.metrics.transaction_latency.values['p(99)'].toFixed(0)}ms`,
  };

  console.log('\n========== 2PC Load Test Summary ==========');
  for (const [key, value] of Object.entries(summary)) {
    console.log(`${key}: ${value}`);
  }
  console.log('============================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
