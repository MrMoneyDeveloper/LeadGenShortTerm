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


def test_non_target_seller_job_and_news_are_hard_negatives():
    samples = [
        "Need life insurance in Durban",
        "Insurance broker in Johannesburg; contact me for insurance",
        "Insurance jobs hiring in Cape Town",
        "Insurance news annual report and industry report from South Africa",
    ]
    for text in samples:
        assert score(text, "bluesky", now())["rejected"]


def test_missing_geography_is_not_an_automatic_hard_reject():
    result = score("Need car insurance and a quote. Can anyone recommend an insurer?", "youtube", now())
    assert "south_africa" not in result["signals"]
    assert not result["rejected"]
    ranked = rank(result, True)
    assert ranked["hard_gates"]["geography"]
    assert not ranked["hard_gates"]["geography_signal"]
    assert ranked["route"] != "REJECT"
