/**
 * Reviews Subgraph
 *
 * Manages review data and extends User and Product types with reviews.
 * This is the key subgraph demonstrating:
 * - Entity extension (adding fields to User and Product)
 * - N+1 query problem detection
 * - DataLoader batching for efficiency
 * - Subgraph failure simulation
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

const PORT = process.env.PORT || 4003;
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const DISABLE_DATALOADER = process.env.DISABLE_DATALOADER === 'true';
const DB_LATENCY_MS = parseInt(process.env.DB_LATENCY_MS || '10', 10);
let FAILURE_RATE = parseFloat(process.env.FAILURE_RATE || '0.0');

const tracer = trace.getTracer('reviews-subgraph');

// Redis client
const redis = new Redis(REDIS_URL);

// Prometheus metrics
const dbQueriesTotal = new Counter({
  name: 'reviews_db_queries_total',
  help: 'Total number of database queries',
  labelNames: ['operation', 'batched'],
});

const dbQueryDuration = new Histogram({
  name: 'reviews_db_query_duration_seconds',
  help: 'Database query duration in seconds',
  labelNames: ['operation'],
  buckets: [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
});

const failuresTotal = new Counter({
  name: 'reviews_failures_total',
  help: 'Total number of simulated failures',
});

// Sample review data
const sampleReviews = [
  { id: 'r1', productId: '101', authorId: '1', rating: '5', comment: 'Amazing streaming quality!', createdAt: '2024-01-15' },
  { id: 'r2', productId: '101', authorId: '2', rating: '4', comment: 'Great device, easy setup.', createdAt: '2024-01-20' },
  { id: 'r3', productId: '102', authorId: '3', rating: '5', comment: 'Best TV I have ever owned.', createdAt: '2024-02-01' },
  { id: 'r4', productId: '102', authorId: '1', rating: '4', comment: 'Picture quality is stunning.', createdAt: '2024-02-10' },
  { id: 'r5', productId: '103', authorId: '4', rating: '3', comment: 'Sound is good but not great.', createdAt: '2024-02-15' },
  { id: 'r6', productId: '103', authorId: '5', rating: '5', comment: 'Perfect for movie nights!', createdAt: '2024-02-20' },
  { id: 'r7', productId: '104', authorId: '6', rating: '4', comment: 'Tasty and convenient.', createdAt: '2024-03-01' },
  { id: 'r8', productId: '106', authorId: '7', rating: '5', comment: 'Incredible sound for the price!', createdAt: '2024-03-05' },
  { id: 'r9', productId: '106', authorId: '2', rating: '4', comment: 'Bass could be deeper.', createdAt: '2024-03-10' },
  { id: 'r10', productId: '107', authorId: '8', rating: '5', comment: 'Works perfectly with my streaming box.', createdAt: '2024-03-15' },
  { id: 'r11', productId: '108', authorId: '3', rating: '4', comment: 'Great ambiance for movie watching.', createdAt: '2024-03-20' },
  { id: 'r12', productId: '101', authorId: '4', rating: '4', comment: 'Reliable and fast.', createdAt: '2024-03-25' },
];

// Initialize Redis with sample data
async function seedData() {
  for (const review of sampleReviews) {
    await redis.hset(`review:${review.id}`, review);
    await redis.sadd(`reviews:product:${review.productId}`, review.id);
    await redis.sadd(`reviews:author:${review.authorId}`, review.id);
  }
  await redis.sadd('reviews:all', ...sampleReviews.map(r => r.id));
  console.log(`Seeded ${sampleReviews.length} reviews to Redis`);
}

// Simulate database latency
async function simulateLatency() {
  if (DB_LATENCY_MS > 0) {
    await new Promise(resolve => setTimeout(resolve, DB_LATENCY_MS));
  }
}

// Simulate random failures
function maybeThrowError() {
  if (FAILURE_RATE > 0 && Math.random() < FAILURE_RATE) {
    failuresTotal.inc();
    throw new Error('Simulated subgraph failure');
  }
}

// Database operations
async function getReviewById(id) {
  return tracer.startActiveSpan('db.getReviewById', async (span) => {
    span.setAttribute('review.id', id);
    const startTime = Date.now();

    maybeThrowError();
    await simulateLatency();
    const review = await redis.hgetall(`review:${id}`);

    const duration = (Date.now() - startTime) / 1000;
    dbQueriesTotal.inc({ operation: 'get_review', batched: 'false' });
    dbQueryDuration.observe({ operation: 'get_review' }, duration);

    span.setAttribute('db.latency_ms', duration * 1000);
    span.end();

    if (Object.keys(review).length === 0) return null;
    return {
      ...review,
      rating: parseInt(review.rating, 10),
    };
  });
}

async function getReviewsByProductId(productId) {
  return tracer.startActiveSpan('db.getReviewsByProductId', async (span) => {
    span.setAttribute('product.id', productId);
    const startTime = Date.now();

    maybeThrowError();
    await simulateLatency();

    const reviewIds = await redis.smembers(`reviews:product:${productId}`);
    if (reviewIds.length === 0) {
      span.end();
      return [];
    }

    const pipeline = redis.pipeline();
    reviewIds.forEach(id => pipeline.hgetall(`review:${id}`));
    const results = await pipeline.exec();

    const reviews = results
      .map(([err, review]) => {
        if (err || Object.keys(review).length === 0) return null;
        return {
          ...review,
          rating: parseInt(review.rating, 10),
        };
      })
      .filter(Boolean);

    const duration = (Date.now() - startTime) / 1000;
    dbQueriesTotal.inc({ operation: 'get_reviews_by_product', batched: 'false' });
    dbQueryDuration.observe({ operation: 'get_reviews_by_product' }, duration);

    span.setAttribute('db.latency_ms', duration * 1000);
    span.setAttribute('reviews.count', reviews.length);
    span.end();

    return reviews;
  });
}

async function getReviewsByAuthorId(authorId) {
  return tracer.startActiveSpan('db.getReviewsByAuthorId', async (span) => {
    span.setAttribute('author.id', authorId);
    const startTime = Date.now();

    maybeThrowError();
    await simulateLatency();

    const reviewIds = await redis.smembers(`reviews:author:${authorId}`);
    if (reviewIds.length === 0) {
      span.end();
      return [];
    }

    const pipeline = redis.pipeline();
    reviewIds.forEach(id => pipeline.hgetall(`review:${id}`));
    const results = await pipeline.exec();

    const reviews = results
      .map(([err, review]) => {
        if (err || Object.keys(review).length === 0) return null;
        return {
          ...review,
          rating: parseInt(review.rating, 10),
        };
      })
      .filter(Boolean);

    const duration = (Date.now() - startTime) / 1000;
    dbQueriesTotal.inc({ operation: 'get_reviews_by_author', batched: 'false' });
    dbQueryDuration.observe({ operation: 'get_reviews_by_author' }, duration);

    span.setAttribute('db.latency_ms', duration * 1000);
    span.setAttribute('reviews.count', reviews.length);
    span.end();

    return reviews;
  });
}

// Batched lookups for DataLoader
async function batchGetReviewsByProductIds(productIds) {
  return tracer.startActiveSpan('db.batchGetReviewsByProductIds', async (span) => {
    span.setAttribute('product.ids', productIds.join(','));
    span.setAttribute('batch.size', productIds.length);
    const startTime = Date.now();

    maybeThrowError();
    await simulateLatency();

    // Get all review IDs for all products in parallel
    const reviewIdResults = await Promise.all(
      productIds.map(productId => redis.smembers(`reviews:product:${productId}`))
    );

    // Flatten all review IDs
    const allReviewIds = reviewIdResults.flat();

    // Fetch all reviews in one batch
    if (allReviewIds.length === 0) {
      span.end();
      return productIds.map(() => []);
    }

    const pipeline = redis.pipeline();
    allReviewIds.forEach(id => pipeline.hgetall(`review:${id}`));
    const reviewResults = await pipeline.exec();

    // Build a map of productId -> reviews
    const reviewMap = new Map();
    reviewResults.forEach(([err, review], idx) => {
      if (!err && Object.keys(review).length > 0) {
        const productId = review.productId;
        if (!reviewMap.has(productId)) {
          reviewMap.set(productId, []);
        }
        reviewMap.get(productId).push({
          ...review,
          rating: parseInt(review.rating, 10),
        });
      }
    });

    const results = productIds.map(productId => reviewMap.get(productId) || []);

    const duration = (Date.now() - startTime) / 1000;
    dbQueriesTotal.inc({ operation: 'batch_get_reviews_by_product', batched: 'true' });
    dbQueryDuration.observe({ operation: 'batch_get_reviews_by_product' }, duration);

    span.setAttribute('db.latency_ms', duration * 1000);
    span.end();

    console.log(`[DataLoader] Batched reviews for ${productIds.length} products in ${(duration * 1000).toFixed(1)}ms`);

    return results;
  });
}

async function batchGetReviewsByAuthorIds(authorIds) {
  return tracer.startActiveSpan('db.batchGetReviewsByAuthorIds', async (span) => {
    span.setAttribute('author.ids', authorIds.join(','));
    span.setAttribute('batch.size', authorIds.length);
    const startTime = Date.now();

    maybeThrowError();
    await simulateLatency();

    const reviewIdResults = await Promise.all(
      authorIds.map(authorId => redis.smembers(`reviews:author:${authorId}`))
    );

    const allReviewIds = reviewIdResults.flat();

    if (allReviewIds.length === 0) {
      span.end();
      return authorIds.map(() => []);
    }

    const pipeline = redis.pipeline();
    allReviewIds.forEach(id => pipeline.hgetall(`review:${id}`));
    const reviewResults = await pipeline.exec();

    const reviewMap = new Map();
    reviewResults.forEach(([err, review]) => {
      if (!err && Object.keys(review).length > 0) {
        const authorId = review.authorId;
        if (!reviewMap.has(authorId)) {
          reviewMap.set(authorId, []);
        }
        reviewMap.get(authorId).push({
          ...review,
          rating: parseInt(review.rating, 10),
        });
      }
    });

    const results = authorIds.map(authorId => reviewMap.get(authorId) || []);

    const duration = (Date.now() - startTime) / 1000;
    dbQueriesTotal.inc({ operation: 'batch_get_reviews_by_author', batched: 'true' });
    dbQueryDuration.observe({ operation: 'batch_get_reviews_by_author' }, duration);

    span.setAttribute('db.latency_ms', duration * 1000);
    span.end();

    console.log(`[DataLoader] Batched reviews for ${authorIds.length} authors in ${(duration * 1000).toFixed(1)}ms`);

    return results;
  });
}

// Create DataLoaders
function createReviewLoaders() {
  return {
    byProductId: new DataLoader(batchGetReviewsByProductIds, { maxBatchSize: 100, cache: true }),
    byAuthorId: new DataLoader(batchGetReviewsByAuthorIds, { maxBatchSize: 100, cache: true }),
  };
}

// GraphQL Schema with Federation directives
const typeDefs = gql`
  extend schema
    @link(url: "https://specs.apollo.dev/federation/v2.0",
          import: ["@key", "@shareable", "@external", "@requires", "@extends"])

  type Query {
    review(id: ID!): Review
    reviews: [Review!]!
    latestReviews(limit: Int = 5): [Review!]!
  }

  type Review @key(fields: "id") {
    id: ID!
    rating: Int!
    comment: String!
    createdAt: String!
    author: User!
    product: Product!
  }

  # Extend User type from users subgraph
  type User @key(fields: "id") {
    id: ID! @external
    reviews: [Review!]!
  }

  # Extend Product type from products subgraph
  type Product @key(fields: "id") {
    id: ID! @external
    reviews: [Review!]!
    averageRating: Float
    reviewCount: Int!
  }
`;

const resolvers = {
  Query: {
    review: async (_, { id }) => {
      return getReviewById(id);
    },
    reviews: async () => {
      return tracer.startActiveSpan('query.reviews', async (span) => {
        const ids = await redis.smembers('reviews:all');
        const reviews = await Promise.all(ids.map(id => getReviewById(id)));
        span.setAttribute('reviews.count', reviews.filter(Boolean).length);
        span.end();
        return reviews.filter(Boolean);
      });
    },
    latestReviews: async (_, { limit }) => {
      return tracer.startActiveSpan('query.latestReviews', async (span) => {
        span.setAttribute('limit', limit);
        const ids = await redis.smembers('reviews:all');
        const reviews = await Promise.all(ids.slice(0, limit).map(id => getReviewById(id)));
        span.end();
        return reviews.filter(Boolean);
      });
    },
  },
  Review: {
    author: (review) => {
      return { __typename: 'User', id: review.authorId };
    },
    product: (review) => {
      return { __typename: 'Product', id: review.productId };
    },
    __resolveReference: async (review) => {
      return getReviewById(review.id);
    },
  },
  User: {
    reviews: async (user, _, { reviewLoaders }) => {
      console.log(`[Reviews] Fetching reviews for user ${user.id}, DataLoader: ${!DISABLE_DATALOADER}`);
      if (DISABLE_DATALOADER) {
        return getReviewsByAuthorId(user.id);
      }
      return reviewLoaders.byAuthorId.load(user.id);
    },
  },
  Product: {
    reviews: async (product, _, { reviewLoaders }) => {
      console.log(`[Reviews] Fetching reviews for product ${product.id}, DataLoader: ${!DISABLE_DATALOADER}`);
      if (DISABLE_DATALOADER) {
        return getReviewsByProductId(product.id);
      }
      return reviewLoaders.byProductId.load(product.id);
    },
    averageRating: async (product, _, { reviewLoaders }) => {
      let reviews;
      if (DISABLE_DATALOADER) {
        reviews = await getReviewsByProductId(product.id);
      } else {
        reviews = await reviewLoaders.byProductId.load(product.id);
      }
      if (reviews.length === 0) return null;
      const sum = reviews.reduce((acc, r) => acc + r.rating, 0);
      return Math.round((sum / reviews.length) * 10) / 10;
    },
    reviewCount: async (product, _, { reviewLoaders }) => {
      let reviews;
      if (DISABLE_DATALOADER) {
        reviews = await getReviewsByProductId(product.id);
      } else {
        reviews = await reviewLoaders.byProductId.load(product.id);
      }
      return reviews.length;
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
  res.json({ status: 'ok', service: 'reviews-subgraph' });
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

// Admin endpoint to control failure rate
app.post('/admin/failure', express.json(), (req, res) => {
  const { rate } = req.body;
  FAILURE_RATE = Math.max(0, Math.min(1, rate || 0));
  res.json({
    failure_rate: FAILURE_RATE,
    message: `Failure rate set to ${(FAILURE_RATE * 100).toFixed(1)}%`,
  });
});

app.get('/admin/failure', (req, res) => {
  res.json({
    failure_rate: FAILURE_RATE,
  });
});

// Start the server
async function start() {
  await seedData();
  await server.start();

  app.use('/graphql', express.json(), expressMiddleware(server, {
    context: async ({ req }) => ({
      reviewLoaders: createReviewLoaders(),
      headers: req.headers,
    }),
  }));

  app.listen(PORT, () => {
    console.log(`Reviews Subgraph ready at http://localhost:${PORT}/graphql`);
    console.log(`DataLoader enabled: ${!DISABLE_DATALOADER}`);
    console.log(`Failure rate: ${(FAILURE_RATE * 100).toFixed(1)}%`);
  });
}

start().catch(console.error);
