/**
 * Subgraph Failure Resilience Test
 *
 * Tests how the federated gateway handles subgraph failures.
 * Enable failures in the reviews subgraph to see partial responses.
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('graphql_errors');
const partialResponseRate = new Rate('partial_responses');
const fullSuccessRate = new Rate('full_success');
const requestCounter = new Counter('total_requests');

export const options = {
  stages: [
    { duration: '15s', target: 5 },
    { duration: '30s', target: 5 },
    { duration: '15s', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<10000'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://gateway:4000';

// Query that touches all subgraphs
const FEDERATED_QUERY = `
  query FederatedQuery {
    products {
      id
      name
      price
      reviews {
        id
        rating
        comment
        author {
          name
        }
      }
    }
    users {
      id
      name
      reviews {
        id
        rating
      }
    }
  }
`;

// Query that only touches products (should succeed even if reviews fail)
const PRODUCTS_ONLY_QUERY = `
  query ProductsOnly {
    products {
      id
      name
      price
      category
      inStock
    }
  }
`;

function graphqlRequest(operationName, query) {
  const payload = JSON.stringify({
    operationName,
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
  requestCounter.add(1);

  // Alternate between queries that need reviews and those that don't
  const useFullQuery = Math.random() > 0.3;

  let res;
  if (useFullQuery) {
    res = graphqlRequest('FederatedQuery', FEDERATED_QUERY);
  } else {
    res = graphqlRequest('ProductsOnly', PRODUCTS_ONLY_QUERY);
  }

  if (res.status !== 200) {
    errorRate.add(true);
    partialResponseRate.add(false);
    fullSuccessRate.add(false);
    return;
  }

  try {
    const body = res.json();

    if (body.errors && body.errors.length > 0) {
      // Has errors but might still have partial data
      if (body.data && Object.keys(body.data).length > 0) {
        // Partial success - got some data despite errors
        partialResponseRate.add(true);
        fullSuccessRate.add(false);
        errorRate.add(false); // Not a complete failure
      } else {
        // Complete failure
        errorRate.add(true);
        partialResponseRate.add(false);
        fullSuccessRate.add(false);
      }
    } else {
      // Full success
      fullSuccessRate.add(true);
      partialResponseRate.add(false);
      errorRate.add(false);
    }
  } catch (e) {
    errorRate.add(true);
    partialResponseRate.add(false);
    fullSuccessRate.add(false);
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
    'response is valid JSON': (r) => {
      try {
        r.json();
        return true;
      } catch (e) {
        return false;
      }
    },
  });

  sleep(0.3);
}

export function handleSummary(data) {
  console.log('\n========================================');
  console.log('SUBGRAPH FAILURE RESILIENCE TEST');
  console.log('========================================\n');

  const fullSuccess = data.metrics.full_success?.values?.rate || 0;
  const partial = data.metrics.partial_responses?.values?.rate || 0;
  const errors = data.metrics.graphql_errors?.values?.rate || 0;

  console.log(`Results:
  - Full Success Rate: ${(fullSuccess * 100).toFixed(1)}%
  - Partial Response Rate: ${(partial * 100).toFixed(1)}%
  - Error Rate: ${(errors * 100).toFixed(1)}%

Interpretation:
- If full_success is high, the system is healthy
- If partial_responses is high, the gateway is gracefully handling failures
- If graphql_errors is high, there are unrecoverable failures
`);

  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
