# Lab 44: Federated Graph Search

Explore Netflix-style federated GraphQL architecture. In this lab, you'll learn how to build a federated graph that composes multiple independent subgraphs into a unified API, handle cross-service queries, detect the N+1 query problem, and implement the DataLoader pattern for efficient batching.

## What You'll Learn

- How Apollo Federation composes multiple subgraphs into a single API
- How federated queries execute across service boundaries
- How to detect and fix N+1 query problems using distributed tracing
- The DataLoader pattern for batching database requests
- How to handle subgraph failures gracefully
- Observability patterns for federated GraphQL systems

## Architecture

```
                              ┌─────────────────────┐
                              │   Federation        │
                              │     Gateway         │
                              │   (Apollo Router)   │
                              └─────────┬───────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
                    ▼                   ▼                   ▼
          ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
          │  Users          │ │  Products       │ │  Reviews        │
          │  Subgraph       │ │  Subgraph       │ │  Subgraph       │
          │  Port: 4001     │ │  Port: 4002     │ │  Port: 4003     │
          └────────┬────────┘ └────────┬────────┘ └────────┬────────┘
                   │                   │                   │
                   ▼                   ▼                   ▼
          ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
          │   Redis         │ │   Redis         │ │   Redis         │
          │   (users-db)    │ │   (products-db) │ │   (reviews-db)  │
          └─────────────────┘ └─────────────────┘ └─────────────────┘
```

### Schema Overview

**Users Subgraph** - Owns the `User` type
```graphql
type User @key(fields: "id") {
  id: ID!
  name: String!
  email: String!
  role: UserRole!
}
```

**Products Subgraph** - Owns the `Product` type
```graphql
type Product @key(fields: "id") {
  id: ID!
  name: String!
  category: ProductCategory!
  price: Float!
  inStock: Boolean!
}
```

**Reviews Subgraph** - Owns `Review` and extends `User` and `Product`
```graphql
type Review @key(fields: "id") {
  id: ID!
  rating: Int!
  comment: String!
  author: User!
  product: Product!
}

# Extensions
extend type User {
  reviews: [Review!]!
}

extend type Product {
  reviews: [Review!]!
  averageRating: Float
  reviewCount: Int!
}
```

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- jq (for JSON formatting)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Verify Services Are Running

```bash
docker compose ps
```

All services should show as "healthy".

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| GraphQL Gateway | http://localhost:4000/graphql | Federation API |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Understanding Federation

First, let's explore how the federated schema works.

**Query a single subgraph (Products only):**

```bash
curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ products { id name price category } }"}' | jq
```

**Query across subgraph boundaries (Products + Reviews):**

```bash
curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ products { id name reviews { rating comment author { name } } } }"}' | jq
```

**Observe the trace in Jaeger:**
1. Open http://localhost:16686
2. Select "federation-gateway" from the Service dropdown
3. Click "Find Traces"
4. Notice how the trace spans multiple services

**Questions:**
- How many services are involved in the federated query?
- What's the relationship between the gateway and subgraphs?

---

### Exercise 2: Detecting the N+1 Problem

The N+1 problem occurs when fetching related data requires N additional queries for N items. Let's see it in action.

**First, disable DataLoader in all subgraphs:**

```bash
# Disable DataLoader in users subgraph
curl -s -X POST http://localhost:4001/admin/dataloader \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq

# Disable DataLoader in products subgraph
curl -s -X POST http://localhost:4002/admin/dataloader \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq

# Disable DataLoader in reviews subgraph
curl -s -X POST http://localhost:4003/admin/dataloader \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq
```

**Run a query that triggers N+1:**

```bash
time curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ products { id name reviews { rating author { name } } } }"}' | jq
```

**Now look at the trace in Jaeger:**
1. Open http://localhost:16686
2. Find the latest trace
3. Count the number of `db.getReviewsByProductId` and `db.getUserById` spans

**Expected:** You'll see many individual database calls - one for each product's reviews, and one for each review's author.

---

### Exercise 3: Fixing N+1 with DataLoader

Now let's enable DataLoader and see the difference.

**Enable DataLoader in all subgraphs:**

```bash
# Enable DataLoader in all subgraphs
curl -s -X POST http://localhost:4001/admin/dataloader \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq

curl -s -X POST http://localhost:4002/admin/dataloader \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq

curl -s -X POST http://localhost:4003/admin/dataloader \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq
```

**Run the same query again:**

```bash
time curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ products { id name reviews { rating author { name } } } }"}' | jq
```

**Compare the traces in Jaeger:**
1. Find the new trace
2. Look for `db.batchGetReviewsByProductIds` and `db.batchGetUsers` spans
3. Notice how all lookups are now batched into single calls

**Questions:**
- How much faster was the batched query?
- How many database round trips were saved?

---

### Exercise 4: Subgraph Failure Handling

What happens when one subgraph fails? Let's find out.

**Enable failures in the reviews subgraph:**

```bash
# Set 50% failure rate
curl -s -X POST http://localhost:4003/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"rate": 0.5}' | jq
```

**Make several requests:**

```bash
for i in {1..5}; do
  echo "Request $i:"
  curl -s -X POST http://localhost:4000/graphql \
    -H "Content-Type: application/json" \
    -d '{"query": "{ products { id name reviews { rating } } }"}' | jq '.errors, .data.products[0]'
  echo ""
done
```

**Observe the behavior:**
- Some requests succeed fully
- Some requests return partial data with errors
- The gateway doesn't crash - it handles failures gracefully

**Query without reviews (should always succeed):**

```bash
curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ products { id name price } }"}' | jq
```

**Reset failure rate:**

```bash
curl -s -X POST http://localhost:4003/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"rate": 0}' | jq
```

---

### Exercise 5: Load Testing and Observability

Generate sustained load to see patterns in metrics.

**Run the basic load test:**

```bash
docker compose run --rm k6 run /scripts/basic.js
```

**While the test runs, observe:**

1. **Grafana Dashboard** (http://localhost:3001)
   - Watch the "Federated GraphQL Dashboard"
   - Observe request rates, latencies, and error rates

2. **Jaeger** (http://localhost:16686)
   - Find traces with high latency
   - Compare spans across services

**Run the N+1 detection test:**

```bash
# First, disable DataLoader
curl -s -X POST http://localhost:4001/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": false}'
curl -s -X POST http://localhost:4002/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": false}'
curl -s -X POST http://localhost:4003/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": false}'

# Run the test
docker compose run --rm k6 run /scripts/n-plus-one.js
```

Note the latency, then re-enable DataLoader and run again:

```bash
# Enable DataLoader
curl -s -X POST http://localhost:4001/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": true}'
curl -s -X POST http://localhost:4002/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": true}'
curl -s -X POST http://localhost:4003/admin/dataloader -H "Content-Type: application/json" -d '{"enabled": true}'

# Run the test again
docker compose run --rm k6 run /scripts/n-plus-one.js
```

**Compare the results:**
- Without DataLoader: High latency, many DB queries
- With DataLoader: Lower latency, batched DB queries

---

### Exercise 6: Deep Dive into Tracing

Let's explore how to use tracing to understand federated query execution.

**Make a complex federated query:**

```bash
curl -s -X POST http://localhost:4000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "query DeepQuery { users { id name reviews { id rating product { name category } } } topProducts(limit: 3) { id name averageRating reviews { comment author { name email } } } }"}' | jq
```

**In Jaeger, analyze the trace:**
1. Find the trace for "DeepQuery"
2. Expand all spans
3. Identify the query execution order
4. Look at span attributes for insights

**Questions to answer from the trace:**
- Which subgraph is called first?
- How many times is each subgraph called?
- Where is the most time spent?
- Are there any parallel operations?

---

## Key Takeaways

1. **Federation enables team independence** - Each subgraph can be developed and deployed independently

2. **N+1 is a real problem** - Without DataLoader, nested queries cause exponential database calls

3. **DataLoader is essential** - Batching transforms N+1 into efficient bulk operations

4. **Tracing is critical** - Federated systems need distributed tracing to understand behavior

5. **Partial failures are okay** - The gateway can return partial data when subgraphs fail

6. **Metrics matter** - Monitor batch vs. individual queries to detect N+1 problems

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### GraphQL errors
Check individual subgraph health:
```bash
curl http://localhost:4001/health
curl http://localhost:4002/health
curl http://localhost:4003/health
```

### Traces not appearing
- Wait 10-15 seconds after making requests
- Check the OTEL collector: `docker compose logs otel-collector`

### Port conflicts
Check for conflicts: `lsof -i :4000` (or other ports)

## Further Reading

- [Apollo Federation Documentation](https://www.apollographql.com/docs/federation/)
- [DataLoader Pattern](https://github.com/graphql/dataloader)
- [GraphQL N+1 Problem](https://shopify.engineering/solving-the-n-1-problem-for-graphql-through-batching)
- [Netflix DGS Framework](https://netflix.github.io/dgs/)

## Next Lab

Explore more distributed systems patterns in the upcoming labs!
