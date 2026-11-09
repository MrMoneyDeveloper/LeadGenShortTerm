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


def term_groups():
    """Ordered, pre-normalized term groups. Pure input for local or remote term matching."""
    keys = rules("keywords")
    groups = {"product:" + p: [normalize(t) for t in terms] for p, terms in keys["products"].items()}
    groups.update({"signal:" + k: [normalize(t) for t in terms] for k, terms in keys["signals"].items()})
    groups["relevance"] = [normalize(t) for t in keys["relevance_terms"]]
    return groups


def hit(text, normalized_term):
    return bool(re.search(r"(?<!\w)" + re.escape(normalized_term) + r"(?!\w)", text))


def match_terms(text, groups):
    """Names of groups with at least one whole-phrase hit in already-normalized text, in group order."""
    return [name for name, terms in groups.items() if any(hit(text, t) for t in terms)]


def score(text, source, published_at, matched=None, profile_text=None):
    """``matched`` may come from a verified stateless worker; otherwise matching runs locally.

    ``profile_text`` is the same author's explicit public profile (e.g. a Bluesky bio). It may only
    supply the South African geography signal (provenance ``profile_bio``); it never feeds intent,
    product or any other signal, and location is never inferred beyond configured explicit terms.
    """
    text = normalize(text)
    keys, cfg = rules("keywords"), rules("scoring")
    found = set(match_terms(text, term_groups()) if matched is None else matched)
    product = next((p for p in keys["products"] if "product:" + p in found), "UNKNOWN")
    signals = [k for k in keys["signals"] if "signal:" + k in found]
    geography = "post" if "south_africa" in signals else None
    if geography is None and profile_text:
        sa_terms = {"signal:south_africa": term_groups()["signal:south_africa"]}
        if match_terms(normalize(profile_text), sa_terms):
            signals.append("south_africa")
            geography = "profile_bio"
    # A consumer asking for a broker is not a broker advertising: the override signal cancels it.
    overridden = {neg for neg, override in cfg.get("negative_overrides", {}).items() if override in signals}
    relevant = product != "UNKNOWN" or "relevance" in found
    value = min(100, max(0, sum(cfg["weights"].get(k, 0) for k in signals if k not in overridden)
                         + cfg["source_weights"].get(source, 0)))
    negative = any(k in signals and k not in overridden for k in cfg["negative_signals"])
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
        "geography_evidence": geography,
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
