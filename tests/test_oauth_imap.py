from __future__ import annotations

import argparse
import builtins
import hashlib
import json
import shutil
import sqlite3
import ssl
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from backend.app.services.imap_checkpoint import ImapCheckpoint, read_checkpoint, write_checkpoint
from backend.app.services.oauth_imap import OAuthImapSettings, OAuthTokenConfig
from backend.app.services.provider_live_sync import preflight_provider_live_sync
from backend.app.services.yahoo_imap import (
    ImapMetrics,
    YahooImapMessage,
    YahooImapScan,
    imap_message_identity,
    redact_exception,
    scan_with_reconnect,
)
from scripts import sync_oauth_imap

SINCE_DATE = date(2024, 7, 1)


class _Socket:
    def settimeout(self, value: float) -> None:
        self.timeout = value


class _Imap:
    def __init__(
        self, host: str, port: int, *, ssl_context: ssl.SSLContext, timeout: float
    ) -> None:
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.timeout = timeout
        self.sock = _Socket()
        self.calls: list[tuple[Any, ...]] = []

    def authenticate(self, mechanism: str, authobject: Any) -> tuple[str, builtins.list[bytes]]:
        response = authobject(b"")
        self.calls.append(("authenticate", mechanism, response))
        return "OK", [b"authenticated"]

    def login(self, user: str, password: str) -> tuple[str, builtins.list[bytes]]:
        raise AssertionError("OAuth transport must not use password login")

    def list(self) -> tuple[str, builtins.list[bytes]]:
        self.calls.append(("list",))
        return "OK", [b'(\\HasNoChildren) "/" "Jobs"']

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, builtins.list[bytes]]:
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def response(self, code: str) -> tuple[str, builtins.list[bytes]]:
        self.calls.append(("response", code))
        return "UIDVALIDITY", [b"700"]

    def uid(self, command: str, *args: Any) -> tuple[str, builtins.list[Any]]:
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b"1"]
        query = str(args[1])
        metadata = b'1 (UID 1 INTERNALDATE "01-Jul-2024 12:00:00 -0700")'
        if "HEADER.FIELDS" in query:
            value = (
                b"Message-ID: <oauth-fixture@example.invalid>\r\n"
                b"Subject: Interview invitation\r\n"
                b"From: Recruiter <recruiter@example.invalid>\r\n"
                b"To: fixture@gmail.example\r\n"
                b"Date: Mon, 1 Jul 2024 12:00:00 -0700\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            )
            return "OK", [(metadata, value)]
        if "BODY.PEEK[TEXT]" in query:
            return "OK", [(metadata, b"Schedule your interview")]
        return "NO", [b"missing"]

    def noop(self) -> tuple[str, builtins.list[bytes]]:
        return "OK", [b"noop"]

    def logout(self) -> tuple[str, builtins.list[bytes]]:
        return "BYE", [b"logout"]


class Factory:
    def __init__(self) -> None:
        self.connections: list[_Imap] = []

    def __call__(
        self, host: str, port: int, *, ssl_context: ssl.SSLContext, timeout: float
    ) -> _Imap:
        connection = _Imap(host, port, ssl_context=ssl_context, timeout=timeout)
        self.connections.append(connection)
        return connection


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _settings(provider: str = "gmail") -> OAuthImapSettings:
    host = "imap.gmail.com" if provider == "gmail" else "outlook.office365.com"
    return OAuthImapSettings(
        provider=provider,
        username=f"fixture@{provider}.example",
        token=OAuthTokenConfig(
            provider=provider,
            token_url="https://oauth.example/token",
            scope="mail.read",
            client_id="fixture-client",
            refresh_token="fixture-refresh",
            access_token="fixture-access",
        ),
        folder="Jobs",
        host=host,
    )


def _message(provider: str, uid: int = 10) -> YahooImapMessage:
    account = f"fixture@{provider}.example"
    identity = imap_message_identity(
        provider=provider,
        account_namespace=account,
        folder="Jobs",
        uidvalidity="700",
        uid=uid,
    )
    return YahooImapMessage(
        uid=uid,
        uidvalidity="700",
        folder="Jobs",
        account_namespace=account,
        message_id=f"fixture-{uid}@example.invalid",
        subject="Application received",
        sender="Applications <updates@acme.example>",
        recipients=(account,),
        received_at=datetime(2026, 1, 1, 12),
        imap_internal_date=datetime(2026, 1, 1, 12),
        requested_since_date=SINCE_DATE,
        text_body="Thank you for applying for Platform Lead. Job ID: OAUTH-100",
        html_fallback_used=False,
        attachments=(),
        identity=identity,
        provider=provider,
    )


def _scan(provider: str) -> YahooImapScan:
    message = _message(provider)
    return YahooImapScan(
        folder="Jobs",
        since_date=SINCE_DATE,
        uidvalidity="700",
        messages=(message,),
        failures=(),
        highest_contiguous_uid=message.uid,
        total_matched_uid_count=1,
        partial_matched_uid_count=1,
        batch_selected_count=1,
        processed_count=1,
        completed_count=1,
        first_uid=message.uid,
        last_uid=message.uid,
        last_uid_attempted=message.uid,
        last_uid_completed=message.uid,
        search_page_count=1,
        search_complete=True,
        reconnect_count=0,
        metrics=ImapMetrics(
            imap_search_commands=1,
            header_fetch_commands=1,
            bodystructure_fetch_commands=0,
            body_fetch_commands=1,
            total_fetch_commands=2,
            messages_requiring_body=1,
            average_fetch_commands_per_message=2.0,
            elapsed_seconds=0.1,
            messages_per_second=10.0,
            bodystructure_parse_failures=0,
            full_message_fallbacks=0,
            full_message_fallback_successes=0,
            full_message_fallback_failures=0,
            oversized_fallback_messages=0,
        ),
    )


def _sync_arguments(database: Path, provider: str) -> argparse.Namespace:
    return argparse.Namespace(
        provider=provider,
        folder="Jobs",
        since_date=SINCE_DATE,
        database=database,
        allow_live_database=False,
    )


def test_oauth_scan_uses_xoauth2_read_only_and_provider_identity() -> None:
    factory = Factory()

    scan = scan_with_reconnect(
        _settings(),
        folder="Jobs",
        since_date=SINCE_DATE,
        connection_factory=factory,
    )

    calls = factory.connections[0].calls
    authentication = next(call for call in calls if call[0] == "authenticate")
    assert authentication[1] == "XOAUTH2"
    assert b"auth=Bearer fixture-access" in authentication[2]
    assert ("select", "Jobs", True) in calls
    assert any(call[:2] == ("uid", "SEARCH") for call in calls)
    assert not any(call[0] in {"store", "move", "expunge"} for call in calls)
    assert scan.messages[0].provider == "gmail"
    assert scan.messages[0].identity == imap_message_identity(
        provider="gmail",
        account_namespace="fixture@gmail.example",
        folder="Jobs",
        uidvalidity="700",
        uid=1,
    )


def test_refresh_token_exchange_is_lazy_and_scoped() -> None:
    requests: list[tuple[str, bytes, float]] = []

    def post(url: str, payload: bytes, timeout: float) -> bytes:
        requests.append((url, payload, timeout))
        return b'{"access_token":"refreshed-access","token_type":"Bearer"}'

    token = OAuthTokenConfig(
        provider="hotmail",
        token_url="https://login.example/token",
        scope="imap offline_access",
        client_id="client",
        refresh_token="refresh",
    )

    assert requests == []
    assert token.resolve_access_token(timeout=12, token_post=post) == "refreshed-access"
    assert requests[0][0] == "https://login.example/token"
    assert b"grant_type=refresh_token" in requests[0][1]
    assert b"scope=imap+offline_access" in requests[0][1]
    assert requests[0][2] == 12


def test_oauth_credentials_are_redacted() -> None:
    settings = _settings()
    error = RuntimeError("fixture@gmail.example fixture-access fixture-refresh fixture-client")

    redacted = redact_exception(error, settings)

    assert "fixture@gmail.example" not in redacted
    assert "fixture-access" not in redacted
    assert "fixture-refresh" not in redacted
    assert "fixture-client" not in redacted
    assert redacted.count("[REDACTED]") == 4


def test_provider_scoped_transport_identities_do_not_merge() -> None:
    identities = {
        imap_message_identity(
            provider=provider,
            account_namespace="same@example.invalid",
            folder="Jobs",
            uidvalidity="700",
            uid=10,
        )
        for provider in ("gmail", "hotmail", "yahoo")
    }

    assert len(identities) == 3


@pytest.mark.parametrize(
    ("provider", "role_family"),
    [
        ("gmail", "Sales Engineer / Delivery Manager"),
        ("hotmail", "Marketing"),
    ],
)
def test_provider_import_is_repeat_safe_and_preserves_account_mapping(
    isolated_app: tuple[Any, Path], provider: str, role_family: str
) -> None:
    _, database = isolated_app
    module: Any = sys.modules["backend.main"]

    first = module.import_imap_messages([_message(provider)])
    second = module.import_imap_messages([_message(provider)])

    assert first["accepted_count"] == 1
    assert second["accepted_count"] == 0
    assert second["skipped_count"] == 1
    with sqlite3.connect(database) as connection:
        stored_provider = connection.execute("SELECT provider FROM imported_messages").fetchone()[0]
        account = connection.execute("SELECT email_account,role_family FROM jobs").fetchone()
        metadata_provider = connection.execute(
            "SELECT provider FROM imap_message_metadata"
        ).fetchone()[0]
    assert stored_provider == provider
    assert metadata_provider == provider
    assert account == (provider, role_family)


def test_checkpoint_scope_remains_provider_specific(
    isolated_app: tuple[Any, Path],
) -> None:
    _, database = isolated_app
    observed = datetime(2026, 1, 1, 12)
    for provider, uid in (("gmail", 20), ("hotmail", 30)):
        write_checkpoint(
            database,
            ImapCheckpoint(
                provider=provider,
                account_namespace=f"fixture@{provider}.example",
                folder="Jobs",
                since_date=SINCE_DATE,
                uidvalidity="700",
                last_successful_uid=uid,
                sync_started_at=observed,
                sync_completed_at=observed,
                scanned_count=1,
                accepted_count=1,
                skipped_count=0,
                failure_count=0,
            ),
        )

    gmail = read_checkpoint(
        database,
        provider="gmail",
        account_namespace="fixture@gmail.example",
        folder="Jobs",
        since_date=SINCE_DATE,
    )
    hotmail = read_checkpoint(
        database,
        provider="hotmail",
        account_namespace="fixture@hotmail.example",
        folder="Jobs",
        since_date=SINCE_DATE,
    )
    assert gmail is not None and gmail.last_successful_uid == 20
    assert hotmail is not None and hotmail.last_successful_uid == 30


@pytest.mark.parametrize("provider", ["gmail", "hotmail"])
def test_two_provider_sync_passes_are_repeat_safe_on_temporary_database(
    isolated_app: tuple[Any, Path], provider: str
) -> None:
    _, database = isolated_app
    settings = _settings(provider)
    values = _sync_arguments(database, provider)

    first = sync_oauth_imap._synchronize(values, settings, _scan(provider))
    second = sync_oauth_imap._synchronize(values, settings, _scan(provider))
    checkpoint = read_checkpoint(
        database,
        provider=provider,
        account_namespace=settings.account_namespace,
        folder="Jobs",
        since_date=SINCE_DATE,
    )

    assert first["import_result"]["accepted_count"] == 1
    assert second["import_result"]["accepted_count"] == 0
    assert second["import_result"]["skipped_count"] == 1
    assert checkpoint is not None and checkpoint.last_successful_uid == 10
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM imported_messages").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM imap_message_metadata").fetchone() == (1,)


def test_sanitized_report_is_written_only_outside_repository(tmp_path: Path) -> None:
    report = {"provider": "gmail", "database_writes": 0, "mailbox_mutations": 0}
    output = tmp_path / "gmail-dry-run.json"

    sync_oauth_imap._write_report(output, report)

    assert json.loads(output.read_text()) == report
    with pytest.raises(ValueError, match="outside the repository"):
        sync_oauth_imap._write_report(
            Path("tests") / "forbidden-sync-evidence.json",
            report,
        )


def test_provider_live_gate_is_offline_read_only_and_content_safe(
    isolated_app: tuple[Any, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, database = isolated_app
    from backend.app.services import provider_live_sync

    monkeypatch.setattr(provider_live_sync, "LIVE_DATABASE", database.resolve())
    backup = tmp_path / "backup.sqlite3"
    shutil.copy2(database, backup)
    metadata = tmp_path / "backup.metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "path": str(backup),
                "checksum_sha256": _sha256(backup),
                "alembic_revision": "20260808_0007",
            }
        )
    )
    dry_run = tmp_path / "dry-run.json"
    dry_run.write_text(
        json.dumps(
            {
                "provider": "gmail",
                "folder": "Jobs",
                "since_date": "2024-07-01",
                "uidvalidity": "700",
                "search_complete": True,
                "processed_count": 10,
                "failure_count": 0,
                "database_writes": 0,
                "mailbox_mutations": 0,
            }
        )
    )
    checksum = _sha256(database)

    result = preflight_provider_live_sync(
        database,
        settings=_settings(),
        provider="gmail",
        folder="Jobs",
        since_date=SINCE_DATE,
        expected_checksum=checksum,
        backup_metadata=metadata,
        dry_run_evidence=dry_run,
        confirmation="GMAIL-LIVE-SYNC",
    )

    assert result["network_connections"] == 0
    assert result["database_writes"] == 0
    assert result["mailbox_mutations"] == 0
    assert _sha256(database) == checksum


def test_provider_live_gate_rejects_message_content_fields(
    isolated_app: tuple[Any, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, database = isolated_app
    from backend.app.services import provider_live_sync

    monkeypatch.setattr(provider_live_sync, "LIVE_DATABASE", database.resolve())
    backup = tmp_path / "backup.sqlite3"
    shutil.copy2(database, backup)
    metadata = tmp_path / "backup.metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "path": str(backup),
                "checksum_sha256": _sha256(backup),
                "alembic_revision": "20260808_0007",
            }
        )
    )
    dry_run = tmp_path / "unsafe.json"
    dry_run.write_text(
        json.dumps(
            {
                "provider": "gmail",
                "folder": "Jobs",
                "since_date": "2024-07-01",
                "uidvalidity": "700",
                "search_complete": True,
                "failure_count": 0,
                "database_writes": 0,
                "mailbox_mutations": 0,
                "messages": [{"subject": "forbidden"}],
            }
        )
    )

    with pytest.raises(ValueError, match="forbidden keys"):
        preflight_provider_live_sync(
            database,
            settings=_settings(),
            provider="gmail",
            folder="Jobs",
            since_date=SINCE_DATE,
            expected_checksum=_sha256(database),
            backup_metadata=metadata,
            dry_run_evidence=dry_run,
            confirmation="GMAIL-LIVE-SYNC",
        )
