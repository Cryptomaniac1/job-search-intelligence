"""Offline approval gate for Gmail and Hotmail production synchronization."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from .imap_checkpoint import EXPECTED_REVISION
from .oauth_imap import PROVIDERS, OAuthImapSettings
from .yahoo_imap import create_verified_tls_context
from .yahoo_incident import database_digests

LIVE_DATABASE = (Path(__file__).resolve().parents[3] / "data" / "jobs.db").resolve()
FORBIDDEN_EVIDENCE_KEYS = {
    "app_password",
    "authorization",
    "body",
    "message_id",
    "password",
    "raw_mime",
    "recipients",
    "refresh_token",
    "sender",
    "subject",
    "token",
    "username",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"{label} does not exist: {resolved}")
    try:
        payload: Any = json.loads(resolved.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def _database_evidence(path: Path, expected_checksum: str) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if resolved != LIVE_DATABASE:
        raise ValueError(f"Live database path must resolve exactly to {LIVE_DATABASE}")
    checksum = sha256_file(resolved)
    if checksum != expected_checksum:
        raise ValueError("Live database checksum does not match the explicit approval value")
    with sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        revision = str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
    if revision != EXPECTED_REVISION or integrity != "ok" or foreign_keys:
        raise ValueError("Live database revision or health validation failed")
    return {
        "path": str(resolved),
        "checksum_sha256": checksum,
        "revision": revision,
        "integrity_check": integrity,
        "foreign_key_violations": 0,
    }


def _backup_evidence(
    metadata_path: Path, expected_checksum: str, live_database: Path
) -> dict[str, Any]:
    metadata = _read_json(metadata_path, "Backup metadata")
    backup = Path(str(metadata.get("path", ""))).expanduser().resolve()
    if not backup.is_file():
        raise ValueError("Backup metadata does not reference a readable database")
    checksum = sha256_file(backup)
    if checksum != metadata.get("checksum_sha256"):
        raise ValueError("Backup checksum does not match metadata")
    if sha256_file(live_database) != expected_checksum:
        raise ValueError("Live source checksum changed after approval")
    if metadata.get("alembic_revision") != EXPECTED_REVISION:
        raise ValueError(f"Backup must be at revision {EXPECTED_REVISION}")
    with sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Backup integrity validation failed")
        if list(connection.execute("PRAGMA foreign_key_check")):
            raise ValueError("Backup foreign-key validation failed")
    if database_digests(backup) != database_digests(live_database):
        raise ValueError("Backup logical table digests do not match the approved live state")
    return {
        "path": str(backup),
        "checksum_sha256": checksum,
        "source_checksum_sha256": expected_checksum,
        "revision": EXPECTED_REVISION,
    }


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(key).casefold() for key in value} | {
            child for item in value.values() for child in _all_keys(item)
        }
    if isinstance(value, list):
        return {child for item in value for child in _all_keys(item)}
    return set()


def _dry_run_evidence(
    path: Path, *, provider: str, folder: str, since_date: date
) -> dict[str, Any]:
    evidence = _read_json(path, "Dry-run evidence")
    forbidden = sorted(_all_keys(evidence) & FORBIDDEN_EVIDENCE_KEYS)
    if forbidden:
        raise ValueError(f"Dry-run evidence contains forbidden keys: {', '.join(forbidden)}")
    expected = {
        "provider": provider,
        "folder": folder,
        "since_date": since_date.isoformat(),
        "search_complete": True,
        "failure_count": 0,
        "database_writes": 0,
        "mailbox_mutations": 0,
    }
    mismatches = [
        key for key, expected_value in expected.items() if evidence.get(key) != expected_value
    ]
    if mismatches or not evidence.get("uidvalidity"):
        fields = ", ".join(mismatches or ["uidvalidity"])
        raise ValueError(f"Dry-run evidence failed required fields: {fields}")
    return {key: evidence[key] for key in expected} | {
        "uidvalidity": str(evidence["uidvalidity"]),
        "processed_count": int(evidence.get("processed_count", 0)),
    }


def preflight_provider_live_sync(
    database: Path,
    *,
    settings: OAuthImapSettings,
    provider: str,
    folder: str,
    since_date: date,
    expected_checksum: str,
    backup_metadata: Path,
    dry_run_evidence: Path,
    confirmation: str,
) -> dict[str, Any]:
    """Validate a production sync without refreshing OAuth, connecting, or writing."""
    if provider not in PROVIDERS or settings.provider != provider:
        raise ValueError("Live provider must be gmail or hotmail")
    expected_token = f"{provider.upper()}-LIVE-SYNC"
    if confirmation != expected_token:
        raise ValueError(f"Live synchronization requires confirmation token {expected_token}")
    if settings.host != PROVIDERS[provider].host or settings.port != 993:
        raise ValueError("Live provider endpoint must use the approved TLS host and port 993")
    if not create_verified_tls_context().get_ca_certs():
        raise ValueError("No trusted TLS certificate authorities are available")
    return {
        "mode": "provider-live-preflight",
        "provider": provider,
        "folder": folder,
        "since_date": since_date.isoformat(),
        "database": _database_evidence(database, expected_checksum),
        "backup": _backup_evidence(backup_metadata, expected_checksum, database),
        "dry_run": _dry_run_evidence(
            dry_run_evidence, provider=provider, folder=folder, since_date=since_date
        ),
        "oauth_configuration_present": True,
        "tls_certificate_verification": True,
        "network_connections": 0,
        "database_writes": 0,
        "mailbox_mutations": 0,
    }
