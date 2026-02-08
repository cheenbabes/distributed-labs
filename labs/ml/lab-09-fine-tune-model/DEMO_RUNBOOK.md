# Demo Runbook: Fine-Tune a Model

This runbook contains all commands for the video demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
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
sleep 15
docker compose ps
```

### Show the Jupyter Lab URL

```bash {"name": "show-url"}
echo "Open Jupyter Lab in your browser:"
echo ""
echo "  http://localhost:8888"
echo ""
echo "Navigate to: notebooks/fine_tuning_lab.ipynb"
```

---

## Part 2: Explore Pre-trained Knowledge

Key cells to run in the notebook:

```python {"name": "load-model"}
# In the notebook — load DistilBERT and explore embeddings
from transformers import AutoTokenizer, AutoModel
import torch

tokenizer = AutoTokenizer.from_pretrained("distilbert-base-uncased")
model = AutoModel.from_pretrained("distilbert-base-uncased")

sentences = [
    "The pitcher threw a fastball for a strike.",
    "The batter hit a home run in the ninth inning.",
    "NASA launched a rocket to the International Space Station.",
    "The telescope discovered a new exoplanet orbiting a distant star."
]

# Tokenize and get embeddings
inputs = tokenizer(sentences, padding=True, truncation=True, return_tensors="pt")
with torch.no_grad():
    outputs = model(**inputs)
embeddings = outputs.last_hidden_state[:, 0, :]  # CLS token
print(f"Embedding shape: {embeddings.shape}")
```

---

## Part 3: Zero-Shot Baseline

```python {"name": "zero-shot"}
# In the notebook — zero-shot classification
from transformers import pipeline

classifier = pipeline("zero-shot-classification", model="distilbert-base-uncased")
result = classifier(
    "The pitcher threw a fastball for a strike.",
    candidate_labels=["baseball", "space"]
)
print(f"Label: {result['labels'][0]}, Score: {result['scores'][0]:.3f}")
```

---

## Part 4: Prepare Data

```python {"name": "prepare-data"}
# In the notebook — load and tokenize data
from datasets import load_dataset

dataset = load_dataset("SetFit/20_newsgroups", trust_remote_code=True)
# Filter to baseball and space categories
# Tokenize with DistilBERT tokenizer
# Create train/val/test splits
```

---

## Part 5: Fine-Tune DistilBERT

```python {"name": "fine-tune"}
# In the notebook — fine-tuning with Trainer
from transformers import DistilBertForSequenceClassification, TrainingArguments, Trainer

model = DistilBertForSequenceClassification.from_pretrained(
    "distilbert-base-uncased", num_labels=2
)

training_args = TrainingArguments(
    output_dir="./results",
    num_train_epochs=3,
    per_device_train_batch_size=16,
    learning_rate=2e-5,
    evaluation_strategy="epoch",
    save_strategy="epoch",
    logging_steps=10,
    load_best_model_at_end=True,
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=train_dataset,
    eval_dataset=val_dataset,
)

trainer.train()
```

---

## Part 6: Evaluate and Compare

```python {"name": "evaluate"}
# In the notebook — compare zero-shot vs fine-tuned
predictions = trainer.predict(test_dataset)
# Print classification report
# Plot confusion matrix
# Show: zero-shot ~70% vs fine-tuned ~95%+
```

---

## Part 7: Visualize Embeddings

```python {"name": "visualize"}
# In the notebook — t-SNE visualization
from sklearn.manifold import TSNE

# Extract embeddings from fine-tuned model
# Reduce to 2D with t-SNE
# Plot with colors by class — clear separation!
```

---

## Part 8: Save the Model

```python {"name": "save-model"}
# In the notebook — save and log
model.save_pretrained("./fine_tuned_model")
tokenizer.save_pretrained("./fine_tuned_model")

import os
total_size = sum(
    os.path.getsize(os.path.join("./fine_tuned_model", f))
    for f in os.listdir("./fine_tuned_model")
)
print(f"Model size: {total_size / (1024*1024):.1f} MB")
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up"
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
| Notebook path | `notebooks/fine_tuning_lab.ipynb` |
| Solution path | `solutions/completed_notebook.ipynb` |
