# ML Lab 02: Your Data Is a Mess

In Lab 1 you trained a model on clean, balanced data and got 97% accuracy. Felt great, right? Now meet reality. Real-world data has duplicates, mislabeled examples, garbage strings, and class imbalance. In this lab, you'll intentionally corrupt a dataset, watch your model fall apart, learn to diagnose the problems, clean them up, and build validation checks so it never happens silently again.

## What You'll Learn

- How common data quality issues (duplicates, mislabels, imbalance) destroy model performance
- How to do exploratory data analysis (EDA) that actually catches problems
- Practical techniques to clean text data before training
- How to build a data validation pipeline as a pre-training gate
- Why data quality matters more than model complexity

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Clean Data  │────▶│  Corrupt It  │────▶│  Train on    │────▶│  Watch It    │
│  (20news)    │     │  (duplicates │     │  Messy Data  │     │  Fall Apart  │
│              │     │   mislabels  │     │              │     │              │
│              │     │   garbage)   │     │              │     │              │
└──────────────┘     └──────────────┘     └──────────────┘     └──────┬───────┘
                                                                      │
                     ┌──────────────┐     ┌──────────────┐            │
                     │  Validation  │◀────│  Clean It Up │◀───────────┘
                     │  Pipeline    │     │  (EDA +      │
                     │  (gates)     │     │   filtering) │
                     └──────────────┘     └──────────────┘
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

Navigate to `notebooks/messy_data_lab.ipynb` and work through each section.

| Service | URL | Purpose |
|---------|-----|---------|
| Jupyter Lab | http://localhost:8888 | Interactive notebook environment |

## Lab Exercises

### Exercise 1: The Pristine Baseline

Load the 20 Newsgroups dataset and train a LogisticRegression pipeline to establish a clean baseline. You already know how to do this from Lab 1 — now see what ~97% accuracy looks like when the data is clean.

**You'll answer:** What accuracy do you get on clean, balanced data?

### Exercise 2: Meet Your Messy Data

Programmatically corrupt the dataset by injecting duplicates, flipping labels, adding garbage strings, and creating class imbalance. Train the same model on the corrupted data and watch every metric crater.

**You'll answer:** How much did each type of corruption hurt? Which one was worst?

### Exercise 3: Explore Before You Train (EDA)

Before touching the model, explore the data. Plot class distributions, text length histograms, check for duplicates and empty strings, and sample examples to spot-check labels. This is what you should do before every training run.

**You'll answer:** Which problems can you spot visually? Which require code to detect?

### Exercise 4: Clean It Up

Apply cleaning steps one by one: remove duplicates, filter garbage strings, flag suspicious labels, and handle class imbalance with `class_weight='balanced'`. Track sample counts at each step.

**You'll answer:** How many samples did you remove? How did you decide what to keep?

### Exercise 5: Retrain and Compare

Train on the cleaned data and compare side-by-side with the messy model. Build a comparison table with accuracy, precision, recall, and F1 per class. Plot confusion matrices side by side.

**You'll answer:** How close did you get to the pristine baseline? What's still missing?

### Exercise 6: Build a Data Validation Pipeline

Write reusable validation functions that check for class balance, duplicates, empty texts, and text length anomalies. Run them as a pre-training gate that would have caught every issue from Section 2.

**You'll answer:** If these checks ran automatically, which problems would they have caught?

## Key Takeaways

1. **Data quality > model complexity** — cleaning your data will improve results more than switching to a fancier algorithm
2. **Always do EDA first** — never train on data you haven't explored visually and statistically
3. **Corruption is silent** — duplicates, mislabels, and imbalance don't throw errors, they just make your model worse
4. **Automate validation** — write checks that run before every training job so problems are caught before they reach the model

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

[ML Lab 03: Deploy a Model as an API](../lab-03-deploy-model-api/) — Take the model you trained and cleaned and serve it behind a FastAPI endpoint with Prometheus metrics and Grafana dashboards.
