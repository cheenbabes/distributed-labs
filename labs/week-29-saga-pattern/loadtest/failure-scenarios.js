import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const sagaSuccessRate = new Rate('saga_success');
const orderLatency = new Trend('order_latency');
const compensationCount = new Counter('compensations');

const BASE_URL = __ENV.BASE_URL || 'http://lab29-order-service:8000';
const INVENTORY_URL = __ENV.INVENTORY_URL || 'http://lab29-inventory-service:8002';
const PAYMENT_URL = __ENV.PAYMENT_URL || 'http://lab29-payment-service:8003';
const SHIPPING_URL = __ENV.SHIPPING_URL || 'http://lab29-shipping-service:8004';

// Test scenarios for different failure modes
export const options = {
  scenarios: {
    // Scenario 1: All services healthy
    healthy: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 10,
      exec: 'healthyScenario',
      startTime: '0s',
      tags: { scenario: 'healthy' },
    },
    // Scenario 2: Payment failures
    payment_failures: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 10,
      exec: 'paymentFailureScenario',
      startTime: '30s',
      tags: { scenario: 'payment_failure' },
    },
    // Scenario 3: Shipping failures
    shipping_failures: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 10,
      exec: 'shippingFailureScenario',
      startTime: '60s',
      tags: { scenario: 'shipping_failure' },
    },
    // Scenario 4: Recovery
    recovery: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 10,
      exec: 'recoveryScenario',
      startTime: '90s',
      tags: { scenario: 'recovery' },
    },
  },
};

function setFailRate(serviceUrl, rate) {
  const res = http.post(
    `${serviceUrl}/admin/fail-rate`,
    JSON.stringify({ rate: rate }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  return res.status === 200;
}

function resetAllFailRates() {
  setFailRate(INVENTORY_URL, 0);
  setFailRate(PAYMENT_URL, 0);
  setFailRate(SHIPPING_URL, 0);
}

function placeOrder() {
  const order = {
    customer_id: `CUST-${Math.floor(Math.random() * 9000) + 1000}`,
    items: [
      { product_id: 'PROD-001', name: 'Widget Pro', quantity: 2, price: 29.99 },
      { product_id: 'PROD-002', name: 'Gadget Plus', quantity: 1, price: 49.99 },
    ],
  };

  const res = http.post(
    `${BASE_URL}/orders`,
    JSON.stringify(order),
    { headers: { 'Content-Type': 'application/json' } }
  );

  orderLatency.add(res.timings.duration);
  errorRate.add(res.status !== 200);

  if (res.status === 200) {
    const result = res.json();
    sagaSuccessRate.add(result.saga_state === 'completed');

    if (result.saga_state === 'compensated') {
      compensationCount.add(1);
    }

    return result;
  }

  return null;
}

// Scenario 1: All services healthy - sagas should complete
export function healthyScenario() {
  group('Healthy Services', function() {
    resetAllFailRates();

    const result = placeOrder();

    check(result, {
      'saga completed': (r) => r && r.saga_state === 'completed',
      'all steps completed': (r) => r && r.completed_steps.length === 3,
      'no compensation needed': (r) => r && r.compensation_completed.length === 0,
    });

    if (result) {
      console.log(`[HEALTHY] Order ${result.order_id}: ${result.saga_state}`);
    }

    sleep(1);
  });
}

// Scenario 2: Payment service fails - should compensate inventory
export function paymentFailureScenario() {
  group('Payment Failures', function() {
    // Set payment to fail 100% of the time
    resetAllFailRates();
    setFailRate(PAYMENT_URL, 1.0);

    const result = placeOrder();

    check(result, {
      'saga compensated': (r) => r && r.saga_state === 'compensated',
      'failed at payment': (r) => r && r.failed_step === 'process_payment',
      'inventory was reserved': (r) => r && r.completed_steps.includes('reserve_inventory'),
      'inventory was released': (r) => r && r.compensation_completed.includes('reserve_inventory'),
    });

    if (result) {
      console.log(`[PAYMENT_FAIL] Order ${result.order_id}: ${result.saga_state}, compensated: ${result.compensation_completed}`);
    }

    sleep(1);
  });
}

// Scenario 3: Shipping service fails - should compensate payment and inventory
export function shippingFailureScenario() {
  group('Shipping Failures', function() {
    // Set shipping to fail 100% of the time
    resetAllFailRates();
    setFailRate(SHIPPING_URL, 1.0);

    const result = placeOrder();

    check(result, {
      'saga compensated': (r) => r && r.saga_state === 'compensated',
      'failed at shipping': (r) => r && r.failed_step === 'ship_order',
      'inventory was reserved': (r) => r && r.completed_steps.includes('reserve_inventory'),
      'payment was processed': (r) => r && r.completed_steps.includes('process_payment'),
      'payment was refunded': (r) => r && r.compensation_completed.includes('process_payment'),
      'inventory was released': (r) => r && r.compensation_completed.includes('reserve_inventory'),
    });

    if (result) {
      console.log(`[SHIPPING_FAIL] Order ${result.order_id}: ${result.saga_state}, compensated: ${result.compensation_completed}`);
    }

    sleep(1);
  });
}

// Scenario 4: Services recover - sagas should complete again
export function recoveryScenario() {
  group('Recovery', function() {
    // Reset all fail rates
    resetAllFailRates();

    const result = placeOrder();

    check(result, {
      'saga completed after recovery': (r) => r && r.saga_state === 'completed',
      'all steps completed': (r) => r && r.completed_steps.length === 3,
    });

    if (result) {
      console.log(`[RECOVERY] Order ${result.order_id}: ${result.saga_state}`);
    }

    sleep(1);
  });
}

export function teardown(data) {
  // Reset all fail rates at the end of the test
  resetAllFailRates();
  console.log('All fail rates reset to 0');
}

export function handleSummary(data) {
  console.log('\n========== FAILURE SCENARIO TEST SUMMARY ==========');
  console.log(`Total Requests: ${data.metrics.http_reqs.values.count}`);
  console.log(`Saga Success Rate: ${(data.metrics.saga_success.values.rate * 100).toFixed(1)}%`);
  console.log(`Total Compensations: ${data.metrics.compensations ? data.metrics.compensations.values.count : 0}`);
  console.log('====================================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
