# Demo Runbook: Your Data Is a Mess

This runbook contains all commands for the video demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "✓ Docker is running" || echo "✗ Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose down -v 2>/dev/null || true
```

---

## Part 1: Start the Lab

### Build and start Jupyter Lab

```bash {"name": "start-lab"}
docker compose up --build -d
```

### Wait for Jupyter Lab to be ready

```bash {"name": "wait-healthy"}
echo "Waiting for Jupyter Lab..."
sleep 10
docker compose ps
```

### Show the Jupyter Lab URL

```bash {"name": "show-url"}
echo "Open Jupyter Lab in your browser:"
echo ""
echo "  http://localhost:8888"
echo ""
echo "Navigate to: notebooks/messy_data_lab.ipynb"
```

---

## Part 2: The Pristine Baseline

Key cells to run in the notebook:

```python {"name": "pristine-baseline"}
# In the notebook — load data and train on clean data
from sklearn.datasets import fetch_20newsgroups
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score

categories = ['rec.sport.baseball', 'sci.space']
train_data = fetch_20newsgroups(subset='train', categories=categories, shuffle=True, random_state=42)
test_data = fetch_20newsgroups(subset='test', categories=categories, shuffle=True, random_state=42)

pipeline = Pipeline([
    ('tfidf', TfidfVectorizer(max_features=10000, stop_words='english')),
    ('clf', LogisticRegression(max_iter=1000, random_state=42))
])
pipeline.fit(train_data.data, train_data.target)
print(f"Pristine accuracy: {accuracy_score(test_data.target, pipeline.predict(test_data.data)):.3f}")
```

---

## Part 3: Meet Your Messy Data

```python {"name": "corrupt-data"}
# Corrupt the dataset: duplicates, mislabels, garbage, imbalance
import numpy as np

np.random.seed(42)
messy_texts = list(train_data.data)
messy_labels = list(train_data.target)

# Inject ~10% duplicates
n_dups = int(len(messy_texts) * 0.10)
dup_idx = np.random.choice(len(messy_texts), n_dups, replace=False)
for i in dup_idx:
    messy_texts.append(messy_texts[i])
    messy_labels.append(messy_labels[i])

print(f"After duplicates: {len(messy_texts)} samples")

# Flip ~5% labels
n_flip = int(len(messy_texts) * 0.05)
flip_idx = np.random.choice(len(messy_texts), n_flip, replace=False)
for i in flip_idx:
    messy_labels[i] = 1 - messy_labels[i]

print(f"Flipped {n_flip} labels")
```

---

## Part 4: Explore Before You Train (EDA)

```python {"name": "eda"}
# Check class distribution, duplicates, empty texts
import pandas as pd

df = pd.DataFrame({'text': messy_texts, 'label': messy_labels})
print("Class distribution:")
print(df['label'].value_counts())
print(f"\nDuplicates: {df.duplicated(subset='text').sum()}")
print(f"Empty/short texts: {(df['text'].str.len() < 10).sum()}")
```

---

## Part 5: Clean It Up

```python {"name": "clean-data"}
# Remove duplicates, garbage, handle imbalance
df_clean = df.drop_duplicates(subset='text')
df_clean = df_clean[df_clean['text'].str.len() >= 10]
print(f"After cleaning: {len(df_clean)} samples (removed {len(df) - len(df_clean)})")
```

---

## Part 6: Retrain and Compare

```python {"name": "retrain"}
# Train on cleaned data and compare
from sklearn.metrics import classification_report

clean_pipeline = Pipeline([
    ('tfidf', TfidfVectorizer(max_features=10000, stop_words='english')),
    ('clf', LogisticRegression(max_iter=1000, class_weight='balanced', random_state=42))
])
clean_pipeline.fit(df_clean['text'].tolist(), df_clean['label'].values)
clean_preds = clean_pipeline.predict(test_data.data)
print(classification_report(test_data.target, clean_preds, target_names=train_data.target_names))
```

---

## Part 7: Data Validation Pipeline

```python {"name": "validation"}
# Build reusable validation functions
def check_class_balance(labels, max_ratio=3.0):
    counts = pd.Series(labels).value_counts()
    ratio = counts.max() / counts.min()
    passed = ratio <= max_ratio
    print(f"  Class balance ratio: {ratio:.1f} ({'PASS' if passed else 'FAIL'})")
    return passed

def check_duplicates(texts, max_pct=1.0):
    n_dups = pd.Series(texts).duplicated().sum()
    pct = n_dups / len(texts) * 100
    passed = pct <= max_pct
    print(f"  Duplicate rate: {pct:.1f}% ({'PASS' if passed else 'FAIL'})")
    return passed

# Run checks
check_class_balance(messy_labels)
check_duplicates(messy_texts)
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "✓ Lab cleaned up"
```

---

## Troubleshooting Commands

### Check Jupyter Lab logs

```bash {"name": "logs"}
docker compose logs jupyter --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart Jupyter Lab

```bash {"name": "restart"}
docker compose restart jupyter
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| View logs | `docker compose logs jupyter` |
| Jupyter Lab | http://localhost:8888 |
| Notebook path | `notebooks/messy_data_lab.ipynb` |
| Solution path | `solutions/completed_notebook.ipynb` |
