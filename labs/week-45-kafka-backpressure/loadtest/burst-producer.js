import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const messagesProduced = new Counter('messages_produced');
const productionLatency = new Trend('production_latency');

// Test configuration - produces messages in bursts to build up consumer lag
export const options = {
  scenarios: {
    // Scenario 1: Steady production
    steady_production: {
      executor: 'constant-arrival-rate',
      rate: 20,  // 20 requests per second
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 10,
      maxVUs: 50,
      exec: 'produceMessage',
      startTime: '0s',
    },
    // Scenario 2: Burst production
    burst_production: {
      executor: 'constant-arrival-rate',
      rate: 100,  // 100 requests per second
      timeUnit: '1s',
      duration: '30s',
      preAllocatedVUs: 20,
      maxVUs: 100,
      exec: 'produceBatch',
      startTime: '2m',  // Start after steady production
    },
    // Scenario 3: Recovery period (lighter load)
    recovery: {
      executor: 'constant-arrival-rate',
      rate: 5,
      timeUnit: '1s',
      duration: '2m',
      preAllocatedVUs: 5,
      maxVUs: 10,
      exec: 'produceMessage',
      startTime: '2m30s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    errors: ['rate<0.1'],
  },
};

const PRODUCER_URL = __ENV.PRODUCER_URL || 'http://producer:8000';

// Produce a single message
export function produceMessage() {
  const res = http.post(`${PRODUCER_URL}/produce`);

  productionLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  if (res.status === 200) {
    messagesProduced.add(1);
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
    'has success field': (r) => {
      try {
        return r.json().success === true;
      } catch {
        return false;
      }
    },
  });
}

// Produce a batch of messages
export function produceBatch() {
  const res = http.post(`${PRODUCER_URL}/produce/batch?count=10`);

  productionLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  if (res.status === 200) {
    try {
      const data = res.json();
      messagesProduced.add(data.successful || 0);
    } catch {
      // ignore
    }
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
    'batch successful': (r) => {
      try {
        const data = r.json();
        return data.successful >= data.total * 0.9;  // At least 90% success
      } catch {
        return false;
      }
    },
  });
}

export function handleSummary(data) {
  const summary = {
    total_messages: data.metrics.messages_produced ? data.metrics.messages_produced.values.count : 0,
    avg_latency_ms: data.metrics.production_latency ? data.metrics.production_latency.values.avg : 0,
    p95_latency_ms: data.metrics.production_latency ? data.metrics.production_latency.values['p(95)'] : 0,
    error_rate: data.metrics.errors ? data.metrics.errors.values.rate : 0,
  };

  console.log('\n=== Production Summary ===');
  console.log(`Total messages produced: ${summary.total_messages}`);
  console.log(`Avg latency: ${summary.avg_latency_ms.toFixed(2)}ms`);
  console.log(`P95 latency: ${summary.p95_latency_ms.toFixed(2)}ms`);
  console.log(`Error rate: ${(summary.error_rate * 100).toFixed(2)}%`);

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
