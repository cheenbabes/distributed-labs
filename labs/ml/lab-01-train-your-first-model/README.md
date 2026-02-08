# ML Lab 01: Train Your First Model

You've heard the term "machine learning model" a thousand times — but what actually IS a model? In this lab, you'll train one from scratch, watch it learn patterns from text data, discover why accuracy is a trap, and save the result to a file you can inspect. By the end, the mystery is gone: a model is just learned parameters stored on disk.

## What You'll Learn

- How to train a text classification model from raw data to predictions
- Why accuracy alone is a misleading metric (and what to use instead)
- How train/validation/test splits prevent you from fooling yourself
- What overfitting looks like and how to fix it
- What a "model file" actually contains

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Raw Text    │────▶│  Preprocess  │────▶│    Train     │────▶│   Evaluate   │
│  (20news)    │     │  (TF-IDF)    │     │  (LogReg /   │     │  (Metrics +  │
│              │     │              │     │   DecTree)   │     │  Confusion)  │
└──────────────┘     └──────────────┘     └──────────────┘     └──────┬───────┘
                                                                      │
                                                                      ▼
                                                               ┌──────────────┐
                                                               │  Save Model  │
                                                               │  (joblib)    │
                                                               └──────────────┘
```

## Prerequisites

- Docker and Docker Compose

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Open Jupyter Lab

Open [http://localhost:8888](http://localhost:8888) in your browser.

### 3. Open the Notebook

Navigate to `notebooks/train_sentiment_model.ipynb` and work through each section.

| Service | URL | Purpose |
|---------|-----|---------|
| Jupyter Lab | http://localhost:8888 | Interactive notebook environment |

## Lab Exercises

### Exercise 1: Load and Explore the Data

Load the 20 Newsgroups dataset (built into scikit-learn) and explore what text classification data looks like. You'll pick two categories — baseball vs. space — for binary classification.

**You'll answer:** How many samples per class? What does the raw text look like?

### Exercise 2: Your First Model

Build a TF-IDF + Logistic Regression pipeline and train it. Get predictions, print accuracy, and feel good about yourself (temporarily).

**You'll answer:** What accuracy did you get? Does it seem trustworthy?

### Exercise 3: Why Accuracy Lies

Create an intentionally imbalanced dataset (95% vs 5%) and watch your model score 95% accuracy by doing nothing useful. Discover precision, recall, F1, and the confusion matrix.

**You'll answer:** What does the confusion matrix reveal that accuracy hides?

### Exercise 4: Proper Train/Test/Validation Split

Learn why two splits aren't enough and implement a three-way split. See how tuning against your test set leaks information.

**You'll answer:** Why is a validation set necessary? What goes wrong without one?

### Exercise 5: Overfit on Purpose

Train a Decision Tree with no constraints and watch it memorize the training data (100% train accuracy) while failing on test data. Then fix it.

**You'll answer:** What's the train/test accuracy gap before and after the fix?

### Exercise 6: Save and Inspect the Model

Save your trained model to disk with joblib, check the file size, reload it, and inspect what's inside. Demystify what a "model" actually is.

**You'll answer:** What's in the model file? How big is it? Can you see the learned weights?

## Key Takeaways

1. **A model is learned parameters** — coefficients, splits, weights stored in a file
2. **Accuracy is dangerous on imbalanced data** — always check precision, recall, and the confusion matrix
3. **Three-way splits prevent self-deception** — train, validate, then do a final test
4. **Overfitting = memorization** — perfect training score with bad test score means the model learned noise, not patterns

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

The container may be running out of memory. Check resource usage:

```bash
docker stats --no-stream
```

## What's Next

[ML Lab 02: Deploy Your Model as an API](../lab-02-deploy-model-api/) — Take the model you just saved and serve it behind a FastAPI endpoint.
