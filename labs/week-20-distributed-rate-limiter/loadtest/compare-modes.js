import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

/**
 * Compare Modes Test
 *
 * This script compares the behavior of different rate limiting modes:
 * - local: Each instance has its own counter (limit not enforced correctly)
 * - redis_naive: Shared Redis, but race conditions possible
 * - redis_atomic: Shared Redis with Lua scripts (correct behavior)
 *
 * Run this script AFTER switching modes via the admin API.
 *
 * Example workflow:
 * 1. Set mode: curl -X POST http://localhost:8080/admin/config -d '{"use_redis":true,"use_lua_script":true}'
 * 2. Reset: curl -X POST http://localhost:8080/admin/reset
 * 3. Run: docker compose run --rm k6 run /scripts/compare-modes.js
 */

const allowedCounter = new Counter('requests_allowed');
const rejectedCounter = new Counter('requests_rejected');
const latencyTrend = new Trend('request_latency');

export const options = {
  scenarios: {
    burst_test: {
      executor: 'shared-iterations',
      vus: 20,
      iterations: 500,
      maxDuration: '30s',
    },
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://nginx:80';
const API_KEY = __ENV.API_KEY || 'burst-test-client';

export function setup() {
  // Get current mode
  const configRes = http.get(`${BASE_URL}/admin/config`);
  let config = {};
  try {
    config = JSON.parse(configRes.body);
  } catch (e) {
    console.log('Could not parse config');
  }

  console.log(`\nCurrent mode: ${config.mode || 'unknown'}`);
  console.log(`Rate limit: ${config.rate_limit || 100} requests per ${config.window_size_seconds || 60} seconds`);
  console.log(`Sending 500 requests from 20 concurrent VUs...\n`);

  return { mode: config.mode };
}

export default function (data) {
  const res = http.get(`${BASE_URL}/api/request`, {
    headers: {
      'X-API-Key': API_KEY,
    },
  });

  latencyTrend.add(res.timings.duration);

  if (res.status === 200) {
    allowedCounter.add(1);
  } else if (res.status === 429) {
    rejectedCounter.add(1);
  }

  check(res, {
    'valid response': (r) => r.status === 200 || r.status === 429,
  });
}

export function handleSummary(data) {
  const allowed = data.metrics.requests_allowed ? data.metrics.requests_allowed.values.count : 0;
  const rejected = data.metrics.requests_rejected ? data.metrics.requests_rejected.values.count : 0;
  const total = allowed + rejected;
  const duration = data.state.testRunDurationMs / 1000;

  console.log('\n=== Mode Comparison Summary ===');
  console.log(`Test duration: ${duration.toFixed(1)}s`);
  console.log(`Total requests: ${total}`);
  console.log(`Allowed: ${allowed}`);
  console.log(`Rejected: ${rejected}`);
  console.log(`Expected allowed (with 100/60s limit): ~100`);
  console.log('');

  if (allowed > 110) {
    console.log('WARNING: Allowed requests > expected limit!');
    console.log('This indicates either:');
    console.log('  - local mode (no shared state)');
    console.log('  - race conditions in naive mode');
  } else if (allowed >= 95 && allowed <= 105) {
    console.log('SUCCESS: Rate limit correctly enforced!');
    console.log('This indicates atomic Redis operations are working.');
  } else if (allowed < 95) {
    console.log('NOTE: Fewer requests allowed than expected.');
    console.log('This could be due to:');
    console.log('  - Test ran for less than the full window');
    console.log('  - Previous requests still in the window');
  }

  console.log('===============================\n');

  return {
    stdout: JSON.stringify({
      allowed: allowed,
      rejected: rejected,
      total: total,
      duration_seconds: duration,
      latency_p95: data.metrics.request_latency ? data.metrics.request_latency.values['p(95)'] : 0,
    }, null, 2),
  };
}
