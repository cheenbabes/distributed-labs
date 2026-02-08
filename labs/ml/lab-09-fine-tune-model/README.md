# ML Lab 09: Fine-Tune a Model

Pre-trained models like BERT already understand language -- they've read the entire internet. But they don't know YOUR task. In this lab, you'll take DistilBERT (a smaller, faster BERT), see what it already knows, then fine-tune it on a specific classification task. A few hundred examples and three epochs later, you'll have a specialist model that crushes zero-shot performance. This is how real ML teams adapt foundation models to production problems.

## What You'll Learn

- What a pre-trained language model already knows before you touch it
- How zero-shot classification works and where it falls short
- How to fine-tune DistilBERT for text classification with HuggingFace Transformers
- How training loss, learning rate, and epochs affect model quality
- How to visualize learned embeddings to see what the model has learned
- How to save and track fine-tuned models for reproducibility

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Pre-trained │────▶│  Zero-Shot   │────▶│  Fine-Tune   │────▶│   Evaluate   │
│  DistilBERT  │     │  Baseline    │     │  (3 epochs)  │     │  (Accuracy,  │
│              │     │              │     │              │     │  F1, t-SNE)  │
└──────────────┘     └──────────────┘     └──────────────┘     └──────┬───────┘
                                                                      │
                                                                      ▼
                                                               ┌──────────────┐
                                                               │  Save Model  │
                                                               │  + Lineage   │
                                                               └──────────────┘
```

## Prerequisites

- Docker and Docker Compose
- At least 4 GB of free RAM (DistilBERT is small but fine-tuning needs headroom)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

Note: The first build downloads PyTorch and Transformers (~2 GB). Subsequent builds use the Docker cache.

### 2. Open Jupyter Lab

Open [http://localhost:8888](http://localhost:8888) in your browser.

### 3. Open the Notebook

Navigate to `notebooks/fine_tuning_lab.ipynb` and work through each section.

| Service | URL | Purpose |
|---------|-----|---------|
| Jupyter Lab | http://localhost:8888 | Interactive notebook environment |

## Lab Exercises

### Exercise 1: What Does a Pre-trained Model Already Know?

Load DistilBERT and explore its embeddings. See that similar sentences produce nearby vectors -- the model already understands language structure without any task-specific training.

**You'll answer:** How similar are the embeddings for two sentences about the same topic?

### Exercise 2: Zero-Shot vs Fine-Tuned

Use DistilBERT's zero-shot classification pipeline on baseball vs space examples. Measure accuracy and see where general knowledge falls short on specific tasks.

**You'll answer:** What accuracy does zero-shot get? Where does it make mistakes?

### Exercise 3: Prepare Training Data

Load the 20 Newsgroups dataset (baseball vs space), tokenize it with the DistilBERT tokenizer, and create proper train/validation/test splits.

**You'll answer:** How many tokens does a typical example have? What does tokenized text look like?

### Exercise 4: Fine-Tune DistilBERT

Use the HuggingFace Trainer to fine-tune DistilBertForSequenceClassification. Watch training loss decrease over 3 epochs. Evaluate on the test set.

**You'll answer:** What accuracy did fine-tuning achieve? How much better is it than zero-shot?

### Exercise 5: Compare Results and Visualize

Build a comparison table, generate a classification report, plot a confusion matrix, and use t-SNE to visualize the learned embeddings in 2D.

**You'll answer:** How clearly separated are the two classes in embedding space after fine-tuning?

### Exercise 6: Save and Track Your Model

Save the fine-tuned model and tokenizer. Check file sizes. Record the full experiment lineage: base model, training data, hyperparameters, and final metrics.

**You'll answer:** What files make up a fine-tuned model? How big is it compared to the original?

## Key Takeaways

1. **Pre-trained models already understand language** -- they just need to be pointed at your task
2. **Zero-shot is a useful baseline** -- but fine-tuning with even a small dataset dramatically improves performance
3. **Fine-tuning is transfer learning** -- you're adapting existing knowledge, not starting from scratch
4. **Embeddings reveal what the model learned** -- t-SNE shows clear class separation after fine-tuning
5. **Model lineage matters** -- base model + data + hyperparameters = reproducible results

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Jupyter Lab not loading

```bash
docker compose logs jupyter
```

### Port 8888 already in use

```bash
lsof -i :8888
```

Kill the process or change the port in `docker-compose.yml`.

### Notebook kernel dies

Fine-tuning on CPU uses significant memory. If the kernel dies:

```bash
docker stats --no-stream
```

Try reducing `per_device_train_batch_size` from 16 to 8 in the training arguments.

### Build takes a long time

The first build downloads PyTorch CPU (~200 MB) and Transformers. This is cached after the first build:

```bash
docker compose build --progress=plain jupyter
```

### Model download is slow

DistilBERT (~260 MB) is downloaded the first time you run the notebook. HuggingFace caches it inside the container. If the container restarts, it will re-download. To persist the cache, add a volume for `/root/.cache/huggingface`.

## What's Next

[ML Lab 10: Build a RAG Pipeline](../lab-10-build-rag-pipeline/) -- Combine retrieval with generation to build a system that answers questions using your own documents.
