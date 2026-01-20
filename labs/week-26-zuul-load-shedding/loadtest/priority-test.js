import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics for VIP traffic
const vipSuccessRate = new Rate('vip_success');
const normalSuccessRate = new Rate('normal_success');
const vipLatency = new Trend('vip_latency');
const normalLatency = new Trend('normal_latency');
const vipShedCounter = new Counter('vip_shed');
const normalShedCounter = new Counter('normal_shed');

// Test configuration - high load to trigger shedding
export const options = {
  stages: [
    { duration: '20s', target: 30 },  // Ramp up
    { duration: '1m', target: 60 },   // High load - should trigger shedding
    { duration: '1m', target: 60 },   // Stay at high load
    { duration: '20s', target: 0 },   // Ramp down
  ],
  thresholds: {
    vip_success: ['rate>0.8'],        // VIP should have >80% success
    normal_success: ['rate>0.3'],     // Normal traffic may be shed more
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab26-gateway:8000';

export default function () {
  // 20% of traffic is VIP
  const isVip = Math.random() < 0.2;
  const headers = isVip ? { 'X-Priority': 'vip' } : {};

  const res = http.get(`${BASE_URL}/api/process`, { headers });

  const isSuccess = res.status === 200;
  const isShed = res.status === 503;

  if (isVip) {
    vipSuccessRate.add(isSuccess);
    vipLatency.add(res.timings.duration);
    if (isShed) vipShedCounter.add(1);
  } else {
    normalSuccessRate.add(isSuccess);
    normalLatency.add(res.timings.duration);
    if (isShed) normalShedCounter.add(1);
  }

  // Validate response
  check(res, {
    'status is valid': (r) => r.status === 200 || r.status === 503 || r.status === 504,
  });

  sleep(0.1);
}

export function handleSummary(data) {
  return {
    stdout: prioritySummary(data),
  };
}

function prioritySummary(data) {
  const summary = [];
  summary.push('\n=== Priority-Based Load Shedding Test Summary ===\n');

  const vipSuccess = data.metrics.vip_success;
  const normalSuccess = data.metrics.normal_success;

  if (vipSuccess) {
    summary.push(`VIP Traffic:`);
    summary.push(`  Success Rate: ${(vipSuccess.values.rate * 100).toFixed(2)}%`);
  }

  const vipLat = data.metrics.vip_latency;
  if (vipLat && vipLat.values['p(95)']) {
    summary.push(`  p95 Latency: ${vipLat.values['p(95)'].toFixed(0)}ms`);
  }

  const vipShed = data.metrics.vip_shed;
  if (vipShed) {
    summary.push(`  Shed: ${vipShed.values.count}`);
  }

  if (normalSuccess) {
    summary.push(`\nNormal Traffic:`);
    summary.push(`  Success Rate: ${(normalSuccess.values.rate * 100).toFixed(2)}%`);
  }

  const normalLat = data.metrics.normal_latency;
  if (normalLat && normalLat.values['p(95)']) {
    summary.push(`  p95 Latency: ${normalLat.values['p(95)'].toFixed(0)}ms`);
  }

  const normalShed = data.metrics.normal_shed;
  if (normalShed) {
    summary.push(`  Shed: ${normalShed.values.count}`);
  }

  if (vipSuccess && normalSuccess) {
    const ratio = vipSuccess.values.rate / Math.max(normalSuccess.values.rate, 0.001);
    summary.push(`\nVIP Advantage Ratio: ${ratio.toFixed(2)}x`);
  }

  summary.push('\n=================================================\n');
  return summary.join('\n');
}
