import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const latencyTrend = new Trend('graphql_latency');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 10 },  // Ramp up to 10 users
    { duration: '1m', target: 10 },   // Stay at 10 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000'], // 95% of requests should be < 2s
    errors: ['rate<0.1'],              // Error rate should be < 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://gateway:4000';

// GraphQL queries
const queries = {
  // Simple query - just products
  products: `
    query Products {
      products {
        id
        name
        price
        category
      }
    }
  `,

  // Federated query - products with reviews (crosses subgraph boundaries)
  productsWithReviews: `
    query ProductsWithReviews {
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
        averageRating
        reviewCount
      }
    }
  `,

  // Deep nested query - triggers N+1 if DataLoader is disabled
  topProductsDeep: `
    query TopProductsDeep {
      topProducts(limit: 5) {
        id
        name
        reviews {
          id
          rating
          comment
          author {
            id
            name
            email
            reviews {
              id
              rating
            }
          }
        }
      }
    }
  `,

  // User query with their reviews
  me: `
    query Me {
      me {
        id
        name
        email
        role
        reviews {
          id
          rating
          comment
          product {
            name
            price
          }
        }
      }
    }
  `,
};

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

  const res = http.post(`${BASE_URL}/graphql`, payload, params);

  // Record metrics
  latencyTrend.add(res.timings.duration);
  const hasErrors = res.status !== 200 || (res.json().errors && res.json().errors.length > 0);
  errorRate.add(hasErrors);

  return res;
}

export default function () {
  // Randomly select a query type to simulate realistic traffic mix
  const queryTypes = [
    { name: 'Products', query: queries.products, weight: 3 },
    { name: 'ProductsWithReviews', query: queries.productsWithReviews, weight: 4 },
    { name: 'TopProductsDeep', query: queries.topProductsDeep, weight: 2 },
    { name: 'Me', query: queries.me, weight: 1 },
  ];

  // Weighted random selection
  const totalWeight = queryTypes.reduce((sum, q) => sum + q.weight, 0);
  let random = Math.random() * totalWeight;
  let selectedQuery = queryTypes[0];

  for (const qt of queryTypes) {
    random -= qt.weight;
    if (random <= 0) {
      selectedQuery = qt;
      break;
    }
  }

  const res = graphqlRequest(selectedQuery.name, selectedQuery.query);

  // Validate response
  check(res, {
    'status is 200': (r) => r.status === 200,
    'no graphql errors': (r) => {
      const body = r.json();
      return !body.errors || body.errors.length === 0;
    },
    'has data': (r) => {
      const body = r.json();
      return body.data !== null && body.data !== undefined;
    },
  });

  // Small pause between requests
  sleep(0.3 + Math.random() * 0.4);
}

export function handleSummary(data) {
  return {
    stdout: JSON.stringify(data, null, 2),
  };
}
