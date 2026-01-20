/**
 * Products Subgraph
 *
 * Manages product catalog and provides the Product type for federation.
 * Demonstrates DataLoader pattern for batching product lookups.
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

const PORT = process.env.PORT || 4002;
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const DISABLE_DATALOADER = process.env.DISABLE_DATALOADER === 'true';
const DB_LATENCY_MS = parseInt(process.env.DB_LATENCY_MS || '10', 10);

const tracer = trace.getTracer('products-subgraph');

// Redis client
const redis = new Redis(REDIS_URL);

// Prometheus metrics
const dbQueriesTotal = new Counter({
  name: 'products_db_queries_total',
  help: 'Total number of database queries',
  labelNames: ['operation', 'batched'],
});

const dbQueryDuration = new Histogram({
  name: 'products_db_query_duration_seconds',
  help: 'Database query duration in seconds',
  labelNames: ['operation'],
  buckets: [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
});

// Sample product data
const sampleProducts = [
  { id: '101', name: 'Streaming Box Pro', category: 'ELECTRONICS', price: '149.99', inStock: 'true' },
  { id: '102', name: 'Smart TV 55"', category: 'ELECTRONICS', price: '599.99', inStock: 'true' },
  { id: '103', name: 'Wireless Headphones', category: 'ELECTRONICS', price: '199.99', inStock: 'true' },
  { id: '104', name: 'Movie Night Popcorn', category: 'FOOD', price: '4.99', inStock: 'true' },
  { id: '105', name: 'Cozy Blanket', category: 'HOME', price: '39.99', inStock: 'false' },
  { id: '106', name: 'Sound Bar', category: 'ELECTRONICS', price: '249.99', inStock: 'true' },
  { id: '107', name: 'Streaming Remote', category: 'ELECTRONICS', price: '29.99', inStock: 'true' },
  { id: '108', name: 'LED Light Strip', category: 'HOME', price: '24.99', inStock: 'true' },
  { id: '109', name: 'HDMI Cable 6ft', category: 'ELECTRONICS', price: '12.99', inStock: 'true' },
  { id: '110', name: 'Wall Mount Kit', category: 'HOME', price: '49.99', inStock: 'false' },
];

// Initialize Redis with sample data
async function seedData() {
  for (const product of sampleProducts) {
    await redis.hset(`product:${product.id}`, product);
  }
  // Store product IDs for listing
  await redis.sadd('products:all', ...sampleProducts.map(p => p.id));
  console.log(`Seeded ${sampleProducts.length} products to Redis`);
}

// Simulate database latency
async function simulateLatency() {
  if (DB_LATENCY_MS > 0) {
    await new Promise(resolve => setTimeout(resolve, DB_LATENCY_MS));
  }
}

// Database operations
async function getProductById(id) {
  return tracer.startActiveSpan('db.getProductById', async (span) => {
    span.setAttribute('product.id', id);
    const startTime = Date.now();

    await simulateLatency();
    const product = await redis.hgetall(`product:${id}`);

    const duration = (Date.now() - startTime) / 1000;
    dbQueriesTotal.inc({ operation: 'get_product', batched: 'false' });
    dbQueryDuration.observe({ operation: 'get_product' }, duration);

    span.setAttribute('db.latency_ms', duration * 1000);
    span.end();

    if (Object.keys(product).length === 0) return null;
    return {
      ...product,
      price: parseFloat(product.price),
      inStock: product.inStock === 'true',
    };
  });
}

// Batched product fetch for DataLoader
async function batchGetProducts(ids) {
  return tracer.startActiveSpan('db.batchGetProducts', async (span) => {
    span.setAttribute('product.ids', ids.join(','));
    span.setAttribute('batch.size', ids.length);
    const startTime = Date.now();

    await simulateLatency();

    const pipeline = redis.pipeline();
    ids.forEach(id => pipeline.hgetall(`product:${id}`));
    const results = await pipeline.exec();

    const products = results.map(([err, product]) => {
      if (err || Object.keys(product).length === 0) return null;
      return {
        ...product,
        price: parseFloat(product.price),
        inStock: product.inStock === 'true',
      };
    });

    const duration = (Date.now() - startTime) / 1000;
    dbQueriesTotal.inc({ operation: 'batch_get_products', batched: 'true' });
    dbQueryDuration.observe({ operation: 'batch_get_products' }, duration);

    span.setAttribute('db.latency_ms', duration * 1000);
    span.end();

    console.log(`[DataLoader] Batched ${ids.length} product lookups in ${(duration * 1000).toFixed(1)}ms`);

    return products;
  });
}

// Create DataLoader for batching
function createProductLoader() {
  return new DataLoader(batchGetProducts, {
    maxBatchSize: 100,
    cache: true,
  });
}

// GraphQL Schema with Federation directives
const typeDefs = gql`
  extend schema
    @link(url: "https://specs.apollo.dev/federation/v2.0",
          import: ["@key", "@shareable", "@external", "@provides"])

  type Query {
    product(id: ID!): Product
    products(category: ProductCategory): [Product!]!
    topProducts(limit: Int = 5): [Product!]!
  }

  type Product @key(fields: "id") {
    id: ID!
    name: String!
    category: ProductCategory!
    price: Float!
    inStock: Boolean!
  }

  enum ProductCategory {
    ELECTRONICS
    HOME
    FOOD
  }
`;

const resolvers = {
  Query: {
    product: async (_, { id }, { productLoader }) => {
      if (DISABLE_DATALOADER) {
        return getProductById(id);
      }
      return productLoader.load(id);
    },
    products: async (_, { category }, { productLoader }) => {
      return tracer.startActiveSpan('query.products', async (span) => {
        const ids = await redis.smembers('products:all');
        span.setAttribute('products.total_ids', ids.length);

        let products;
        if (DISABLE_DATALOADER) {
          products = await Promise.all(ids.map(id => getProductById(id)));
        } else {
          products = await productLoader.loadMany(ids);
        }

        products = products.filter(Boolean);
        if (category) {
          products = products.filter(p => p.category === category);
        }

        span.setAttribute('products.returned', products.length);
        span.end();
        return products;
      });
    },
    topProducts: async (_, { limit }, { productLoader }) => {
      return tracer.startActiveSpan('query.topProducts', async (span) => {
        span.setAttribute('limit', limit);

        const ids = await redis.smembers('products:all');
        const productIds = ids.slice(0, limit);

        let products;
        if (DISABLE_DATALOADER) {
          products = await Promise.all(productIds.map(id => getProductById(id)));
        } else {
          products = await productLoader.loadMany(productIds);
        }

        span.setAttribute('products.returned', products.filter(Boolean).length);
        span.end();
        return products.filter(Boolean);
      });
    },
  },
  Product: {
    __resolveReference: async (product, { productLoader }) => {
      console.log(`[Products] Resolving reference for product ${product.id}, DataLoader: ${!DISABLE_DATALOADER}`);
      if (DISABLE_DATALOADER) {
        return getProductById(product.id);
      }
      return productLoader.load(product.id);
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
  res.json({ status: 'ok', service: 'products-subgraph' });
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
      productLoader: createProductLoader(),
      headers: req.headers,
    }),
  }));

  app.listen(PORT, () => {
    console.log(`Products Subgraph ready at http://localhost:${PORT}/graphql`);
    console.log(`DataLoader enabled: ${!DISABLE_DATALOADER}`);
  });
}

start().catch(console.error);
