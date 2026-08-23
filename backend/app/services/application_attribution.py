"""Deterministic role attribution for application evidence."""

import re

ROLE_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Solutions Consulting",
        re.compile(r"\b(?:solutions?|technical)\s+consultant\b", re.I),
    ),
    (
        "Sales Engineering",
        re.compile(
            r"\b(?:sales|pre[ -]?sales|field|enterprise|technical|systems?|solutions?)"
            r"\s+engineer\b",
            re.I,
        ),
    ),
    ("Delivery Management", re.compile(r"\b(?:technical\s+)?delivery\s+manager\b", re.I)),
    (
        "Operations Management",
        re.compile(r"\b(?:operations?|ops)\s+(?:program\s+)?(?:manager|analyst)\b", re.I),
    ),
)


def infer_application_role_family(text: str) -> str | None:
    """Return a role only when a specific job-family phrase is present."""
    for role_family, pattern in ROLE_FAMILY_PATTERNS:
        if pattern.search(text):
            return role_family
    return None
