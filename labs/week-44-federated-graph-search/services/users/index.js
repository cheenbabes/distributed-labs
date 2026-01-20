/**
 * Users Subgraph
 *
 * Manages user data and provides the User type for federation.
 * Demonstrates DataLoader pattern for batching user lookups.
 */

// Initialize tracing BEFORE other imports
import './tracing.js';

import { ApolloServer } from '@apollo/server';
import { expressMiddleware } from '@apollo/server/express4';
import { buildSubgraphSchema } from '@apollo/subgraph';
import { gql } from 'graphql-tag';
import express from 'express';
import DataLoader from 'dataloader';
import Redis from 'ioredis';
import { register, Counter, Histogram } from 'prom-client';
import { trace } from '@opentelemetry/api';

const PORT = process.env.PORT || 4001;
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const DISABLE_DATALOADER = process.env.DISABLE_DATALOADER === 'true';
const DB_LATENCY_MS = parseInt(process.env.DB_LATENCY_MS || '10', 10);

const tracer = trace.getTracer('users-subgraph');

// Redis client
const redis = new Redis(REDIS_URL);

// Prometheus metrics
const dbQueriesTotal = new Counter({
  name: 'users_db_queries_total',
  help: 'Total number of database queries',
  labelNames: ['operation', 'batched'],
});

const dbQueryDuration = new Histogram({
  name: 'users_db_query_duration_seconds',
  help: 'Database query duration in seconds',
  labelNames: ['operation'],
  buckets: [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
});

// Sample user data
const sampleUsers = [
  { id: '1', name: 'Alice Johnson', email: 'alice@example.com', role: 'ADMIN' },
  { id: '2', name: 'Bob Smith', email: 'bob@example.com', role: 'USER' },
  { id: '3', name: 'Charlie Brown', email: 'charlie@example.com', role: 'USER' },
  { id: '4', name: 'Diana Prince', email: 'diana@example.com', role: 'MODERATOR' },
  { id: '5', name: 'Eve Wilson', email: 'eve@example.com', role: 'USER' },
  { id: '6', name: 'Frank Miller', email: 'frank@example.com', role: 'USER' },
  { id: '7', name: 'Grace Lee', email: 'grace@example.com', role: 'ADMIN' },
  { id: '8', name: 'Henry Davis', email: 'henry@example.com', role: 'USER' },
];

// Initialize Redis with sample data
async function seedData() {
  for (const user of sampleUsers) {
    await redis.hset(`user:${user.id}`, user);
  }
  console.log(`Seeded ${sampleUsers.length} users to Redis`);
}

// Simulate database latency
async function simulateLatency() {
  if (DB_LATENCY_MS > 0) {
    await new Promise(resolve => setTimeout(resolve, DB_LATENCY_MS));
  }
}

// Database operations
async function getUserById(id) {
  return tracer.startActiveSpan('db.getUserById', async (span) => {
    span.setAttribute('user.id', id);
    const startTime = Date.now();

    await simulateLatency();
    const user = await redis.hgetall(`user:${id}`);

    const duration = (Date.now() - startTime) / 1000;
    dbQueriesTotal.inc({ operation: 'get_user', batched: 'false' });
    dbQueryDuration.observe({ operation: 'get_user' }, duration);

    span.setAttribute('db.latency_ms', duration * 1000);
    span.end();

    return Object.keys(user).length > 0 ? user : null;
  });
}

// Batched user fetch for DataLoader
async function batchGetUsers(ids) {
  return tracer.startActiveSpan('db.batchGetUsers', async (span) => {
    span.setAttribute('user.ids', ids.join(','));
    span.setAttribute('batch.size', ids.length);
    const startTime = Date.now();

    await simulateLatency();

    const pipeline = redis.pipeline();
    ids.forEach(id => pipeline.hgetall(`user:${id}`));
    const results = await pipeline.exec();

    const users = results.map(([err, user]) => {
      if (err || Object.keys(user).length === 0) return null;
      return user;
    });

    const duration = (Date.now() - startTime) / 1000;
    dbQueriesTotal.inc({ operation: 'batch_get_users', batched: 'true' });
    dbQueryDuration.observe({ operation: 'batch_get_users' }, duration);

    span.setAttribute('db.latency_ms', duration * 1000);
    span.end();

    console.log(`[DataLoader] Batched ${ids.length} user lookups in ${(duration * 1000).toFixed(1)}ms`);

    return users;
  });
}

// Create DataLoader for batching
function createUserLoader() {
  return new DataLoader(batchGetUsers, {
    maxBatchSize: 100,
    cache: true,
  });
}

// GraphQL Schema with Federation directives
const typeDefs = gql`
  extend schema
    @link(url: "https://specs.apollo.dev/federation/v2.0",
          import: ["@key", "@shareable"])

  type Query {
    user(id: ID!): User
    users: [User!]!
    me: User
  }

  type User @key(fields: "id") {
    id: ID!
    name: String!
    email: String!
    role: UserRole!
  }

  enum UserRole {
    ADMIN
    MODERATOR
    USER
  }
`;

const resolvers = {
  Query: {
    user: async (_, { id }, { userLoader }) => {
      if (DISABLE_DATALOADER) {
        return getUserById(id);
      }
      return userLoader.load(id);
    },
    users: async () => {
      return tracer.startActiveSpan('query.users', async (span) => {
        const users = [];
        for (let i = 1; i <= 8; i++) {
          const user = await redis.hgetall(`user:${i}`);
          if (Object.keys(user).length > 0) {
            users.push(user);
          }
        }
        span.setAttribute('users.count', users.length);
        span.end();
        return users;
      });
    },
    me: async (_, __, { userLoader }) => {
      // Simulates authenticated user (always user 1)
      if (DISABLE_DATALOADER) {
        return getUserById('1');
      }
      return userLoader.load('1');
    },
  },
  User: {
    __resolveReference: async (user, { userLoader }) => {
      console.log(`[Users] Resolving reference for user ${user.id}, DataLoader: ${!DISABLE_DATALOADER}`);
      if (DISABLE_DATALOADER) {
        return getUserById(user.id);
      }
      return userLoader.load(user.id);
    },
  },
};

// Create Apollo Server
const server = new ApolloServer({
  schema: buildSubgraphSchema({ typeDefs, resolvers }),
});

// Express app
const app = express();

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'users-subgraph' });
});

// Metrics endpoint
app.get('/metrics', async (req, res) => {
  res.set('Content-Type', register.contentType);
  res.end(await register.metrics());
});

// Admin endpoint to toggle DataLoader
app.post('/admin/dataloader', express.json(), (req, res) => {
  const { enabled } = req.body;
  process.env.DISABLE_DATALOADER = enabled ? 'false' : 'true';
  res.json({
    dataloader_enabled: enabled,
    message: `DataLoader ${enabled ? 'enabled' : 'disabled'}. N+1 queries ${enabled ? 'will be batched' : 'will NOT be batched'}.`,
  });
});

app.get('/admin/dataloader', (req, res) => {
  res.json({
    dataloader_enabled: process.env.DISABLE_DATALOADER !== 'true',
  });
});

// Start the server
async function start() {
  await seedData();
  await server.start();

  app.use('/graphql', express.json(), expressMiddleware(server, {
    context: async ({ req }) => ({
      userLoader: createUserLoader(),
      headers: req.headers,
    }),
  }));

  app.listen(PORT, () => {
    console.log(`Users Subgraph ready at http://localhost:${PORT}/graphql`);
    console.log(`DataLoader enabled: ${!DISABLE_DATALOADER}`);
  });
}

start().catch(console.error);
