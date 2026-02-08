#!/bin/bash
set -e

echo "=== Model API Startup ==="

# Train initial model if it doesn't exist
if [ ! -f /app/model/sentiment_model.joblib ]; then
    echo "No model found — training initial model..."
    python train_model.py
    echo "Initial model trained."
else
    echo "Existing model found — skipping initial training."
fi

echo "Starting API server..."
exec uvicorn app:app --host 0.0.0.0 --port 8000 --log-level info
