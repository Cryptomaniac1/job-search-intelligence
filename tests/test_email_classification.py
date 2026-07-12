from __future__ import annotations

import json
from pathlib import Path

import pytest
from backend.app.services.email_classification import (
    CLASSIFIER_VERSION,
    EmailType,
    classify_email,
)

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "classification" / "cases.json").read_text()
)


@pytest.mark.parametrize("case", CASES, ids=[case["expected"] for case in CASES])
def test_every_canonical_type_has_an_explainable_fixture(case: dict[str, str]) -> None:
    result = classify_email(
        subject=case["subject"],
        sender="recruiter@sanitized.example",
        body=case["body"],
    )

    assert result.classification == EmailType(case["expected"])
    assert 0.0 <= result.confidence <= 1.0
    assert result.classifier_version == CLASSIFIER_VERSION
    assert result.reasons


def test_classification_is_provider_agnostic_and_deterministic() -> None:
    inputs = {
        "subject": "Interview",
        "sender": "recruiter@sanitized.example",
        "body": "Please schedule your interview.",
    }

    assert classify_email(**inputs) == classify_email(**inputs)


def test_specific_rule_wins_over_generic_offer_signal() -> None:
    result = classify_email(
        subject="Offer update",
        sender="recruiter@sanitized.example",
        body="Your revised offer includes an offer letter.",
    )

    assert result.classification == EmailType.OFFER_UPDATE
