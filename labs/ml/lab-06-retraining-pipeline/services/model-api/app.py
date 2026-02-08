"""
Model API with hot-reload and feedback collection.

Serves predictions from a scikit-learn model, collects user feedback,
and automatically reloads the model when a new version is saved to disk.
"""

import fastapi
import joblib
import json
import time
import os
import threading
import logging
import numpy as np
from datetime import datetime
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = fastapi.FastAPI(title="Model API with Retraining", version="2.0.0")

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

PREDICTION_COUNT = Counter(
    "model_predictions_total",
    "Total predictions",
    ["predicted_class", "model_version"],
)
PREDICTION_LATENCY = Histogram(
    "model_prediction_duration_seconds",
    "Prediction latency in seconds",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)
FEEDBACK_COUNT = Counter(
    "model_feedback_total",
    "Feedback entries received",
    ["correct"],
)
MODEL_VERSION_GAUGE = Gauge(
    "model_version_info",
    "Current model version number",
)
MODEL_RELOAD_COUNT = Counter(
    "model_reload_total",
    "Number of times the model has been reloaded",
)
FEEDBACK_QUEUE_SIZE = Gauge(
    "feedback_queue_size",
    "Number of feedback entries stored on disk",
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

MODEL_PATH = "/app/model/sentiment_model.joblib"
METADATA_PATH = "/app/model/model_metadata.json"
FEEDBACK_PATH = "/app/data/feedback.jsonl"

# ---------------------------------------------------------------------------
# Global model state
# ---------------------------------------------------------------------------

model = None
model_classes = None
model_version = "0"
model_mtime = 0.0

# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class PredictRequest(BaseModel):
    text: str


class PredictResponse(BaseModel):
    predicted_class: str
    confidence: float
    model_version: str


class FeedbackRequest(BaseModel):
    text: str
    predicted_class: str
    correct_class: str


class FeedbackResponse(BaseModel):
    status: str
    feedback_count: int


class ModelInfo(BaseModel):
    version: str
    accuracy: float | None = None
    training_date: str | None = None
    classes: list[str]
    n_training_samples: int | None = None
    n_feedback_samples: int | None = None

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def load_model():
    """Load model and metadata from disk."""
    global model, model_classes, model_version, model_mtime

    if not os.path.exists(MODEL_PATH):
        logger.warning("Model file not found at %s", MODEL_PATH)
        return

    try:
        model = joblib.load(MODEL_PATH)
        model_mtime = os.path.getmtime(MODEL_PATH)

        # Load metadata
        if os.path.exists(METADATA_PATH):
            with open(METADATA_PATH) as f:
                meta = json.load(f)
            model_classes = meta.get("classes", ["rec.sport.baseball", "sci.space"])
            model_version = meta.get("version", "1.0.0")
        else:
            model_classes = ["rec.sport.baseball", "sci.space"]
            model_version = "1.0.0"

        MODEL_VERSION_GAUGE.set(hash(model_version) % 1000)
        MODEL_RELOAD_COUNT.inc()
        logger.info("Model loaded — version=%s, mtime=%.1f", model_version, model_mtime)

    except Exception as exc:
        logger.error("Failed to load model: %s", exc)

# ---------------------------------------------------------------------------
# Background model watcher (polls every 10 s)
# ---------------------------------------------------------------------------


def watch_model():
    """Periodically check for a newer model file and reload if found."""
    global model_mtime
    while True:
        time.sleep(10)
        try:
            if os.path.exists(MODEL_PATH):
                current_mtime = os.path.getmtime(MODEL_PATH)
                if current_mtime > model_mtime:
                    logger.info("New model detected (mtime %.1f > %.1f), reloading...", current_mtime, model_mtime)
                    load_model()
                    logger.info("Model reloaded: version=%s", model_version)
        except Exception as exc:
            logger.error("Model watcher error: %s", exc)

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@app.on_event("startup")
def startup():
    os.makedirs("/app/data", exist_ok=True)
    os.makedirs("/app/model", exist_ok=True)
    load_model()
    watcher = threading.Thread(target=watch_model, daemon=True)
    watcher.start()
    logger.info("Model API started — watcher thread running")

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    """Return a prediction for the given text."""
    if model is None:
        raise fastapi.HTTPException(status_code=503, detail="Model not loaded")

    start = time.perf_counter()
    proba = model.predict_proba([req.text])[0]
    pred_idx = int(np.argmax(proba))
    predicted_class = model_classes[pred_idx]
    confidence = float(proba[pred_idx])
    elapsed = time.perf_counter() - start

    PREDICTION_COUNT.labels(predicted_class=predicted_class, model_version=model_version).inc()
    PREDICTION_LATENCY.observe(elapsed)

    return PredictResponse(
        predicted_class=predicted_class,
        confidence=round(confidence, 4),
        model_version=model_version,
    )


@app.post("/feedback", response_model=FeedbackResponse)
def feedback(req: FeedbackRequest):
    """Store user feedback for future retraining."""
    is_correct = req.predicted_class == req.correct_class
    FEEDBACK_COUNT.labels(correct=str(is_correct).lower()).inc()

    entry = {
        "text": req.text,
        "predicted_class": req.predicted_class,
        "correct_class": req.correct_class,
        "is_correct": is_correct,
        "timestamp": datetime.utcnow().isoformat(),
    }

    with open(FEEDBACK_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Count total feedback entries
    count = 0
    if os.path.exists(FEEDBACK_PATH):
        with open(FEEDBACK_PATH) as f:
            count = sum(1 for _ in f)
    FEEDBACK_QUEUE_SIZE.set(count)

    return FeedbackResponse(status="recorded", feedback_count=count)


@app.get("/model/info", response_model=ModelInfo)
def model_info():
    """Return information about the currently loaded model."""
    meta = {}
    if os.path.exists(METADATA_PATH):
        with open(METADATA_PATH) as f:
            meta = json.load(f)

    feedback_count = 0
    if os.path.exists(FEEDBACK_PATH):
        with open(FEEDBACK_PATH) as f:
            feedback_count = sum(1 for _ in f)

    return ModelInfo(
        version=model_version,
        accuracy=meta.get("accuracy"),
        training_date=meta.get("training_date"),
        classes=model_classes or [],
        n_training_samples=meta.get("n_train"),
        n_feedback_samples=feedback_count,
    )


@app.get("/health")
def health():
    """Liveness probe."""
    return {"status": "healthy"}


@app.get("/ready")
def ready():
    """Readiness probe — only ready when a model is loaded."""
    if model is None:
        raise fastapi.HTTPException(status_code=503, detail="Model not loaded")
    return {"status": "ready", "model_version": model_version}


@app.get("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
