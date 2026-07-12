from __future__ import annotations

import os
import subprocess
import sys

from backend.app.services.import_identity import (
    normalize_message_id,
    normalize_text,
    stable_message_identity,
)


def test_message_id_normalization_is_explicit() -> None:
    assert normalize_message_id("  <ABC.123@Example.COM> \n") == "abc.123@example.com"
    assert normalize_text("  Product\tManager\nRole  ") == "product manager role"


def test_gmail_and_hotmail_identities_are_account_scoped() -> None:
    gmail = stable_message_identity(provider=" Gmail ", message_id="<same@example.com>")
    hotmail = stable_message_identity(provider="hotmail", message_id="same@example.com")

    assert gmail == stable_message_identity(provider="gmail", message_id="same@example.com")
    assert gmail != hotmail


def test_yahoo_identity_uses_message_id_when_available() -> None:
    first = stable_message_identity(
        provider="yahoo",
        message_id="<yahoo-1@example.com>",
        subject="Original title",
    )
    second = stable_message_identity(
        provider="YAHOO",
        message_id="yahoo-1@example.com",
        subject="Changed presentation",
    )

    assert first == second


def test_missing_message_id_fingerprint_is_process_independent() -> None:
    script = (
        "from backend.app.services.import_identity import stable_message_identity;"
        "print(stable_message_identity(provider='gmail', message_id=None, "
        "subject=' Role  Update ', sender='Recruiter@Example.com', "
        "body='Thank you\\nfor applying'))"
    )
    identities = []
    for seed in ("1", "999"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            env=environment,
            capture_output=True,
            text=True,
        )
        identities.append(result.stdout.strip())

    assert identities[0] == identities[1]
    assert identities[0].startswith("v1:")
