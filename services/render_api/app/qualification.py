import hashlib
import html
import re
import unicodedata

from app.config import rules
from app.models import now


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", html.unescape(value or "")).split()).casefold()


def digest(value):
    return hashlib.sha256(normalize(value).encode()).hexdigest()


def matches(text, phrase):
    return bool(re.search(r"(?<!\w)" + re.escape(normalize(phrase)) + r"(?!\w)", text))


def score(text, source, published_at):
    text = normalize(text)
    keys, cfg = rules("keywords"), rules("scoring")
    product = next((p for p, terms in keys["products"].items() if any(matches(text, t) for t in terms)), "UNKNOWN")
    signals = [k for k, terms in keys["signals"].items() if any(matches(text, t) for t in terms)]
    relevant = product != "UNKNOWN" or any(matches(text, term) for term in keys["relevance_terms"])
    value = min(100, max(0, sum(cfg["weights"].get(k, 0) for k in signals) + cfg["source_weights"].get(source, 0)))
    negative = any(k in signals for k in cfg["negative_signals"])
    age = (now() - published_at).total_seconds() / 86400
    rejected = not relevant or negative or age > cfg["max_age_days"] or age < -1 or value < cfg["discard_below"]
    confident = value >= cfg["accept_without_llm_above"] and product != "UNKNOWN"
    if cfg["require_south_africa"] and "south_africa" not in signals:
        confident = False
    return {
        "score": value,
        "product_type": product,
        "signals": signals,
        "rejected": rejected,
        "confident": confident,
        "version": cfg["version"],
    }


def discovery_match(text):
    cfg = rules("keywords")
    terms = [t for values in cfg["products"].values() for t in values]
    terms += [
        t for k, values in cfg["signals"].items() if k not in rules("scoring")["negative_signals"] for t in values
    ]
    text = normalize(text)
    return any(matches(text, term) for term in terms)
