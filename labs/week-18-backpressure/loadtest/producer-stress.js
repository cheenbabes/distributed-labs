import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const producerQueueDepth = new Trend('producer_queue_depth');
const queueMemoryBytes = new Trend('queue_memory_bytes');
const rejectedItems = new Counter('rejected_items');

// Test configuration - stress the producer to observe backpressure
export const options = {
  scenarios: {
    // Scenario 1: Baseline - observe normal operation
    baseline: {
      executor: 'constant-vus',
      vus: 1,
      duration: '30s',
      startTime: '0s',
      env: { SCENARIO: 'baseline' },
    },
    // Scenario 2: Ramp up - increase load to trigger backpressure
    ramp_up: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '30s', target: 5 },
        { duration: '1m', target: 10 },
        { duration: '30s', target: 0 },
      ],
      startTime: '30s',
      env: { SCENARIO: 'ramp_up' },
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<5000'], // Allow higher latency for backpressure scenarios
    errors: ['rate<0.5'],              // Higher error tolerance for backpressure testing
  },
};

const PRODUCER_URL = __ENV.PRODUCER_URL || 'http://lab18-producer:8000';

export function setup() {
  console.log('Backpressure stress test starting...');
  console.log(`Producer URL: ${PRODUCER_URL}`);

  // Check initial status
  const statusRes = http.get(`${PRODUCER_URL}/status`);
  if (statusRes.status === 200) {
    const status = statusRes.json();
    console.log(`Initial queue mode: ${status.queue.mode}`);
    console.log(`Initial queue size: ${status.queue.size}`);
  }

  return { startTime: Date.now() };
}

export default function (data) {
  const scenario = __ENV.SCENARIO || 'unknown';

  // Check producer status
  const statusRes = http.get(`${PRODUCER_URL}/status`);

  if (statusRes.status === 200) {
    const status = statusRes.json();

    // Record queue metrics
    producerQueueDepth.add(status.queue.size);
    queueMemoryBytes.add(status.queue.memory_bytes);

    // Check for concerning conditions
    check(statusRes, {
      'producer is healthy': (r) => r.status === 200,
      'queue is not exploding': () => status.queue.size < 10000,
      'memory is under control': () => status.queue.memory_mb < 200,
    });

    // Log periodically
    if (Math.random() < 0.1) {
      console.log(`[${scenario}] Queue: ${status.queue.size} items, ${status.queue.memory_mb}MB`);
    }
  } else {
    errorRate.add(1);
  }

  // Manually produce items to increase load
  const produceRes = http.post(
    `${PRODUCER_URL}/produce`,
    JSON.stringify({ count: 5 }),
    { headers: { 'Content-Type': 'application/json' } }
  );

  if (produceRes.status === 200) {
    const result = produceRes.json();
    if (result.rejected > 0) {
      rejectedItems.add(result.rejected);
      console.log(`Items rejected: ${result.rejected} (bounded queue full)`);
    }
  }

  errorRate.add(produceRes.status !== 200);

  sleep(0.5);
}

export function teardown(data) {
  const duration = (Date.now() - data.startTime) / 1000;
  console.log(`Test completed in ${duration}s`);

  // Final status check
  const statusRes = http.get(`${PRODUCER_URL}/status`);
  if (statusRes.status === 200) {
    const status = statusRes.json();
    console.log(`Final queue size: ${status.queue.size}`);
    console.log(`Final queue memory: ${status.queue.memory_mb}MB`);
  }
}
