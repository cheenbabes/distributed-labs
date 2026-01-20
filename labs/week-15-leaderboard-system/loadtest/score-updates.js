import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const scoreUpdateLatency = new Trend('score_update_latency');
const rankQueryLatency = new Trend('rank_query_latency');
const topNQueryLatency = new Trend('top_n_query_latency');
const operationsCounter = new Counter('total_operations');

// Test configuration - simulates high-volume gaming leaderboard usage
export const options = {
  scenarios: {
    // Scenario 1: High-volume score updates (majority of traffic)
    score_updates: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 50 },   // Ramp up to 50 users
        { duration: '2m', target: 100 },   // Ramp to 100 users
        { duration: '1m', target: 100 },   // Stay at 100
        { duration: '30s', target: 0 },    // Ramp down
      ],
      exec: 'scoreUpdates',
    },
    // Scenario 2: Rank queries (players checking their rank)
    rank_queries: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 20 },
        { duration: '2m', target: 30 },
        { duration: '1m', target: 30 },
        { duration: '30s', target: 0 },
      ],
      exec: 'rankQueries',
    },
    // Scenario 3: Top N queries (leaderboard page views)
    top_n_queries: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 10 },
        { duration: '2m', target: 20 },
        { duration: '1m', target: 20 },
        { duration: '30s', target: 0 },
      ],
      exec: 'topNQueries',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<100'],  // 95% of requests should be < 100ms
    errors: ['rate<0.01'],              // Error rate should be < 1%
    score_update_latency: ['p(99)<50'], // 99% of score updates < 50ms
    rank_query_latency: ['p(99)<50'],   // 99% of rank queries < 50ms
    top_n_query_latency: ['p(99)<100'], // 99% of top N queries < 100ms
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://lab15-leaderboard-api:8000';

// Generate a random player ID from a pool of simulated players
function getPlayerId() {
  const playerCount = __ENV.PLAYER_POOL_SIZE ? parseInt(__ENV.PLAYER_POOL_SIZE) : 10000;
  const playerId = Math.floor(Math.random() * playerCount);
  return `player_${playerId.toString().padStart(6, '0')}`;
}

// Get a random leaderboard type
function getLeaderboardType() {
  const types = ['all_time', 'weekly', 'daily'];
  return types[Math.floor(Math.random() * types.length)];
}

// Scenario 1: High-volume score updates
export function scoreUpdates() {
  const playerId = getPlayerId();
  const leaderboard = getLeaderboardType();
  const increment = Math.floor(Math.random() * 100) + 1;  // Random score 1-100

  const url = `${BASE_URL}/players/${playerId}/score?leaderboard=${leaderboard}`;
  const payload = JSON.stringify({ increment: increment });
  const params = {
    headers: { 'Content-Type': 'application/json' },
    tags: { operation: 'score_update' },
  };

  const startTime = new Date().getTime();
  const res = http.post(url, payload, params);
  const duration = new Date().getTime() - startTime;

  scoreUpdateLatency.add(duration);
  operationsCounter.add(1);
  errorRate.add(res.status !== 200);

  check(res, {
    'score update status is 200': (r) => r.status === 200,
    'score update has player_id': (r) => r.json().player_id !== undefined,
    'score update has new score': (r) => r.json().score !== undefined,
    'score update has rank': (r) => r.json().rank !== undefined,
  });

  // Small pause between requests (simulating player actions)
  sleep(0.1 + Math.random() * 0.2);
}

// Scenario 2: Rank queries (players checking their position)
export function rankQueries() {
  const playerId = getPlayerId();
  const leaderboard = getLeaderboardType();

  const url = `${BASE_URL}/players/${playerId}/rank?leaderboard=${leaderboard}`;
  const params = {
    tags: { operation: 'rank_query' },
  };

  const startTime = new Date().getTime();
  const res = http.get(url, params);
  const duration = new Date().getTime() - startTime;

  rankQueryLatency.add(duration);
  operationsCounter.add(1);

  // 404 is acceptable if player doesn't exist
  const isValidResponse = res.status === 200 || res.status === 404;
  errorRate.add(!isValidResponse);

  if (res.status === 200) {
    check(res, {
      'rank query has player_id': (r) => r.json().player_id !== undefined,
      'rank query has rank': (r) => r.json().rank !== undefined,
      'rank query has score': (r) => r.json().score !== undefined,
    });
  }

  // Simulate thinking time before next query
  sleep(0.5 + Math.random() * 1.0);
}

// Scenario 3: Top N queries (leaderboard page views)
export function topNQueries() {
  const leaderboard = getLeaderboardType();
  // Vary the page size - most common are 10, 25, 50, 100
  const pageSizes = [10, 25, 50, 100];
  const n = pageSizes[Math.floor(Math.random() * pageSizes.length)];

  const url = `${BASE_URL}/leaderboard/top?n=${n}&leaderboard=${leaderboard}`;
  const params = {
    tags: { operation: 'top_n_query' },
  };

  const startTime = new Date().getTime();
  const res = http.get(url, params);
  const duration = new Date().getTime() - startTime;

  topNQueryLatency.add(duration);
  operationsCounter.add(1);
  errorRate.add(res.status !== 200);

  check(res, {
    'top N status is 200': (r) => r.status === 200,
    'top N returns array': (r) => Array.isArray(r.json()),
    'top N has correct size': (r) => r.json().length <= n,
    'top N entries have rank': (r) => r.json().length === 0 || r.json()[0].rank !== undefined,
    'top N entries have score': (r) => r.json().length === 0 || r.json()[0].score !== undefined,
  });

  // Simulate page view time before next request
  sleep(1.0 + Math.random() * 2.0);
}

// Additional standalone test for around-player queries
export function aroundPlayerQueries() {
  const playerId = getPlayerId();
  const leaderboard = getLeaderboardType();

  const url = `${BASE_URL}/leaderboard/around/${playerId}?range=5&leaderboard=${leaderboard}`;
  const params = {
    tags: { operation: 'around_query' },
  };

  const res = http.get(url, params);

  const isValidResponse = res.status === 200 || res.status === 404;
  errorRate.add(!isValidResponse);

  if (res.status === 200) {
    check(res, {
      'around query returns array': (r) => Array.isArray(r.json()),
      'around query has entries': (r) => r.json().length > 0,
    });
  }

  sleep(0.5);
}

export function handleSummary(data) {
  console.log('\n========== LEADERBOARD LOAD TEST SUMMARY ==========\n');

  // HTTP request summary
  const httpReqs = data.metrics.http_reqs;
  if (httpReqs && httpReqs.values) {
    console.log(`Total Requests: ${httpReqs.values.count}`);
    console.log(`Requests/sec: ${httpReqs.values.rate.toFixed(2)}`);
  }

  // Latency by operation type
  console.log('\n--- Latency by Operation (ms) ---');

  if (data.metrics.score_update_latency && data.metrics.score_update_latency.values) {
    const su = data.metrics.score_update_latency.values;
    console.log(`Score Updates:  p50=${su.med?.toFixed(2) || 'N/A'}  p95=${su['p(95)']?.toFixed(2) || 'N/A'}  p99=${su['p(99)']?.toFixed(2) || 'N/A'}`);
  }

  if (data.metrics.rank_query_latency && data.metrics.rank_query_latency.values) {
    const rq = data.metrics.rank_query_latency.values;
    console.log(`Rank Queries:   p50=${rq.med?.toFixed(2) || 'N/A'}  p95=${rq['p(95)']?.toFixed(2) || 'N/A'}  p99=${rq['p(99)']?.toFixed(2) || 'N/A'}`);
  }

  if (data.metrics.top_n_query_latency && data.metrics.top_n_query_latency.values) {
    const tn = data.metrics.top_n_query_latency.values;
    console.log(`Top N Queries:  p50=${tn.med?.toFixed(2) || 'N/A'}  p95=${tn['p(95)']?.toFixed(2) || 'N/A'}  p99=${tn['p(99)']?.toFixed(2) || 'N/A'}`);
  }

  // Error rate
  if (data.metrics.errors && data.metrics.errors.values) {
    const errRate = (data.metrics.errors.values.rate * 100).toFixed(2);
    console.log(`\nError Rate: ${errRate}%`);
  }

  console.log('\n====================================================\n');

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
