"""HTTP fetch layer shared by the adapters (decision 0013).

`HttpFetcher` adds a timeout, bounded retries with exponential backoff, a minimum interval
between requests to one host and an identifying User-Agent. A body cut short, by a dropped
connection or against its Content-Length, is retried like any transient failure. A failed
download's error names the server's own error text (truncated), the reason of a connection
failure, the redirect chain and the final status. API keys are read from the environment by
name and never appear in a logged URL, an error message or the manifest: `Download.url` and
every error text are redacted.

HTTPS connections verify the server's certificate and host name against the operating
system's certificate store, through `truststore`, not against a bundled list. On a machine
where security software inspects HTTPS traffic and re-signs it with its own root, that root
is installed in the system store and the download works; with a bundled list it fails with
CERTIFICATE_VERIFY_FAILED. Verification is never switched off (decision 0013).
"""

import http.client
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from email.message import Message
from importlib.metadata import version
from types import TracebackType
from typing import IO, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
import truststore

USER_AGENT = f"tiferet/{version('tiferet')} (+https://github.com/giacomobandinelli/tiferet)"
SECRET_PARAMS = frozenset({"api_key"})
REDACTED = "REDACTED"
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_BYTES = 256 * 1024 * 1024
ACCEPT = "*/*"
"""Sent with every request; some CDNs refuse requests without an Accept header."""
MAX_ERROR_TEXT = 300
"""Characters of a server's error text kept in a `FetchError`."""
MAX_ERROR_BODY = 64 * 1024
MAX_RETRY_AFTER = 120.0
"""Longest server-requested wait honoured, in seconds; a longer one fails the download."""


class FetchError(RuntimeError):
    """A download failed after its retries. The message holds only redacted text.

    `status` is the final HTTP status, if any; `detail` is the server's error text or the
    reason of a connection failure, redacted and truncated.
    """

    def __init__(self, message: str, *, status: int | None = None, detail: str | None = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


class _TruncatedError(Exception):
    """The body is shorter than its Content-Length."""


class MissingKeyError(RuntimeError):
    """An API key named in the configuration is not set in the environment."""


@dataclass(frozen=True)
class Download:
    """One retrieved file: its redacted URL, its bytes and the UTC retrieval time."""

    url: str
    content: bytes = field(repr=False)
    retrieved_at: pd.Timestamp


class Fetcher(Protocol):
    def get(self, url: str, params: Mapping[str, str] | None = None) -> Download: ...


class Response(Protocol):
    headers: Message

    def read(self, amt: int, /) -> bytes: ...

    def __enter__(self) -> "Response": ...

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> None: ...


class Opener(Protocol):
    def open(self, request: urllib.request.Request, *, timeout: float) -> Response: ...


def tls_context() -> ssl.SSLContext:
    """A client context that verifies certificates and host names against the system store."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def build_url(url: str, params: Mapping[str, str] | None = None) -> str:
    """`url` with `params` appended to its query string."""
    if not params:
        return url
    parts = urlsplit(url)
    query = urlencode([*parse_qsl(parts.query), *params.items()])
    return urlunsplit(parts._replace(query=query))


def redact(url: str) -> str:
    """`url` with the value of every secret query parameter replaced."""
    parts = urlsplit(url)
    pairs = [(k, REDACTED if k in SECRET_PARAMS else v) for k, v in parse_qsl(parts.query)]
    return urlunsplit(parts._replace(query=urlencode(pairs)))


def api_key(name: str, env: Mapping[str, str] | None = None) -> str:
    """The API key held in the environment variable `name`."""
    value = (os.environ if env is None else env).get(name, "")
    if not value.strip():
        raise MissingKeyError(f"environment variable {name} is not set")
    return value


def redact_text(text: str, secrets: Iterable[str]) -> str:
    """`text` with every secret value replaced, collapsed to one line and truncated."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    line = " ".join(text.split())
    return line if len(line) <= MAX_ERROR_TEXT else line[: MAX_ERROR_TEXT - 3] + "..."


def server_text(error: urllib.error.HTTPError) -> str:
    """The error text of an HTTP error body: `error_message` of a JSON body, else the body."""
    try:
        body = error.read(MAX_ERROR_BODY) if error.fp is not None else b""
    except (OSError, http.client.HTTPException):
        body = b""
    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if isinstance(parsed, dict) and isinstance(parsed.get("error_message"), str):
        return str(parsed["error_message"])
    return text


class RedirectLog(urllib.request.HTTPRedirectHandler):
    """Follows redirects like urllib and records each one as (status, target URL)."""

    def __init__(self) -> None:
        self.chain: list[tuple[int, str]] = []

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        self.chain.append((code, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    def describe(self) -> str:
        if not self.chain:
            return "no redirects"
        return "redirects " + " -> ".join(f"{code} {redact(url)}" for code, url in self.chain)


def _retry_after(headers: Message | None) -> float | None:
    raw = headers.get("Retry-After") if headers is not None else None
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


class HttpFetcher:
    """Fetches URLs politely, retrying transient failures."""

    def __init__(
        self,
        *,
        timeout: float = 60.0,
        attempts: int = 4,
        backoff: float = 2.0,
        min_interval: float = 1.0,
        max_bytes: int = MAX_BYTES,
        opener: Opener | None = None,
        redirect_log: RedirectLog | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self._timeout, self._attempts, self._backoff = timeout, attempts, backoff
        self._min_interval, self._max_bytes = min_interval, max_bytes
        if opener is None:
            self._redirects: RedirectLog | None = redirect_log or RedirectLog()
            self._opener: Opener = urllib.request.build_opener(
                self._redirects, urllib.request.HTTPSHandler(context=tls_context())
            )
        else:
            self._redirects, self._opener = redirect_log, opener
        self._clock, self._sleep = clock, sleep
        self._last_request: dict[str, float] = {}

    def get(self, url: str, params: Mapping[str, str] | None = None) -> Download:
        full = build_url(url, params)
        shown = redact(full)
        secrets = [v for k, v in parse_qsl(urlsplit(full).query) if k in SECRET_PARAMS]
        for attempt in range(1, self._attempts + 1):
            self._wait_for_host(urlsplit(full).netloc)
            if self._redirects is not None:
                self._redirects.chain.clear()
            try:
                content = self._read(full)
            except urllib.error.HTTPError as error:
                delay = _retry_after(error.headers)
                last = error.code not in RETRY_STATUS or attempt == self._attempts
                if last or (delay is not None and delay > MAX_RETRY_AFTER):
                    detail = redact_text(server_text(error), secrets)
                    wait = f"; the server asks to wait {delay:.0f} s" if not last else ""
                    raise FetchError(
                        f"HTTP {error.code} from {shown}{wait}: {detail or 'no error text'}"
                        f" ({self._describe_redirects()}; final status {error.code})",
                        status=error.code,
                        detail=detail,
                    ) from None
            except (OSError, http.client.HTTPException, _TruncatedError) as error:
                if attempt == self._attempts:
                    reason = error.reason if isinstance(error, urllib.error.URLError) else error
                    detail = redact_text(str(reason), secrets)
                    raise FetchError(
                        f"{type(error).__name__} from {shown}: {detail or 'no reason given'}"
                        f" ({self._describe_redirects()}; no final status)",
                        detail=detail,
                    ) from None
                delay = None
            else:
                return Download(shown, content, pd.Timestamp.now(tz="UTC"))
            self._sleep(delay if delay is not None else self._backoff ** (attempt - 1))
        raise AssertionError("unreachable")  # pragma: no cover

    def _describe_redirects(self) -> str:
        if self._redirects is None:
            return "redirects not recorded"
        return self._redirects.describe()

    def _wait_for_host(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            remaining = self._min_interval - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[host] = self._clock()

    def _read(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT})
        with self._opener.open(request, timeout=self._timeout) as response:
            content = response.read(self._max_bytes + 1)
            declared = response.headers.get("Content-Length")
        if len(content) > self._max_bytes:
            raise FetchError(f"response larger than {self._max_bytes} bytes from {redact(url)}")
        if declared is not None and declared.isdigit() and int(declared) != len(content):
            raise _TruncatedError
        return content
