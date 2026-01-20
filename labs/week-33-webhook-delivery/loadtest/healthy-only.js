import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const webhookLatency = new Trend('webhook_queue_latency');

// Test configuration - only send to healthy endpoint
export const options = {
  stages: [
    { duration: '20s', target: 5 },   // Ramp up
    { duration: '40s', target: 5 },   // Stay steady
    { duration: '20s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
    errors: ['rate<0.05'],  // Should have almost no errors
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab33-webhook-sender:8000';
const HEALTHY_ENDPOINT = 'http://lab33-receiver-healthy:8001/webhook';

const EVENT_TYPES = [
  'order.created',
  'order.updated',
  'payment.completed'
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
      amount: Math.floor(Math.random() * 10000) / 100
    }
  };
}

export default function () {
  const eventType = randomElement(EVENT_TYPES);
  const payload = generatePayload();

  const webhookRequest = {
    endpoint: HEALTHY_ENDPOINT,
    event_type: eventType,
    payload: payload
  };

  const res = http.post(`${BASE_URL}/webhooks`, JSON.stringify(webhookRequest), {
    headers: { 'Content-Type': 'application/json' }
  });

  webhookLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  check(res, {
    'status is 200': (r) => r.status === 200,
    'has webhook id': (r) => {
      try {
        return r.json().id !== undefined;
      } catch (e) {
        return false;
      }
    }
  });

  sleep(0.3);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
