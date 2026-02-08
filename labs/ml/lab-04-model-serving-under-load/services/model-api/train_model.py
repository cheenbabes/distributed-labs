"""Train a sentiment model and save it to disk."""
import joblib
from sklearn.datasets import fetch_20newsgroups
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.metrics import classification_report
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL_PATH = "/app/model/sentiment_model.joblib"


def train():
    logger.info("Loading 20 Newsgroups dataset...")
    categories = ["rec.sport.baseball", "sci.space"]
    train_data = fetch_20newsgroups(
        subset="train", categories=categories, shuffle=True, random_state=42
    )
    test_data = fetch_20newsgroups(
        subset="test", categories=categories, shuffle=True, random_state=42
    )

    logger.info(
        f"Training samples: {len(train_data.data)}, Test samples: {len(test_data.data)}"
    )
    logger.info(f"Classes: {train_data.target_names}")

    logger.info("Building TF-IDF + Logistic Regression pipeline...")
    pipeline = Pipeline(
        [
            ("tfidf", TfidfVectorizer(max_features=10000, stop_words="english")),
            ("clf", LogisticRegression(max_iter=1000, random_state=42)),
        ]
    )

    logger.info("Training model...")
    pipeline.fit(train_data.data, train_data.target)

    predictions = pipeline.predict(test_data.data)
    report = classification_report(
        test_data.target, predictions, target_names=train_data.target_names
    )
    logger.info(f"Classification Report:\n{report}")

    logger.info(f"Saving model to {MODEL_PATH}")
    joblib.dump(
        {"pipeline": pipeline, "classes": train_data.target_names}, MODEL_PATH
    )
    logger.info("Model saved successfully!")


if __name__ == "__main__":
    train()
