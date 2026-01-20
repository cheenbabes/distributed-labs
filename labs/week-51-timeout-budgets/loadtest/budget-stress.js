import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

/**
 * Budget Stress Test
 *
 * This test deliberately uses tight budgets to stress the deadline propagation
 * system and observe early termination behavior.
 */

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const budgetExhaustedCount = new Counter('budget_exhausted');
const successCount = new Counter('success_requests');
const earlyTerminationCount = new Counter('early_terminations');

export const options = {
  scenarios: {
    // Scenario 1: Tight budget that should often exhaust
    tight_budget: {
      executor: 'constant-vus',
      vus: 5,
      duration: '1m',
      exec: 'tightBudget',
      tags: { scenario: 'tight_budget' },
    },
    // Scenario 2: Comfortable budget that should succeed
    comfortable_budget: {
      executor: 'constant-vus',
      vus: 5,
      duration: '1m',
      exec: 'comfortableBudget',
      startTime: '1m10s',
      tags: { scenario: 'comfortable_budget' },
    },
    // Scenario 3: Very tight budget - almost always fails
    impossible_budget: {
      executor: 'constant-vus',
      vus: 5,
      duration: '1m',
      exec: 'impossibleBudget',
      startTime: '2m20s',
      tags: { scenario: 'impossible_budget' },
    },
  },
  thresholds: {
    'http_req_duration{scenario:comfortable_budget}': ['p(95)<1500'],
    'errors{scenario:comfortable_budget}': ['rate<0.1'],
    'errors{scenario:impossible_budget}': ['rate>0.5'], // Expect high failure rate
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://frontend-api:8000';

// Helper to process response
function processResponse(res, budgetMs) {
  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  if (res.status === 200) {
    successCount.add(1);
  } else if (res.status === 504) {
    budgetExhaustedCount.add(1);
    try {
      const body = res.json();
      if (body.detail && body.detail.error === 'timeout_budget_exhausted') {
        earlyTerminationCount.add(1);
      }
    } catch (e) {}
  }

  check(res, {
    'response is valid': (r) => r.status === 200 || r.status === 504,
  });
}

// Tight budget: 600ms (normal processing is ~500ms)
export function tightBudget() {
  const budgetMs = 600;
  const res = http.get(`${BASE_URL}/api/process?budget_ms=${budgetMs}`);
  processResponse(res, budgetMs);
  sleep(0.3);
}

// Comfortable budget: 1500ms (plenty of time)
export function comfortableBudget() {
  const budgetMs = 1500;
  const res = http.get(`${BASE_URL}/api/process?budget_ms=${budgetMs}`);
  processResponse(res, budgetMs);
  sleep(0.3);
}

// Impossible budget: 200ms (cannot complete)
export function impossibleBudget() {
  const budgetMs = 200;
  const res = http.get(`${BASE_URL}/api/process?budget_ms=${budgetMs}`);
  processResponse(res, budgetMs);
  sleep(0.3);
}

export function handleSummary(data) {
  console.log('\n=== Budget Stress Test Summary ===\n');

  const scenarios = ['tight_budget', 'comfortable_budget', 'impossible_budget'];

  scenarios.forEach(scenario => {
    const metrics = data.metrics;
    const durationKey = `http_req_duration{scenario:${scenario}}`;
    const errorKey = `errors{scenario:${scenario}}`;

    if (metrics[durationKey]) {
      console.log(`${scenario}:`);
      console.log(`  p95 latency: ${metrics[durationKey].values['p(95)'].toFixed(2)}ms`);
    }
    if (metrics[errorKey]) {
      console.log(`  error rate: ${(metrics[errorKey].values.rate * 100).toFixed(1)}%`);
    }
    console.log('');
  });

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
