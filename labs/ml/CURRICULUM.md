# ML for Systems Engineers: Learning by Doing

A senior distributed systems engineer learns ML from scratch and documents everything.

**Premise:** I don't know ML. I know distributed systems. I'm going to learn by building, make mistakes in public, and document what I wish someone had told me.

**Format:** 11 labs. Each one has a single "aha moment" — the thing that clicked. The content is the journey, not the destination.

**What this is NOT:** A course. A certification prep. A "complete guide to ML." It's an engineer's lab notebook.

---

## The 11 Labs

### Lab 1: Train Your First Model

**Goal:** Understand what a "model" actually is — not the word, the artifact.

**What you build:**
- Sentiment classifier on product reviews using scikit-learn
- Train it, save the model file, inspect what's inside
- Overfit it on purpose, then fix it
- Proper train/test/validation split — understand why you need three sets, not two
- Measure accuracy, precision, recall, F1 — understand what these mean and when each one lies
- Build a confusion matrix — see exactly where the model gets it wrong

**What you should know after:**
- A model is a function with learned parameters stored in a file
- Training is just optimization — minimize a loss function
- Overfitting means memorizing the training data instead of learning patterns
- "Accuracy" alone is a misleading metric — a model that predicts "not spam" for every email is 95% accurate if 95% of email isn't spam
- The validation set is your "staging environment" — test set is production. You never tune against production.

**Format:** Jupyter notebook + Python scripts

**Distributed systems bridge:** A model file is like a database snapshot — it captures state at a point in time. Training is like a long-running batch job that produces an artifact. The train/test split is like the separation between dev and prod environments — you don't validate in production.

**Time estimate:** 1 week

---

### Lab 2: Your Data is a Mess

**Goal:** Discover that the model is only as good as the data — and real data is never clean.

**The setup:** In Lab 1 you trained on a pristine, pre-packaged dataset. That's like learning to drive in an empty parking lot. Real datasets have missing values, mislabeled examples, duplicates, class imbalance, and subtle biases. Most ML teams spend 60-80% of their time on data, not models. This lab shows you why.

**What you build:**
- Take a realistic, messy dataset (e.g., product reviews with missing fields, inconsistent labels, duplicates, mixed languages, heavy class imbalance)
- Train the Lab 1 model on this raw data. Watch accuracy plummet — or worse, watch "accuracy" look fine while the model is useless (class imbalance trap)
- Explore the data: distribution of labels, missing value patterns, outliers, duplicate detection. Learn to look before you train.
- Clean it systematically: handle missing values, fix mislabeled examples, remove duplicates, address class imbalance (oversampling, undersampling, class weights)
- Retrain and compare: cleaned data vs. raw data. See the jump.
- Build a simple data validation pipeline: automated checks that run before training to catch quality issues early (schema checks, distribution checks, null rate thresholds)
- Version your dataset: track what data produced which model. Use DVC or even just hash + metadata — the tool matters less than the habit.

**What you should know after:**
- Data quality matters more than model choice. A simple model on clean data beats a complex model on dirty data — every time.
- "Garbage in, garbage out" isn't a cliché — it's the #1 failure mode in production ML
- Data exploration (EDA) is not a formality — you must understand your data before training
- Data versioning is as important as code versioning — you need to reproduce results and trace problems back to specific datasets
- Class imbalance silently destroys models. 96% "accuracy" when 96% of the data is one class means the model learned nothing.

**Format:** Jupyter notebook + Python scripts

**Distributed systems bridge:** Data validation before training is input validation at API boundaries — you reject bad data before it propagates through the system. Data versioning is artifact versioning — same as container image tags or database migration numbers. A data pipeline is an ETL pipeline — the same patterns you've built for databases and event streams.

**Time estimate:** 1 week

---

### Lab 3: Deploy a Model as an API

**Goal:** Turn your trained model into a running service and see how it differs from a normal HTTP API.

**What you build:**
- FastAPI service that loads the model from Labs 1-2 and serves predictions
- Health check, readiness probe (model loaded vs. not)
- Prometheus metrics: request latency, prediction distribution, model version
- Grafana dashboard
- Log every prediction with input, output, confidence, and timestamp — this becomes your feedback data for later labs

**What you should know after:**
- Model loading is a cold start problem — the model must be in memory before you can serve
- Inference latency is more variable than typical business logic
- You need to monitor what the model is predicting, not just whether the HTTP call succeeded
- Prediction logging is the foundation of everything that comes later — drift detection, retraining, evaluation

**Format:** Docker Compose + Grafana (same as distributed systems labs)

**Distributed systems bridge:** This is a stateful service. The "state" is the model weights in memory. Deploying a new model version is like a rolling database migration. Prediction logging is the ML equivalent of request logging — you'll mine it for insights the same way you'd analyze access logs.

**Time estimate:** 1 week

---

### Lab 4: Model Serving Under Load

**Goal:** Discover why ML inference has fundamentally different performance characteristics.

**What you build:**
- Load test the API from Lab 3 with k6
- Profile CPU usage during inference — see that it's compute-bound, not I/O-bound
- Implement request batching (collect N requests, predict as a batch)
- Compare: single-request vs. batched throughput and latency
- Show the tradeoff: batching improves throughput but adds latency for individual requests

**What you should know after:**
- ML inference is CPU/GPU-bound, not I/O-bound like most services
- Batching is the primary optimization lever (like connection pooling for compute)
- There's a fundamental throughput vs. latency tradeoff controlled by batch size and wait time
- GPU utilization matters — a GPU doing single requests is like a database processing one row at a time

**Format:** Docker Compose + Grafana + k6

**Distributed systems bridge:** Request batching is the same pattern as Nagle's algorithm (TCP), write coalescing (databases), and connection pooling. The tradeoff curve is identical.

**Time estimate:** 1 week

---

### Lab 5: Your Model is Wrong and Your Dashboard Doesn't Know

**Goal:** Experience the failure mode unique to ML — the service is healthy but the answers are garbage.

**What you build:**
- Deploy the sentiment model from Labs 1-4
- Simulate data drift: gradually shift input distribution (e.g., model trained on English reviews, start sending Spanish, or trained on electronics reviews, start sending food reviews)
- Watch: HTTP latency is fine, error rate is zero, but accuracy craters
- Build drift detection: compare input feature distributions against training data
- Implement alerting: KL divergence or Population Stability Index (PSI) thresholds
- Build a Grafana dashboard that shows model health alongside service health
- Create a "golden evaluation set" — a curated set of labeled examples that you periodically score the live model against. This is your canary for model quality.

**What you should know after:**
- ML models assume the future looks like the past. When that assumption breaks, the model fails silently.
- Traditional monitoring (latency, errors, throughput) cannot detect model degradation
- You need to monitor the data, not just the service
- Drift detection is statistical — you're comparing distributions, not checking thresholds
- A golden evaluation set is your most reliable quality signal — it's the ML equivalent of an integration test suite, run against production

**Format:** Docker Compose + Grafana + custom drift monitoring

**Distributed systems bridge:** This is like monitoring a cache where the hit rate looks fine but the cached values are stale. The system is "working" but serving wrong answers. Same class of problem as eventual consistency — correctness is invisible to health checks. The golden evaluation set is a synthetic monitor / canary — same concept as pinging a known endpoint with a known-good request to verify correctness.

**Time estimate:** 1.5 weeks

---

### Lab 6: Retraining Pipeline — Closing the Loop

**Goal:** When your model drifts (Lab 5), retrain it on new data and swap it into production without downtime.

**What you build:**
- Feedback collection: log predictions + eventual ground truth (e.g., user clicked "helpful" or "not helpful" on a sentiment prediction)
- Data accumulation: new labeled examples stream into a store (Postgres or files — keep it simple)
- Data quality gate: validate incoming training data against the checks from Lab 2 before it enters the training pool. Bad feedback data is worse than no data.
- Retrain trigger: when drift score from Lab 5 crosses a threshold OR on a schedule, kick off retraining
- Retraining job: train a new model on old data + new data combined
- Validation gate: new model must beat the current model on the golden evaluation set from Lab 5 before it can deploy. This is your promotion criteria.
- Hot swap: load the new model into the running service without restarting (or blue-green the model version)
- Grafana dashboard showing: drift score, retrain events, model version in production, accuracy before/after

**What you should know after:**
- Batch retraining (periodic retrain on accumulated data) is how 90% of production ML works — not continuous/online learning
- The feedback loop is the hardest part: getting ground truth labels after prediction is often slow, noisy, or impossible
- You must validate before deploying — a retrained model can be worse if the new data is bad or too small
- Data quality gates are essential in the retraining loop — Lab 2's checks apply to incoming data, not just initial training data
- "Online learning" (updating the model on every new example) sounds appealing but is dangerous in practice: catastrophic forgetting, feedback loops that amplify bias, and no way to roll back
- The retraining pipeline is a CI/CD pipeline where the "tests" are statistical and the "artifact" is a model file

**Format:** Docker Compose. Reuses the service from Labs 3-5. Adds a retraining job (Python script or container) and a simple data store.

**Distributed systems bridge:** This is a deployment pipeline with a validation gate — exactly like canary releases (week 19 of the distributed systems series). The drift trigger is an alert-driven workflow, like auto-scaling based on metrics. The hot swap is a blue-green deployment for model versions. The feedback loop is an eventually consistent system — ground truth arrives minutes, hours, or days after the prediction, just like eventual consistency in replicated databases.

**Why batch retraining, not online learning:**

| Approach | How it works | When to use |
|----------|-------------|-------------|
| **Batch retraining** | Accumulate new data, retrain periodically, validate, deploy | Default choice. Predictable, testable, rollback-friendly. |
| **Online/incremental learning** | Model updates weights on every new example in real-time | Rare. Useful for recommendation systems with rapid preference changes. Dangerous without guardrails. |
| **Scheduled retraining** | Retrain on a fixed cadence (daily, weekly) regardless of drift | Simple. Good enough when data changes slowly and predictably. |

This lab focuses on batch retraining triggered by drift. It's the approach you'll encounter at most companies.

**Time estimate:** 1.5 weeks

---

### Lab 7: Train a Neural Network — Why scikit-learn Isn't Enough

**Goal:** Hit the ceiling of classical ML, then understand why neural networks exist.

**The setup:** Take the scikit-learn model from Labs 1-6 and try to make it understand images or raw text. It can't — not without you hand-engineering every feature. Neural networks learn their own features. That's the whole point.

**What you build:**
- First, try: throw raw image pixels at scikit-learn. Watch it struggle. This is the motivation.
- Image classifier using PyTorch (CIFAR-10 — simple but can't be faked with manual features)
- Implement a training loop from scratch: forward pass, loss, backward pass, optimizer step
- Visualize what the network learns (activation maps, loss curves) — see that early layers learn edges, later layers learn shapes
- Train on CPU, then on GPU if available — measure the speedup
- Save checkpoints, resume training from a checkpoint
- Track your experiments: log hyperparameters (learning rate, batch size, architecture), training metrics, and results for every run. Start simple — a CSV or JSON log is fine. The point is the habit, not the tool. When you've run 20 experiments and can't remember which one worked, you'll understand why MLflow exists.

**What you should know after:**
- Classical ML needs hand-crafted features. Neural networks learn features from raw data. That's the difference.
- Neural networks are function composition — layers of simple transformations
- Backpropagation is just the chain rule applied automatically
- GPUs are fast for ML because matrix multiplication parallelizes perfectly
- Checkpointing is how you get fault tolerance in training (same concept as WAL/snapshots)
- Experiment tracking is essential — after a dozen training runs with different hyperparameters, you will lose track without it. This is the ML equivalent of structured logging.

**Format:** Jupyter notebook. GPU optional but recommended.

**Distributed systems bridge:** Checkpointing during training is exactly like write-ahead logging. If training crashes, you resume from the last checkpoint. The tradeoff (checkpoint frequency vs. training speed) is the same as WAL fsync frequency vs. write throughput. Experiment tracking is structured logging and observability for the training process — you'd never run a production service without logs, don't run training without them either.

**Time estimate:** 1.5 weeks

---

### Lab 8: Run an LLM Locally

**Goal:** Demystify large language models. They're not magic — they're next-token predictors with a lot of parameters.

**What you build:**
- Run Llama 3.2 (1B) locally via Ollama — one command, no GPU required
- Benchmark: tokens per second, time to first token, memory usage
- Compare model sizes: 1B vs 3B vs 8B — see the resource/quality tradeoff
- Run with and without quantization (FP16 vs INT4) — measure quality vs. speed vs. memory
- Serve it as an API, load test it, see how fundamentally different LLM latency is (streaming, variable response length)

**What you should know after:**
- An LLM generates one token at a time — latency scales with output length
- Time-to-first-token (TTFT) and tokens-per-second (TPS) are the metrics that matter, not just p99 latency
- Model size determines memory requirements (roughly 2x parameters in bytes for FP16)
- Quantization trades precision for memory/speed — often negligible quality loss
- Bigger models are not always better for every task

**Format:** Docker + CLI. Grafana for benchmarking dashboards.

**Distributed systems bridge:** LLM serving is a streaming system. The response is generated incrementally, like a paginated API or a Kafka consumer. Quantization is lossy compression — same tradeoff as downsampling time-series data.

**Time estimate:** 1 week

---

### Lab 9: Fine-Tune a Model

**Goal:** Understand how production ML actually works — you almost never train from scratch. You adapt existing models.

**The setup:** In Lab 7 you trained a neural net from scratch on CIFAR-10. That took significant compute for a small model on a toy dataset. In the real world, nobody does this. Instead, you take a model someone else already trained on massive data and adapt it to your specific problem. This is called fine-tuning, and it's how 90% of production ML works.

**What you build:**
- Take a pre-trained model (DistilBERT — a small transformer)
- Explore what it already knows: feed it text, look at its internal representations (embeddings). Notice that similar meanings cluster together in vector space. This is a concept you'll use heavily in Lab 10.
- Fine-tune it on a specific task (e.g., classify support tickets by category, or detect toxic comments)
- Compare: pre-trained model zero-shot vs. fine-tuned model on your data
- Build a proper evaluation: create a labeled test set, measure before/after, use the golden set pattern from Lab 5. Don't just eyeball it — measure it.
- Measure the cost: how much data, how much compute, how much time
- Deploy the fine-tuned model alongside the base model, compare them in production (callback to Lab 4 for serving, Lab 5 for monitoring, Lab 6 for retraining pipeline)
- Track the full lineage: which base model, which training data (version from Lab 2), which hyperparameters (tracking from Lab 7), which evaluation results

**What you should know after:**
- Transfer learning is the default approach — training from scratch is rare and expensive
- Pre-trained models already understand language/images — you're just teaching them your specific task
- Fine-tuning updates a small fraction of the model's parameters on your specific data
- The amount of training data needed is surprisingly small (hundreds to thousands of examples)
- The real cost of fine-tuning is not compute — it's creating good labeled training data
- Embeddings are the internal representations a model creates — similar inputs produce nearby vectors. This is the foundation of search, recommendations, and RAG.
- Model lineage matters — in production, you need to answer "what data and parameters produced this model?" at any time

**Format:** Jupyter notebook for fine-tuning, Docker Compose for deployment (reuse infra from Labs 3-6).

**Distributed systems bridge:** Fine-tuning is like database schema migration — you're taking an existing system and adapting it for new requirements without rebuilding from scratch. Model versioning is artifact versioning — same problem as container image tags or database migration numbers. Model lineage is provenance tracking — the same concept as tracing a request through a service chain, but for the model's entire history.

**Time estimate:** 1.5 weeks

---

### Lab 10: Build a RAG System

**Goal:** Build the most common LLM production pattern end-to-end and learn where it breaks.

**Why RAG matters:** RAG (Retrieval-Augmented Generation) is the default architecture for LLM applications that need to answer questions about specific data. It ties together data quality (Lab 2), model serving (Lab 3), performance under load (Lab 4), monitoring quality not just uptime (Lab 5), LLM inference (Lab 8), and embeddings (Lab 9). It's also the retrieval backbone you'll use inside the agent system in Lab 11.

**What you build:**
- Ingestion pipeline: load documents, chunk them, generate embeddings (using what you learned in Lab 9), store in a vector DB (ChromaDB or Qdrant)
- Query pipeline: embed the question, retrieve relevant chunks, feed to LLM with context, generate answer
- Instrument every step: chunking time, embedding latency, retrieval scores, LLM generation time — trace the full request like you traced service chains in your distributed systems labs
- Break it on purpose: ask questions the documents don't cover, use bad chunking strategies, retrieve too few/many chunks, feed it contradictory documents
- **Evaluation — the hard part:**
  - Build a golden Q&A set: questions with known correct answers. Score retrieval (did it find the right chunks?) and generation (did the LLM produce the right answer?) separately.
  - Try LLM-as-judge: use a second LLM to grade the first LLM's answers. See where this works and where it fails.
  - Measure retrieval precision and recall independently from answer quality — if retrieval is bad, a better LLM won't help.
  - Compare: same questions, different chunking strategies, different retrieval parameters. Quantify the difference.

**What you should know after:**
- RAG quality depends on retrieval quality, not LLM quality — garbage in, garbage out
- Chunking strategy matters enormously — too small loses context, too large dilutes relevance
- The retrieval step is a nearest-neighbor search on embeddings, which is an approximate algorithm with tunable precision (you saw embeddings in Lab 9 — now you're using them at scale)
- Evaluating generative AI is fundamentally harder than evaluating classifiers — there's no single number. You need multiple evaluation strategies (golden sets, LLM-as-judge, human review) and none of them is sufficient alone.
- Debugging "bad answers" is a pipeline tracing problem — was the chunk missing? Was it retrieved but ranked low? Did the LLM hallucinate despite good context?

**Format:** Docker Compose (vector DB + embedding service + LLM + API). Mix of notebooks for exploration and services for the production pipeline.

**Distributed systems bridge:** RAG is a multi-stage pipeline — like a microservice chain where each hop transforms the request. Debugging "bad answers" requires tracing through the pipeline, just like debugging latency in a service chain (week 1 of the distributed systems series). Vector search is a probabilistic data structure — like bloom filters (week 22), it trades exactness for speed. The ingestion pipeline is a batch processing system. The query pipeline is a real-time service. You've built both patterns before. Evaluation is the new challenge — unlike distributed systems where "correct" is binary (the response matches or it doesn't), generative AI correctness is a spectrum.

**Time estimate:** 2 weeks

---

### Lab 11: Design an AI Agent — The Capstone

**Goal:** Build an AI agent that reasons, uses tools, and completes multi-step tasks — then learn why this is a distributed systems problem disguised as an AI problem.

**Why this is the finale:** Every concept in this series converges here. An agent uses an LLM (Lab 8) with tool calling. It retrieves knowledge via RAG (Lab 10). It needs evaluation (Labs 1-10 progression). It needs guardrails (data quality thinking from Lab 2). And the architecture — orchestration loops, state management, failure handling, cost control — is pure distributed systems engineering. This is the system design question companies are asking now.

**What you build:**
- **The agent loop from scratch:** LLM receives a task → decides what tool to call → executes the tool → observes the result → decides next step → repeats until done. Build this yourself before using a framework. Understand the loop.
- **Tool integration:** Give the agent 3-4 tools — a RAG retriever (reuse Lab 10), a calculator, a web search mock, and a database query tool. Define tool schemas (name, description, parameters, return type). Handle tool call parsing. Handle tools that fail, return garbage, or timeout. Note: the industry is converging on standardized tool protocols — Anthropic's MCP (Model Context Protocol) is the emerging standard for how LLMs discover and call tools, the same way REST standardized web APIs. You don't need to use MCP here, but understand the problem it solves: without a standard, every tool integration is a snowflake.
- **Memory and state:**
  - Short-term: conversation history (context window management — what do you do when the conversation exceeds the context limit?)
  - Long-term: persistent memory store (what did the agent learn in previous sessions?). This is distributed session state.
- **Planning patterns:** Implement two approaches and compare:
  - ReAct (reason-then-act): LLM thinks step by step, acts, observes, repeats. Simple but can loop.
  - Plan-then-execute: LLM creates a plan upfront, then executes steps. More structured but brittle when plans need to change.
- **Guardrails and safety:**
  - Input guardrails: reject prompt injections, off-topic requests
  - Output guardrails: validate tool calls before executing (don't let the agent delete your database)
  - Execution limits: max steps, max cost, timeout. An agent without limits will run forever and cost you money.
- **Cost awareness:** Log the cost of every LLM call. See how quickly a multi-step agent burns through tokens. Implement cost optimizations: route simple decisions to a smaller/cheaper model, cache frequent tool results, short-circuit when the answer is already known.
- **Evaluation — the hardest yet:**
  - Build a task suite: 20 tasks with known correct outcomes. Run the agent on each. Measure: did it complete the task? How many steps? How much cost? Did it use the right tools?
  - Test failure recovery: kill a tool mid-execution. Does the agent retry? Recover? Give up gracefully?
  - Test guardrails: try to get the agent to do something it shouldn't. Does it refuse?

**What you should know after:**
- An agent is a control loop — the same concept as a Kubernetes controller, a thermostat, or a reconciliation loop. It observes state, decides on action, acts, and repeats.
- Tool failures are the #1 production issue — the LLM is usually fine, but the tools it calls fail, timeout, or return unexpected data. This is RPC reliability all over again.
- State management is the hard architectural decision — stateless agents are simple but forgetful, stateful agents are powerful but complex to scale. Same tradeoff as stateless vs. stateful services.
- Agents without limits are dangerous — they will loop, burn money, and take unintended actions. Cost controls and execution limits are not optional.
- Multi-step evaluation is fundamentally harder than single-turn evaluation — you're testing a path through a decision tree, not a single input/output pair. It's closer to integration testing than unit testing.
- Most production agents are simpler than you'd think — a well-designed RAG system with a thin agent layer beats a complex autonomous agent for most use cases. Know when NOT to build an agent.

**Format:** Docker Compose (LLM + tool services + memory store + API). Python for the agent orchestrator.

**Distributed systems bridge:**

| Agent concept | Distributed systems equivalent | Where you've seen it |
|---------------|-------------------------------|---------------------|
| Agent loop (observe → decide → act → repeat) | Reconciliation / control loop | Kubernetes controllers, thermostats |
| Tool calls that fail or timeout | RPC failures | Lab 4 (load), weeks 6-7 of DS series (retries, circuit breakers) |
| Retry on tool failure with backoff | Retry storms, exponential backoff | Week 6 of DS series |
| Conversation memory / context window | Distributed session state | Stateful services, session stores |
| Long-term memory | Persistent storage, caches | Labs 2-6 (data stores), DS series caching arc |
| Multi-agent coordination | Microservice orchestration | Saga pattern (week 29 of DS series) |
| Guardrails | Input validation, authorization | API boundary validation, RBAC |
| Cost limits, execution timeouts | Rate limiting, timeout budgets | Week 11 (rate limiting), week 51 (timeout budgets) of DS series |
| Plan-then-execute | Workflow orchestration | Job scheduler (week 24 of DS series) |

This is the lab where the distributed systems background pays off the most. Half the "new" agent problems are old distributed systems problems with new names.

**Time estimate:** 2.5 weeks

---

## Progression Map

```
Phase 1: The ML Lifecycle (Labs 1-6)
Build, ship, break, detect, repair — one model, end to end.

  Lab 1 → Lab 2 → Lab 3 → Lab 4 → Lab 5 → Lab 6
  Train    Clean    Deploy   Stress   Drift    Retrain
           data              test     detect   pipeline

Phase 2: Level Up to Deep Learning (Labs 7-8)
Why classical ML has limits. Enter neural nets and LLMs.

  Lab 7 → Lab 8
  Neural   Run an
  nets     LLM

Phase 3: Production AI (Labs 9-11)
Adapt models, build real systems, design agent architectures.

  Lab 9 → Lab 10 → Lab 11
  Fine-    RAG       Agent
  tune     system    architecture
                     (capstone)
```

Each phase builds on the previous. Phase 1 teaches the full lifecycle with the simplest possible model — so the concepts (data quality, evaluation, monitoring, retraining) are clear before the models get complex. Phase 2 upgrades the model sophistication. Phase 3 builds production systems of increasing complexity: Lab 9 introduces embeddings and transfer learning, Lab 10 combines them into a retrieval pipeline, Lab 11 wraps everything into an autonomous agent that uses RAG as a tool. The finale is where your distributed systems background becomes your superpower.

---

## What You'll Need

| Lab | Python | Docker Compose | Jupyter | GPU |
|:---:|:------:|:--------------:|:-------:|:---:|
| 1   | yes    |                | yes     |     |
| 2   | yes    |                | yes     |     |
| 3   | yes    | yes            |         |     |
| 4   | yes    | yes            |         |     |
| 5   | yes    | yes            |         |     |
| 6   | yes    | yes            |         |     |
| 7   | yes    |                | yes     | optional |
| 8   |        | yes            |         | optional |
| 9   | yes    | yes            | yes     | optional |
| 10  | yes    | yes            | yes     | optional |
| 11  | yes    | yes            |         | optional |

GPU is never required — just makes Labs 7-11 faster. Everything runs on CPU for learning purposes.

---

## What This Deliberately Skips

| Topic | Why |
|-------|-----|
| Distributed training | You'll never need to train across GPUs. If you do, your distributed systems background makes it easy to pick up. |
| MLOps platforms (MLflow, SageMaker, Kubeflow) | Learn the pain before learning the painkiller. These labs give you the pain. Lab 7 introduces experiment tracking manually — you'll know exactly why MLflow exists by the time you're done. |
| Math (backprop derivations, linear algebra proofs) | You need intuition, not proofs. The code teaches the intuition. |
| Kaggle-style competitions | Different skill (feature engineering for leaderboards). Not production-relevant. |
| Multi-tenant serving platforms | Too broad, too vague. Build one service well before building a platform. |
| Feature stores | Interesting but premature. You need to understand features (from Lab 1), data quality (from Lab 2), drift (from Lab 5), and retraining (from Lab 6) before a feature store makes sense. Come back to this after completing all 11 labs. |
| Multi-agent frameworks (LangChain, CrewAI, AutoGen) | Lab 11 builds an agent from scratch first. Same philosophy as the rest of the series — understand the pain before adopting the painkiller. Once you've built the loop yourself, you'll evaluate frameworks with informed skepticism. |

---

## Recurring Themes

These concepts weave through multiple labs rather than living in a single one:

### Evaluation
Evaluation isn't one lab — it's a skill that deepens throughout the series.

| Lab | Evaluation concept introduced |
|:---:|-------------------------------|
| 1   | Train/test/validation splits. Accuracy, precision, recall, F1. Why accuracy lies. |
| 2   | Data quality as the first evaluation — check your inputs before checking your outputs. |
| 5   | Golden evaluation sets — a curated, stable test set for ongoing model quality. |
| 6   | Validation gates — new model must beat old model on the golden set before deploying. |
| 9   | Evaluating fine-tuned models — before/after comparison with statistical rigor. |
| 10  | Evaluating generative AI — golden Q&A sets, LLM-as-judge, retrieval vs. generation quality. |
| 11  | Evaluating multi-step agents — task completion rate, cost per task, failure recovery, guardrail testing. The hardest evaluation problem: testing a decision tree, not a single call. |

**The progression:** Simple metrics (Lab 1) → data validation (Lab 2) → ongoing monitoring (Lab 5) → automated gates (Lab 6) → rigorous comparison (Lab 9) → evaluating non-deterministic outputs (Lab 10) → evaluating non-deterministic multi-step behavior (Lab 11).

### Experiment Tracking
Introduced in Lab 7 (neural nets) where hyperparameter tuning creates the need. Reinforced in Lab 9 (fine-tuning) where model lineage becomes critical. Kept deliberately simple (CSV/JSON logs) so you understand the problem before adopting tools.

### Data Quality
Introduced in Lab 2 as a dedicated lab. Reinforced in Lab 6 where incoming feedback data must pass quality checks before entering the retraining pipeline. The lesson: data quality isn't a one-time cleanup — it's a continuous gate in any ML pipeline.

---

## Content Angle

Each lab's video/writeup follows the same structure:

1. **What I thought going in** — the misconception or gap
2. **What I built** — the hands-on work
3. **The moment it clicked** — the specific aha
4. **How it connects to distributed systems** — the bridge for the audience
5. **What I'd do differently** — honest retrospective

This is not "expert teaches ML." This is "systems engineer figures out ML in public." The honesty is the content.

---

## Suggested Timeline

| Weeks | Labs | Phase |
|:-----:|:----:|-------|
| 1-2   | 1-2  | **Phase 1a:** Train a model, then face real data |
| 3-5   | 3-5  | **Phase 1b:** Deploy, stress test, detect drift |
| 6-7   | 6    | **Phase 1c:** Close the loop — retraining pipeline |
| 8-9   | 7    | **Phase 2a:** Why scikit-learn isn't enough — neural nets |
| 10    | 8    | **Phase 2b:** Run an LLM locally — demystify the hype |
| 11-12 | 9    | **Phase 3a:** Fine-tune a model — how production ML works |
| 13-14 | 10   | **Phase 3b:** Build a RAG system — the core LLM architecture |
| 15-17 | 11   | **Phase 3c:** Design an AI agent — the capstone |

17 weeks total at a focused pace. Stretch to 22-24 if learning alongside other work.

### Narrative arcs

**Arc 1: "Train → Break → Fix" (Labs 1-6)** — The complete ML lifecycle with classical ML. Start with clean data (Lab 1), face reality (Lab 2), ship it (Lab 3), stress it (Lab 4), watch it degrade (Lab 5), fix it automatically (Lab 6). This mirrors the "caching journey" arc from the distributed systems series: build it, break it, fix it.

**Arc 2: "Level Up" (Labs 7-8)** — Hit the ceiling of classical ML, discover deep learning, then see what happens at billion-parameter scale. The aha is: same concepts, different order of magnitude.

**Arc 3: "Put It All Together" (Labs 9-11)** — Fine-tuning shows how production ML really works (adapt, don't build from scratch). RAG combines retrieval, embeddings, and LLMs into the most common production pattern. The agent lab is the grand finale — where your distributed systems background becomes your superpower. The "new" problems in agent architecture (orchestration loops, tool failure handling, state management, cost control, execution limits) are old distributed systems problems wearing new hats. This is the moment the two worlds merge.
