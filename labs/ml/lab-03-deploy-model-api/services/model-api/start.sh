#!/bin/bash
set -e
echo "=== Training model ==="
python train_model.py
echo "=== Starting API server ==="
exec uvicorn app:app --host 0.0.0.0 --port 8000
