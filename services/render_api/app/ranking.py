"""Auditable ordinal ranks, not calibrated conversion probabilities."""

from app.config import rules


def rank(result, valid_contact, ml=None):
    cfg = rules("scoring")
    policy = cfg["ranking"]
    signals = result.get("signals", [])
    product = result.get("product_type") in {"MOTOR", "HOME", "CONTENTS"}
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
    if result.get("rejected") or negative_ml or value < policy["defer_at"]:
        route = "REJECT"
    elif not valid_contact:
        route = "CONTACT_REVIEW"
    elif value >= policy["direct_at"] and product and geography_gate:
        route = "DIRECT_FINAL"
    elif value >= policy["semantic_at"]:
        route = "SEMANTIC"
    else:
        route = "DEFERRED"
    return {
        "rank_score": value,
        "route": route,
        "version": policy["version"],
        "hard_gates": {
            "contact": bool(valid_contact),
            "product": product,
            "geography": geography_gate,
            "geography_signal": geography_signal,
        },
    }


def semantic_qualified(result):
    cfg = rules("scoring")
    return (
        result.is_short_term_insurance_relevant
        and result.is_consumer
        and not result.is_advertisement
        and not result.is_broker_or_agent
        and result.intent_level in {"HIGH", "MEDIUM"}
        and result.product_type in {"MOTOR", "HOME", "CONTENTS"}
        and result.score >= cfg["ranking"]["semantic_accept_score"]
        and (result.south_africa_signal or not cfg["require_south_africa"])
    )
