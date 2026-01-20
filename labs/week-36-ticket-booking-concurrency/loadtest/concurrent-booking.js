import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';
import { SharedArray } from 'k6/data';

// Custom metrics
const doubleBookings = new Counter('double_bookings');
const successfulBookings = new Counter('successful_bookings');
const failedBookings = new Counter('failed_bookings');
const bookingLatency = new Trend('booking_latency');
const errorRate = new Rate('errors');

// Generate seat numbers (A1-J10 = 100 seats)
const seats = new SharedArray('seats', function() {
  const seatList = [];
  for (let row = 0; row < 10; row++) {
    for (let num = 1; num <= 10; num++) {
      seatList.push(String.fromCharCode(65 + row) + num);
    }
  }
  return seatList;
});

// Test configuration
export const options = {
  scenarios: {
    // Scenario 1: Concurrent booking race condition test
    race_condition_test: {
      executor: 'per-vu-iterations',
      vus: 20,           // 20 concurrent users
      iterations: 5,     // Each VU makes 5 booking attempts
      maxDuration: '2m',
      exec: 'raceConditionTest',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    errors: ['rate<0.5'],  // Some failures expected due to race conditions
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab36-booking-service:8000';

// Race condition test - multiple VUs try to book same seats
export function raceConditionTest() {
  const customerId = `customer_${__VU}_${__ITER}_${Date.now()}`;

  // Each VU tries to book a subset of seats
  // With 20 VUs targeting overlapping seats, we should see race conditions
  const seatIndex = Math.floor(Math.random() * 20);  // Target first 20 seats
  const seatNumber = seats[seatIndex];

  const payload = JSON.stringify({
    event_id: 1,
    seat_number: seatNumber,
    customer_id: customerId,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
  };

  const startTime = Date.now();
  const res = http.post(`${BASE_URL}/book`, payload, params);
  const duration = Date.now() - startTime;

  bookingLatency.add(duration);

  const success = check(res, {
    'status is 200': (r) => r.status === 200,
    'has status field': (r) => {
      try {
        return r.json().status !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  if (!success) {
    errorRate.add(1);
    return;
  }

  errorRate.add(0);

  try {
    const body = res.json();
    if (body.status === 'confirmed') {
      successfulBookings.add(1);
    } else if (body.status === 'double_booked') {
      doubleBookings.add(1);
      console.log(`DOUBLE BOOKING: Seat ${seatNumber} - ${body.message}`);
    } else if (body.status === 'failed') {
      failedBookings.add(1);
    }
  } catch (e) {
    console.log(`Error parsing response: ${e}`);
  }

  sleep(0.1);
}

// High concurrency test - stress test the booking system
export function highConcurrencyTest() {
  const customerId = `customer_${__VU}_${__ITER}_${Date.now()}`;
  const seatIndex = Math.floor(Math.random() * 100);
  const seatNumber = seats[seatIndex];

  const payload = JSON.stringify({
    event_id: 1,
    seat_number: seatNumber,
    customer_id: customerId,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
  };

  const res = http.post(`${BASE_URL}/book`, payload, params);

  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  sleep(0.05);
}

export function setup() {
  // Reset the event before starting
  const resetRes = http.post(`${BASE_URL}/admin/reset?event_id=1`);
  console.log(`Reset response: ${resetRes.status}`);

  // Get current strategy
  const strategyRes = http.get(`${BASE_URL}/admin/strategy`);
  if (strategyRes.status === 200) {
    console.log(`Current strategy: ${strategyRes.json().strategy}`);
  }

  return { startTime: Date.now() };
}

export function teardown(data) {
  // Check for double bookings
  const dbRes = http.get(`${BASE_URL}/admin/double-bookings`);
  if (dbRes.status === 200) {
    const body = dbRes.json();
    console.log(`\n========================================`);
    console.log(`FINAL RESULTS`);
    console.log(`========================================`);
    console.log(`Double bookings detected: ${body.count}`);
    if (body.count > 0) {
      console.log(`Double booked seats:`);
      body.double_bookings.forEach(db => {
        console.log(`  Seat ${db.seat_number}: booked by ${db.customers.join(', ')}`);
      });
    }
  }

  // Get stats
  const statsRes = http.get(`${BASE_URL}/admin/stats`);
  if (statsRes.status === 200) {
    const stats = statsRes.json();
    console.log(`\nBooking Statistics:`);
    console.log(`  Confirmed: ${stats.totals.confirmed}`);
    console.log(`  Failed: ${stats.totals.failed}`);
    console.log(`  Total: ${stats.totals.total}`);
    console.log(`  Strategy: ${stats.current_strategy}`);
  }

  const duration = (Date.now() - data.startTime) / 1000;
  console.log(`\nTest duration: ${duration.toFixed(2)}s`);
  console.log(`========================================\n`);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify({
      metrics: {
        double_bookings: data.metrics.double_bookings?.values?.count || 0,
        successful_bookings: data.metrics.successful_bookings?.values?.count || 0,
        failed_bookings: data.metrics.failed_bookings?.values?.count || 0,
        booking_latency_avg: data.metrics.booking_latency?.values?.avg || 0,
        booking_latency_p95: data.metrics.booking_latency?.values['p(95)'] || 0,
        http_reqs: data.metrics.http_reqs?.values?.count || 0,
      },
    }, null, 2),
  };
}
