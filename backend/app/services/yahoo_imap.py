"""TLS-only, read-only Yahoo IMAP transport for the Jobs folder."""

from __future__ import annotations

import builtins
import hashlib
import html
import imaplib
import json
import os
import re
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from email import policy
from email.header import decode_header, make_header
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Protocol, cast
from urllib.parse import unquote_to_bytes

from .email_classification import EmailType, classify_email
from .import_identity import normalize_text

DEFAULT_HOST = "imap.mail.yahoo.com"
DEFAULT_PORT = 993
DEFAULT_CONNECT_TIMEOUT = 30.0
DEFAULT_READ_TIMEOUT = 60.0
DEFAULT_MAX_MIME_PARTS = 50
DEFAULT_MAX_FALLBACK_MESSAGE_BYTES = 10 * 1024 * 1024
DEFAULT_SEARCH_PAGE_SIZE = 1_000
PROVIDER = "yahoo"
BODY_REQUIRED = set(EmailType)
TRANSIENT_ERRORS = (
    TimeoutError,
    BrokenPipeError,
    ConnectionResetError,
    imaplib.IMAP4.abort,
    OSError,
)


class ImapConnection(Protocol):
    """Narrow IMAP interface used by the transport and fake test servers."""

    def login(self, user: str, password: str) -> tuple[str, builtins.list[bytes]]: ...

    def list(self) -> tuple[str, builtins.list[bytes] | None]: ...

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, builtins.list[bytes]]: ...

    def response(self, code: str) -> tuple[str, builtins.list[bytes] | None]: ...

    def uid(self, command: str, *args: Any) -> tuple[str, builtins.list[Any] | None]: ...

    def noop(self) -> tuple[str, builtins.list[bytes]]: ...

    def logout(self) -> tuple[str, builtins.list[bytes]]: ...


ConnectionFactory = Callable[..., ImapConnection]
DEFAULT_CONNECTION_FACTORY = cast(ConnectionFactory, imaplib.IMAP4_SSL)


@dataclass(frozen=True)
class YahooImapSettings:
    """Credential and endpoint settings loaded without persisting secrets."""

    username: str
    app_password: str = field(repr=False)
    folder: str = ""
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    max_mime_parts: int = DEFAULT_MAX_MIME_PARTS
    max_fallback_message_bytes: int = DEFAULT_MAX_FALLBACK_MESSAGE_BYTES

    @property
    def account_namespace(self) -> str:
        return normalize_text(self.username)

    @classmethod
    def from_environment(
        cls,
        *,
        folder: str | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        max_mime_parts: int = DEFAULT_MAX_MIME_PARTS,
        max_fallback_message_bytes: int = DEFAULT_MAX_FALLBACK_MESSAGE_BYTES,
    ) -> YahooImapSettings:
        username = os.environ.get("YAHOO_IMAP_USERNAME", "").strip()
        app_password = os.environ.get("YAHOO_IMAP_APP_PASSWORD", "")
        selected_folder = folder if folder is not None else os.environ.get("YAHOO_IMAP_FOLDER", "")
        if not username or not app_password:
            raise ValueError("Yahoo IMAP requires YAHOO_IMAP_USERNAME and YAHOO_IMAP_APP_PASSWORD")
        if port != DEFAULT_PORT:
            raise ValueError("Yahoo IMAP requires TLS on port 993; plaintext IMAP is refused")
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("Yahoo IMAP timeouts must be greater than zero")
        if max_mime_parts <= 0:
            raise ValueError("Yahoo IMAP max MIME parts must be greater than zero")
        if max_fallback_message_bytes <= 0:
            raise ValueError("Yahoo IMAP fallback message size must be greater than zero")
        return cls(
            username,
            app_password,
            selected_folder.strip(),
            host,
            port,
            connect_timeout,
            read_timeout,
            max_mime_parts,
            max_fallback_message_bytes,
        )


@dataclass(frozen=True)
class AttachmentMetadata:
    filename: str
    content_type: str
    disposition: str


@dataclass(frozen=True)
class YahooImapMessage:
    uid: int
    uidvalidity: str
    folder: str
    account_namespace: str
    message_id: str
    subject: str
    sender: str
    recipients: tuple[str, ...]
    received_at: datetime | None
    imap_internal_date: datetime | None
    requested_since_date: date
    text_body: str
    html_fallback_used: bool
    attachments: tuple[AttachmentMetadata, ...]
    identity: str


@dataclass(frozen=True)
class ScanFailure:
    uid: int
    error: str


@dataclass(frozen=True)
class YahooImapScan:
    folder: str
    since_date: date
    uidvalidity: str
    messages: tuple[YahooImapMessage, ...]
    failures: tuple[ScanFailure, ...]
    highest_contiguous_uid: int
    total_matched_uid_count: int | None
    partial_matched_uid_count: int
    batch_selected_count: int
    processed_count: int
    completed_count: int
    first_uid: int | None
    last_uid: int | None
    last_uid_attempted: int | None
    last_uid_completed: int | None
    search_page_count: int
    search_complete: bool
    reconnect_count: int
    metrics: ImapMetrics


@dataclass(frozen=True)
class ImapMetrics:
    imap_search_commands: int
    header_fetch_commands: int
    bodystructure_fetch_commands: int
    body_fetch_commands: int
    total_fetch_commands: int
    messages_requiring_body: int
    average_fetch_commands_per_message: float
    elapsed_seconds: float
    messages_per_second: float
    bodystructure_parse_failures: int
    full_message_fallbacks: int
    full_message_fallback_successes: int
    full_message_fallback_failures: int
    oversized_fallback_messages: int


@dataclass(frozen=True)
class UidSearchResult:
    uids: tuple[int, ...]
    page_count: int
    complete: bool


@dataclass(frozen=True)
class ScanProgress:
    processed_count: int
    total_matched_uid_count: int | None
    current_uid: int
    last_uid_completed: int | None
    elapsed_seconds: float
    reconnect_count: int
    failure_count: int


ProgressCallback = Callable[[ScanProgress], None]


class MimePartLimitError(RuntimeError):
    """Raised when local BODYSTRUCTURE parsing reaches the configured safety cap."""


class FallbackMessageTooLargeError(RuntimeError):
    """Raised when a bounded full-message fallback exceeds its configured maximum."""


@dataclass(frozen=True)
class MimePart:
    section: str
    content_type: str
    charset: str | None
    filename: str
    disposition: str

    @property
    def is_attachment(self) -> bool:
        return self.disposition == "attachment" or bool(self.filename)


class YahooImapClient:
    """Read-only Yahoo mailbox session using only non-mutating IMAP commands."""

    def __init__(
        self,
        settings: YahooImapSettings,
        *,
        connection_factory: ConnectionFactory = DEFAULT_CONNECTION_FACTORY,
    ) -> None:
        self.settings = settings
        self.connection_factory = connection_factory
        self.connection: ImapConnection | None = None
        self.reconnect_count = 0
        self.imap_search_commands = 0
        self.header_fetch_commands = 0
        self.bodystructure_fetch_commands = 0
        self.body_fetch_commands = 0
        self.messages_requiring_body = 0
        self.bodystructure_parse_failures = 0
        self.full_message_fallbacks = 0
        self.full_message_fallback_successes = 0
        self.full_message_fallback_failures = 0
        self.oversized_fallback_messages = 0

    def __enter__(self) -> YahooImapClient:
        try:
            self._connect()
        except (imaplib.IMAP4.abort, OSError):
            raise
        except Exception as exc:
            raise RuntimeError(redact_exception(exc, self.settings)) from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self.connection is None:
            return
        try:
            self.connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass

    def list_folders(self) -> tuple[str, ...]:
        connection = self._connection()
        status, response = connection.list()
        if status != "OK" or response is None:
            raise RuntimeError("Yahoo IMAP folder listing failed")
        return tuple(_folder_name(item) for item in response)

    def select_exact_folder(self, folder: str) -> str:
        if not folder:
            raise ValueError("An exact Yahoo IMAP folder is required")
        folders = self.list_folders()
        if folder not in folders:
            raise ValueError(f'Yahoo IMAP folder not found: "{folder}"')
        status, _ = self._connection().select(folder, readonly=True)
        if status != "OK":
            raise RuntimeError(f'Yahoo IMAP could not select folder: "{folder}"')
        status, response = self._connection().response("UIDVALIDITY")
        if status != "UIDVALIDITY" or not response:
            raise RuntimeError("Yahoo IMAP did not return UIDVALIDITY")
        return response[0].decode("ascii", errors="strict")

    def scan(
        self,
        *,
        folder: str,
        since_date: date,
        start_uid: int = 1,
        limit: int | None = None,
        expected_uidvalidity: str | None = None,
        count_only: bool = False,
        progress_every: int = 100,
        progress_callback: ProgressCallback | None = None,
    ) -> YahooImapScan:
        if progress_every <= 0:
            raise ValueError("Progress interval must be greater than zero")
        started_at = time.monotonic()
        uidvalidity = self.select_exact_folder(folder)
        if expected_uidvalidity and expected_uidvalidity != uidvalidity:
            raise RuntimeError(
                "Yahoo IMAP UIDVALIDITY changed; explicit rescan approval is required"
            )
        search = self._search_uids(start_uid, since_date)
        if count_only:
            return self._empty_scan(folder, since_date, uidvalidity, search, started_at)
        uids = search.uids[:limit] if limit is not None else search.uids
        messages: list[YahooImapMessage] = []
        failures: list[ScanFailure] = []
        contiguous = start_uid - 1
        failure_seen = False
        last_attempted: int | None = None
        last_completed: int | None = None
        for processed_count, uid in enumerate(uids, start=1):
            last_attempted = uid
            try:
                messages.append(
                    self._fetch_message_with_retry(uid, uidvalidity, folder, since_date)
                )
                last_completed = uid
                if not failure_seen:
                    contiguous = uid
            except Exception as exc:
                failure_seen = True
                failures.append(ScanFailure(uid, redact_exception(exc, self.settings)))
            if progress_callback and processed_count % progress_every == 0:
                progress_callback(
                    ScanProgress(
                        processed_count=processed_count,
                        total_matched_uid_count=(len(search.uids) if search.complete else None),
                        current_uid=uid,
                        last_uid_completed=last_completed,
                        elapsed_seconds=round(time.monotonic() - started_at, 3),
                        reconnect_count=self.reconnect_count,
                        failure_count=len(failures),
                    )
                )
        elapsed = time.monotonic() - started_at
        return YahooImapScan(
            folder=folder,
            since_date=since_date,
            uidvalidity=uidvalidity,
            messages=tuple(messages),
            failures=tuple(failures),
            highest_contiguous_uid=contiguous,
            total_matched_uid_count=len(search.uids) if search.complete else None,
            partial_matched_uid_count=len(search.uids),
            batch_selected_count=len(uids),
            processed_count=len(uids),
            completed_count=len(messages),
            first_uid=search.uids[0] if search.uids else None,
            last_uid=search.uids[-1] if search.uids else None,
            last_uid_attempted=last_attempted,
            last_uid_completed=last_completed,
            search_page_count=search.page_count,
            search_complete=search.complete,
            reconnect_count=self.reconnect_count,
            metrics=self._metrics(len(uids), elapsed),
        )

    def _empty_scan(
        self,
        folder: str,
        since_date: date,
        uidvalidity: str,
        search: UidSearchResult,
        started_at: float,
    ) -> YahooImapScan:
        elapsed = time.monotonic() - started_at
        return YahooImapScan(
            folder=folder,
            since_date=since_date,
            uidvalidity=uidvalidity,
            messages=(),
            failures=(),
            highest_contiguous_uid=0,
            total_matched_uid_count=len(search.uids) if search.complete else None,
            partial_matched_uid_count=len(search.uids),
            batch_selected_count=0,
            processed_count=0,
            completed_count=0,
            first_uid=search.uids[0] if search.uids else None,
            last_uid=search.uids[-1] if search.uids else None,
            last_uid_attempted=None,
            last_uid_completed=None,
            search_page_count=search.page_count,
            search_complete=search.complete,
            reconnect_count=self.reconnect_count,
            metrics=self._metrics(0, elapsed),
        )

    def _search_uids(self, start_uid: int, since_date: date) -> UidSearchResult:
        collected: list[int] = []
        cursor = start_uid
        page_count = 0
        while True:
            page = self._search_uid_page(cursor, since_date)
            page_count += 1
            if not page:
                return UidSearchResult(tuple(collected), page_count, True)
            if page != sorted(set(page)) or page[0] < cursor:
                return UidSearchResult(tuple(collected), page_count, False)
            if collected and page[0] <= collected[-1]:
                return UidSearchResult(tuple(collected), page_count, False)
            collected.extend(page)
            if len(page) < DEFAULT_SEARCH_PAGE_SIZE:
                return UidSearchResult(tuple(collected), page_count, True)
            next_cursor = page[-1] + 1
            if next_cursor <= cursor:
                return UidSearchResult(tuple(collected), page_count, False)
            cursor = next_cursor

    def _search_uid_page(self, start_uid: int, since_date: date) -> list[int]:
        self.imap_search_commands += 1
        status, response = self._connection().uid(
            "SEARCH",
            None,
            "SINCE",
            format_imap_since_date(since_date),
            "UID",
            f"{start_uid}:*",
        )
        if status != "OK" or not response:
            raise RuntimeError("Yahoo IMAP UID search failed")
        return [int(value) for value in bytes(response[0]).split()]

    def _metrics(self, processed_count: int, elapsed: float) -> ImapMetrics:
        total_fetches = (
            self.header_fetch_commands
            + self.bodystructure_fetch_commands
            + self.body_fetch_commands
        )
        return ImapMetrics(
            imap_search_commands=self.imap_search_commands,
            header_fetch_commands=self.header_fetch_commands,
            bodystructure_fetch_commands=self.bodystructure_fetch_commands,
            body_fetch_commands=self.body_fetch_commands,
            total_fetch_commands=total_fetches,
            messages_requiring_body=self.messages_requiring_body,
            average_fetch_commands_per_message=(
                round(total_fetches / processed_count, 3) if processed_count else 0.0
            ),
            elapsed_seconds=round(elapsed, 3),
            messages_per_second=(
                round(processed_count / elapsed, 3) if processed_count and elapsed else 0.0
            ),
            bodystructure_parse_failures=self.bodystructure_parse_failures,
            full_message_fallbacks=self.full_message_fallbacks,
            full_message_fallback_successes=self.full_message_fallback_successes,
            full_message_fallback_failures=self.full_message_fallback_failures,
            oversized_fallback_messages=self.oversized_fallback_messages,
        )

    def _fetch_message(
        self, uid: int, uidvalidity: str, folder: str, since_date: date
    ) -> YahooImapMessage:
        metadata, header_bytes = self._fetch_with_metadata(uid, _header_query(), "header")
        header = BytesParser(policy=cast(Any, policy.default)).parsebytes(
            header_bytes, headersonly=True
        )
        subject = _decoded_header(header.get("Subject"))
        sender = _decoded_header(header.get("From"))
        classification = classify_email(subject=subject, sender=sender, body="").classification
        body_required = classification in BODY_REQUIRED
        if body_required:
            self.messages_requiring_body += 1
        body, html_fallback, attachments = self._body_and_attachments(uid, header, body_required)
        identity = imap_message_identity(
            account_namespace=self.settings.account_namespace,
            folder=folder,
            uidvalidity=uidvalidity,
            uid=uid,
        )
        return YahooImapMessage(
            uid=uid,
            uidvalidity=uidvalidity,
            folder=folder,
            account_namespace=self.settings.account_namespace,
            message_id=_decoded_header(header.get("Message-ID")),
            subject=subject,
            sender=sender,
            recipients=_recipients(header),
            received_at=_received_at(header.get("Date")),
            imap_internal_date=_internal_date(metadata),
            requested_since_date=since_date,
            text_body=body,
            html_fallback_used=html_fallback,
            attachments=attachments,
            identity=identity,
        )

    def _fetch_message_with_retry(
        self, uid: int, uidvalidity: str, folder: str, since_date: date
    ) -> YahooImapMessage:
        for attempt in range(2):
            try:
                if self.connection is None:
                    self._reconnect(folder, uidvalidity)
                return self._fetch_message(uid, uidvalidity, folder, since_date)
            except TRANSIENT_ERRORS:
                self.connection = None
                if attempt:
                    raise
                self._reconnect(folder, uidvalidity)
        raise RuntimeError(f"Yahoo IMAP retry exhausted for UID {uid}")

    def _body_and_attachments(
        self, uid: int, header: Message, body_required: bool
    ) -> tuple[str, bool, tuple[AttachmentMetadata, ...]]:
        if header.get_content_maintype() != "multipart":
            if not body_required:
                return "", False, ()
            raw = self._fetch_body(uid, "TEXT")
            content_type = header.get_content_type()
            return _decode_body(raw, header), content_type == "text/html", ()
        try:
            parts = parse_bodystructure(
                self._fetch_bodystructure(uid), max_parts=self.settings.max_mime_parts
            )
        except (ValueError, MimePartLimitError):
            self.bodystructure_parse_failures += 1
            return self._full_message_fallback(uid, body_required)
        attachments = tuple(
            AttachmentMetadata(part.filename, part.content_type, part.disposition)
            for part in parts
            if part.is_attachment
        )
        if not body_required:
            return "", False, attachments
        plain = next(
            (
                part
                for part in parts
                if part.content_type == "text/plain" and not part.is_attachment
            ),
            None,
        )
        selected = plain or next(
            (part for part in parts if part.content_type == "text/html" and not part.is_attachment),
            None,
        )
        if selected is None:
            return "", False, attachments
        raw = self._fetch_body(uid, selected.section)
        return _decode_mime_part(raw, selected), plain is None, attachments

    def _full_message_fallback(
        self, uid: int, body_required: bool
    ) -> tuple[str, bool, tuple[AttachmentMetadata, ...]]:
        self.full_message_fallbacks += 1
        maximum = self.settings.max_fallback_message_bytes
        try:
            raw = self._fetch_full_message(uid, maximum + 1)
            if len(raw) > maximum:
                self.oversized_fallback_messages += 1
                raise FallbackMessageTooLargeError(
                    f"UID {uid} exceeds the {maximum}-byte full-message fallback limit"
                )
            result = _parse_full_message(raw, body_required=body_required)
        except Exception:
            self.full_message_fallback_failures += 1
            raise
        self.full_message_fallback_successes += 1
        return result

    def _fetch_bodystructure(self, uid: int) -> bytes:
        self.bodystructure_fetch_commands += 1
        status, response = self._connection().uid("FETCH", str(uid), "(BODYSTRUCTURE)")
        if status != "OK" or not response:
            raise RuntimeError(f"Yahoo IMAP BODYSTRUCTURE fetch failed for UID {uid}")
        raw = b" ".join(_response_chunks(response))
        if b"BODYSTRUCTURE" not in raw.upper():
            raise RuntimeError(f"Yahoo IMAP returned no BODYSTRUCTURE for UID {uid}")
        return raw

    def _fetch_body(self, uid: int, section: str) -> bytes:
        return self._fetch_with_metadata(uid, f"(BODY.PEEK[{section}])", "body")[1]

    def _fetch_full_message(self, uid: int, maximum_bytes: int) -> bytes:
        query = f"(BODY.PEEK[]<0.{maximum_bytes}>)"
        return self._fetch_with_metadata(uid, query, "body")[1]

    def _fetch_with_metadata(self, uid: int, query: str, command_kind: str) -> tuple[bytes, bytes]:
        if command_kind == "header":
            self.header_fetch_commands += 1
        else:
            self.body_fetch_commands += 1
        status, response = self._connection().uid("FETCH", str(uid), query)
        if status != "OK" or not response:
            raise RuntimeError(f"Yahoo IMAP fetch failed for UID {uid}")
        for item in response:
            if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
                metadata = item[0] if isinstance(item[0], bytes) else b""
                return metadata, item[1]
            if isinstance(item, bytes) and item:
                return b"", item
        raise RuntimeError(f"Yahoo IMAP returned no data for UID {uid}")

    def _connect(self) -> None:
        context = ssl.create_default_context()
        self.connection = self.connection_factory(
            self.settings.host,
            self.settings.port,
            ssl_context=context,
            timeout=self.settings.connect_timeout,
        )
        self._apply_read_timeout()
        status, _ = self.connection.login(self.settings.username, self.settings.app_password)
        if status != "OK":
            raise RuntimeError("Yahoo IMAP login failed")

    def _reconnect(self, folder: str, expected_uidvalidity: str) -> None:
        self.connection = None
        self._connect()
        current_uidvalidity = self.select_exact_folder(folder)
        if current_uidvalidity != expected_uidvalidity:
            self.connection = None
            raise RuntimeError(
                "Yahoo IMAP UIDVALIDITY changed during reconnect; explicit rescan approval required"
            )
        self.reconnect_count += 1

    def _apply_read_timeout(self) -> None:
        connection = self._connection()
        underlying_socket = getattr(connection, "sock", None)
        if underlying_socket is None or not hasattr(underlying_socket, "settimeout"):
            if self.connection_factory is DEFAULT_CONNECTION_FACTORY:
                raise RuntimeError("Yahoo IMAP SSL socket is unavailable for timeout configuration")
            return
        underlying_socket.settimeout(self.settings.read_timeout)

    def noop(self) -> None:
        status, _ = self._connection().noop()
        if status != "OK":
            raise RuntimeError("Yahoo IMAP NOOP failed")

    def _connection(self) -> ImapConnection:
        if self.connection is None:
            raise RuntimeError("Yahoo IMAP client is not connected")
        return self.connection


def scan_with_reconnect(
    settings: YahooImapSettings,
    *,
    folder: str,
    since_date: date,
    start_uid: int = 1,
    limit: int | None = None,
    expected_uidvalidity: str | None = None,
    count_only: bool = False,
    progress_every: int = 100,
    progress_callback: ProgressCallback | None = None,
    connection_factory: ConnectionFactory = DEFAULT_CONNECTION_FACTORY,
) -> YahooImapScan:
    """Retry one transient connection abort without changing mailbox state."""
    for attempt in range(2):
        try:
            with YahooImapClient(settings, connection_factory=connection_factory) as client:
                scan = client.scan(
                    folder=folder,
                    since_date=since_date,
                    start_uid=start_uid,
                    limit=limit,
                    expected_uidvalidity=expected_uidvalidity,
                    count_only=count_only,
                    progress_every=progress_every,
                    progress_callback=progress_callback,
                )
                return replace(scan, reconnect_count=scan.reconnect_count + attempt)
        except (imaplib.IMAP4.abort, OSError) as exc:
            if attempt:
                raise RuntimeError(redact_exception(exc, settings)) from exc
    raise RuntimeError("Yahoo IMAP scan failed")


def list_folders(
    settings: YahooImapSettings,
    *,
    connection_factory: ConnectionFactory = DEFAULT_CONNECTION_FACTORY,
) -> tuple[str, ...]:
    with YahooImapClient(settings, connection_factory=connection_factory) as client:
        return client.list_folders()


def imap_message_identity(
    *, account_namespace: str, folder: str, uidvalidity: str, uid: int
) -> str:
    """Create provider/account/folder/UIDVALIDITY/UID transport identity."""
    payload = {
        "account_namespace": normalize_text(account_namespace),
        "folder": normalize_text(folder),
        "kind": "imap-uid",
        "provider": PROVIDER,
        "uid": uid,
        "uidvalidity": uidvalidity,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"v1:{hashlib.sha256(encoded).hexdigest()}"


def redact_exception(exc: BaseException, settings: YahooImapSettings) -> str:
    """Remove credentials from diagnostic text."""
    message = str(exc)
    for secret in (settings.app_password, settings.username):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message or type(exc).__name__


def _header_query() -> str:
    fields = "MESSAGE-ID SUBJECT FROM TO CC DATE CONTENT-TYPE"
    return f"(INTERNALDATE BODY.PEEK[HEADER.FIELDS ({fields})])"


def format_imap_since_date(value: date) -> str:
    """Format an inclusive IMAP SEARCH SINCE date."""
    return value.strftime("%d-%b-%Y")


def _folder_name(value: bytes) -> str:
    decoded = value.decode("utf-8", errors="replace")
    match = re.search(r'(?:(?:"((?:[^"\\]|\\.)*)")|([^ ]+))$', decoded)
    if not match:
        raise ValueError(f"Malformed Yahoo IMAP LIST response: {decoded}")
    return (match.group(1) or match.group(2) or "").replace(r"\"", '"')


def _decoded_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        return value


def _recipients(message: Message) -> tuple[str, ...]:
    values = message.get_all("To", []) + message.get_all("Cc", [])
    return tuple(address.casefold() for _, address in getaddresses(values) if address)


def _received_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


def _internal_date(metadata: bytes) -> datetime | None:
    match = re.search(rb'INTERNALDATE "([^"]+)"', metadata, re.IGNORECASE)
    if not match:
        return None
    try:
        parsed = datetime.strptime(match.group(1).decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _response_chunks(response: builtins.list[Any]) -> list[bytes]:
    chunks: list[bytes] = []
    for item in response:
        if isinstance(item, bytes):
            chunks.append(item)
        elif isinstance(item, tuple):
            chunks.extend(value for value in item if isinstance(value, bytes))
    return chunks


def parse_bodystructure(raw: bytes, *, max_parts: int) -> tuple[MimePart, ...]:
    """Parse one IMAP BODYSTRUCTURE response into bounded local part descriptors."""
    marker = raw.upper().find(b"BODYSTRUCTURE")
    if marker < 0:
        raise ValueError("BODYSTRUCTURE marker is missing")
    tokens = _bodystructure_tokens(raw[marker + len(b"BODYSTRUCTURE") :])
    structure, _ = _parse_sexpression(tokens, 0)
    if not isinstance(structure, list):
        raise ValueError("BODYSTRUCTURE is malformed")
    parts: list[MimePart] = []
    _collect_mime_parts(structure, "", parts, max_parts)
    return tuple(parts)


def _bodystructure_tokens(raw: bytes) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(raw):
        character = raw[index]
        if chr(character).isspace():
            index += 1
        elif character in b"()":
            tokens.append(chr(character))
            index += 1
        elif character == ord('"'):
            value, index = _quoted_token(raw, index + 1)
            tokens.append(value)
        elif character == ord("{"):
            value, index = _literal_token(raw, index)
            tokens.append(value)
        else:
            end = index
            while end < len(raw) and not chr(raw[end]).isspace() and raw[end] not in b"()":
                end += 1
            tokens.append(raw[index:end].decode("utf-8", errors="replace"))
            index = end
    return tokens


def _quoted_token(raw: bytes, index: int) -> tuple[str, int]:
    value = bytearray()
    escaped = False
    while index < len(raw):
        character = raw[index]
        index += 1
        if escaped:
            value.append(character)
            escaped = False
        elif character == ord("\\"):
            escaped = True
        elif character == ord('"'):
            return value.decode("utf-8", errors="replace"), index
        else:
            value.append(character)
    raise ValueError("Unterminated BODYSTRUCTURE quoted string")


def _literal_token(raw: bytes, index: int) -> tuple[str, int]:
    end = raw.find(b"}", index + 1)
    if end < 0 or not raw[index + 1 : end].isdigit():
        raise ValueError("Malformed BODYSTRUCTURE literal length")
    length = int(raw[index + 1 : end])
    start = end + 1
    if raw[start : start + 2] == b"\r\n":
        start += 2
    elif raw[start : start + 1] == b"\n":
        start += 1
    else:
        raise ValueError("BODYSTRUCTURE literal is missing its line break")
    finish = start + length
    if finish > len(raw):
        raise ValueError("BODYSTRUCTURE literal is truncated")
    return raw[start:finish].decode("utf-8", errors="replace"), finish


def _parse_sexpression(tokens: list[str], index: int) -> tuple[Any, int]:
    while index < len(tokens) and tokens[index] != "(":
        index += 1
    if index >= len(tokens):
        raise ValueError("BODYSTRUCTURE expression is missing")
    values: list[Any] = []
    index += 1
    while index < len(tokens):
        token = tokens[index]
        if token == "(":
            nested, index = _parse_sexpression(tokens, index)
            values.append(nested)
        elif token == ")":
            return values, index + 1
        else:
            values.append(None if token.upper() == "NIL" else token)
            index += 1
    raise ValueError("Unterminated BODYSTRUCTURE expression")


def _collect_mime_parts(
    node: list[Any], prefix: str, parts: list[MimePart], max_parts: int
) -> None:
    if node and isinstance(node[0], list):
        child_index = 1
        for child in node:
            if not isinstance(child, list):
                break
            section = f"{prefix}.{child_index}" if prefix else str(child_index)
            _collect_mime_parts(child, section, parts, max_parts)
            child_index += 1
        return
    if len(parts) >= max_parts:
        raise MimePartLimitError(f"BODYSTRUCTURE exceeds the {max_parts}-part MIME safety cap")
    if len(node) < 2 or not _node_text(node, 0) or not _node_text(node, 1):
        raise ValueError("BODYSTRUCTURE leaf is missing its media type")
    content_type = f"{_node_text(node, 0)}/{_node_text(node, 1)}".casefold()
    parameters = _parameter_map(node[2] if len(node) > 2 else None)
    disposition, disposition_parameters = _disposition(node)
    filename = disposition_parameters.get("filename") or parameters.get("name", "")
    parts.append(
        MimePart(
            section=prefix or "TEXT",
            content_type=content_type,
            charset=parameters.get("charset"),
            filename=filename,
            disposition=disposition,
        )
    )


def _node_text(node: list[Any], index: int) -> str:
    return str(node[index]) if len(node) > index and node[index] is not None else ""


def _parameter_map(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    raw_parameters = {
        str(value[index]).casefold(): str(value[index + 1])
        for index in range(0, len(value) - 1, 2)
        if value[index] is not None and value[index + 1] is not None
    }
    parameters: dict[str, str] = {}
    continuations: dict[str, list[tuple[int, bool, str]]] = {}
    for name, parameter in raw_parameters.items():
        match = re.fullmatch(r"(.+?)\*(\d+)(\*)?", name)
        if match:
            continuations.setdefault(match.group(1), []).append(
                (int(match.group(2)), bool(match.group(3)), parameter)
            )
        elif name.endswith("*"):
            parameters[name[:-1]] = _decode_rfc2231_parameter(parameter)
        else:
            parameters[name] = _decoded_header(parameter)
    for name, sections in continuations.items():
        joined = "".join(section[2] for section in sorted(sections))
        encoded = any(section[1] for section in sections)
        parameters[name] = _decode_rfc2231_parameter(joined) if encoded else _decoded_header(joined)
    return parameters


def _decode_rfc2231_parameter(value: str) -> str:
    pieces = value.split("'", 2)
    charset = pieces[0] if len(pieces) == 3 and pieces[0] else "utf-8"
    encoded = pieces[2] if len(pieces) == 3 else value
    try:
        decoded = unquote_to_bytes(encoded).decode(charset, errors="replace")
    except LookupError:
        decoded = unquote_to_bytes(encoded).decode("utf-8", errors="replace")
    return _decoded_header(decoded)


def _disposition(node: list[Any]) -> tuple[str, dict[str, str]]:
    for value in node[3:]:
        if not isinstance(value, list) or not value:
            continue
        name = str(value[0]).casefold()
        if name in {"attachment", "inline"}:
            parameters = _parameter_map(value[1] if len(value) > 1 else None)
            return name, parameters
    return "", {}


def _decode_mime_part(raw: bytes, part: MimePart) -> str:
    try:
        text = raw.decode(part.charset or "utf-8", errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    if part.content_type == "text/html":
        text = _html_to_text(text)
    return normalize_text(text)


def _decode_body(raw: bytes, message: Message) -> str:
    charset = message.get_content_charset() or "utf-8"
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    if message.get_content_type() == "text/html":
        text = _html_to_text(text)
    return normalize_text(text)


def _parse_full_message(
    raw: bytes, *, body_required: bool
) -> tuple[str, bool, tuple[AttachmentMetadata, ...]]:
    message = BytesParser(policy=cast(Any, policy.default)).parsebytes(raw)
    attachments: list[AttachmentMetadata] = []
    plain: str | None = None
    html_text: str | None = None
    for part in message.walk():
        if part.is_multipart():
            continue
        if _is_attachment(part):
            attachments.append(_attachment(part))
            continue
        if not body_required:
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        decoded = _decoded_message_payload(part)
        if content_type == "text/plain" and plain is None:
            plain = normalize_text(decoded)
        elif content_type == "text/html" and html_text is None:
            html_text = normalize_text(_html_to_text(decoded))
    selected = plain if plain is not None else html_text or ""
    return selected, plain is None and html_text is not None, tuple(attachments)


def _decoded_message_payload(message: Message) -> str:
    payload = message.get_payload(decode=True)
    if payload is None:
        value = message.get_payload()
        return value if isinstance(value, str) else ""
    if not isinstance(payload, bytes):
        return str(payload)
    charset = message.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _html_to_text(text: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except AssertionError:
        parser = _TextExtractor()
        parser.feed(text.replace("<![", "&lt;!["))
        parser.close()
    return parser.text


def _is_attachment(message: Message) -> bool:
    return message.get_content_disposition() == "attachment" or bool(message.get_filename())


def _attachment(message: Message) -> AttachmentMetadata:
    return AttachmentMetadata(
        _decoded_header(message.get_filename()),
        message.get_content_type(),
        message.get_content_disposition() or "attachment",
    )


def _is_text(message: Message, subtype: str) -> bool:
    return not _is_attachment(message) and message.get_content_type() == f"text/{subtype}"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(html.unescape(data))

    @property
    def text(self) -> str:
        return " ".join(self.parts)
