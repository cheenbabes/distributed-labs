"""
Train the initial model.

Downloads the 20 Newsgroups dataset, trains a TF-IDF + Logistic Regression
pipeline on two categories, and saves the model + metadata to /app/model/.

Run once at container startup before the API begins serving.
"""

import json
import os
from datetime import datetime

import joblib
from sklearn.datasets import fetch_20newsgroups
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.pipeline import Pipeline

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CATEGORIES = ["rec.sport.baseball", "sci.space"]
MODEL_DIR = "/app/model"
MODEL_PATH = f"{MODEL_DIR}/sentiment_model.joblib"
METADATA_PATH = f"{MODEL_DIR}/model_metadata.json"

# ---------------------------------------------------------------------------
# Train
# ---------------------------------------------------------------------------


def main():
    print("Loading 20 Newsgroups data...")
    train_data = fetch_20newsgroups(
        subset="train", categories=CATEGORIES, shuffle=True, random_state=42
    )
    test_data = fetch_20newsgroups(
        subset="test", categories=CATEGORIES, shuffle=True, random_state=42
    )

    print(f"Training samples: {len(train_data.data)}")
    print(f"Test samples:     {len(test_data.data)}")
    print(f"Classes:          {CATEGORIES}")

    pipeline = Pipeline(
        [
            ("tfidf", TfidfVectorizer(max_features=10000, stop_words="english")),
            ("clf", LogisticRegression(max_iter=1000, random_state=42)),
        ]
    )

    print("Training model...")
    pipeline.fit(train_data.data, train_data.target)

    predictions = pipeline.predict(test_data.data)
    accuracy = accuracy_score(test_data.target, predictions)
    print(f"Test accuracy: {accuracy:.3f}")

    # Save model
    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(pipeline, MODEL_PATH)
    print(f"Model saved to {MODEL_PATH}")

    # Save metadata
    metadata = {
        "classes": CATEGORIES,
        "accuracy": round(accuracy, 4),
        "n_train": len(train_data.data),
        "n_feedback": 0,
        "version": "1.0.0",
        "training_date": datetime.utcnow().isoformat(),
    }
    with open(METADATA_PATH, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Metadata saved to {METADATA_PATH}")
    print("Done.")


if __name__ == "__main__":
    main()
