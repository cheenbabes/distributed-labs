import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const webhookLatency = new Trend('webhook_queue_latency');
const webhooksQueued = new Counter('webhooks_queued');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 5 },   // Ramp up to 5 users
    { duration: '1m', target: 5 },    // Stay at 5 users
    { duration: '30s', target: 10 },  // Ramp up to 10 users
    { duration: '1m', target: 10 },   // Stay at 10 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<1000'], // 95% of requests should be < 1s
    errors: ['rate<0.3'],               // Error rate should be < 30% (we have flaky endpoints)
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab33-webhook-sender:8000';

// Endpoints to test
const ENDPOINTS = [
  'http://lab33-receiver-healthy:8001/webhook',
  'http://lab33-receiver-flaky:8002/webhook',
  'http://lab33-receiver-dead:8003/webhook'
];

const EVENT_TYPES = [
  'order.created',
  'order.updated',
  'user.registered',
  'payment.completed',
  'inventory.updated'
];

function randomElement(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function generatePayload() {
  return {
    event_id: `evt_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
    timestamp: new Date().toISOString(),
    data: {
      order_id: `order_${Math.random().toString(36).substr(2, 9)}`,
      customer_id: `cust_${Math.random().toString(36).substr(2, 6)}`,
      amount: Math.floor(Math.random() * 10000) / 100,
      currency: 'USD',
      items: Math.floor(Math.random() * 5) + 1
    }
  };
}

export default function () {
  const endpoint = randomElement(ENDPOINTS);
  const eventType = randomElement(EVENT_TYPES);
  const payload = generatePayload();

  const webhookRequest = {
    endpoint: endpoint,
    event_type: eventType,
    payload: payload
  };

  const res = http.post(`${BASE_URL}/webhooks`, JSON.stringify(webhookRequest), {
    headers: { 'Content-Type': 'application/json' }
  });

  // Record metrics
  webhookLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200 && res.status !== 201);

  // Validate response
  const success = check(res, {
    'status is 200': (r) => r.status === 200,
    'response has webhook id': (r) => {
      try {
        return r.json().id !== undefined;
      } catch (e) {
        return false;
      }
    },
    'response has signature': (r) => {
      try {
        return r.json().signature !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  if (success) {
    webhooksQueued.add(1);
  }

  // Small pause between requests
  sleep(0.2 + Math.random() * 0.3);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
