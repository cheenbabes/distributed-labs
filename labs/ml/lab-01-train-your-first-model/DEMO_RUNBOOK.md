# Demo Runbook: Train Your First Model

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
echo "Navigate to: notebooks/train_sentiment_model.ipynb"
```

---

## Part 2: Load and Explore the Data

Key cells to run in the notebook:

```python {"name": "load-data"}
# In the notebook — load the 20 Newsgroups dataset
from sklearn.datasets import fetch_20newsgroups

categories = ['rec.sport.baseball', 'sci.space']
train_data = fetch_20newsgroups(subset='train', categories=categories, shuffle=True, random_state=42)
test_data = fetch_20newsgroups(subset='test', categories=categories, shuffle=True, random_state=42)

print(f"Training samples: {len(train_data.data)}")
print(f"Test samples: {len(test_data.data)}")
print(f"Classes: {train_data.target_names}")
```

---

## Part 3: Train Your First Model

```python {"name": "first-model"}
# Build a TF-IDF + Logistic Regression pipeline
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score

pipeline = Pipeline([
    ('tfidf', TfidfVectorizer(max_features=10000, stop_words='english')),
    ('clf', LogisticRegression(max_iter=1000, random_state=42))
])

pipeline.fit(train_data.data, train_data.target)
predictions = pipeline.predict(test_data.data)
print(f"Accuracy: {accuracy_score(test_data.target, predictions):.3f}")
```

---

## Part 4: Why Accuracy Lies

```python {"name": "accuracy-lies"}
# Create an imbalanced dataset and watch accuracy mislead you
import numpy as np

# 95% class 0, 5% class 1
np.random.seed(42)
n_majority = 950
n_minority = 50
# ... (see notebook for full implementation)
# Key reveal: confusion matrix shows model only predicts majority class
```

---

## Part 5: Proper Train/Val/Test Split

```python {"name": "three-way-split"}
# Split into train/validation/test
from sklearn.model_selection import train_test_split

# 60% train, 20% validation, 20% test
X_train, X_temp, y_train, y_temp = train_test_split(
    train_data.data, train_data.target, test_size=0.4, random_state=42
)
X_val, X_test, y_val, y_test = train_test_split(
    X_temp, y_temp, test_size=0.5, random_state=42
)
print(f"Train: {len(X_train)}, Val: {len(X_val)}, Test: {len(X_test)}")
```

---

## Part 6: Overfit on Purpose

```python {"name": "overfitting"}
# Train a Decision Tree with no limits — watch it memorize
from sklearn.tree import DecisionTreeClassifier

# No constraints = memorization
overfit_pipe = Pipeline([
    ('tfidf', TfidfVectorizer(max_features=10000, stop_words='english')),
    ('clf', DecisionTreeClassifier(random_state=42))  # no max_depth!
])
overfit_pipe.fit(X_train, y_train)

train_acc = overfit_pipe.score(X_train, y_train)
test_acc = overfit_pipe.score(X_test, y_test)
print(f"Train accuracy: {train_acc:.3f}")  # ~1.000
print(f"Test accuracy:  {test_acc:.3f}")   # much lower
print(f"Gap: {train_acc - test_acc:.3f}")  # this IS overfitting
```

---

## Part 7: Save and Inspect the Model

```python {"name": "save-model"}
# Save the trained pipeline
import joblib
import os

joblib.dump(pipeline, 'notebooks/sentiment_model.joblib')
size_mb = os.path.getsize('notebooks/sentiment_model.joblib') / (1024 * 1024)
print(f"Model saved! Size: {size_mb:.2f} MB")

# Load it back and verify
loaded_model = joblib.load('notebooks/sentiment_model.joblib')
loaded_preds = loaded_model.predict(test_data.data[:3])
print(f"Predictions from loaded model: {loaded_preds}")
```

---

## Part 8: Inspect Model Internals

```python {"name": "inspect-model"}
# Look inside the model
feature_names = pipeline.named_steps['tfidf'].get_feature_names_out()
coefficients = pipeline.named_steps['clf'].coef_[0]

# Top features for each class
top_positive = np.argsort(coefficients)[-10:]
top_negative = np.argsort(coefficients)[:10]

print("Top words for 'sci.space':")
for i in top_positive:
    print(f"  {feature_names[i]}: {coefficients[i]:.3f}")

print("\nTop words for 'rec.sport.baseball':")
for i in top_negative:
    print(f"  {feature_names[i]}: {coefficients[i]:.3f}")
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
| Notebook path | `notebooks/train_sentiment_model.ipynb` |
| Solution path | `solutions/completed_notebook.ipynb` |
