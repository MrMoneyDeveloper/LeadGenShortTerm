"""Explicit provider selection: GroqCloud and xAI are different services."""

from app.classifiers import grok, groq
from app.config import settings


class SemanticUnavailable(Exception):
    pass


def provider():
    return "groq" if settings().semantic_provider == "groq" else "grok"


def model():
    return settings().groq_model if settings().semantic_provider == "groq" else settings().xai_model


def classify(text, score):
    client = groq if settings().semantic_provider == "groq" else grok
    try:
        return client.classify(text, score)
    except (groq.GroqUnavailable, grok.GrokUnavailable) as exc:
        raise SemanticUnavailable(str(exc)) from None
