import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';

/**
 * Deploy Simulation Test
 *
 * This test simulates what happens during a deployment:
 * 1. Initial steady state (all traffic to warm instance)
 * 2. Deploy event (cold instance starts receiving traffic)
 * 3. Observe latency spike on cold instance
 * 4. Eventually cold instance warms up
 *
 * Run this while using the warmup service to shift traffic.
 */

const latencyByInstance = new Trend('latency_by_instance');
const warmLatency = new Trend('warm_latency');
const coldLatency = new Trend('cold_latency');
const loadBalancedLatency = new Trend('lb_latency');
const errorRate = new Rate('errors');
const requestCount = new Counter('requests');

export const options = {
  scenarios: {
    steady_load: {
      executor: 'constant-arrival-rate',
      rate: 20,
      timeUnit: '1s',
      duration: '5m',
      preAllocatedVUs: 20,
      maxVUs: 100,
    },
  },
  thresholds: {
    errors: ['rate<0.05'],
    lb_latency: ['p(99)<5000'],
  },
};

const NGINX_URL = __ENV.NGINX_URL || 'http://nginx:80';
const WARM_URL = __ENV.WARM_URL || 'http://api-warm:8000';
const COLD_URL = __ENV.COLD_URL || 'http://api-cold:8000';

export default function () {
  const productId = Math.floor(Math.random() * 100) + 1;

  // Primary: Load balanced traffic (simulates real production traffic)
  group('Load Balanced', () => {
    const res = http.get(`${NGINX_URL}/api/product/${productId}`);

    const success = check(res, {
      'lb: status is 200': (r) => r.status === 200,
    });

    errorRate.add(!success);
    requestCount.add(1);

    if (res.status === 200) {
      loadBalancedLatency.add(res.timings.duration);

      try {
        const body = res.json();
        if (body.instance) {
          latencyByInstance.add(res.timings.duration, { instance: body.instance });
        }
      } catch (e) {
        // ignore
      }
    }
  });

  // Also track individual instances for comparison
  if (Math.random() < 0.2) {  // 20% sample rate for direct checks
    group('Direct Monitoring', () => {
      // Check warm instance
      const warmRes = http.get(`${WARM_URL}/api/product/${productId}`);
      if (warmRes.status === 200) {
        warmLatency.add(warmRes.timings.duration);
      }

      // Check cold instance
      const coldRes = http.get(`${COLD_URL}/api/product/${productId}`);
      if (coldRes.status === 200) {
        coldLatency.add(coldRes.timings.duration);
      }
    });
  }

  sleep(0.05);
}

export function setup() {
  console.log('Starting deploy simulation test');
  console.log('');
  console.log('To simulate a deploy, run these commands in another terminal:');
  console.log('');
  console.log('1. Clear the cold instance cache (simulate fresh deploy):');
  console.log('   curl -X POST http://localhost:8002/admin/clear-cache');
  console.log('');
  console.log('2. Start gradual traffic shift to cold instance:');
  console.log('   curl -X POST "http://localhost:8003/traffic/shift?from_instance=warm&to_instance=cold&duration_seconds=60"');
  console.log('');
  console.log('Watch the Grafana dashboard to see the latency impact!');
  console.log('');
}

export function handleSummary(data) {
  const summary = {
    test_duration_seconds: data.state.testRunDurationMs / 1000,
    total_requests: data.metrics.requests ? data.metrics.requests.values.count : 0,
    error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
    load_balanced: {
      p50: data.metrics.lb_latency ? data.metrics.lb_latency.values['p(50)'] : null,
      p95: data.metrics.lb_latency ? data.metrics.lb_latency.values['p(95)'] : null,
      p99: data.metrics.lb_latency ? data.metrics.lb_latency.values['p(99)'] : null,
      max: data.metrics.lb_latency ? data.metrics.lb_latency.values.max : null,
    },
    warm_instance: {
      p50: data.metrics.warm_latency ? data.metrics.warm_latency.values['p(50)'] : null,
      p95: data.metrics.warm_latency ? data.metrics.warm_latency.values['p(95)'] : null,
      p99: data.metrics.warm_latency ? data.metrics.warm_latency.values['p(99)'] : null,
    },
    cold_instance: {
      p50: data.metrics.cold_latency ? data.metrics.cold_latency.values['p(50)'] : null,
      p95: data.metrics.cold_latency ? data.metrics.cold_latency.values['p(95)'] : null,
      p99: data.metrics.cold_latency ? data.metrics.cold_latency.values['p(99)'] : null,
    },
  };

  console.log('\n=== Deploy Simulation Results ===\n');
  console.log(`Test Duration: ${summary.test_duration_seconds.toFixed(1)}s`);
  console.log(`Total Requests: ${summary.total_requests}`);
  console.log(`Error Rate: ${(summary.error_rate * 100).toFixed(2)}%`);
  console.log('\nLoad Balanced Traffic:');
  console.log(`  p50: ${summary.load_balanced.p50?.toFixed(2)}ms`);
  console.log(`  p95: ${summary.load_balanced.p95?.toFixed(2)}ms`);
  console.log(`  p99: ${summary.load_balanced.p99?.toFixed(2)}ms`);
  console.log(`  max: ${summary.load_balanced.max?.toFixed(2)}ms`);
  console.log('\nWarm Instance (direct):');
  console.log(`  p50: ${summary.warm_instance.p50?.toFixed(2)}ms`);
  console.log(`  p95: ${summary.warm_instance.p95?.toFixed(2)}ms`);
  console.log(`  p99: ${summary.warm_instance.p99?.toFixed(2)}ms`);
  console.log('\nCold Instance (direct):');
  console.log(`  p50: ${summary.cold_instance.p50?.toFixed(2)}ms`);
  console.log(`  p95: ${summary.cold_instance.p95?.toFixed(2)}ms`);
  console.log(`  p99: ${summary.cold_instance.p99?.toFixed(2)}ms`);
  console.log('');

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
