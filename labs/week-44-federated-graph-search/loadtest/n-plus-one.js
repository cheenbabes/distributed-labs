/**
 * N+1 Query Detection Load Test
 *
 * This test specifically exercises queries that trigger the N+1 problem.
 * Run this test with and without DataLoader to see the difference.
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('graphql_latency');
const queryCounter = new Counter('queries_executed');

export const options = {
  stages: [
    { duration: '20s', target: 5 },
    { duration: '40s', target: 5 },
    { duration: '20s', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<5000'],
    errors: ['rate<0.2'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://gateway:4000';

// This query fetches all products and their reviews with author details.
// Without DataLoader, this triggers:
// - 1 query for products
// - N queries for reviews (one per product)
// - M queries for authors (one per review)
//
// With DataLoader:
// - 1 query for products
// - 1 batched query for all reviews
// - 1 batched query for all authors
const N_PLUS_ONE_QUERY = `
  query AllProductsWithReviewsAndAuthors {
    products {
      id
      name
      price
      category
      reviews {
        id
        rating
        comment
        createdAt
        author {
          id
          name
          email
          role
        }
      }
      averageRating
      reviewCount
    }
  }
`;

function graphqlRequest(query) {
  const payload = JSON.stringify({
    operationName: 'AllProductsWithReviewsAndAuthors',
    query,
    variables: {},
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
  };

  return http.post(`${BASE_URL}/graphql`, payload, params);
}

export default function () {
  const startTime = Date.now();
  const res = graphqlRequest(N_PLUS_ONE_QUERY);
  const duration = Date.now() - startTime;

  latencyTrend.add(duration);
  queryCounter.add(1);

  const hasErrors = res.status !== 200;
  if (!hasErrors) {
    try {
      const body = res.json();
      if (body.errors && body.errors.length > 0) {
        errorRate.add(true);
      } else {
        errorRate.add(false);
      }
    } catch (e) {
      errorRate.add(true);
    }
  } else {
    errorRate.add(true);
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
    'has products data': (r) => {
      try {
        const body = r.json();
        return body.data && body.data.products && body.data.products.length > 0;
      } catch (e) {
        return false;
      }
    },
    'products have reviews': (r) => {
      try {
        const body = r.json();
        return body.data.products.some(p => p.reviews && p.reviews.length > 0);
      } catch (e) {
        return false;
      }
    },
    'reviews have authors': (r) => {
      try {
        const body = r.json();
        const productWithReviews = body.data.products.find(p => p.reviews && p.reviews.length > 0);
        if (!productWithReviews) return true; // No reviews, skip check
        return productWithReviews.reviews.every(r => r.author && r.author.name);
      } catch (e) {
        return false;
      }
    },
  });

  sleep(0.5);
}

export function handleSummary(data) {
  console.log('\n========================================');
  console.log('N+1 QUERY DETECTION TEST SUMMARY');
  console.log('========================================\n');

  const p50 = data.metrics.http_req_duration?.values?.['p(50)'] || 0;
  const p95 = data.metrics.http_req_duration?.values?.['p(95)'] || 0;
  const p99 = data.metrics.http_req_duration?.values?.['p(99)'] || 0;
  const avg = data.metrics.http_req_duration?.values?.avg || 0;

  console.log(`Latency:
  - Average: ${avg.toFixed(2)}ms
  - p50: ${p50.toFixed(2)}ms
  - p95: ${p95.toFixed(2)}ms
  - p99: ${p99.toFixed(2)}ms

If latency is HIGH (>500ms), DataLoader is likely DISABLED.
If latency is LOW (<200ms), DataLoader is likely ENABLED.

Check Jaeger traces to see the difference in database calls!
`);

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
