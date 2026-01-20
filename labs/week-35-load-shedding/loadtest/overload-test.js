import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const shedRate = new Rate('shed_rate');
const latencyTrend = new Trend('request_latency');
const p99Latency = new Trend('p99_latency');

// Extreme overload test - designed to break the system
export const options = {
  scenarios: {
    spike_test: {
      executor: 'ramping-arrival-rate',
      startRate: 10,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 200,
      stages: [
        { duration: '30s', target: 50 },   // Ramp to 50 req/s
        { duration: '30s', target: 100 },  // Ramp to 100 req/s (overload)
        { duration: '1m', target: 150 },   // Extreme load
        { duration: '30s', target: 50 },   // Recover
        { duration: '30s', target: 10 },   // Cool down
      ],
    },
  },
  thresholds: {
    // With shedding disabled, we expect the system to struggle
    // These thresholds are intentionally lenient
    http_req_duration: ['p(99)<30000'], // p99 under 30s
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab35-api-service:8000';

export default function () {
  const start = Date.now();
  const res = http.get(`${BASE_URL}/api/process`, {
    timeout: '30s',
  });
  const duration = Date.now() - start;

  latencyTrend.add(res.timings.duration);
  p99Latency.add(duration);

  const wasShed = res.status === 503;
  shedRate.add(wasShed);
  errorRate.add(res.status !== 200 && res.status !== 503);

  if (res.status === 200) {
    check(res, {
      'response is valid': (r) => r.json().service === 'api-service',
    });
  }

  // No sleep - maximum pressure
}

export function handleSummary(data) {
  const shedRateVal = data.metrics.shed_rate ?
    (data.metrics.shed_rate.values.rate * 100).toFixed(2) : 0;
  const errorRateVal = data.metrics.errors ?
    (data.metrics.errors.values.rate * 100).toFixed(2) : 0;
  const p50 = data.metrics.request_latency ?
    (data.metrics.request_latency.values['p(50)'] / 1000).toFixed(2) : 'N/A';
  const p95 = data.metrics.request_latency ?
    (data.metrics.request_latency.values['p(95)'] / 1000).toFixed(2) : 'N/A';
  const p99 = data.metrics.request_latency ?
    (data.metrics.request_latency.values['p(99)'] / 1000).toFixed(2) : 'N/A';

  console.log('\n=== Overload Test Summary ===');
  console.log(`Shed Rate: ${shedRateVal}%`);
  console.log(`Error Rate: ${errorRateVal}%`);
  console.log(`Latency - p50: ${p50}s, p95: ${p95}s, p99: ${p99}s`);
  console.log('============================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
