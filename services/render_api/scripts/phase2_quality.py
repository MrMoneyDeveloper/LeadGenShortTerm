"""Evaluate current deterministic/ranking policy against labelled synthetic review fixtures."""

import csv
import json
from pathlib import Path

from app.models import now
from app.qualification import score
from app.ranking import rank

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "services" / "render_api" / "tests" / "fixtures" / "short_term_quality.csv"


def evaluate():
    with FIXTURE.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    results = []
    for row in rows:
        deterministic = score(row["text"], "quality_fixture", now())
        ranked = rank(deterministic, True)
        predicted = ranked["route"] != "REJECT"
        results.append({
            "text": row["text"],
            "expected": row["expected"],
            "predicted_positive": predicted,
            "deterministic_score": deterministic["score"],
            "rank_score": ranked["rank_score"],
            "route": ranked["route"],
            "signals": deterministic["signals"],
        })

    positives = [item for item in results if item["expected"] == "POSITIVE"]
    negatives = [item for item in results if item["expected"] == "NEGATIVE"]
    true_positive = sum(item["predicted_positive"] for item in positives)
    false_positive = sum(item["predicted_positive"] for item in negatives)
    true_negative = len(negatives) - false_positive
    predicted_positive = true_positive + false_positive
    ordered = sorted(results, key=lambda item: (item["rank_score"], item["deterministic_score"]), reverse=True)

    def precision_at(limit):
        sample = ordered[:limit]
        return sum(item["expected"] == "POSITIVE" for item in sample) / len(sample)

    review_indices = [0, 1, 10, 28, 29, 49, 50, 54, 55, 59, 64, len(results) - 1]
    return {
        "fixture_count": len(results),
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "positive_precision": true_positive / predicted_positive if predicted_positive else 0,
        "negative_false_positive_rate": false_positive / len(negatives),
        "accuracy": (true_positive + true_negative) / len(results),
        "precision_at_10": precision_at(10),
        "precision_at_25": precision_at(25),
        "precision_at_50": precision_at(50),
        "route_counts": {route: sum(item["route"] == route for item in results)
                         for route in ("DIRECT_FINAL", "SEMANTIC", "DEFERRED", "REJECT")},
        "human_review": [results[index] for index in review_indices],
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=True, indent=2))
