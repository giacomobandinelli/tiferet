"""Shared pytest configuration.

From configuration to the end of the session, the test process cannot open a network
connection, resolve a host name or send a datagram: adapters are tested on fixtures only
(CLAUDE.md invariant 7, decision 0013). Subprocesses started by tests are not covered.
"""

import socket
from typing import Any, NoReturn

import pytest

GUARDED: tuple[tuple[Any, str], ...] = (
    (socket, "create_connection"),
    (socket, "getaddrinfo"),
    (socket.socket, "connect"),
    (socket.socket, "connect_ex"),
    (socket.socket, "sendto"),
)
_saved: dict[tuple[int, str], object] = {}


def _refuse(*args: object, **kwargs: object) -> NoReturn:
    raise OSError("tests do not use the network")


def pytest_configure(config: pytest.Config) -> None:
    for owner, name in GUARDED:
        _saved[(id(owner), name)] = getattr(owner, name)
        setattr(owner, name, _refuse)


def pytest_unconfigure(config: pytest.Config) -> None:
    for owner, name in GUARDED:
        setattr(owner, name, _saved.pop((id(owner), name)))
