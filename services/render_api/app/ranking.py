"""Auditable ordinal ranks, not calibrated conversion probabilities."""

from app.config import rules


def rank(result, valid_contact, ml=None):
    cfg = rules("scoring")
    policy = cfg["ranking"]
    signals = result.get("signals", [])
    product = result.get("product_type") in set(cfg.get("target_products", ["MOTOR", "HOME", "CONTENTS"]))
    # When the operator does not require an email, the contact gate is satisfied by policy so the
    # calibrated rank thresholds stay unchanged; intent/product/geography gates still apply.
    valid_contact = valid_contact or not cfg.get("require_contact", True)
    geography_signal = "south_africa" in signals
    geography_gate = geography_signal or not cfg["require_south_africa"]
    confident_ml = ml and ml["confidence"] >= cfg["local_confidence"]
    negative_ml = confident_ml and ml["label"] not in {"HIGH_INTENT", "MEDIUM_INTENT"}
    value = min(
        10,
        max(
            0,
            result.get("score", 0) / 10 * policy["rules_weight"]
            + policy["contact_points"] * bool(valid_contact)
            + policy["product_points"] * product
            + policy["geography_points"] * geography_signal
            + (policy["local_positive_bonus"] if confident_ml and not negative_ml else 0),
        ),
    )
    value = round(value, 3)
    review = policy.get("semantic_review", {})
    # Narrow review route: explicit consumer intent + in-scope product + South African evidence, but
    # a low rule score. Groq reviews it; final acceptance (semantic_qualified) is unchanged.
    reviewable = bool(
        review.get("enabled") and not result.get("rejected") and not negative_ml and valid_contact
        and product and geography_signal and set(signals) & set(review.get("intent_signals", []))
    )
    if reviewable and value < policy["semantic_at"]:
        route = "SEMANTIC"
    elif result.get("rejected") or negative_ml or value < policy["defer_at"]:
        route = "REJECT"
    elif not valid_contact:
        route = "CONTACT_REVIEW"
    elif value >= policy["direct_at"] and product and geography_gate and not conflicting(result, cfg):
        route = "DIRECT_FINAL"
    elif value >= policy["semantic_at"]:
        route = "SEMANTIC"
    else:
        route = "DEFERRED"
    return {
        "rank_score": value,
        "route": route,
        "review_route": bool(reviewable and route == "SEMANTIC" and value < policy["semantic_at"]),
        "version": policy["version"],
        "hard_gates": {
            "contact": bool(valid_contact),
            "product": product,
            "geography": geography_gate,
            "geography_signal": geography_signal,
        },
    }


def conflicting(result, cfg):
    """Conflicting/review-only evidence, or a rules result from an older rules version (scored before
    the current seller/conflict gates existed), blocks DIRECT_FINAL; semantic review still applies."""
    version = str(result.get("version", ""))
    legacy = version.startswith("rules-") and version != cfg["version"]
    return bool(result.get("conflicting_signals")) or legacy


def semantic_qualified(result):
    cfg = rules("scoring")
    return (
        result.is_insurance_relevant
        and result.is_consumer
        and not result.is_advertisement
        and not result.is_broker_or_agent
        and result.intent_level in {"HIGH", "MEDIUM"}
        and result.product_type in set(cfg.get("target_products", ["MOTOR", "HOME", "CONTENTS"]))
        and result.score >= cfg["ranking"]["semantic_accept_score"]
        and (result.south_africa_signal or not cfg["require_south_africa"])
    )
