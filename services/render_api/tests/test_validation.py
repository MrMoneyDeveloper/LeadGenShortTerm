from types import SimpleNamespace

import dns.resolver

from app.validators import contacts, normalize_email, validate_contact


class Resolver:
    def __init__(self, answer=None, error=None):
        self.answer, self.error = answer, error

    def resolve(self, domain, kind):
        if self.error:
            raise self.error
        return self.answer


def test_contact_provenance():
    assert normalize_email(" Person@Example.com ") == "person@example.com"
    assert (
        contacts("Write person@example.com.", "https://example.org/post")[0]["source_url"] == "https://example.org/post"
    )


def test_validation_without_network():
    assert validate_contact("bad")[0] == "INVALID"
    assert validate_contact("p@mailinator.com")[0] == "RISKY"
    assert validate_contact("person@example.org", Resolver(error=dns.resolver.NXDOMAIN()))[0] == "NO_DOMAIN"
    assert validate_contact("person@example.org", Resolver(error=dns.resolver.NoAnswer()))[0] == "NO_MX"
    assert validate_contact("person@example.org", Resolver([SimpleNamespace(exchange=".")]))[0] == "NO_MX"
    assert (
        validate_contact("person@example.org", Resolver([SimpleNamespace(exchange="mx.example.org.")]))[0]
        == "DELIVERABLE_DOMAIN"
    )
