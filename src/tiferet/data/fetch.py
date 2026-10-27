"""HTTP fetch layer shared by the adapters (decision 0013).

`HttpFetcher` adds a timeout, bounded retries with exponential backoff, a minimum interval
between requests to one host and an identifying User-Agent. A body cut short, by a dropped
connection or against its Content-Length, is retried like any transient failure. API keys
are read from the environment by name and never appear in a logged URL, an error message or
the manifest: `Download.url` is always redacted.
"""

import http.client
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import Message
from importlib.metadata import version
from types import TracebackType
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd

USER_AGENT = f"tiferet/{version('tiferet')} (+https://github.com/giacomobandinelli/tiferet)"
SECRET_PARAMS = frozenset({"api_key"})
REDACTED = "REDACTED"
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_BYTES = 256 * 1024 * 1024
MAX_RETRY_AFTER = 120.0
"""Longest server-requested wait honoured, in seconds; a longer one fails the download."""


class FetchError(RuntimeError):
    """A download failed after its retries. The message holds only the redacted URL."""


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
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self._timeout, self._attempts, self._backoff = timeout, attempts, backoff
        self._min_interval, self._max_bytes = min_interval, max_bytes
        self._opener: Opener = opener if opener is not None else urllib.request.build_opener()
        self._clock, self._sleep = clock, sleep
        self._last_request: dict[str, float] = {}

    def get(self, url: str, params: Mapping[str, str] | None = None) -> Download:
        full = build_url(url, params)
        shown = redact(full)
        for attempt in range(1, self._attempts + 1):
            self._wait_for_host(urlsplit(full).netloc)
            try:
                content = self._read(full)
            except urllib.error.HTTPError as error:
                if error.code not in RETRY_STATUS or attempt == self._attempts:
                    raise FetchError(f"HTTP {error.code} from {shown}") from None
                delay = _retry_after(error.headers)
                if delay is not None and delay > MAX_RETRY_AFTER:
                    raise FetchError(
                        f"HTTP {error.code} from {shown}; the server asks to wait {delay:.0f} s"
                    ) from None
            except (OSError, http.client.HTTPException, _TruncatedError) as error:
                if attempt == self._attempts:
                    raise FetchError(f"{type(error).__name__} from {shown}") from None
                delay = None
            else:
                return Download(shown, content, pd.Timestamp.now(tz="UTC"))
            self._sleep(delay if delay is not None else self._backoff ** (attempt - 1))
        raise AssertionError("unreachable")  # pragma: no cover

    def _wait_for_host(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            remaining = self._min_interval - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[host] = self._clock()

    def _read(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with self._opener.open(request, timeout=self._timeout) as response:
            content = response.read(self._max_bytes + 1)
            declared = response.headers.get("Content-Length")
        if len(content) > self._max_bytes:
            raise FetchError(f"response larger than {self._max_bytes} bytes from {redact(url)}")
        if declared is not None and declared.isdigit() and int(declared) != len(content):
            raise _TruncatedError
        return content
