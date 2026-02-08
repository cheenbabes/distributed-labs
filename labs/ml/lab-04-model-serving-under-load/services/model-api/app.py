"""Model Serving API with single and batch prediction endpoints."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import joblib
import time
import os
import logging
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Model Serving API", version="1.0.0")

# ---------------------------------------------------------------------------
# Prometheus Metrics
# ---------------------------------------------------------------------------
PREDICTION_COUNT = Counter(
    "model_predictions_total",
    "Total number of predictions made",
    ["predicted_class", "mode"],
)
PREDICTION_LATENCY = Histogram(
    "model_prediction_duration_seconds",
    "Time spent computing predictions",
    ["mode"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)
BATCH_SIZE = Histogram(
    "model_batch_size",
    "Number of items in batch prediction requests",
    buckets=[1, 2, 5, 10, 20, 50, 100],
)
THROUGHPUT = Gauge(
    "model_predictions_per_second",
    "Recent prediction throughput",
)
REQUEST_COUNT = Counter(
    "model_requests_total",
    "Total HTTP requests to prediction endpoints",
    ["endpoint", "status"],
)

# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------
model = None
model_classes = None
MODEL_PATH = "/app/model/sentiment_model.joblib"


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------
class PredictRequest(BaseModel):
    text: str


class BatchPredictRequest(BaseModel):
    texts: list[str]


class PredictResponse(BaseModel):
    predicted_class: str
    confidence: float
    probabilities: dict


class BatchPredictResponse(BaseModel):
    predictions: list[PredictResponse]
    batch_size: int
    total_latency_ms: float
    per_item_latency_ms: float


# ---------------------------------------------------------------------------
# Lifecycle Events
# ---------------------------------------------------------------------------
@app.on_event("startup")
def load_model():
    global model, model_classes
    if not os.path.exists(MODEL_PATH):
        logger.error(f"Model file not found at {MODEL_PATH}")
        return
    logger.info(f"Loading model from {MODEL_PATH}")
    data = joblib.load(MODEL_PATH)
    model = data["pipeline"]
    model_classes = data["classes"]
    logger.info(f"Model loaded. Classes: {model_classes}")


# ---------------------------------------------------------------------------
# Health & Readiness
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/ready")
def ready():
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return {"status": "ready", "model_classes": model_classes}


# ---------------------------------------------------------------------------
# Single Prediction
# ---------------------------------------------------------------------------
@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest):
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    start = time.perf_counter()
    try:
        proba = model.predict_proba([request.text])[0]
        predicted_idx = int(np.argmax(proba))
        predicted_class = model_classes[predicted_idx]
        confidence = float(proba[predicted_idx])

        duration = time.perf_counter() - start

        # Record metrics
        PREDICTION_LATENCY.labels(mode="single").observe(duration)
        PREDICTION_COUNT.labels(predicted_class=predicted_class, mode="single").inc()
        REQUEST_COUNT.labels(endpoint="/predict", status="success").inc()
        THROUGHPUT.set(1.0 / max(duration, 1e-9))

        return PredictResponse(
            predicted_class=predicted_class,
            confidence=confidence,
            probabilities={
                model_classes[i]: float(proba[i]) for i in range(len(model_classes))
            },
        )
    except Exception as e:
        REQUEST_COUNT.labels(endpoint="/predict", status="error").inc()
        logger.error(f"Prediction failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Batch Prediction
# ---------------------------------------------------------------------------
@app.post("/predict/batch", response_model=BatchPredictResponse)
def predict_batch(request: BatchPredictRequest):
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    if not request.texts:
        raise HTTPException(status_code=400, detail="texts list cannot be empty")

    if len(request.texts) > 100:
        raise HTTPException(status_code=400, detail="Maximum batch size is 100")

    start = time.perf_counter()
    try:
        # Vectorize all inputs at once -- this is the key optimization.
        # The TF-IDF step and model inference happen once for the entire batch
        # instead of N separate times.
        probas = model.predict_proba(request.texts)
        duration = time.perf_counter() - start

        predictions = []
        for i, proba in enumerate(probas):
            predicted_idx = int(np.argmax(proba))
            predicted_class = model_classes[predicted_idx]
            confidence = float(proba[predicted_idx])

            PREDICTION_COUNT.labels(predicted_class=predicted_class, mode="batch").inc()

            predictions.append(
                PredictResponse(
                    predicted_class=predicted_class,
                    confidence=confidence,
                    probabilities={
                        model_classes[j]: float(proba[j])
                        for j in range(len(model_classes))
                    },
                )
            )

        batch_size = len(request.texts)

        # Record metrics
        PREDICTION_LATENCY.labels(mode="batch").observe(duration)
        BATCH_SIZE.observe(batch_size)
        REQUEST_COUNT.labels(endpoint="/predict/batch", status="success").inc()
        THROUGHPUT.set(batch_size / max(duration, 1e-9))

        return BatchPredictResponse(
            predictions=predictions,
            batch_size=batch_size,
            total_latency_ms=round(duration * 1000, 2),
            per_item_latency_ms=round((duration / batch_size) * 1000, 2),
        )
    except Exception as e:
        REQUEST_COUNT.labels(endpoint="/predict/batch", status="error").inc()
        logger.error(f"Batch prediction failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Prometheus Metrics Endpoint
# ---------------------------------------------------------------------------
@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
