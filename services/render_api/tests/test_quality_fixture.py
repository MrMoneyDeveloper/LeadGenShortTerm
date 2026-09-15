from scripts.phase2_quality import evaluate


def test_labelled_short_term_quality_fixture_is_measurable():
    result = evaluate()
    assert result["fixture_count"] >= 75
    assert result["positive_count"] >= 50
    assert result["negative_count"] >= 20
    assert result["positive_precision"] >= 0.9
    assert result["negative_false_positive_rate"] <= 0.1
    assert result["precision_at_10"] >= 0.9
    assert len(result["human_review"]) >= 10
