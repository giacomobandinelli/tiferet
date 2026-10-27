"""Tests for the HTTP fetch layer with a fake opener (decision 0013)."""

import http.client
import io
import json
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
import truststore

from tiferet.data import fetch
from tiferet.data.fetch import (
    ACCEPT,
    USER_AGENT,
    FetchError,
    HttpFetcher,
    MissingKeyError,
    RedirectLog,
    api_key,
    build_url,
    redact,
    tls_context,
)

URL = "https://api.example.org/series"
KEY = "secret-key-9876543210"


class FakeResponse:
    def __init__(self, body: bytes, declared: int | None = None) -> None:
        self._body = io.BytesIO(body)
        self.headers = Message()
        if declared is not None:
            self.headers["Content-Length"] = str(declared)

    def read(self, amt: int, /) -> bytes:
        return self._body.read(amt)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(f"{URL}?api_key={KEY}", code, "error", headers, None)


class FakeOpener:
    """Replays a script of responses and errors, recording each request."""

    def __init__(self, *script: bytes | FakeResponse | Exception) -> None:
        self.script = list(script)
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def open(self, request: urllib.request.Request, *, timeout: float) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step if isinstance(step, FakeResponse) else FakeResponse(step)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def fetcher(opener: FakeOpener, clock: Clock, **kwargs: Any) -> HttpFetcher:
    return HttpFetcher(opener=opener, clock=clock, sleep=clock.sleep, **kwargs)


def test_success_sends_user_agent_and_timeout_and_redacts_the_url() -> None:
    opener, clock = FakeOpener(b"payload"), Clock()
    result = fetcher(opener, clock, timeout=12.0).get(URL, {"api_key": KEY, "id": "X"})
    assert result.content == b"payload"
    assert result.url == f"{URL}?api_key=REDACTED&id=X"
    assert result.retrieved_at.tzname() == "UTC"
    assert opener.requests[0].get_header("User-agent") == USER_AGENT
    assert opener.requests[0].full_url == f"{URL}?api_key={KEY}&id=X"
    assert opener.timeouts == [12.0]
    assert KEY not in repr(result)


def test_transient_errors_are_retried_with_backoff() -> None:
    opener, clock = FakeOpener(http_error(503), urllib.error.URLError("reset"), b"ok"), Clock()
    assert fetcher(opener, clock, backoff=2.0).get(URL).content == b"ok"
    assert clock.sleeps == [1.0, 2.0]


def test_retry_after_is_honoured() -> None:
    opener, clock = FakeOpener(http_error(429, "7"), b"ok"), Clock()
    fetcher(opener, clock).get(URL)
    assert clock.sleeps == [7.0]


def test_gives_up_after_the_last_attempt_without_leaking_the_key() -> None:
    opener, clock = FakeOpener(*(http_error(500) for _ in range(3))), Clock()
    with pytest.raises(FetchError, match="HTTP 500") as raised:
        fetcher(opener, clock, attempts=3).get(URL, {"api_key": KEY})
    assert KEY not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert len(opener.requests) == 3


def test_client_errors_are_not_retried() -> None:
    opener, clock = FakeOpener(http_error(404), b"unused"), Clock()
    with pytest.raises(FetchError, match="HTTP 404"):
        fetcher(opener, clock).get(URL)
    assert len(opener.requests) == 1


def test_requests_to_one_host_are_spaced() -> None:
    opener, clock = FakeOpener(b"a", b"b", b"c"), Clock()
    client = fetcher(opener, clock, min_interval=1.5)
    client.get(URL)
    clock.now += 0.5
    client.get(URL)
    client.get("https://other.example.org/x")
    assert clock.sleeps == [1.0]


def test_oversized_response_is_refused() -> None:
    opener, clock = FakeOpener(b"x" * 11), Clock()
    with pytest.raises(FetchError, match="larger than 10 bytes"):
        fetcher(opener, clock, max_bytes=10).get(URL)


def test_real_opener_cannot_reach_the_network_in_tests() -> None:
    with pytest.raises(FetchError):
        HttpFetcher(attempts=1).get("https://example.org/")


def test_url_helpers() -> None:
    assert build_url(URL, {"a": "1"}) == f"{URL}?a=1"
    assert build_url(f"{URL}?a=1", {"b": "2"}) == f"{URL}?a=1&b=2"
    assert redact(f"{URL}?api_key={KEY}&x=1") == f"{URL}?api_key=REDACTED&x=1"


def test_api_key_is_read_by_name() -> None:
    assert api_key("FRED_API_KEY", {"FRED_API_KEY": KEY}) == KEY
    with pytest.raises(MissingKeyError, match="FRED_API_KEY is not set"):
        api_key("FRED_API_KEY", {})


def test_cut_short_bodies_and_read_errors_are_retried() -> None:
    script = (
        FakeResponse(b"part", declared=10),
        http.client.IncompleteRead(b"part"),
        ssl.SSLError("bad record"),
        FakeResponse(b"0123456789", declared=10),
    )
    opener, clock = FakeOpener(*script), Clock()
    assert fetcher(opener, clock, attempts=4).get(URL).content == b"0123456789"
    assert len(opener.requests) == 4


def test_cut_short_body_on_the_last_attempt_fails() -> None:
    opener, clock = FakeOpener(FakeResponse(b"part", declared=10)), Clock()
    with pytest.raises(FetchError, match="TruncatedError"):
        fetcher(opener, clock, attempts=1).get(URL)


def test_long_retry_after_fails_instead_of_waiting() -> None:
    opener, clock = FakeOpener(http_error(429, "86400"), b"unused"), Clock()
    with pytest.raises(FetchError, match="asks to wait 86400 s"):
        fetcher(opener, clock).get(URL)
    assert clock.sleeps == []


@pytest.mark.parametrize(
    "call",
    [
        lambda: socket.getaddrinfo("example.org", 443),
        lambda: socket.create_connection(("example.org", 443)),
        lambda: socket.socket().connect_ex(("127.0.0.1", 9)),
    ],
    ids=["dns", "tcp", "connect-ex"],
)
def test_the_test_process_has_no_network(call: Callable[[], object]) -> None:
    with pytest.raises(OSError, match="tests do not use the network"):
        call()


def http_error_with_body(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        f"{URL}?api_key={KEY}", code, "error", Message(), io.BytesIO(body)
    )


def test_error_names_fred_error_message_with_the_key_redacted() -> None:
    body = json.dumps(
        {"error_code": 400, "error_message": f"Bad Request.  Key {KEY} has no ALFRED vintages."}
    ).encode()
    opener, clock = FakeOpener(http_error_with_body(400, body)), Clock()
    with pytest.raises(FetchError) as raised:
        fetcher(opener, clock).get(URL, {"api_key": KEY})
    error = raised.value
    assert error.status == 400
    assert error.detail == "Bad Request. Key REDACTED has no ALFRED vintages."
    assert "HTTP 400" in str(error) and "has no ALFRED vintages" in str(error)
    assert "redirects not recorded; final status 400" in str(error)
    assert KEY not in str(error)


def test_plain_error_text_is_truncated_to_300_characters() -> None:
    opener, clock = FakeOpener(http_error_with_body(404, b"x" * 1000)), Clock()
    with pytest.raises(FetchError) as raised:
        fetcher(opener, clock).get(URL)
    assert raised.value.detail is not None and len(raised.value.detail) == 300


def test_connection_failure_names_its_reason() -> None:
    reason = urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    opener, clock = FakeOpener(reason), Clock()
    with pytest.raises(FetchError, match=r"URLError from .*CERTIFICATE_VERIFY_FAILED") as raised:
        fetcher(opener, clock, attempts=1).get(URL)
    assert "no final status" in str(raised.value)


def test_redirect_chain_and_final_status_are_reported() -> None:
    log = RedirectLog()

    class RedirectingOpener(FakeOpener):
        def open(self, request: urllib.request.Request, *, timeout: float) -> FakeResponse:
            log.chain.append((302, f"https://cdn.example.org/file.xls?api_key={KEY}"))
            return super().open(request, timeout=timeout)

    opener, clock = RedirectingOpener(http_error_with_body(403, b"Forbidden")), Clock()
    client = HttpFetcher(opener=opener, redirect_log=log, clock=clock, sleep=clock.sleep)
    with pytest.raises(FetchError) as raised:
        client.get(URL, {"api_key": KEY})
    message = str(raised.value)
    assert "redirects 302 https://cdn.example.org/file.xls?api_key=REDACTED" in message
    assert "final status 403" in message and "Forbidden" in message
    assert KEY not in message
    assert opener.requests[0].get_header("Accept") == ACCEPT


def test_redirect_log_records_each_hop() -> None:
    log = RedirectLog()
    request = urllib.request.Request("https://a.example.org/x")
    follow = log.redirect_request(
        request, io.BytesIO(b""), 301, "moved", http.client.HTTPMessage(), "https://b.example.org/y"
    )
    assert follow is not None and follow.full_url == "https://b.example.org/y"
    assert log.chain == [(301, "https://b.example.org/y")]


def test_redirects_through_a_supplied_opener_are_not_claimed_absent() -> None:
    opener, clock = FakeOpener(http_error_with_body(403, b"Forbidden")), Clock()
    with pytest.raises(FetchError, match="redirects not recorded; final status 403"):
        fetcher(opener, clock).get(URL)


def test_key_in_the_url_is_redacted_from_the_server_text() -> None:
    body = json.dumps({"error_message": f"Key {KEY} rejected"}).encode()
    opener, clock = FakeOpener(http_error_with_body(400, body)), Clock()
    with pytest.raises(FetchError) as raised:
        fetcher(opener, clock).get(f"{URL}?api_key={KEY}")
    assert KEY not in str(raised.value) and raised.value.detail == "Key REDACTED rejected"


def https_context(fetcher: HttpFetcher) -> object:
    """The SSL context of the fetcher's HTTPS handler."""
    handlers = [h for h in fetcher._opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]  # type: ignore[attr-defined]
    assert len(handlers) == 1
    return getattr(handlers[0], "_context", None)


def test_tls_context_verifies_against_the_operating_system_store() -> None:
    context = tls_context()
    assert isinstance(context, truststore.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_default_fetcher_uses_a_verifying_system_store_context() -> None:
    context = https_context(HttpFetcher())
    assert isinstance(context, truststore.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_default_fetcher_takes_its_context_from_tls_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(fetch, "tls_context", lambda: marker)
    assert https_context(HttpFetcher()) is marker


def test_verification_is_never_switched_off_in_the_package() -> None:
    forbidden = [
        "CERT_NONE",
        "_create_unverified_context",
        "check_hostname = False",
        "verify=False",
    ]
    root = Path(fetch.__file__).resolve().parents[1]
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert [word for word in forbidden if word in text] == [], path.name
