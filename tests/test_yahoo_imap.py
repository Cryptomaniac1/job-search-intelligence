from __future__ import annotations

import argparse
import builtins
import imaplib
import json
import os
import sqlite3
import ssl
import subprocess
import sys
from datetime import date, datetime
from email.parser import BytesParser
from pathlib import Path
from typing import Any

import pytest
from backend.app.services.imap_checkpoint import (
    ImapCheckpoint,
    UidValidityChangedError,
    read_checkpoint,
    require_stable_uidvalidity,
    write_checkpoint,
)
from backend.app.services.yahoo_imap import (
    ImapMetrics,
    ScanProgress,
    YahooImapClient,
    YahooImapMessage,
    YahooImapScan,
    YahooImapSettings,
    format_imap_since_date,
    imap_message_identity,
    parse_bodystructure,
    scan_with_reconnect,
)

ROOT = Path(__file__).resolve().parents[1]
SINCE_DATE = date(2024, 7, 1)


def header(
    *,
    subject: str = "Interview invitation",
    message_id: str = "message@example.invalid",
    content_type: str = "text/plain; charset=utf-8",
) -> bytes:
    return (
        f"Message-ID: <{message_id}>\r\n"
        f"Subject: {subject}\r\n"
        "From: Avery Recruiter <avery@acme.example>\r\n"
        "To: Person <person@yahoo.com>\r\n"
        "Date: Fri, 2 Jan 2027 12:00:00 -0800\r\n"
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()


PLAIN_PART = b'("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 42 1 NIL NIL NIL NIL)'
HTML_PART = b'("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL "7BIT" 42 1 NIL NIL NIL NIL)'
PDF_PART = (
    b'("APPLICATION" "PDF" ("NAME" "resume.pdf") NIL NIL "BASE64" 100 '
    b'NIL ("ATTACHMENT" ("FILENAME" "resume.pdf")) NIL NIL)'
)


def alternative_structure() -> bytes:
    return b"(" + PLAIN_PART + b" " + HTML_PART + b' "ALTERNATIVE" ("BOUNDARY" "x"))'


def mixed_structure() -> bytes:
    return b"(" + alternative_structure() + b" " + PDF_PART + b' "MIXED" ("BOUNDARY" "y"))'


def many_part_structure(part_count: int) -> bytes:
    return b"(" + b" ".join(PLAIN_PART for _ in range(part_count)) + b' "MIXED")'


class FakeImap:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext,
        timeout: float,
        bad_password: bool = False,
        abort_login: bool = False,
        folders: tuple[str, ...] = ("Inbox", "Jobs"),
        uids: tuple[int, ...] = (1,),
        headers: dict[int, bytes] | None = None,
        bodies: dict[tuple[int, str], bytes] | None = None,
        bodystructures: dict[int, bytes] | None = None,
        full_messages: dict[int, bytes] | None = None,
        internal_dates: dict[int, date] | None = None,
        broken_fetches: set[tuple[int, str]] | None = None,
        timeout_fetches: dict[tuple[int, str], int] | None = None,
        timeout_message: str = "simulated socket timeout",
        default_messages: bool = False,
        search_page_size: int | None = None,
        repeat_search_page: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.timeout = timeout
        self.sock = FakeSocket()
        self.bad_password = bad_password
        self.abort_login = abort_login
        self.folders = folders
        self.uids = uids
        self.headers = headers or {1: header()}
        self.bodies = bodies or {(1, "TEXT"): b"Schedule your interview. Senior Recruiter"}
        self.bodystructures = bodystructures or {}
        self.full_messages = full_messages or {}
        self.internal_dates = internal_dates or {uid: SINCE_DATE for uid in uids}
        self.broken_fetches = broken_fetches if broken_fetches is not None else set()
        self.timeout_fetches = timeout_fetches if timeout_fetches is not None else {}
        self.timeout_message = timeout_message
        self.default_messages = default_messages
        self.search_page_size = search_page_size
        self.repeat_search_page = repeat_search_page
        self.calls: builtins.list[tuple[Any, ...]] = []

    def login(self, user: str, password: str) -> tuple[str, builtins.list[bytes]]:
        self.calls.append(("login", user, password))
        if self.abort_login:
            raise imaplib.IMAP4.abort("temporary disconnect")
        if self.bad_password:
            raise imaplib.IMAP4.error(f"bad password {password} for {user}")
        return "OK", [b"authenticated"]

    def list(self) -> tuple[str, builtins.list[bytes]]:
        self.calls.append(("list",))
        return "OK", [f'(\\HasNoChildren) "/" "{item}"'.encode() for item in self.folders]

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, builtins.list[bytes]]:
        self.calls.append(("select", mailbox, readonly))
        return "OK", [str(len(self.uids)).encode()]

    def response(self, code: str) -> tuple[str, builtins.list[bytes]]:
        self.calls.append(("response", code))
        return "UIDVALIDITY", [b"700"]

    def uid(self, command: str, *args: Any) -> tuple[str, builtins.list[Any] | None]:
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            assert args[1] == "SINCE"
            since_date = datetime.strptime(str(args[2]), "%d-%b-%Y").date()
            assert args[3] == "UID"
            start_uid = int(str(args[4]).removesuffix(":*"))
            matches = [
                uid
                for uid in self.uids
                if uid >= start_uid and self.internal_dates[uid] >= since_date
            ]
            if self.repeat_search_page and start_uid > min(self.uids, default=1):
                matches = list(self.uids)
            if self.search_page_size is not None:
                matches = matches[: self.search_page_size]
            return "OK", [" ".join(str(uid) for uid in matches).encode()]
        uid = int(args[0])
        query = str(args[1])
        failure_key = (uid, query)
        remaining_timeouts = self.timeout_fetches.get(failure_key, 0)
        if remaining_timeouts:
            self.timeout_fetches[failure_key] = remaining_timeouts - 1
            raise TimeoutError(self.timeout_message)
        if failure_key in self.broken_fetches:
            self.broken_fetches.remove(failure_key)
            raise BrokenPipeError("simulated broken pipe")
        if query == "(BODYSTRUCTURE)":
            value = self.bodystructures.get(uid)
            if value is None:
                return "NO", [b"missing"]
            return "OK", [f"{uid} (UID {uid} BODYSTRUCTURE ".encode() + value + b")"]
        if query.startswith("(BODY.PEEK[]<0."):
            value = self.full_messages.get(uid)
            if value is None:
                return "NO", [b"missing"]
            maximum = int(query.removeprefix("(BODY.PEEK[]<0.").removesuffix(">)"))
            value = value[:maximum]
        elif "HEADER.FIELDS" in query:
            value = self.headers.get(uid)
            if value is None and self.default_messages:
                value = header(message_id=f"message-{uid}@example.invalid")
        elif "BODY.PEEK[TEXT]" in query:
            value = self.bodies.get((uid, "TEXT"))
            if value is None and self.default_messages:
                value = b"Schedule your interview"
        else:
            section = query.removeprefix("(BODY.PEEK[").removesuffix("])")
            value = self.bodies.get((uid, section))
        if value is None:
            return "NO", [b"missing"]
        internal_date = self.internal_dates[uid].strftime("%d-%b-%Y")
        metadata = f'{uid} (UID {uid} INTERNALDATE "{internal_date} 12:00:00 -0700")'.encode()
        return "OK", [(metadata, value)]

    def logout(self) -> tuple[str, builtins.list[bytes]]:
        self.calls.append(("logout",))
        return "BYE", [b"logout"]

    def noop(self) -> tuple[str, builtins.list[bytes]]:
        self.calls.append(("noop",))
        return "OK", [b"noop"]


class FakeSocket:
    def __init__(self) -> None:
        self.timeout: float | None = None

    def settimeout(self, value: float) -> None:
        self.timeout = value


class Factory:
    def __init__(self, **options: Any) -> None:
        self.options = options
        self.connections: builtins.list[FakeImap] = []

    def __call__(
        self, host: str, port: int, *, ssl_context: ssl.SSLContext, timeout: float
    ) -> FakeImap:
        connection = FakeImap(host, port, ssl_context=ssl_context, timeout=timeout, **self.options)
        self.connections.append(connection)
        return connection


def settings(username: str = "person@yahoo.com", password: str = "app-secret") -> YahooImapSettings:
    return YahooImapSettings(username, password, "Jobs")


def test_successful_tls_login_and_folder_listing() -> None:
    factory = Factory()
    with YahooImapClient(settings(), connection_factory=factory) as client:
        folders = client.list_folders()

    connection = factory.connections[0]
    assert folders == ("Inbox", "Jobs")
    assert connection.port == 993
    assert isinstance(connection.ssl_context, ssl.SSLContext)
    assert connection.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert connection.ssl_context.check_hostname is True
    assert connection.timeout == 30
    assert connection.sock.timeout == 60


def test_read_timeout_also_applies_to_noop_and_logout() -> None:
    factory = Factory()
    with YahooImapClient(settings(), connection_factory=factory) as client:
        client.noop()

    connection = factory.connections[0]
    assert connection.sock.timeout == 60
    assert ("noop",) in connection.calls
    assert ("logout",) in connection.calls


def test_bad_app_password_is_redacted() -> None:
    factory = Factory(bad_password=True)

    with pytest.raises(RuntimeError) as error:
        with YahooImapClient(settings(), connection_factory=factory):
            pass

    assert "app-secret" not in str(error.value)
    assert "person@yahoo.com" not in str(error.value)
    assert "[REDACTED]" in str(error.value)


def test_missing_credentials_and_plaintext_port_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YAHOO_IMAP_USERNAME", raising=False)
    monkeypatch.delenv("YAHOO_IMAP_APP_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="YAHOO_IMAP_USERNAME"):
        YahooImapSettings.from_environment()
    monkeypatch.setenv("YAHOO_IMAP_USERNAME", "person@yahoo.com")
    monkeypatch.setenv("YAHOO_IMAP_APP_PASSWORD", "app-secret")
    with pytest.raises(ValueError, match="plaintext IMAP is refused"):
        YahooImapSettings.from_environment(port=143)


def test_exact_folder_selection_is_read_only() -> None:
    factory = Factory()
    with YahooImapClient(settings(), connection_factory=factory) as client:
        uidvalidity = client.select_exact_folder("Jobs")

    assert uidvalidity == "700"
    assert ("select", "Jobs", True) in factory.connections[0].calls


def test_missing_or_inexact_folder_is_refused() -> None:
    factory = Factory()
    with YahooImapClient(settings(), connection_factory=factory) as client:
        with pytest.raises(ValueError, match="folder not found"):
            client.select_exact_folder("jobs")


def test_scan_uses_headers_first_and_never_mutating_commands() -> None:
    factory = Factory()
    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )
    calls = factory.connections[0].calls
    fetches = [call for call in calls if call[:2] == ("uid", "FETCH")]

    assert scan.messages[0].text_body == "schedule your interview. senior recruiter"
    assert "HEADER.FIELDS" in fetches[0][3]
    assert "BODY.PEEK[TEXT]" in fetches[1][3]
    assert not any(call[0] in {"store", "move", "expunge"} for call in calls)


def test_date_search_is_inclusive_and_excludes_older_messages_server_side() -> None:
    factory = Factory(
        uids=(1, 2),
        headers={1: header(), 2: header(message_id="included@example.invalid")},
        bodies={
            (1, "TEXT"): b"Schedule your interview",
            (2, "TEXT"): b"Schedule your interview",
        },
        internal_dates={1: date(2024, 6, 30), 2: SINCE_DATE},
    )

    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert [message.uid for message in scan.messages] == [2]
    assert scan.total_matched_uid_count == 1
    assert (
        "uid",
        "SEARCH",
        None,
        "SINCE",
        "01-Jul-2024",
        "UID",
        "1:*",
    ) in factory.connections[0].calls


def test_future_since_date_has_zero_matches_and_no_fetches() -> None:
    factory = Factory()

    scan = scan_with_reconnect(
        settings(),
        folder="Jobs",
        since_date=date(2030, 1, 1),
        connection_factory=factory,
    )

    assert scan.total_matched_uid_count == 0
    assert scan.processed_count == 0
    assert not any(call[:2] == ("uid", "FETCH") for call in factory.connections[0].calls)


def test_search_never_enumerates_the_full_folder() -> None:
    factory = Factory()
    scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    searches = [call for call in factory.connections[0].calls if call[:2] == ("uid", "SEARCH")]
    assert searches == [("uid", "SEARCH", None, "SINCE", "01-Jul-2024", "UID", "1:*")]


def test_internal_date_is_audited_independently_from_date_header() -> None:
    factory = Factory(internal_dates={1: SINCE_DATE})

    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )
    message = scan.messages[0]

    assert message.imap_internal_date == datetime(2024, 7, 1, 19)
    assert message.received_at == datetime(2027, 1, 2, 20)
    assert message.requested_since_date == SINCE_DATE


def test_invalid_date_format_is_rejected() -> None:
    from scripts.sync_yahoo_imap import parse_since_date

    with pytest.raises(argparse.ArgumentTypeError, match="YYYY-MM-DD"):
        parse_since_date("07/01/2024")
    with pytest.raises(argparse.ArgumentTypeError, match="cannot be before"):
        parse_since_date("2024-06-30")


def test_imap_since_date_format() -> None:
    assert format_imap_since_date(SINCE_DATE) == "01-Jul-2024"


def test_html_is_normalized_only_as_fallback() -> None:
    factory = Factory(
        headers={1: header(content_type="text/html; charset=utf-8")},
        bodies={(1, "TEXT"): b"<p>Schedule <b>your interview</b></p>"},
    )
    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert scan.messages[0].html_fallback_used is True
    assert scan.messages[0].text_body == "schedule your interview"


def test_multipart_prefers_plain_and_never_fetches_attachment_body() -> None:
    factory = Factory(
        headers={1: header(content_type='multipart/mixed; boundary="x"')},
        bodystructures={1: mixed_structure()},
        bodies={
            (1, "1.1"): b"Schedule your interview",
            (1, "1.2"): b"<p>HTML fallback</p>",
        },
    )
    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )
    queries = [
        str(call[3]) for call in factory.connections[0].calls if call[:2] == ("uid", "FETCH")
    ]

    assert scan.messages[0].text_body == "schedule your interview"
    assert scan.messages[0].attachments[0].filename == "resume.pdf"
    assert "(BODY.PEEK[2])" not in queries
    assert queries.count("(BODYSTRUCTURE)") == 1
    assert not any(".MIME" in query for query in queries)


def test_multipart_alternative_uses_html_only_as_fallback() -> None:
    html_only = b"(" + HTML_PART + b' "ALTERNATIVE" ("BOUNDARY" "x"))'
    factory = Factory(
        headers={1: header(content_type='multipart/alternative; boundary="x"')},
        bodystructures={1: html_only},
        bodies={(1, "1"): b"<p>Schedule <b>your interview</b></p>"},
    )

    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert scan.messages[0].html_fallback_used is True
    assert scan.messages[0].text_body == "schedule your interview"
    assert scan.metrics.bodystructure_fetch_commands == 1
    assert scan.metrics.body_fetch_commands == 1


def test_nested_multipart_selects_plain_part_and_preserves_attachment_metadata() -> None:
    factory = Factory(
        headers={1: header(content_type='multipart/mixed; boundary="y"')},
        bodystructures={1: mixed_structure()},
        bodies={(1, "1.1"): b"Plain interview details"},
    )

    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert scan.messages[0].text_body == "plain interview details"
    assert scan.messages[0].attachments == (scan.messages[0].attachments[0],)
    assert scan.messages[0].attachments[0].content_type == "application/pdf"
    fetch_queries = [
        call[3] for call in factory.connections[0].calls if call[:2] == ("uid", "FETCH")
    ]
    assert fetch_queries.count("(BODYSTRUCTURE)") == 1
    assert "(BODY.PEEK[1.1])" in fetch_queries
    assert "(BODY.PEEK[2])" not in fetch_queries


def test_bodystructure_extensions_quoted_strings_literals_and_nil_are_tolerated() -> None:
    structure = (
        b'1 (UID 1 BODYSTRUCTURE ("TEXT" "PLAIN" ("CHARSET" "UTF-8" '
        b'"TITLE" "Interview details with spaces") NIL NIL "7BIT" 42 1 NIL NIL '
        b'("INLINE" ("FILENAME" {11}\r\nreport name)) ("en" "fr") '
        b'"body/location" NIL "unexpected-extension"))'
    )

    parts = parse_bodystructure(structure, max_parts=10)

    assert len(parts) == 1
    assert parts[0].content_type == "text/plain"
    assert parts[0].filename == "report name"
    assert parts[0].disposition == "inline"


def test_bodystructure_escaped_quote_and_rfc2231_filename_are_decoded() -> None:
    escaped = (
        b'1 (BODYSTRUCTURE ("APPLICATION" "PDF" '
        b'("NAME" "report \\"final\\".pdf") NIL NIL "BASE64" 100 NIL '
        b'("ATTACHMENT" ("FILENAME*" "utf-8\'\'r%C3%A9sum%C3%A9.pdf")) NIL NIL))'
    )

    parts = parse_bodystructure(escaped, max_parts=10)

    assert parts[0].filename == "résumé.pdf"
    assert parts[0].is_attachment is True


def test_nested_multipart_accepts_language_location_and_unknown_extensions() -> None:
    structure = (
        b"1 (BODYSTRUCTURE ("
        + alternative_structure()
        + b" "
        + PDF_PART
        + b' "MIXED" ("BOUNDARY" "outer") ("INLINE" NIL) '
        + b'("en") "archive/location" NIL ("X-EXT" "value")))'
    )

    parts = parse_bodystructure(structure, max_parts=10)

    assert [part.section for part in parts] == ["1.1", "1.2", "2"]
    assert parts[-1].filename == "resume.pdf"


@pytest.mark.parametrize(
    "fixture_name",
    ["yahoo_marked_section_en.eml", "yahoo_marked_section_e.eml"],
)
def test_sanitized_marked_section_regressions_do_not_fail_html_parsing(
    fixture_name: str,
) -> None:
    raw_message = (ROOT / "tests" / "fixtures" / fixture_name).read_bytes()
    factory = Factory(
        headers={1: header(content_type='multipart/mixed; boundary="x"')},
        bodystructures={1: b"(BROKEN)"},
        full_messages={1: raw_message},
    )

    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert len(scan.messages) == 1
    assert scan.failures == ()
    assert scan.metrics.bodystructure_parse_failures == 1
    assert scan.metrics.full_message_fallbacks == 1
    assert scan.metrics.full_message_fallback_successes == 1
    assert scan.metrics.full_message_fallback_failures == 0
    assert "sanitized" in scan.messages[0].text_body


@pytest.mark.parametrize(
    "fixture_name",
    ["yahoo_marked_section_en.eml", "yahoo_marked_section_e.eml"],
)
def test_sanitized_marked_sections_are_tolerated_on_selected_html_part(
    fixture_name: str,
) -> None:
    raw_message = (ROOT / "tests" / "fixtures" / fixture_name).read_bytes()
    parsed = BytesParser().parsebytes(raw_message)
    payload = parsed.get_payload(decode=True)
    assert isinstance(payload, bytes)
    factory = Factory(
        headers={1: header(content_type='multipart/alternative; boundary="x"')},
        bodystructures={1: b"(" + HTML_PART + b' "ALTERNATIVE")'},
        bodies={(1, "1"): payload},
    )

    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert scan.failures == ()
    assert len(scan.messages) == 1
    assert "sanitized" in scan.messages[0].text_body
    assert scan.metrics.full_message_fallbacks == 0


def test_oversized_fallback_records_one_failure_and_continues_next_uid() -> None:
    maximum = 100
    factory = Factory(
        uids=(1, 2),
        headers={
            1: header(content_type='multipart/mixed; boundary="x"'),
            2: header(message_id="next@example.invalid"),
        },
        bodystructures={1: b"(BROKEN)"},
        full_messages={1: b"x" * (maximum + 1)},
        bodies={(2, "TEXT"): b"Schedule your interview"},
    )
    bounded_settings = YahooImapSettings(
        "person@yahoo.com",
        "app-secret",
        "Jobs",
        max_fallback_message_bytes=maximum,
    )

    scan = scan_with_reconnect(
        bounded_settings,
        folder="Jobs",
        since_date=SINCE_DATE,
        connection_factory=factory,
    )

    assert [failure.uid for failure in scan.failures] == [1]
    assert [message.uid for message in scan.messages] == [2]
    assert scan.metrics.full_message_fallbacks == 1
    assert scan.metrics.full_message_fallback_successes == 0
    assert scan.metrics.full_message_fallback_failures == 1
    assert scan.metrics.oversized_fallback_messages == 1
    assert scan.metrics.body_fetch_commands == 2


def test_malformed_message_is_ledgered_without_aborting_scan() -> None:
    factory = Factory(
        uids=(1, 2),
        headers={1: header()},
        bodies={(1, "TEXT"): b"Schedule your interview"},
    )
    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert len(scan.messages) == 1
    assert scan.failures[0].uid == 2
    assert scan.highest_contiguous_uid == 1


def test_missing_message_id_uses_stable_uid_identity() -> None:
    first = imap_message_identity(
        account_namespace="person@yahoo.com", folder="Jobs", uidvalidity="700", uid=8
    )
    repeated = imap_message_identity(
        account_namespace="person@yahoo.com", folder="Jobs", uidvalidity="700", uid=8
    )
    other_account = imap_message_identity(
        account_namespace="other@yahoo.com", folder="Jobs", uidvalidity="700", uid=8
    )

    assert first == repeated
    assert first != other_account


def test_uidvalidity_change_stops_before_uid_search() -> None:
    factory = Factory()
    with pytest.raises(RuntimeError, match="explicit rescan approval"):
        scan_with_reconnect(
            settings(),
            folder="Jobs",
            since_date=SINCE_DATE,
            expected_uidvalidity="699",
            connection_factory=factory,
        )

    assert not any(call[:2] == ("uid", "SEARCH") for call in factory.connections[0].calls)


def test_incremental_scan_searches_after_checkpoint_uid() -> None:
    factory = Factory(uids=(26, 27))
    scan_with_reconnect(
        settings(),
        folder="Jobs",
        since_date=SINCE_DATE,
        start_uid=26,
        limit=1,
        connection_factory=factory,
    )

    assert (
        "uid",
        "SEARCH",
        None,
        "SINCE",
        "01-Jul-2024",
        "UID",
        "26:*",
    ) in factory.connections[0].calls


def test_transient_connection_abort_reconnects_once() -> None:
    factories = [Factory(abort_login=True), Factory()]

    def reconnecting(
        host: str, port: int, *, ssl_context: ssl.SSLContext, timeout: float
    ) -> FakeImap:
        factory = factories.pop(0)
        return factory(host, port, ssl_context=ssl_context, timeout=timeout)

    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=reconnecting
    )

    assert len(scan.messages) == 1
    assert len(factories) == 0


def test_broken_pipe_reconnects_and_retries_the_same_uid() -> None:
    broken_fetches = {
        (
            1,
            "(INTERNALDATE BODY.PEEK[HEADER.FIELDS "
            "(MESSAGE-ID SUBJECT FROM TO CC DATE CONTENT-TYPE)])",
        )
    }
    factory = Factory(broken_fetches=broken_fetches)

    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert [message.uid for message in scan.messages] == [1]
    assert scan.reconnect_count == 1
    assert len(factory.connections) == 2
    header_attempts = [
        call
        for connection in factory.connections
        for call in connection.calls
        if call[:3] == ("uid", "FETCH", "1") and "HEADER.FIELDS" in str(call[3])
    ]
    assert len(header_attempts) == 2
    assert ("select", "Jobs", True) in factory.connections[1].calls
    assert ("response", "UIDVALIDITY") in factory.connections[1].calls


def test_bodystructure_timeout_reconnects_and_retries_same_uid() -> None:
    query = "(BODYSTRUCTURE)"
    factory = Factory(
        headers={1: header(content_type='multipart/mixed; boundary="x"')},
        bodystructures={1: alternative_structure()},
        bodies={(1, "1"): b"Schedule your interview"},
        timeout_fetches={(1, query): 1},
    )

    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert [message.uid for message in scan.messages] == [1]
    assert scan.failures == ()
    assert scan.reconnect_count == 1
    attempts = [
        call
        for connection in factory.connections
        for call in connection.calls
        if call[:4] == ("uid", "FETCH", "1", query)
    ]
    assert len(attempts) == 2


def test_timeout_exhaustion_records_one_failure_and_next_uid_uses_fresh_connection() -> None:
    query = "(BODYSTRUCTURE)"
    factory = Factory(
        uids=(1, 2),
        headers={
            1: header(content_type='multipart/mixed; boundary="x"'),
            2: header(message_id="second@example.invalid"),
        },
        bodystructures={1: alternative_structure()},
        bodies={(1, "1"): b"Schedule your interview", (2, "TEXT"): b"Schedule your interview"},
        timeout_fetches={(1, query): 2},
    )

    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert [failure.uid for failure in scan.failures] == [1]
    assert [message.uid for message in scan.messages] == [2]
    assert len(factory.connections) == 3
    assert ("select", "Jobs", True) in factory.connections[2].calls
    assert ("response", "UIDVALIDITY") in factory.connections[2].calls


def test_mime_part_cap_records_bounded_failure_without_unbounded_probing() -> None:
    factory = Factory(
        headers={1: header(content_type='multipart/mixed; boundary="x"')},
        bodystructures={1: many_part_structure(3)},
        full_messages={
            1: (ROOT / "tests" / "fixtures" / "yahoo_marked_section_en.eml").read_bytes()
        },
    )
    limited_settings = YahooImapSettings("person@yahoo.com", "app-secret", "Jobs", max_mime_parts=2)

    scan = scan_with_reconnect(
        limited_settings,
        folder="Jobs",
        since_date=SINCE_DATE,
        connection_factory=factory,
    )
    fetch_queries = [
        call[3]
        for connection in factory.connections
        for call in connection.calls
        if call[:3] == ("uid", "FETCH", "1")
    ]

    assert scan.failures == ()
    assert scan.metrics.bodystructure_parse_failures == 1
    assert scan.metrics.full_message_fallback_successes == 1
    assert fetch_queries[:2] == [
        "(INTERNALDATE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM TO CC DATE CONTENT-TYPE)])",
        "(BODYSTRUCTURE)",
    ]
    assert len(fetch_queries) == 3
    assert fetch_queries[-1].startswith("(BODY.PEEK[]<0.")


def test_progress_output_contains_only_safe_operational_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.sync_yahoo_imap import write_progress

    factory = Factory(uids=(1, 2, 3, 4, 5), default_messages=True)
    scan_with_reconnect(
        settings(),
        folder="Jobs",
        since_date=SINCE_DATE,
        progress_every=2,
        progress_callback=write_progress,
        connection_factory=factory,
    )
    lines = capsys.readouterr().err.splitlines()
    reports = [json.loads(line) for line in lines]

    assert [report["processed_count"] for report in reports] == [2, 4]
    assert set(reports[0]) == set(ScanProgress.__dataclass_fields__)
    assert "app-secret" not in "\n".join(lines)
    assert "person@yahoo.com" not in "\n".join(lines)


def test_count_only_fetches_no_headers_or_bodies_and_writes_nothing() -> None:
    from scripts.sync_yahoo_imap import count_only_report

    factory = Factory(uids=tuple(range(1, 101)), default_messages=True)
    scan = scan_with_reconnect(
        settings(),
        folder="Jobs",
        since_date=SINCE_DATE,
        count_only=True,
        connection_factory=factory,
    )
    report = count_only_report(scan)

    assert scan.total_matched_uid_count == 100
    assert scan.processed_count == 0
    assert not any(call[:2] == ("uid", "FETCH") for call in factory.connections[0].calls)
    assert report["database_writes"] == 0
    assert report["mailbox_mutations"] == 0


def test_paginated_uid_search_above_one_thousand_is_complete() -> None:
    factory = Factory(
        uids=tuple(range(1, 2_501)),
        search_page_size=1_000,
        default_messages=True,
    )

    scan = scan_with_reconnect(
        settings(),
        folder="Jobs",
        since_date=SINCE_DATE,
        count_only=True,
        connection_factory=factory,
    )

    assert scan.total_matched_uid_count == 2_500
    assert scan.first_uid == 1
    assert scan.last_uid == 2_500
    assert scan.search_page_count == 3
    assert scan.search_complete is True
    assert scan.metrics.imap_search_commands == 3


def test_repeated_search_page_reports_incomplete_without_looping() -> None:
    from scripts.sync_yahoo_imap import count_only_report

    factory = Factory(
        uids=tuple(range(1, 1_501)),
        search_page_size=1_000,
        repeat_search_page=True,
        default_messages=True,
    )
    scan = scan_with_reconnect(
        settings(),
        folder="Jobs",
        since_date=SINCE_DATE,
        count_only=True,
        connection_factory=factory,
    )
    report = count_only_report(scan)

    assert scan.total_matched_uid_count is None
    assert scan.partial_matched_uid_count == 1_000
    assert scan.search_page_count == 2
    assert report["search_complete"] is False
    assert report["total_matched_uid_count"] is None
    assert report["last_uid"] == 1_000


def test_limit_one_hundred_has_unambiguous_report_counters() -> None:
    from scripts.sync_yahoo_imap import dry_run_report

    factory = Factory(
        uids=tuple(range(1, 1_501)),
        search_page_size=1_000,
        default_messages=True,
    )
    scan = scan_with_reconnect(
        settings(),
        folder="Jobs",
        since_date=SINCE_DATE,
        limit=100,
        connection_factory=factory,
    )
    report = dry_run_report(scan)

    assert report["total_matched_uid_count"] == 1_500
    assert report["batch_selected_count"] == 100
    assert report["processed_count"] == 100
    assert report["completed_count"] == 100
    assert report["accepted_candidates"] == 100
    assert report["failure_count"] == 0


def test_after_uid_resume_starts_at_next_uid_without_previous_fetches() -> None:
    factory = Factory(uids=tuple(range(1, 201)), default_messages=True)

    scan = scan_with_reconnect(
        settings(),
        folder="Jobs",
        since_date=SINCE_DATE,
        start_uid=101,
        limit=10,
        connection_factory=factory,
    )

    assert scan.first_uid == 101
    assert scan.last_uid_attempted == 110
    fetched_uids = {
        int(call[2]) for call in factory.connections[0].calls if call[:2] == ("uid", "FETCH")
    }
    assert fetched_uids == set(range(101, 111))


def test_fetch_metrics_bound_simple_message_commands() -> None:
    factory = Factory(uids=tuple(range(1, 1_001)), default_messages=True)

    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert scan.metrics.header_fetch_commands == 1_000
    assert scan.metrics.bodystructure_fetch_commands == 0
    assert scan.metrics.body_fetch_commands == 1_000
    assert scan.metrics.total_fetch_commands == 2_000
    assert scan.metrics.average_fetch_commands_per_message == 2.0
    assert scan.metrics.messages_requiring_body == 1_000


def test_thousand_message_regression_recovers_four_bodystructure_failures() -> None:
    from scripts.sync_yahoo_imap import dry_run_report

    fallback_uids = (27, 67, 78, 87)
    fixture_names = (
        "yahoo_marked_section_en.eml",
        "yahoo_marked_section_e.eml",
        "yahoo_marked_section_en.eml",
        "yahoo_marked_section_e.eml",
    )
    headers = {
        uid: header(
            message_id=f"fallback-{uid}@example.invalid",
            content_type='multipart/mixed; boundary="x"',
        )
        for uid in fallback_uids
    }
    full_messages = {
        uid: (ROOT / "tests" / "fixtures" / name).read_bytes()
        for uid, name in zip(fallback_uids, fixture_names, strict=True)
    }
    factory = Factory(
        uids=tuple(range(1, 1_001)),
        headers=headers,
        bodystructures={uid: b"(BROKEN)" for uid in fallback_uids},
        full_messages=full_messages,
        default_messages=True,
        search_page_size=1_000,
    )

    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert scan.completed_count == 1_000
    assert scan.failures == ()
    assert scan.metrics.bodystructure_parse_failures == 4
    assert scan.metrics.full_message_fallbacks == 4
    assert scan.metrics.full_message_fallback_successes == 4
    assert scan.metrics.full_message_fallback_failures == 0
    assert scan.metrics.total_fetch_commands == 2_004
    assert scan.metrics.average_fetch_commands_per_message == 2.004
    report = dry_run_report(scan)
    assert report["database_writes"] == 0
    assert report["mailbox_mutations"] == 0


def test_count_only_cli_defaults_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.sync_yahoo_imap import parse_arguments

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sync_yahoo_imap.py",
            "--folder",
            "job",
            "--since-date",
            "2024-07-01",
            "--count-only",
        ],
    )

    arguments = parse_arguments()

    assert arguments.count_only is True
    assert arguments.connect_timeout == 30
    assert arguments.read_timeout == 60
    assert arguments.progress_every == 100
    assert arguments.max_mime_parts == 50
    assert arguments.max_fallback_message_bytes == 10_485_760


def test_after_uid_cli_is_converted_to_next_start_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.sync_yahoo_imap import parse_arguments

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sync_yahoo_imap.py",
            "--folder",
            "job",
            "--since-date",
            "2024-07-01",
            "--count-only",
            "--after-uid",
            "53284",
        ],
    )

    arguments = parse_arguments()

    assert arguments.after_uid == 53_284
    assert arguments.start_uid is None


def test_timeout_failure_redacts_credentials() -> None:
    query = "(BODYSTRUCTURE)"
    secret_settings = settings("private@yahoo.com", "top-secret")
    factory = Factory(
        headers={1: header(content_type='multipart/mixed; boundary="x"')},
        bodystructures={1: alternative_structure()},
        timeout_fetches={(1, query): 2},
        timeout_message="timeout for private@yahoo.com using top-secret",
    )

    scan = scan_with_reconnect(
        secret_settings,
        folder="Jobs",
        since_date=SINCE_DATE,
        connection_factory=factory,
    )

    assert len(scan.failures) == 1
    assert "private@yahoo.com" not in scan.failures[0].error
    assert "top-secret" not in scan.failures[0].error
    assert "[REDACTED]" in scan.failures[0].error


def test_mocked_large_folder_recovers_from_timeout_and_continues() -> None:
    timed_out_uid = 5_000
    query = "(BODYSTRUCTURE)"
    factory = Factory(
        uids=tuple(range(1, 10_001)),
        headers={timed_out_uid: header(content_type='multipart/mixed; boundary="x"')},
        bodystructures={timed_out_uid: alternative_structure()},
        timeout_fetches={(timed_out_uid, query): 2},
        default_messages=True,
        search_page_size=1_000,
    )
    progress: list[ScanProgress] = []

    scan = scan_with_reconnect(
        settings(),
        folder="Jobs",
        since_date=SINCE_DATE,
        progress_every=1_000,
        progress_callback=progress.append,
        connection_factory=factory,
    )

    assert scan.total_matched_uid_count == 10_000
    assert [failure.uid for failure in scan.failures] == [timed_out_uid]
    assert scan.last_uid_completed == 10_000
    assert scan.reconnect_count == 2
    assert scan.search_page_count == 11
    assert scan.search_complete is True
    assert progress[-1].processed_count == 10_000
    assert progress[-1].failure_count == 1


def test_checkpoint_incremental_state_and_uidvalidity_guard(
    isolated_app: tuple[Any, Path],
) -> None:
    _, database = isolated_app
    now = datetime(2027, 1, 2, 12)
    checkpoint = ImapCheckpoint(
        "yahoo", "person@yahoo.com", "Jobs", SINCE_DATE, "700", 25, now, now, 25, 20, 5, 0
    )
    write_checkpoint(database, checkpoint)
    stored = read_checkpoint(
        database,
        provider="yahoo",
        account_namespace="person@yahoo.com",
        folder="Jobs",
        since_date=SINCE_DATE,
    )

    assert stored is not None and stored.last_successful_uid == 25
    require_stable_uidvalidity(stored, "700")
    with pytest.raises(UidValidityChangedError):
        require_stable_uidvalidity(stored, "701")


def test_checkpoint_is_isolated_by_since_date(isolated_app: tuple[Any, Path]) -> None:
    _, database = isolated_app
    now = datetime(2027, 1, 2, 12)
    first = ImapCheckpoint(
        "yahoo", "person@yahoo.com", "Jobs", SINCE_DATE, "700", 25, now, now, 25, 20, 5, 0
    )
    later_date = date(2025, 1, 1)
    second = ImapCheckpoint(
        "yahoo", "person@yahoo.com", "Jobs", later_date, "700", 80, now, now, 10, 8, 2, 0
    )
    write_checkpoint(database, first)
    write_checkpoint(database, second)

    first_stored = read_checkpoint(
        database,
        provider="yahoo",
        account_namespace="person@yahoo.com",
        folder="Jobs",
        since_date=SINCE_DATE,
    )
    second_stored = read_checkpoint(
        database,
        provider="yahoo",
        account_namespace="person@yahoo.com",
        folder="Jobs",
        since_date=later_date,
    )

    assert first_stored is not None and first_stored.last_successful_uid == 25
    assert second_stored is not None and second_stored.last_successful_uid == 80


def yahoo_message(
    uid: int = 1, account: str = "person@yahoo.com", folder: str = "Jobs"
) -> YahooImapMessage:
    identity = imap_message_identity(
        account_namespace=account, folder=folder, uidvalidity="700", uid=uid
    )
    return YahooImapMessage(
        uid=uid,
        uidvalidity="700",
        folder=folder,
        account_namespace=account,
        message_id=f"message-{uid}@example.invalid",
        subject="Interview invitation",
        sender="Avery Recruiter <avery@acme.example>",
        recipients=(account,),
        received_at=datetime(2027, 1, 2, 12),
        imap_internal_date=datetime(2024, 7, 1, 19),
        requested_since_date=SINCE_DATE,
        text_body="Schedule your interview for Job ID: REQ-9000. Senior Recruiter at Acme.",
        html_fallback_used=False,
        attachments=(),
        identity=identity,
    )


def test_repeated_pipeline_sync_is_idempotent_and_account_isolated(
    isolated_app: tuple[Any, Path],
) -> None:
    _, database = isolated_app
    module = sys.modules["backend.main"]
    first = module.import_yahoo_imap_messages([yahoo_message()])
    second = module.import_yahoo_imap_messages([yahoo_message()])
    other = module.import_yahoo_imap_messages([yahoo_message(account="other@yahoo.com")])

    assert first["accepted_count"] == 1
    assert second["skipped_count"] == 1
    assert other["accepted_count"] == 1
    with sqlite3.connect(database) as connection:
        providers = connection.execute("SELECT DISTINCT provider FROM imported_messages").fetchall()
        accounts = connection.execute(
            "SELECT account_namespace FROM imap_message_metadata ORDER BY id"
        ).fetchall()
    assert providers == [("yahoo",)]
    assert accounts == [("person@yahoo.com",), ("other@yahoo.com",)]


def test_dry_run_report_has_no_database_writes() -> None:
    from scripts.sync_yahoo_imap import dry_run_report

    factory = Factory()
    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )
    report = dry_run_report(scan)

    assert report["database_writes"] == 0
    assert report["mailbox_mutations"] == 0


def test_temporary_database_sync_records_checkpoint(
    isolated_app: tuple[Any, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import sync_yahoo_imap

    _, database = isolated_app
    message = yahoo_message(uid=53290, folder="job")
    scan = YahooImapScan(
        folder="job",
        since_date=SINCE_DATE,
        uidvalidity="700",
        messages=(message,),
        failures=(),
        highest_contiguous_uid=53290,
        total_matched_uid_count=1,
        partial_matched_uid_count=1,
        batch_selected_count=1,
        processed_count=1,
        completed_count=1,
        first_uid=53290,
        last_uid=53290,
        last_uid_attempted=53290,
        last_uid_completed=53290,
        search_page_count=1,
        search_complete=True,
        reconnect_count=0,
        metrics=ImapMetrics(1, 1, 0, 1, 2, 1, 2.0, 1.0, 1.0, 0, 0, 0, 0, 0),
    )
    monkeypatch.setattr(sync_yahoo_imap, "scan_with_reconnect", lambda *args, **kwargs: scan)

    result = sync_yahoo_imap.synchronize(
        settings(),
        database,
        folder="job",
        since_date=SINCE_DATE,
        limit=100,
        verify_idempotency=True,
    )
    with sqlite3.connect(database) as connection:
        first_counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in (
                "email_imports",
                "imported_messages",
                "email_classifications",
                "recruiters",
                "recruiter_email_addresses",
                "interviews",
                "interview_events",
                "imap_message_metadata",
            )
        }
    repeated = sync_yahoo_imap.synchronize(
        settings(), database, folder="job", since_date=SINCE_DATE, limit=100
    )
    with sqlite3.connect(database) as connection:
        second_counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in first_counts
        }
    checkpoint = read_checkpoint(
        database,
        provider="yahoo",
        account_namespace="person@yahoo.com",
        folder="job",
        since_date=SINCE_DATE,
    )

    assert result["accepted_candidates"] == 1
    assert result["mailbox_mutations"] == 0
    assert (
        result["pre_sync_database"]["checksum_sha256"]
        != result["post_sync_database"]["checksum_sha256"]
    )
    assert result["table_deltas"]["imported_messages"] == 1
    assert result["idempotency_verification"]["passed"] is True
    assert result["idempotency_token"]
    assert result["immediate_second_pass"]["passed"] is True
    assert repeated["accepted_candidates"] == 0
    assert repeated["skipped_count"] == 1
    assert first_counts == second_counts
    assert checkpoint is not None and checkpoint.last_successful_uid == 53290


def test_cli_refuses_live_database_before_connection() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "YAHOO_IMAP_USERNAME": "person@yahoo.com",
            "YAHOO_IMAP_APP_PASSWORD": "not-a-real-secret",
            "YAHOO_IMAP_FOLDER": "Jobs",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/sync_yahoo_imap.py",
            "--sync",
            "--database",
            "data/jobs.db",
            "--since-date",
            "2024-07-01",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "backup metadata and dry-run evidence" in completed.stderr
    assert "not-a-real-secret" not in completed.stderr


def test_partial_failure_ledger_does_not_hide_successful_messages() -> None:
    factory = Factory(
        uids=(1, 2),
        headers={1: header()},
        bodies={(1, "TEXT"): b"Schedule your interview"},
    )
    scan = scan_with_reconnect(
        settings(), folder="Jobs", since_date=SINCE_DATE, connection_factory=factory
    )

    assert scan.processed_count == 2
    assert len(scan.messages) == 1
    assert len(scan.failures) == 1
