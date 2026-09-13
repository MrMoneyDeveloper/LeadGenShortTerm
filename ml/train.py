"""Train and evaluate a trusted local artifact; does not collect data or call APIs."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

LABELS = {
    "HIGH_INTENT",
    "MEDIUM_INTENT",
    "LOW_INTENT",
    "NOT_INSURANCE",
    "ADVERTISEMENT",
    "BROKER_OR_AGENT",
    "NEWS",
    "JOB_POST",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    with open(args.input, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    rows = list({r["text"].strip().casefold(): r for r in rows if r["text"].strip()}.values())
    if len(rows) < 500 or any(r["label"] not in LABELS for r in rows):
        raise SystemExit("Need at least 500 deduplicated labelled examples with supported labels")
    if any(sum(r["label"] == label for r in rows) < 10 for label in LABELS):
        raise SystemExit("Need at least 10 examples of every supported label")
    train_x, test_x, train_y, test_y = train_test_split(
        [r["text"] for r in rows],
        [r["label"] for r in rows],
        test_size=0.2,
        stratify=[r["label"] for r in rows],
        random_state=42,
    )
    pipeline = Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), max_features=30000, sublinear_tf=True)),
            ("classifier", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42)),
        ]
    )
    pipeline.fit(train_x, train_y)
    report = classification_report(test_y, pipeline.predict(test_x), output_dict=True, zero_division=0)
    for path in (args.output, args.report):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipeline, "version": args.version, "labels": sorted(LABELS)}, args.output)
    report["artifact_sha256"] = hashlib.sha256(Path(args.output).read_bytes()).hexdigest()
    report["training_rows"], report["holdout_rows"] = len(train_x), len(test_x)
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Artifact and holdout report written. Review metrics before setting LOCAL_MODEL_PATH and LOCAL_MODEL_SHA256.")


if __name__ == "__main__":
    main()
