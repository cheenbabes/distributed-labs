from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import joblib
import time
import os
import logging
import numpy as np
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Sentiment Model API", version="1.0.0")

# Metrics
PREDICTION_COUNT = Counter('model_predictions_total', 'Total predictions', ['predicted_class'])
PREDICTION_LATENCY = Histogram('model_prediction_duration_seconds', 'Prediction latency',
                                buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0])
PREDICTION_CONFIDENCE = Histogram('model_prediction_confidence', 'Prediction confidence scores',
                                   buckets=[0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0])
MODEL_LOADED = Gauge('model_loaded', 'Whether the model is loaded (1=yes, 0=no)')

# Model state
model = None
model_classes = None
MODEL_PATH = "/app/model/sentiment_model.joblib"
MODEL_METADATA_PATH = "/app/model/model_metadata.json"


class PredictRequest(BaseModel):
    text: str


class PredictResponse(BaseModel):
    predicted_class: str
    confidence: float
    probabilities: dict


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool


@app.on_event("startup")
def load_model():
    global model, model_classes
    if os.path.exists(MODEL_PATH):
        model = joblib.load(MODEL_PATH)
        if os.path.exists(MODEL_METADATA_PATH):
            with open(MODEL_METADATA_PATH) as f:
                metadata = json.load(f)
            model_classes = metadata["classes"]
        else:
            model_classes = ["rec.sport.baseball", "sci.space"]
        MODEL_LOADED.set(1)
        logger.info(f"Model loaded from {MODEL_PATH}")
    else:
        MODEL_LOADED.set(0)
        logger.warning(f"Model not found at {MODEL_PATH}")


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="healthy", model_loaded=model is not None)


@app.get("/ready")
def ready():
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return {"status": "ready"}


@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest):
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    start = time.time()
    proba = model.predict_proba([request.text])[0]
    predicted_idx = np.argmax(proba)
    predicted_class = model_classes[predicted_idx]
    confidence = float(proba[predicted_idx])
    duration = time.time() - start

    # Record metrics
    PREDICTION_COUNT.labels(predicted_class=predicted_class).inc()
    PREDICTION_LATENCY.observe(duration)
    PREDICTION_CONFIDENCE.observe(confidence)

    # Log prediction
    logger.info(f"prediction: class={predicted_class} confidence={confidence:.3f} "
                f"latency={duration:.4f}s text_length={len(request.text)}")

    return PredictResponse(
        predicted_class=predicted_class,
        confidence=confidence,
        probabilities={model_classes[i]: float(p) for i, p in enumerate(proba)}
    )


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
