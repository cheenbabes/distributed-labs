"""Model Serving API with drift detection metrics."""
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

app = FastAPI(title="Model Serving API (Drift Detection)", version="1.0.0")

# ---------------------------------------------------------------------------
# Prometheus Metrics — Standard
# ---------------------------------------------------------------------------
PREDICTION_COUNT = Counter(
    "model_predictions_total",
    "Total number of predictions made",
    ["predicted_class"],
)
PREDICTION_LATENCY = Histogram(
    "model_prediction_duration_seconds",
    "Time spent computing predictions",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

# ---------------------------------------------------------------------------
# Prometheus Metrics — Drift Detection
# ---------------------------------------------------------------------------
INPUT_TEXT_LENGTH = Histogram(
    "model_input_text_length",
    "Character length of input texts (shifts indicate distribution change)",
    buckets=[50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000],
)
PREDICTION_CONFIDENCE = Histogram(
    "model_prediction_confidence",
    "Confidence of the predicted class (drops indicate OOD inputs)",
    buckets=[0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0],
)
PREDICTION_ENTROPY = Histogram(
    "model_prediction_entropy",
    "Entropy of prediction probability distribution (high = uncertain = possible drift)",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)
CONFIDENCE_GAUGE = Gauge(
    "model_prediction_confidence_current",
    "Most recent prediction confidence (for quick dashboard view)",
)
ENTROPY_GAUGE = Gauge(
    "model_prediction_entropy_current",
    "Most recent prediction entropy (for quick dashboard view)",
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


class PredictResponse(BaseModel):
    predicted_class: str
    confidence: float
    probabilities: dict
    entropy: float
    text_length: int


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
# Helper: Calculate entropy
# ---------------------------------------------------------------------------
def calculate_entropy(probabilities: np.ndarray) -> float:
    """Calculate Shannon entropy of a probability distribution.

    Entropy is maximized when all classes are equally likely (maximum uncertainty).
    For binary classification, max entropy is log2(2) = 1.0.
    Low entropy means the model is confident; high entropy means it's uncertain.
    """
    # Clip to avoid log(0)
    p = np.clip(probabilities, 1e-10, 1.0)
    entropy = -np.sum(p * np.log2(p))
    return float(entropy)


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
# Prediction with Drift Metrics
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
        entropy = calculate_entropy(proba)
        text_length = len(request.text)

        duration = time.perf_counter() - start

        # Standard metrics
        PREDICTION_LATENCY.observe(duration)
        PREDICTION_COUNT.labels(predicted_class=predicted_class).inc()

        # Drift detection metrics
        INPUT_TEXT_LENGTH.observe(text_length)
        PREDICTION_CONFIDENCE.observe(confidence)
        PREDICTION_ENTROPY.observe(entropy)
        CONFIDENCE_GAUGE.set(confidence)
        ENTROPY_GAUGE.set(entropy)

        return PredictResponse(
            predicted_class=predicted_class,
            confidence=confidence,
            probabilities={
                model_classes[i]: float(proba[i]) for i in range(len(model_classes))
            },
            entropy=entropy,
            text_length=text_length,
        )
    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Prometheus Metrics Endpoint
# ---------------------------------------------------------------------------
@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
