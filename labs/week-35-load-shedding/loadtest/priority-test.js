import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics per priority
const vipSuccessRate = new Rate('vip_success_rate');
const highSuccessRate = new Rate('high_success_rate');
const normalSuccessRate = new Rate('normal_success_rate');
const lowSuccessRate = new Rate('low_success_rate');

const vipLatency = new Trend('vip_latency');
const highLatency = new Trend('high_latency');
const normalLatency = new Trend('normal_latency');
const lowLatency = new Trend('low_latency');

const vipShed = new Counter('vip_shed');
const highShed = new Counter('high_shed');
const normalShed = new Counter('normal_shed');
const lowShed = new Counter('low_shed');

// Test configuration - heavy load to trigger priority shedding
export const options = {
  stages: [
    { duration: '20s', target: 30 },   // Ramp up
    { duration: '2m', target: 60 },    // Heavy load
    { duration: '20s', target: 0 },    // Ramp down
  ],
  thresholds: {
    vip_success_rate: ['rate>0.95'],   // VIP should have 95%+ success
    high_success_rate: ['rate>0.80'],  // HIGH should have 80%+ success
    // NORMAL and LOW may have lower success under load
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab35-api-service:8000';

// Priority distribution: 10% VIP, 20% HIGH, 50% NORMAL, 20% LOW
const priorities = [
  { name: 'vip', weight: 10 },
  { name: 'high', weight: 20 },
  { name: 'normal', weight: 50 },
  { name: 'low', weight: 20 },
];

function selectPriority() {
  const rand = Math.random() * 100;
  let cumulative = 0;
  for (const p of priorities) {
    cumulative += p.weight;
    if (rand < cumulative) {
      return p.name;
    }
  }
  return 'normal';
}

export default function () {
  const priority = selectPriority();
  const headers = { 'X-Priority': priority };

  const res = http.get(`${BASE_URL}/api/process`, { headers });

  const success = res.status === 200;
  const shed = res.status === 503;

  // Record metrics by priority
  switch (priority) {
    case 'vip':
      vipSuccessRate.add(success);
      vipLatency.add(res.timings.duration);
      if (shed) vipShed.add(1);
      break;
    case 'high':
      highSuccessRate.add(success);
      highLatency.add(res.timings.duration);
      if (shed) highShed.add(1);
      break;
    case 'normal':
      normalSuccessRate.add(success);
      normalLatency.add(res.timings.duration);
      if (shed) normalShed.add(1);
      break;
    case 'low':
      lowSuccessRate.add(success);
      lowLatency.add(res.timings.duration);
      if (shed) lowShed.add(1);
      break;
  }

  if (success) {
    check(res, {
      'response has priority': (r) => r.json().priority === priority,
    });
  }

  sleep(0.1);
}

export function handleSummary(data) {
  console.log('\n=== Priority-Based Shedding Summary ===');

  const metrics = [
    { name: 'VIP', success: 'vip_success_rate', shed: 'vip_shed', latency: 'vip_latency' },
    { name: 'HIGH', success: 'high_success_rate', shed: 'high_shed', latency: 'high_latency' },
    { name: 'NORMAL', success: 'normal_success_rate', shed: 'normal_shed', latency: 'normal_latency' },
    { name: 'LOW', success: 'low_success_rate', shed: 'low_shed', latency: 'low_latency' },
  ];

  for (const m of metrics) {
    const successRate = data.metrics[m.success] ?
      (data.metrics[m.success].values.rate * 100).toFixed(1) : 'N/A';
    const shedCount = data.metrics[m.shed] ?
      data.metrics[m.shed].values.count : 0;
    const p50 = data.metrics[m.latency] ?
      data.metrics[m.latency].values['p(50)'].toFixed(0) : 'N/A';

    console.log(`${m.name}: Success=${successRate}%, Shed=${shedCount}, p50=${p50}ms`);
  }

  console.log('=======================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
