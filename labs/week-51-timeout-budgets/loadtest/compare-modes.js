import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

/**
 * Mode Comparison Test
 *
 * Compares behavior between budget propagation mode and fixed timeout mode.
 * Shows how deadline propagation prevents wasted work.
 */

const errorRate = new Rate('errors');
const latencyTrend = new Trend('request_latency');
const wastedWorkMs = new Counter('wasted_work_ms');

export const options = {
  scenarios: {
    // First: Enable slow mode and test with budget propagation
    budget_mode_slow: {
      executor: 'constant-vus',
      vus: 5,
      duration: '30s',
      exec: 'budgetModeSlow',
      tags: { mode: 'budget', slow: 'true' },
    },
    // Then: Test with fixed timeouts (no propagation)
    fixed_mode_slow: {
      executor: 'constant-vus',
      vus: 5,
      duration: '30s',
      exec: 'fixedModeSlow',
      startTime: '40s',
      tags: { mode: 'fixed', slow: 'true' },
    },
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://frontend-api:8000';

// Setup: Enable slow mode on service-c
export function setup() {
  console.log('Enabling slow mode on service-c...');
  http.post(`${BASE_URL.replace('frontend-api:8000', 'service-c:8003')}/admin/slow-mode?enabled=true&ms=800`);
  sleep(1);
}

// Teardown: Disable slow mode
export function teardown() {
  console.log('Disabling slow mode on service-c...');
  http.post(`${BASE_URL.replace('frontend-api:8000', 'service-c:8003')}/admin/slow-mode?enabled=false&ms=0`);
}

export function budgetModeSlow() {
  // Set mode to budget propagation
  http.post(`${BASE_URL}/admin/config`, JSON.stringify({
    mode: 'budget',
    default_budget_ms: 800,
    processing_time_ms: 50
  }), { headers: { 'Content-Type': 'application/json' } });

  const res = http.get(`${BASE_URL}/api/process?budget_ms=800`);

  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  // With budget propagation, services should terminate early
  check(res, {
    'response received': (r) => r.status === 200 || r.status === 504,
  });

  if (res.status === 504) {
    // Early termination - work was saved!
    console.log('Budget exhausted - early termination saved work');
  }

  sleep(0.3);
}

export function fixedModeSlow() {
  // Set mode to fixed timeouts (no propagation)
  http.post(`${BASE_URL}/admin/config`, JSON.stringify({
    mode: 'fixed',
    default_budget_ms: 800,
    processing_time_ms: 50
  }), { headers: { 'Content-Type': 'application/json' } });

  const res = http.get(`${BASE_URL}/api/process?budget_ms=800`);

  latencyTrend.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  // Without budget propagation, all services do their full work
  // even if the overall request will timeout
  check(res, {
    'response received': (r) => r.status === 200 || r.status === 504,
  });

  // If we got a timeout, downstream services still did their work = wasted
  if (res.status === 504 && res.timings.duration > 800) {
    // Estimate wasted work (processing time of downstream services)
    wastedWorkMs.add(res.timings.duration - 800);
  }

  sleep(0.3);
}

export function handleSummary(data) {
  console.log('\n=== Mode Comparison Summary ===\n');
  console.log('Budget mode: Services terminate early when budget exhausted');
  console.log('Fixed mode: All services do full work regardless of overall timeout\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
