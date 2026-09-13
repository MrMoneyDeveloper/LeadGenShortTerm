import hashlib
import logging
from functools import lru_cache
from pathlib import Path

from app.config import settings

LABELS = [
    "HIGH_INTENT",
    "MEDIUM_INTENT",
    "LOW_INTENT",
    "NOT_INSURANCE",
    "ADVERTISEMENT",
    "BROKER_OR_AGENT",
    "NEWS",
    "JOB_POST",
]


@lru_cache
def artifact():
    cfg = settings()
    if not cfg.local_model_path:
        return None
    path = Path(cfg.local_model_path)
    if not path.is_file() or not cfg.local_model_sha256:
        return None
    if hashlib.sha256(path.read_bytes()).hexdigest() != cfg.local_model_sha256:
        return None
    # joblib can execute code: only operator-provided, hash-pinned artifacts are loaded.
    import joblib

    return joblib.load(path)


def classify(text):
    try:
        model = artifact()
    except Exception as exc:
        logging.getLogger("leadgen.ml").warning("local_model_unavailable code=%s", type(exc).__name__)
        return None
    if model is None:
        return None
    try:
        probabilities = model["pipeline"].predict_proba([text])[0]
        best = int(probabilities.argmax())
        label = str(model["pipeline"].classes_[best])
        if label not in LABELS:
            return None
        return {"label": label, "confidence": float(probabilities[best]), "version": model["version"]}
    except (KeyError, ValueError, AttributeError):
        return None
