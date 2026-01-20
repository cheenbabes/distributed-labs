import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter, Gauge } from 'k6/metrics';

// Custom metrics
const successRate = new Rate('success_rate');
const latencyTrend = new Trend('request_latency');
const concurrencyGauge = new Gauge('observed_concurrency');

// Test configuration - variable load to test adaptive algorithm
export const options = {
  stages: [
    // Phase 1: Low load - adaptive limit should increase
    { duration: '30s', target: 10 },

    // Phase 2: Medium load - find equilibrium
    { duration: '30s', target: 30 },

    // Phase 3: High load spike - adaptive should respond
    { duration: '20s', target: 80 },

    // Phase 4: Return to medium - adaptive should recover
    { duration: '30s', target: 30 },

    // Phase 5: Gradual ramp down
    { duration: '30s', target: 5 },

    // Phase 6: Cleanup
    { duration: '20s', target: 0 },
  ],
  thresholds: {
    success_rate: ['rate>0.5'],
    http_req_duration: ['p(99)<10000'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab26-gateway:8000';

export default function () {
  const res = http.get(`${BASE_URL}/api/process`);

  // Record metrics
  latencyTrend.add(res.timings.duration);
  successRate.add(res.status === 200);

  // Try to get concurrency from response
  if (res.status === 200) {
    try {
      const body = res.json();
      if (body.load_shedding && body.load_shedding.current_concurrent) {
        concurrencyGauge.add(body.load_shedding.current_concurrent);
      }
    } catch (e) {
      // Ignore JSON parse errors
    }
  }

  check(res, {
    'status is valid': (r) => [200, 503, 504].includes(r.status),
  });

  sleep(0.05);
}

export function handleSummary(data) {
  return {
    stdout: adaptiveSummary(data),
  };
}

function adaptiveSummary(data) {
  const summary = [];
  summary.push('\n=== Adaptive Concurrency Test Summary ===\n');

  const reqs = data.metrics.http_reqs;
  if (reqs) {
    summary.push(`Total Requests: ${reqs.values.count}`);
  }

  const success = data.metrics.success_rate;
  if (success) {
    summary.push(`Overall Success Rate: ${(success.values.rate * 100).toFixed(2)}%`);
  }

  const duration = data.metrics.http_req_duration;
  if (duration) {
    summary.push(`\nLatency Distribution:`);
    summary.push(`  p50: ${duration.values['p(50)'].toFixed(0)}ms`);
    summary.push(`  p90: ${duration.values['p(90)'].toFixed(0)}ms`);
    summary.push(`  p95: ${duration.values['p(95)'].toFixed(0)}ms`);
    summary.push(`  p99: ${duration.values['p(99)'].toFixed(0)}ms`);
  }

  const concurrency = data.metrics.observed_concurrency;
  if (concurrency) {
    summary.push(`\nObserved Gateway Concurrency:`);
    summary.push(`  min: ${concurrency.values.min}`);
    summary.push(`  max: ${concurrency.values.max}`);
    summary.push(`  avg: ${concurrency.values.avg.toFixed(1)}`);
  }

  summary.push('\n');
  summary.push('NOTE: Watch Grafana dashboard for adaptive_concurrency_limit changes');
  summary.push('The limit should increase during low load and decrease during high load');
  summary.push('\n==========================================\n');
  return summary.join('\n');
}
