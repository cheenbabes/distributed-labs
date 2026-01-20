import http from 'k6/http';
import { check } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

// Metrics for zero-downtime verification
const errorRate = new Rate('errors');
const successfulRequests = new Counter('successful_requests');
const failedRequests = new Counter('failed_requests');
const latencyTrend = new Trend('request_latency');

// High-frequency test to detect any dropped requests
export const options = {
  stages: [
    { duration: '5s', target: 20 },   // Quick ramp up
    { duration: '2m', target: 20 },   // Sustained high load
    { duration: '5s', target: 0 },    // Quick ramp down
  ],
  thresholds: {
    errors: ['rate<0.001'],           // Less than 0.1% error rate for zero-downtime
    http_req_duration: ['p(99)<500'], // 99% under 500ms
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://nginx:80';

export default function () {
  const startTime = Date.now();
  const res = http.get(`${BASE_URL}/api/data`, {
    timeout: '5s',
  });
  const duration = Date.now() - startTime;

  latencyTrend.add(duration);

  const isSuccess = res.status === 200;
  errorRate.add(!isSuccess);

  if (isSuccess) {
    successfulRequests.add(1);
  } else {
    failedRequests.add(1);
    console.log(`[ERROR] Request failed: status=${res.status}, duration=${duration}ms`);
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  // No sleep - continuous requests to detect any gaps
}

export function handleSummary(data) {
  const successful = data.metrics.successful_requests ? data.metrics.successful_requests.values.count : 0;
  const failed = data.metrics.failed_requests ? data.metrics.failed_requests.values.count : 0;
  const total = successful + failed;
  const errorPct = total > 0 ? (failed / total) * 100 : 0;

  const zeroDowntime = errorPct < 0.1;

  console.log('\n========================================');
  console.log('     ZERO-DOWNTIME VERIFICATION');
  console.log('========================================');
  console.log(`Total Requests:     ${total}`);
  console.log(`Successful:         ${successful}`);
  console.log(`Failed:             ${failed}`);
  console.log(`Error Rate:         ${errorPct.toFixed(4)}%`);
  console.log(`p99 Latency:        ${data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(99)'].toFixed(2) : 0}ms`);
  console.log('----------------------------------------');
  if (zeroDowntime) {
    console.log('RESULT: ZERO-DOWNTIME ACHIEVED');
  } else {
    console.log('RESULT: DOWNTIME DETECTED');
  }
  console.log('========================================\n');

  return {
    stdout: JSON.stringify({
      zero_downtime: zeroDowntime,
      total_requests: total,
      successful_requests: successful,
      failed_requests: failed,
      error_rate_percent: errorPct,
      p99_latency_ms: data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(99)'] : 0,
    }, null, 2),
  };
}
