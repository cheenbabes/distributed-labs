import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics for comparison
const hedgedLatency = new Trend('hedged_latency_ms');
const directLatency = new Trend('direct_latency_ms');
const hedgedErrors = new Rate('hedged_errors');
const directErrors = new Rate('direct_errors');
const hedgeWins = new Counter('hedge_wins');
const primaryWins = new Counter('primary_wins');

// Test configuration
export const options = {
  scenarios: {
    // Hedged requests scenario
    hedged: {
      executor: 'constant-arrival-rate',
      rate: 10,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 20,
      maxVUs: 50,
      exec: 'hedgedRequest',
    },
    // Direct requests scenario (for comparison)
    direct: {
      executor: 'constant-arrival-rate',
      rate: 10,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 20,
      maxVUs: 50,
      exec: 'directRequest',
      startTime: '2m30s', // Start after hedged scenario + buffer
    },
  },
  thresholds: {
    // Hedged requests should have lower P99
    'hedged_latency_ms{quantile:0.99}': ['value<200'],
    // Direct requests will have higher P99
    'direct_latency_ms{quantile:0.99}': ['value<400'],
    // Low error rates for both
    'hedged_errors': ['rate<0.05'],
    'direct_errors': ['rate<0.05'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab52-frontend:8000';
const HEDGER_URL = __ENV.HEDGER_URL || 'http://lab52-hedger:8080';

export function hedgedRequest() {
  const res = http.get(`${BASE_URL}/api/process?mode=hedged`);

  const success = res.status === 200;
  hedgedLatency.add(res.timings.duration);
  hedgedErrors.add(!success);

  if (success) {
    try {
      const body = res.json();
      const result = body.downstream_result;
      if (result && result.result_type === 'hedge_won') {
        hedgeWins.add(1);
      } else if (result && result.result_type === 'primary_won') {
        primaryWins.add(1);
      }
    } catch (e) {
      // Ignore parse errors
    }
  }

  check(res, {
    '[hedged] status is 200': (r) => r.status === 200,
    '[hedged] has downstream result': (r) => {
      try {
        return r.json().downstream_result !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  sleep(0.1);
}

export function directRequest() {
  const res = http.get(`${BASE_URL}/api/process?mode=direct`);

  const success = res.status === 200;
  directLatency.add(res.timings.duration);
  directErrors.add(!success);

  check(res, {
    '[direct] status is 200': (r) => r.status === 200,
    '[direct] has downstream result': (r) => {
      try {
        return r.json().downstream_result !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  sleep(0.1);
}

export function handleSummary(data) {
  // Extract key metrics for comparison
  const hedgedP50 = data.metrics.hedged_latency_ms?.values?.['p(50)'] || 'N/A';
  const hedgedP95 = data.metrics.hedged_latency_ms?.values?.['p(95)'] || 'N/A';
  const hedgedP99 = data.metrics.hedged_latency_ms?.values?.['p(99)'] || 'N/A';

  const directP50 = data.metrics.direct_latency_ms?.values?.['p(50)'] || 'N/A';
  const directP95 = data.metrics.direct_latency_ms?.values?.['p(95)'] || 'N/A';
  const directP99 = data.metrics.direct_latency_ms?.values?.['p(99)'] || 'N/A';

  const summary = `
================================================================================
                    SPECULATIVE EXECUTION COMPARISON RESULTS
================================================================================

HEDGED REQUESTS (with speculative execution):
  P50 Latency: ${typeof hedgedP50 === 'number' ? hedgedP50.toFixed(2) : hedgedP50}ms
  P95 Latency: ${typeof hedgedP95 === 'number' ? hedgedP95.toFixed(2) : hedgedP95}ms
  P99 Latency: ${typeof hedgedP99 === 'number' ? hedgedP99.toFixed(2) : hedgedP99}ms

DIRECT REQUESTS (without speculative execution):
  P50 Latency: ${typeof directP50 === 'number' ? directP50.toFixed(2) : directP50}ms
  P95 Latency: ${typeof directP95 === 'number' ? directP95.toFixed(2) : directP95}ms
  P99 Latency: ${typeof directP99 === 'number' ? directP99.toFixed(2) : directP99}ms

IMPROVEMENT:
  P99 Reduction: ${typeof hedgedP99 === 'number' && typeof directP99 === 'number'
    ? ((directP99 - hedgedP99) / directP99 * 100).toFixed(1) + '%'
    : 'N/A'}

Hedge Wins: ${data.metrics.hedge_wins?.values?.count || 0}
Primary Wins: ${data.metrics.primary_wins?.values?.count || 0}

================================================================================
`;

  console.log(summary);

  return {
    stdout: summary,
    'summary.json': JSON.stringify(data, null, 2),
  };
}
