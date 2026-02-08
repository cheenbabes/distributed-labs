"""
Retraining job.

Reads feedback data, combines with original training data, trains a new model,
validates against a golden evaluation set, and saves the new model only if it
is at least as good as the current one.

The model API watches the model file and hot-reloads automatically.
"""

import json
import os
import time
import logging
from datetime import datetime

import joblib
import numpy as np
from sklearn.datasets import fetch_20newsgroups
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
from sklearn.pipeline import Pipeline

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL_DIR = "/app/model"
FEEDBACK_PATH = "/app/data/feedback.jsonl"
MODEL_PATH = f"{MODEL_DIR}/sentiment_model.joblib"
METADATA_PATH = f"{MODEL_DIR}/model_metadata.json"

CATEGORIES = ["rec.sport.baseball", "sci.space"]
MIN_FEEDBACK_SAMPLES = 20   # Need at least this many feedback samples to retrain
IMPROVEMENT_THRESHOLD = 0.0  # New model must be at least this much better
RETRAIN_INTERVAL = 60        # Seconds between retrain checks
STARTUP_DELAY = 30           # Seconds to wait before first retrain attempt

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_feedback():
    """Load feedback data from the JSONL file."""
    if not os.path.exists(FEEDBACK_PATH):
        return [], []

    texts, labels = [], []
    class_to_idx = {c: i for i, c in enumerate(CATEGORIES)}

    with open(FEEDBACK_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                correct_class = entry.get("correct_class", "")
                if correct_class in class_to_idx:
                    texts.append(entry["text"])
                    labels.append(class_to_idx[correct_class])
            except json.JSONDecodeError:
                logger.warning("Skipping malformed feedback line")

    return texts, labels


def create_golden_set():
    """Create a golden evaluation set from held-out test data."""
    test_data = fetch_20newsgroups(subset="test", categories=CATEGORIES, random_state=42)
    # Use first 100 samples as a stable golden set
    return test_data.data[:100], test_data.target[:100]


def bump_version(current_version: str) -> str:
    """Increment the patch version number."""
    parts = current_version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)

# ---------------------------------------------------------------------------
# Main retraining logic
# ---------------------------------------------------------------------------


def retrain():
    """Run one retraining cycle."""
    logger.info("=== Starting retraining check ===")

    # Load feedback
    fb_texts, fb_labels = load_feedback()
    logger.info("Feedback samples available: %d", len(fb_texts))

    if len(fb_texts) < MIN_FEEDBACK_SAMPLES:
        logger.info(
            "Not enough feedback (%d < %d). Skipping retrain.",
            len(fb_texts),
            MIN_FEEDBACK_SAMPLES,
        )
        return

    # Load original training data
    train_data = fetch_20newsgroups(subset="train", categories=CATEGORIES, random_state=42)

    # Combine original training data + feedback
    all_texts = list(train_data.data) + fb_texts
    all_labels = list(train_data.target) + fb_labels
    logger.info(
        "Combined training data: %d samples (%d original + %d feedback)",
        len(all_texts),
        len(train_data.data),
        len(fb_texts),
    )

    # Train new model
    new_model = Pipeline(
        [
            ("tfidf", TfidfVectorizer(max_features=10000, stop_words="english")),
            ("clf", LogisticRegression(max_iter=1000, random_state=42)),
        ]
    )
    new_model.fit(all_texts, all_labels)

    # Validate against golden set
    golden_texts, golden_labels = create_golden_set()
    new_accuracy = accuracy_score(golden_labels, new_model.predict(golden_texts))

    # Compare with current model
    current_accuracy = 0.0
    if os.path.exists(MODEL_PATH):
        try:
            current_model = joblib.load(MODEL_PATH)
            current_accuracy = accuracy_score(golden_labels, current_model.predict(golden_texts))
        except Exception as exc:
            logger.warning("Could not load current model for comparison: %s", exc)

    logger.info("Current model accuracy: %.4f", current_accuracy)
    logger.info("New model accuracy:     %.4f", new_accuracy)

    if new_accuracy >= current_accuracy + IMPROVEMENT_THRESHOLD:
        # Determine new version
        version = "1.0.1"
        if os.path.exists(METADATA_PATH):
            with open(METADATA_PATH) as f:
                old_meta = json.load(f)
            version = bump_version(old_meta.get("version", "1.0.0"))

        # Save new model
        os.makedirs(MODEL_DIR, exist_ok=True)
        joblib.dump(new_model, MODEL_PATH)

        metadata = {
            "classes": CATEGORIES,
            "accuracy": round(float(new_accuracy), 4),
            "previous_accuracy": round(float(current_accuracy), 4),
            "n_train": len(all_texts),
            "n_feedback": len(fb_texts),
            "version": version,
            "training_date": datetime.utcnow().isoformat(),
        }
        with open(METADATA_PATH, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info("New model saved! version=%s accuracy=%.4f", version, new_accuracy)

        # Print classification report for logging
        report = classification_report(
            golden_labels,
            new_model.predict(golden_texts),
            target_names=CATEGORIES,
        )
        logger.info("Classification report:\n%s", report)
    else:
        logger.info(
            "New model (%.4f) is not better than current (%.4f). Keeping current model.",
            new_accuracy,
            current_accuracy,
        )

# ---------------------------------------------------------------------------
# Entry point — runs in a loop
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Retrainer starting. Waiting %ds for feedback to accumulate...", STARTUP_DELAY)
    time.sleep(STARTUP_DELAY)

    while True:
        try:
            retrain()
        except Exception as exc:
            logger.error("Retraining failed: %s", exc, exc_info=True)

        logger.info("Next retrain check in %ds...", RETRAIN_INTERVAL)
        time.sleep(RETRAIN_INTERVAL)
