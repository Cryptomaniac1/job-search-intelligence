"""OAuth2 settings for Gmail and Hotmail read-only IMAP synchronization."""

from __future__ import annotations

import json
import os
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi

from .import_identity import normalize_text
from .yahoo_imap import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_FALLBACK_MESSAGE_BYTES,
    DEFAULT_MAX_MIME_PARTS,
    DEFAULT_READ_TIMEOUT,
    ImapConnection,
)

TokenPost = Callable[[str, bytes, float], bytes]


@dataclass(frozen=True)
class OAuthProviderConfig:
    provider: str
    environment_prefix: str
    host: str
    token_url: str
    scope: str
    default_folder: str = "INBOX"
    port: int = 993


PROVIDERS = {
    "gmail": OAuthProviderConfig(
        provider="gmail",
        environment_prefix="GMAIL",
        host="imap.gmail.com",
        token_url="https://oauth2.googleapis.com/token",
        scope="https://mail.google.com/",
    ),
    "hotmail": OAuthProviderConfig(
        provider="hotmail",
        environment_prefix="HOTMAIL",
        host="outlook.office365.com",
        token_url="https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        scope="https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
    ),
}


def _post_token(url: str, payload: bytes, timeout: float) -> bytes:
    request = Request(
        url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(  # noqa: S310 - URL is validated by the caller and TLS uses the pinned CA bundle
        request,
        timeout=timeout,
        context=context,
    ) as response:
        return response.read()


@dataclass(frozen=True)
class OAuthTokenConfig:
    provider: str
    token_url: str
    scope: str
    client_id: str
    refresh_token: str = field(repr=False)
    client_secret: str = field(default="", repr=False)
    access_token: str = field(default="", repr=False)

    @property
    def redaction_values(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (self.access_token, self.refresh_token, self.client_secret, self.client_id)
            if value
        )

    def resolve_access_token(self, *, timeout: float, token_post: TokenPost = _post_token) -> str:
        """Return an injected token or exchange a refresh token over verified HTTPS."""
        if self.access_token:
            return self.access_token
        if not self.token_url.startswith("https://"):
            raise ValueError("OAuth token exchange requires HTTPS")
        fields = {
            "client_id": self.client_id,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }
        if self.client_secret:
            fields["client_secret"] = self.client_secret
        if self.provider == "hotmail":
            fields["scope"] = self.scope
        raw = token_post(self.token_url, urlencode(fields).encode(), timeout)
        try:
            payload: Any = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("OAuth token endpoint returned invalid JSON") from exc
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise RuntimeError("OAuth token endpoint returned no access token")
        return token


@dataclass(frozen=True)
class OAuthImapSettings:
    """Provider-neutral OAuth settings compatible with the bounded IMAP transport."""

    provider: str
    username: str
    token: OAuthTokenConfig = field(repr=False)
    folder: str
    host: str
    port: int = 993
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    max_mime_parts: int = DEFAULT_MAX_MIME_PARTS
    max_fallback_message_bytes: int = DEFAULT_MAX_FALLBACK_MESSAGE_BYTES
    token_post: TokenPost = field(default=_post_token, repr=False, compare=False)

    @property
    def account_namespace(self) -> str:
        return normalize_text(self.username)

    @property
    def redaction_values(self) -> tuple[str, ...]:
        return (self.username, *self.token.redaction_values)

    def authenticate(self, connection: ImapConnection) -> None:
        access_token = self.token.resolve_access_token(
            timeout=self.connect_timeout,
            token_post=self.token_post,
        )
        response = f"user={self.username}\x01auth=Bearer {access_token}\x01\x01".encode()
        status, _ = connection.authenticate("XOAUTH2", lambda _: response)
        if status != "OK":
            raise RuntimeError(f"{self.provider} IMAP OAuth authentication failed")

    @classmethod
    def from_environment(
        cls,
        provider: str,
        *,
        folder: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        max_mime_parts: int = DEFAULT_MAX_MIME_PARTS,
        max_fallback_message_bytes: int = DEFAULT_MAX_FALLBACK_MESSAGE_BYTES,
        token_post: TokenPost = _post_token,
    ) -> OAuthImapSettings:
        config = PROVIDERS.get(provider)
        if config is None:
            raise ValueError("OAuth IMAP provider must be gmail or hotmail")
        prefix = config.environment_prefix
        username = os.environ.get(f"{prefix}_IMAP_USERNAME", "").strip()
        access_token = os.environ.get(f"{prefix}_OAUTH_ACCESS_TOKEN", "")
        client_id = os.environ.get(f"{prefix}_OAUTH_CLIENT_ID", "").strip()
        client_secret = os.environ.get(f"{prefix}_OAUTH_CLIENT_SECRET", "")
        refresh_token = os.environ.get(f"{prefix}_OAUTH_REFRESH_TOKEN", "")
        selected_folder = (
            folder
            if folder is not None
            else os.environ.get(f"{prefix}_IMAP_FOLDER", config.default_folder)
        )
        if not username:
            raise ValueError(f"{provider} IMAP requires {prefix}_IMAP_USERNAME")
        if not access_token and (not client_id or not refresh_token):
            raise ValueError(
                f"{provider} OAuth requires {prefix}_OAUTH_ACCESS_TOKEN or both "
                f"{prefix}_OAUTH_CLIENT_ID and {prefix}_OAUTH_REFRESH_TOKEN"
            )
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("OAuth IMAP timeouts must be greater than zero")
        if max_mime_parts <= 0 or max_fallback_message_bytes <= 0:
            raise ValueError("OAuth IMAP safety limits must be greater than zero")
        return cls(
            provider=provider,
            username=username,
            token=OAuthTokenConfig(
                provider=provider,
                token_url=config.token_url,
                scope=config.scope,
                client_id=client_id,
                refresh_token=refresh_token,
                client_secret=client_secret,
                access_token=access_token,
            ),
            folder=selected_folder.strip(),
            host=config.host,
            port=config.port,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_mime_parts=max_mime_parts,
            max_fallback_message_bytes=max_fallback_message_bytes,
            token_post=token_post,
        )
