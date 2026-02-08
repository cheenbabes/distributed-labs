# ML Lab 07: Train a Neural Network

You've been using scikit-learn for everything so far -- and it's great for tabular data and text. But try feeding raw pixels from an image into logistic regression and watch it struggle. Images have spatial structure (edges, textures, shapes) that flat feature vectors can't capture. In this lab, you'll build your first neural network from scratch in PyTorch, train it on real images, see why CNNs beat feedforward networks, and learn to checkpoint your training so you don't lose hours of work to a crash.

## What You'll Learn

- Why scikit-learn fails on raw image data and what neural networks do differently
- How to build a feedforward neural network from scratch in PyTorch
- How the training loop works: forward pass, loss, backward pass, optimizer step
- How to visualize training progress and detect overfitting
- Why CNNs outperform feedforward networks on image data
- How to save and restore model checkpoints (fault tolerance for training)
- How to track experiments and compare results

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  CIFAR-10    │────▶│  DataLoader  │────▶│  Neural Net  │────▶│  Evaluate    │
│  (images)    │     │  (batches)   │     │  (PyTorch)   │     │  (accuracy)  │
└──────────────┘     └──────────────┘     └──────┬───────┘     └──────┬───────┘
                                                  │                    │
                                           ┌──────┴───────┐           │
                                           │  Training    │           │
                                           │  Loop        │           │
                                           │  (epochs)    │           │
                                           └──────┬───────┘           │
                                                  │                    │
                                                  ▼                    ▼
                                           ┌──────────────┐     ┌──────────────┐
                                           │  Checkpoint  │     │  Experiment  │
                                           │  (save/load) │     │  Tracking    │
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

Navigate to `notebooks/neural_network_lab.ipynb` and work through each section.

| Service | URL | Purpose |
|---------|-----|---------|
| Jupyter Lab | http://localhost:8888 | Interactive notebook environment |

## Lab Exercises

### Exercise 1: Why scikit-learn Isn't Enough

Load CIFAR-10 images, flatten the pixels into a vector, and feed them to scikit-learn's logistic regression. Get around 40% accuracy on 10 classes -- better than random (10%), but terrible for a real task.

**You'll answer:** Why can't logistic regression learn spatial patterns from raw pixels?

### Exercise 2: Your First Neural Network

Build a three-layer feedforward neural network in PyTorch using `nn.Module`. Define the architecture: input (3072) -> hidden 1 (512, ReLU) -> hidden 2 (256, ReLU) -> output (10 classes).

**You'll answer:** How many learnable parameters does the network have? What does each layer do?

### Exercise 3: The Training Loop

Implement the complete training loop from scratch: forward pass, compute CrossEntropyLoss, backward pass, SGD optimizer step. Train for 10 epochs and watch loss decrease.

**You'll answer:** What happens to loss and accuracy over epochs? How do you know training is working?

### Exercise 4: Watch It Learn

Plot the training loss curve, compare train vs test accuracy per epoch, and visualize model predictions on sample images. Identify overfitting when train accuracy rises but test accuracy plateaus.

**You'll answer:** At which epoch does overfitting start? How big is the train-test accuracy gap?

### Exercise 5: Improve with a CNN

Build a convolutional neural network (2 conv layers + 2 fully connected layers) and train it on the same data. Compare accuracy against the feedforward network.

**You'll answer:** How much does the CNN improve over the feedforward network? Why?

### Exercise 6: Checkpointing

Save a model checkpoint with `torch.save` (state dict, optimizer state, epoch, loss). Load it back, resume training, and verify the loaded model produces identical predictions.

**You'll answer:** What's in a checkpoint file? How is this like write-ahead logging in databases?

### Exercise 7: Experiment Tracking

Log results from multiple experiments to a JSON file, then compare them in a pandas DataFrame. Try different learning rates and architectures.

**You'll answer:** Which hyperparameters had the biggest impact on accuracy?

## Key Takeaways

1. **scikit-learn has limits** -- flat feature vectors lose spatial structure, which matters for images
2. **Neural networks learn features** -- hidden layers extract increasingly abstract representations
3. **The training loop is everything** -- forward, loss, backward, step; that cycle is all of deep learning
4. **CNNs exploit spatial structure** -- convolutions learn local patterns (edges, textures) that feedforward nets miss
5. **Checkpointing is fault tolerance** -- save your training state so a crash doesn't mean starting over
6. **Track your experiments** -- without logging, you'll forget what you tried and repeat failed attempts

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

The container may be running out of memory. PyTorch on CPU with CIFAR-10 should be fine with 4GB, but check:

```bash
docker stats --no-stream
```

### PyTorch installation issues

The Dockerfile uses CPU-only PyTorch from the official wheel index. If you see import errors, rebuild:

```bash
docker compose build --no-cache
```

## What's Next

[ML Lab 08: Run an LLM Locally](../lab-08-run-llm-locally/) -- Move from training models to running pre-trained large language models on your own machine.
