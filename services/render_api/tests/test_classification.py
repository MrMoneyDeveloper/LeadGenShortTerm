import json

import pytest
from pydantic import ValidationError

from app.classifiers.grok import SemanticResult, redact


def semantic():
    return {
        "is_short_term_insurance_relevant": True,
        "intent_level": "HIGH",
        "product_type": "MOTOR",
        "is_consumer": True,
        "is_advertisement": False,
        "is_broker_or_agent": False,
        "south_africa_signal": True,
        "score": 90,
        "reason": "Explicit consumer request",
    }


def test_grok_json_contract():
    assert SemanticResult.model_validate_json(json.dumps(semantic())).score == 90
    for change in ({"score": 101}, {"intent_level": "MAYBE"}, {"is_consumer": "true"}, {"extra": "x"}):
        with pytest.raises(ValidationError):
            SemanticResult.model_validate_json(json.dumps({**semantic(), **change}))


def test_personal_data_redaction():
    text = redact("Contact user@example.com or +27 82 555 1234 @handle https://example.com/profile")
    assert "user@example" not in text and "555" not in text and "@handle" not in text and "https://" not in text
