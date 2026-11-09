"""Explicit provider selection: GroqCloud and xAI are different services."""

from app.classifiers import grok, groq
from app.config import settings


class SemanticUnavailable(Exception):
    def __init__(self, code, retry_at=None):
        super().__init__(code)
        self.retry_at = retry_at


def provider():
    return "groq" if settings().semantic_provider == "groq" else "grok"


def model():
    return settings().groq_model if settings().semantic_provider == "groq" else settings().xai_model


def classify(text, score, geography=None):
    """``geography`` is a bounded excerpt of the same author's public profile location, labelled
    separately from the intent text; it can only support the South African signal."""
    client = groq if settings().semantic_provider == "groq" else grok
    try:
        return client.classify(text, score, geography)
    except (groq.GroqUnavailable, grok.GrokUnavailable) as exc:
        raise SemanticUnavailable(str(exc), getattr(exc, "retry_at", None)) from None
