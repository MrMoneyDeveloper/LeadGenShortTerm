# Local classifier lifecycle

No trained artifact is supplied because labelled production data does not exist yet.
`training/example.csv` documents the format only and is deliberately insufficient for training.
Keep real training data private and de-identify contacts, names, handles, and URLs.

After Phase 2 and human labelling, use a Python environment with the service requirements:

```sh
python ml/train.py --input ml/training/labelled.csv --output ml/models/intent-v1.joblib --report ml/evaluation/intent-v1.json --version intent-v1
```

The script requires 500 distinct examples and 10 per label, uses a stratified 80/20 split,
and writes precision/recall/F1 plus a SHA-256 digest. Review the holdout results and source/time
leakage before promotion. There is no automatic promotion. Set `LOCAL_MODEL_PATH` and
`LOCAL_MODEL_SHA256` together. Treat artifacts as executable code; do not download untrusted joblib files.
Deploy a reviewed artifact with the service or place it through an approved deployment step;
Render's runtime filesystem is ephemeral. Rebuild for the same scikit-learn version used at training.

Without a valid artifact, rules run first and ambiguous candidates use Grok when enabled;
otherwise they remain in the retry/review queue. Confidence is a model probability, not calibrated truth.
