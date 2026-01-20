import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { SharedArray } from 'k6/data';

// Custom metrics per strategy
const metrics = {
  none: {
    doubleBookings: new Counter('none_double_bookings'),
    successful: new Counter('none_successful'),
    failed: new Counter('none_failed'),
    latency: new Trend('none_latency'),
  },
  pessimistic: {
    doubleBookings: new Counter('pessimistic_double_bookings'),
    successful: new Counter('pessimistic_successful'),
    failed: new Counter('pessimistic_failed'),
    latency: new Trend('pessimistic_latency'),
  },
  optimistic: {
    doubleBookings: new Counter('optimistic_double_bookings'),
    successful: new Counter('optimistic_successful'),
    failed: new Counter('optimistic_failed'),
    latency: new Trend('optimistic_latency'),
  },
  distributed: {
    doubleBookings: new Counter('distributed_double_bookings'),
    successful: new Counter('distributed_successful'),
    failed: new Counter('distributed_failed'),
    latency: new Trend('distributed_latency'),
  },
};

// Generate seat numbers
const seats = new SharedArray('seats', function() {
  const seatList = [];
  for (let row = 0; row < 10; row++) {
    for (let num = 1; num <= 10; num++) {
      seatList.push(String.fromCharCode(65 + row) + num);
    }
  }
  return seatList;
});

export const options = {
  scenarios: {
    // Test each strategy sequentially
    strategy_comparison: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 1,
      exec: 'compareStrategies',
      maxDuration: '10m',
    },
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab36-booking-service:8000';

function setStrategy(strategy) {
  const res = http.post(
    `${BASE_URL}/admin/strategy`,
    JSON.stringify({ strategy: strategy }),
    { headers: { 'Content-Type': 'application/json' } }
  );
  return res.status === 200;
}

function resetEvent() {
  return http.post(`${BASE_URL}/admin/reset?event_id=1`);
}

function getStats() {
  const res = http.get(`${BASE_URL}/admin/stats`);
  if (res.status === 200) {
    return res.json();
  }
  return null;
}

function getDoubleBookings() {
  const res = http.get(`${BASE_URL}/admin/double-bookings`);
  if (res.status === 200) {
    return res.json();
  }
  return null;
}

function runConcurrentBookings(strategy, vus, iterations) {
  console.log(`\n--- Testing ${strategy} strategy with ${vus} concurrent users ---`);

  // Create parallel booking requests
  const requests = [];
  for (let i = 0; i < vus * iterations; i++) {
    const customerId = `customer_${strategy}_${i}_${Date.now()}`;
    const seatIndex = i % 20;  // Target first 20 seats for collision
    const seatNumber = seats[seatIndex];

    requests.push({
      method: 'POST',
      url: `${BASE_URL}/book`,
      body: JSON.stringify({
        event_id: 1,
        seat_number: seatNumber,
        customer_id: customerId,
      }),
      params: {
        headers: { 'Content-Type': 'application/json' },
      },
    });
  }

  // Execute in batches
  const batchSize = vus;
  const strategyMetrics = metrics[strategy];

  for (let i = 0; i < requests.length; i += batchSize) {
    const batch = requests.slice(i, i + batchSize);
    const startTime = Date.now();
    const responses = http.batch(batch);
    const batchLatency = Date.now() - startTime;

    responses.forEach(res => {
      if (res.status === 200) {
        try {
          const body = JSON.parse(res.body);
          strategyMetrics.latency.add(batchLatency / batchSize);

          if (body.status === 'confirmed') {
            strategyMetrics.successful.add(1);
          } else if (body.status === 'double_booked') {
            strategyMetrics.doubleBookings.add(1);
          } else if (body.status === 'failed') {
            strategyMetrics.failed.add(1);
          }
        } catch (e) {
          console.log(`Parse error: ${e}`);
        }
      }
    });
  }
}

export function compareStrategies() {
  const strategies = ['none', 'pessimistic', 'optimistic', 'distributed'];
  const results = {};

  for (const strategy of strategies) {
    group(`Testing ${strategy} strategy`, () => {
      // Reset and set strategy
      resetEvent();
      sleep(1);
      setStrategy(strategy);
      sleep(1);

      // Run concurrent bookings
      runConcurrentBookings(strategy, 10, 5);
      sleep(1);

      // Collect results
      const stats = getStats();
      const doubleBookings = getDoubleBookings();

      results[strategy] = {
        stats: stats,
        doubleBookings: doubleBookings?.count || 0,
      };

      console.log(`\n${strategy.toUpperCase()} RESULTS:`);
      console.log(`  Confirmed: ${stats?.totals?.confirmed || 0}`);
      console.log(`  Failed: ${stats?.totals?.failed || 0}`);
      console.log(`  Double Bookings: ${doubleBookings?.count || 0}`);
    });

    sleep(2);
  }

  console.log(`\n========================================`);
  console.log(`COMPARISON SUMMARY`);
  console.log(`========================================`);
  for (const [strategy, data] of Object.entries(results)) {
    const status = data.doubleBookings > 0 ? 'UNSAFE' : 'SAFE';
    console.log(`${strategy}: ${status} (${data.doubleBookings} double bookings)`);
  }
  console.log(`========================================\n`);
}

export function handleSummary(data) {
  const summary = {
    strategies: {},
  };

  for (const strategy of ['none', 'pessimistic', 'optimistic', 'distributed']) {
    summary.strategies[strategy] = {
      double_bookings: data.metrics[`${strategy}_double_bookings`]?.values?.count || 0,
      successful: data.metrics[`${strategy}_successful`]?.values?.count || 0,
      failed: data.metrics[`${strategy}_failed`]?.values?.count || 0,
      avg_latency: data.metrics[`${strategy}_latency`]?.values?.avg || 0,
    };
  }

  return {
    stdout: JSON.stringify(summary, null, 2),
  };
}
