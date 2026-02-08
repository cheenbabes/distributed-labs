"""Train and save the sentiment model at container startup."""
from sklearn.datasets import fetch_20newsgroups
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
import joblib
import json
import os

MODEL_DIR = "/app/model"
MODEL_PATH = f"{MODEL_DIR}/sentiment_model.joblib"
METADATA_PATH = f"{MODEL_DIR}/model_metadata.json"


def train():
    os.makedirs(MODEL_DIR, exist_ok=True)

    categories = ['rec.sport.baseball', 'sci.space']
    print(f"Loading 20newsgroups dataset: {categories}")

    train_data = fetch_20newsgroups(
        subset='train', categories=categories, shuffle=True, random_state=42
    )
    test_data = fetch_20newsgroups(
        subset='test', categories=categories, shuffle=True, random_state=42
    )

    pipeline = Pipeline([
        ('tfidf', TfidfVectorizer(max_features=10000, stop_words='english')),
        ('clf', LogisticRegression(max_iter=1000, random_state=42))
    ])

    print("Training model...")
    pipeline.fit(train_data.data, train_data.target)

    accuracy = accuracy_score(test_data.target, pipeline.predict(test_data.data))
    print(f"Test accuracy: {accuracy:.3f}")

    joblib.dump(pipeline, MODEL_PATH)
    print(f"Model saved to {MODEL_PATH}")

    metadata = {
        "classes": list(train_data.target_names),
        "accuracy": accuracy,
        "n_train": len(train_data.data),
        "n_test": len(test_data.data),
        "version": "1.0.0"
    }
    with open(METADATA_PATH, 'w') as f:
        json.dump(metadata, f, indent=2)
    print(f"Metadata saved to {METADATA_PATH}")


if __name__ == "__main__":
    train()
