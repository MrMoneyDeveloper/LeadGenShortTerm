import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from app.classifiers import local


def test_train_hash_pin_and_load_local_classifier(tmp_path, isolated_settings):
    examples = {
        "HIGH_INTENT": "urgent consumer needs a car insurance quote Johannesburg",
        "MEDIUM_INTENT": "consumer comparing home insurance prices Cape Town",
        "LOW_INTENT": "consumer casually wonders about vehicle cover someday",
        "NOT_INSURANCE": "garden weather football recipe unrelated topic",
        "ADVERTISEMENT": "sponsored promotion buy our insurance special offer",
        "BROKER_OR_AGENT": "licensed insurance broker agent selling policies",
        "NEWS": "insurance press release market report newspaper headline",
        "JOB_POST": "insurance vacancy hiring careers apply for job",
    }
    source = tmp_path / "training.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["text", "label"])
        writer.writeheader()
        for label, text in examples.items():
            for number in range(65):
                writer.writerow({"text": f"{text} controlled sample {label.lower()} {number}", "label": label})

    artifact = tmp_path / "model.joblib"
    report = tmp_path / "report.json"
    root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "ml" / "train.py"),
            "--input",
            str(source),
            "--output",
            str(artifact),
            "--report",
            str(report),
            "--version",
            "phase2-fixture-v1",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    metrics = json.loads(report.read_text(encoding="utf-8"))
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert metrics["training_rows"] == 416 and metrics["holdout_rows"] == 104
    assert metrics["artifact_sha256"] == digest

    isolated_settings.local_model_path = str(artifact)
    isolated_settings.local_model_sha256 = digest
    local.artifact.cache_clear()
    result = local.classify("urgent consumer needs a car insurance quote Johannesburg")
    assert result["label"] == "HIGH_INTENT" and result["version"] == "phase2-fixture-v1"

    isolated_settings.local_model_sha256 = "0" * 64
    local.artifact.cache_clear()
    assert local.classify("insurance quote") is None
    local.artifact.cache_clear()
