from datetime import timedelta

from app.models import now
from app.qualification import digest, normalize, score
from app.services.reporting import csv_text


def test_normalization_and_hashes():
    assert normalize("  CAR\n Insurance &amp; HOME ") == "car insurance & home"
    assert digest("USER@EXAMPLE.COM ") == digest("user@example.com")
    assert digest("different") != digest("user@example.com")


def test_intent_and_negative_rules():
    good = score(
        "Need a quote for car insurance in Johannesburg, premium increased, changing insurer", "bluesky", now()
    )
    assert not good["rejected"] and good["confident"]
    assert score("Insurance agent, need a quote for car insurance, hiring", "youtube", now())["rejected"]
    assert score("Need a quote for car insurance", "youtube", now() - timedelta(days=40))["rejected"]


def test_geography_not_inferred_from_platform():
    result = score(
        "Need a quote for car insurance, premium increased, changing insurer, insurance recommendations",
        "youtube",
        now(),
    )
    assert result["confident"]
    assert "south_africa" not in result["signals"]


def test_formula_injection_export():
    output = csv_text([{"value": '=HYPERLINK("bad")'}], ["value"])
    assert "'=HYPERLINK" in output
