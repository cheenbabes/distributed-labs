"""Simulate data drift by gradually shifting input distribution.

The simulator sends requests in four phases:
  Phase 1 (Normal)    — 100% in-distribution data (baseball + space)
  Phase 2 (Slight)    — 70% in-distribution, 30% out-of-distribution
  Phase 3 (Moderate)  — 40% in-distribution, 60% out-of-distribution
  Phase 4 (Heavy)     — 10% in-distribution, 90% out-of-distribution

Out-of-distribution data comes from talk.politics.misc, comp.graphics, and
sci.med — categories the model has never seen during training.
"""
import requests
import time
import random
import logging
from sklearn.datasets import fetch_20newsgroups

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

API_URL = "http://model-api:8000/predict"

# -----------------------------------------------------------------------
# Load datasets
# -----------------------------------------------------------------------
logger.info("Loading in-distribution data (baseball + space)...")
in_dist = fetch_20newsgroups(
    subset="test",
    categories=["rec.sport.baseball", "sci.space"],
    random_state=42,
)

logger.info("Loading out-of-distribution data (politics + graphics + medicine)...")
out_dist = fetch_20newsgroups(
    subset="test",
    categories=["talk.politics.misc", "comp.graphics", "sci.med"],
    random_state=42,
)

logger.info(
    f"Loaded {len(in_dist.data)} in-dist samples, {len(out_dist.data)} out-dist samples"
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------
def send_request(text: str) -> dict | None:
    """Send a single prediction request to the model API."""
    try:
        resp = requests.post(API_URL, json={"text": text}, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Request failed: {e}")
        return None


def wait_for_api(max_retries: int = 30, delay: float = 2.0):
    """Wait until the model API is healthy and ready."""
    logger.info("Waiting for model API to be ready...")
    for i in range(max_retries):
        try:
            resp = requests.get("http://model-api:8000/ready", timeout=2)
            if resp.status_code == 200:
                logger.info("Model API is ready!")
                return
        except Exception:
            pass
        logger.info(f"  attempt {i + 1}/{max_retries} — not ready yet")
        time.sleep(delay)
    logger.error("Model API did not become ready in time. Exiting.")
    raise SystemExit(1)


# -----------------------------------------------------------------------
# Simulation
# -----------------------------------------------------------------------
def run_simulation():
    """Run the four-phase drift simulation."""
    phases = [
        {"name": "Normal",         "duration": 60, "drift_ratio": 0.0},
        {"name": "Slight Drift",   "duration": 60, "drift_ratio": 0.3},
        {"name": "Moderate Drift",  "duration": 60, "drift_ratio": 0.6},
        {"name": "Heavy Drift",    "duration": 60, "drift_ratio": 0.9},
    ]

    for phase in phases:
        logger.info(
            f"=== Phase: {phase['name']} "
            f"(drift_ratio={phase['drift_ratio']:.1f}, "
            f"duration={phase['duration']}s) ==="
        )

        end_time = time.time() + phase["duration"]
        count = 0
        in_count = 0
        ood_count = 0
        total_confidence = 0.0
        total_entropy = 0.0

        while time.time() < end_time:
            if random.random() < phase["drift_ratio"]:
                text = random.choice(out_dist.data)
                source = "OOD"
                ood_count += 1
            else:
                text = random.choice(in_dist.data)
                source = "IN"
                in_count += 1

            result = send_request(text)
            count += 1

            if result:
                total_confidence += result.get("confidence", 0)
                total_entropy += result.get("entropy", 0)

            if count % 10 == 0:
                avg_conf = total_confidence / count if count else 0
                avg_ent = total_entropy / count if count else 0
                logger.info(
                    f"  [{phase['name']}] {count} requests "
                    f"(IN={in_count}, OOD={ood_count}) "
                    f"avg_conf={avg_conf:.3f} avg_entropy={avg_ent:.3f}"
                )

            time.sleep(0.5)

        avg_conf = total_confidence / count if count else 0
        avg_ent = total_entropy / count if count else 0
        logger.info(
            f"  Phase complete: {count} requests "
            f"(IN={in_count}, OOD={ood_count}) "
            f"avg_confidence={avg_conf:.3f} avg_entropy={avg_ent:.3f}"
        )

    logger.info("=" * 60)
    logger.info("=== Drift simulation complete ===")
    logger.info("=" * 60)


# -----------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------
if __name__ == "__main__":
    wait_for_api()
    run_simulation()
