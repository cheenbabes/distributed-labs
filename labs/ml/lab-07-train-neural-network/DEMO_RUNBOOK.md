# Demo Runbook: Train a Neural Network

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
echo "Navigate to: notebooks/neural_network_lab.ipynb"
```

---

## Part 2: Why scikit-learn Fails on Images

Key cells to run in the notebook:

```python {"name": "sklearn-baseline"}
# In the notebook -- load CIFAR-10 and try scikit-learn
import torch
import torchvision
import torchvision.transforms as transforms
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
import numpy as np

transform = transforms.Compose([transforms.ToTensor()])
train_dataset = torchvision.datasets.CIFAR10(root='./data', train=True, download=True, transform=transform)
test_dataset = torchvision.datasets.CIFAR10(root='./data', train=False, download=True, transform=transform)

# Flatten images to vectors
X_train = train_dataset.data.reshape(len(train_dataset), -1) / 255.0
y_train = np.array(train_dataset.targets)
X_test = test_dataset.data.reshape(len(test_dataset), -1) / 255.0
y_test = np.array(test_dataset.targets)

# Logistic regression on raw pixels
clf = LogisticRegression(max_iter=1000, random_state=42, solver='saga', n_jobs=-1)
clf.fit(X_train[:5000], y_train[:5000])  # Subset for speed
sklearn_acc = clf.score(X_test, y_test)
print(f"scikit-learn accuracy on CIFAR-10: {sklearn_acc:.3f}")
print("Better than random (10%), but terrible for 10 classes")
```

---

## Part 3: Build a Feedforward Neural Network

```python {"name": "feedforward-net"}
import torch
import torch.nn as nn

class FeedforwardNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.flatten = nn.Flatten()
        self.layers = nn.Sequential(
            nn.Linear(3 * 32 * 32, 512),
            nn.ReLU(),
            nn.Linear(512, 256),
            nn.ReLU(),
            nn.Linear(256, 10)
        )

    def forward(self, x):
        x = self.flatten(x)
        return self.layers(x)

model = FeedforwardNet()
total_params = sum(p.numel() for p in model.parameters())
print(f"Total parameters: {total_params:,}")
print(model)
```

---

## Part 4: The Training Loop

```python {"name": "training-loop"}
# The core training loop
device = torch.device('cpu')
model = FeedforwardNet().to(device)
criterion = nn.CrossEntropyLoss()
optimizer = torch.optim.SGD(model.parameters(), lr=0.01)

train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=64, shuffle=True)

for epoch in range(3):  # Quick demo with 3 epochs
    running_loss = 0.0
    correct = 0
    total = 0
    for images, labels in train_loader:
        images, labels = images.to(device), labels.to(device)
        optimizer.zero_grad()        # Clear gradients
        outputs = model(images)      # Forward pass
        loss = criterion(outputs, labels)  # Compute loss
        loss.backward()              # Backward pass
        optimizer.step()             # Update weights
        running_loss += loss.item()
        _, predicted = outputs.max(1)
        total += labels.size(0)
        correct += predicted.eq(labels).sum().item()

    print(f"Epoch {epoch+1}: loss={running_loss/len(train_loader):.3f}, acc={100.*correct/total:.1f}%")
```

---

## Part 5: Build a CNN

```python {"name": "cnn"}
class SimpleCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2, 2),
            nn.Conv2d(32, 64, 3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2, 2),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Linear(64 * 8 * 8, 256),
            nn.ReLU(),
            nn.Linear(256, 10),
        )

    def forward(self, x):
        x = self.features(x)
        return self.classifier(x)

cnn = SimpleCNN()
print(f"CNN parameters: {sum(p.numel() for p in cnn.parameters()):,}")
print(cnn)
```

---

## Part 6: Checkpointing

```python {"name": "checkpoint"}
# Save checkpoint
checkpoint = {
    'epoch': 10,
    'model_state_dict': model.state_dict(),
    'optimizer_state_dict': optimizer.state_dict(),
    'loss': 0.5,
}
torch.save(checkpoint, 'model_checkpoint.pt')
print("Checkpoint saved!")

# Load checkpoint
loaded = torch.load('model_checkpoint.pt', weights_only=False)
new_model = FeedforwardNet()
new_model.load_state_dict(loaded['model_state_dict'])
print(f"Resumed from epoch {loaded['epoch']}")
```

---

## Part 7: Experiment Tracking

```python {"name": "experiment-tracking"}
import json
import pandas as pd

experiments = [
    {"name": "feedforward_lr001", "architecture": "feedforward", "lr": 0.01, "test_acc": 0.45},
    {"name": "feedforward_lr005", "architecture": "feedforward", "lr": 0.05, "test_acc": 0.42},
    {"name": "cnn_lr001", "architecture": "cnn", "lr": 0.01, "test_acc": 0.62},
]

df = pd.DataFrame(experiments)
print(df.sort_values('test_acc', ascending=False))
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
| Notebook path | `notebooks/neural_network_lab.ipynb` |
| Solution path | `solutions/completed_notebook.ipynb` |
