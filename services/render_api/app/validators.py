import re

import dns.exception
import dns.resolver
from email_validator import EmailNotValidError, validate_email

from app.config import rules

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,63}")


def contacts(text, source_url):
    result = []
    for address in dict.fromkeys(EMAIL_PATTERN.findall(text)):
        address = address.rstrip(".")
        try:
            address = normalize_email(address)
        except EmailNotValidError:
            continue
        result.append({"email": address, "source_url": source_url})
    return result[:5]


def normalize_email(address):
    return validate_email(address.strip(), check_deliverability=False).normalized.casefold()


def validate_contact(address, resolver=None):
    try:
        email = normalize_email(address)
    except EmailNotValidError:
        return "INVALID", "invalid_syntax"
    local, domain = email.rsplit("@", 1)
    cfg = rules("disposable-domains")
    if any(domain == x or domain.endswith("." + x) for x in cfg["domains"]):
        return "RISKY", "disposable_domain"
    if local in cfg["role_accounts"]:
        return "RISKY", "role_account"
    resolver = resolver or dns.resolver.Resolver()
    resolver.lifetime = 4
    resolver.timeout = 2
    try:
        answer = resolver.resolve(domain, "MX")
        # RFC 7505 null MX declares that a domain accepts no mail.
        if not any(str(record.exchange).rstrip(".") for record in answer):
            return "NO_MX", "null_mx"
    except dns.resolver.NXDOMAIN:
        return "NO_DOMAIN", "nxdomain"
    except dns.resolver.NoAnswer:
        return "NO_MX", "no_mx"
    except (dns.exception.Timeout, dns.resolver.NoNameservers):
        return "RISKY", "dns_temporary"
    return "DELIVERABLE_DOMAIN", "syntax_and_mx_passed"
