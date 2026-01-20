/**
 * Apollo Federation Gateway
 *
 * This gateway composes the users, products, and reviews subgraphs
 * into a single unified GraphQL API.
 */

// Initialize tracing BEFORE other imports
import './tracing.js';

import { ApolloServer } from '@apollo/server';
import { expressMiddleware } from '@apollo/server/express4';
import { ApolloGateway, IntrospectAndCompose, RemoteGraphQLDataSource } from '@apollo/gateway';
import express from 'express';
import { register, Counter, Histogram } from 'prom-client';
import { trace, context, SpanStatusCode } from '@opentelemetry/api';

const PORT = process.env.PORT || 4000;
const USERS_SUBGRAPH_URL = process.env.USERS_SUBGRAPH_URL || 'http://localhost:4001/graphql';
const PRODUCTS_SUBGRAPH_URL = process.env.PRODUCTS_SUBGRAPH_URL || 'http://localhost:4002/graphql';
const REVIEWS_SUBGRAPH_URL = process.env.REVIEWS_SUBGRAPH_URL || 'http://localhost:4003/graphql';

const tracer = trace.getTracer('federation-gateway');

// Prometheus metrics
const graphqlRequestsTotal = new Counter({
  name: 'graphql_requests_total',
  help: 'Total number of GraphQL requests',
  labelNames: ['operation_type', 'operation_name', 'status'],
});

const graphqlRequestDuration = new Histogram({
  name: 'graphql_request_duration_seconds',
  help: 'GraphQL request duration in seconds',
  labelNames: ['operation_type', 'operation_name'],
  buckets: [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
});

const subgraphRequestsTotal = new Counter({
  name: 'subgraph_requests_total',
  help: 'Total number of subgraph requests',
  labelNames: ['subgraph', 'status'],
});

const subgraphRequestDuration = new Histogram({
  name: 'subgraph_request_duration_seconds',
  help: 'Subgraph request duration in seconds',
  labelNames: ['subgraph'],
  buckets: [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
});

// Custom RemoteGraphQLDataSource to add tracing and metrics
class TracingDataSource extends RemoteGraphQLDataSource {
  constructor(config) {
    super(config);
    this.subgraphName = config.name || 'unknown';
  }

  async willSendRequest({ request, context: reqContext }) {
    // Propagate trace context to subgraphs
    const activeSpan = trace.getActiveSpan();
    if (activeSpan) {
      const traceId = activeSpan.spanContext().traceId;
      const spanId = activeSpan.spanContext().spanId;
      request.http.headers.set('traceparent', `00-${traceId}-${spanId}-01`);
    }
  }

  async didReceiveResponse({ response, request, context: reqContext }) {
    return response;
  }
}

// Create the gateway
const gateway = new ApolloGateway({
  supergraphSdl: new IntrospectAndCompose({
    subgraphs: [
      { name: 'users', url: USERS_SUBGRAPH_URL },
      { name: 'products', url: PRODUCTS_SUBGRAPH_URL },
      { name: 'reviews', url: REVIEWS_SUBGRAPH_URL },
    ],
  }),
  buildService({ name, url }) {
    return new TracingDataSource({ url, name });
  },
});

// Create Apollo Server
const server = new ApolloServer({
  gateway,
  plugins: [
    {
      async requestDidStart() {
        const startTime = Date.now();
        let operationType = 'unknown';
        let operationName = 'anonymous';

        return {
          async didResolveOperation(requestContext) {
            operationType = requestContext.operation?.operation || 'unknown';
            operationName = requestContext.operationName || 'anonymous';
          },
          async willSendResponse(requestContext) {
            const duration = (Date.now() - startTime) / 1000;
            const hasErrors = requestContext.errors && requestContext.errors.length > 0;

            graphqlRequestsTotal.inc({
              operation_type: operationType,
              operation_name: operationName,
              status: hasErrors ? 'error' : 'success',
            });

            graphqlRequestDuration.observe(
              { operation_type: operationType, operation_name: operationName },
              duration
            );
          },
        };
      },
    },
  ],
});

// Express app
const app = express();

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'federation-gateway' });
});

// Metrics endpoint
app.get('/metrics', async (req, res) => {
  res.set('Content-Type', register.contentType);
  res.end(await register.metrics());
});

// Start the server
async function start() {
  await server.start();

  app.use('/graphql', express.json(), expressMiddleware(server, {
    context: async ({ req }) => ({
      headers: req.headers,
    }),
  }));

  app.listen(PORT, () => {
    console.log(`Federation Gateway ready at http://localhost:${PORT}/graphql`);
    console.log(`Health check at http://localhost:${PORT}/health`);
    console.log(`Metrics at http://localhost:${PORT}/metrics`);
    console.log('Subgraphs:');
    console.log(`  - Users: ${USERS_SUBGRAPH_URL}`);
    console.log(`  - Products: ${PRODUCTS_SUBGRAPH_URL}`);
    console.log(`  - Reviews: ${REVIEWS_SUBGRAPH_URL}`);
  });
}

start().catch(console.error);
