from app.models import now
from app.qualification import score
from app.ranking import rank


def test_strong_short_term_intent_survives_domain_filter():
    cases = [
        ("Durban here. Need car insurance and a quote; can anyone recommend an insurer? My premium went up.", "MOTOR"),
        ("Bought a house in Cape Town and need home insurance quote.", "HOME"),
        ("Need contents insurance in Pretoria, can anyone recommend an insurer?", "CONTENTS"),
    ]
    for text, product in cases:
        result = score(text, "youtube", now())
        assert result["product_type"] == product
        assert not result["rejected"]
        assert result["score"] >= 25


def test_long_term_personal_insurance_is_in_scope():
    cases = [
        ("Need life insurance in Durban, any recommendations?", "LIFE"),
        ("Looking for funeral cover for my parents in Soweto, need a quote", "FUNERAL"),
        ("Self-employed in Pretoria and need income protection, which insurer is best?", "DISABILITY"),
        ("Soek lewensversekering in Kaapstad, enige aanbevelings? need a quote", "LIFE"),
    ]
    for text, product in cases:
        result = score(text, "bluesky", now())
        assert result["product_type"] == product, text
        assert not result["rejected"], text
        assert rank(result, True)["hard_gates"]["product"]


def test_non_target_seller_job_and_news_are_hard_negatives():
    samples = [
        "Need medical aid in Durban",
        "Best retirement annuity investment product",
        "Insurance broker in Johannesburg; contact me for insurance",
        "Insurance jobs hiring in Cape Town",
        "Insurance news annual report and industry report from South Africa",
    ]
    for text in samples:
        assert score(text, "bluesky", now())["rejected"]


def test_missing_geography_is_not_a_hard_reject_but_cannot_finalize_directly():
    result = score("Need car insurance and a quote. Can anyone recommend an insurer?", "youtube", now())
    assert "south_africa" not in result["signals"]
    assert not result["rejected"]
    ranked = rank(result, True)
    assert not ranked["hard_gates"]["geography"]
    assert not ranked["hard_gates"]["geography_signal"]
    assert ranked["route"] not in {"REJECT", "DIRECT_FINAL"}


def test_south_african_evidence_requirement_is_configurable(monkeypatch):
    import app.ranking as ranking
    from app.config import rules

    record = {"score": 100, "product_type": "MOTOR", "signals": []}
    assert not ranking.rank(record, True)["hard_gates"]["geography"]  # repository policy: required
    relaxed = {**rules("scoring"), "require_south_africa": False}
    monkeypatch.setattr(ranking, "rules", lambda name: relaxed)
    assert ranking.rank(record, True)["hard_gates"]["geography"]


def test_semantic_pass_requires_south_african_signal():
    from app.classifiers.grok import SemanticResult
    from app.ranking import semantic_qualified

    base = dict(is_insurance_relevant=True, intent_level="HIGH", product_type="MOTOR", is_consumer=True,
                is_advertisement=False, is_broker_or_agent=False, score=95, reason="fixture")
    assert semantic_qualified(SemanticResult(**base, south_africa_signal=True))
    assert not semantic_qualified(SemanticResult(**base, south_africa_signal=False))
