"""Stable identity rules for imported provider messages."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime


def normalize_provider(value: str) -> str:
    """Normalize an account/provider name for identity namespacing."""
    return normalize_text(value)


def normalize_message_id(value: str | None) -> str:
    """Normalize an RFC Message-ID while retaining its semantic content."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.removeprefix("<").removesuffix(">")


def normalize_text(value: str | None) -> str:
    """Normalize stable textual metadata for deterministic fingerprints."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def stable_message_identity(
    *,
    provider: str,
    message_id: str | None,
    subject: str = "",
    sender: str = "",
    received_at: datetime | None = None,
    body: str = "",
) -> str:
    """Return a versioned SHA-256 identity stable across Python processes."""
    normalized_provider = normalize_provider(provider)
    normalized_message_id = normalize_message_id(message_id)
    if normalized_message_id:
        identity_input = {
            "kind": "message-id",
            "message_id": normalized_message_id,
            "provider": normalized_provider,
        }
    else:
        identity_input = {
            "body": normalize_text(body),
            "kind": "content",
            "provider": normalized_provider,
            "received_at": received_at.isoformat() if received_at else "",
            "sender": normalize_text(sender),
            "subject": normalize_text(subject),
        }
    encoded = json.dumps(identity_input, sort_keys=True, separators=(",", ":")).encode()
    return f"v1:{hashlib.sha256(encoded).hexdigest()}"
