"""Unit tests for session socket read handling."""
from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

import pytest

from librespot.apresolver import ApResolver
from librespot.session import Session, _ConnectionHolder


class _FakeSocket:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def recv(self, size: int) -> bytes:
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        assert len(chunk) <= size
        return chunk

    def close(self) -> None:
        pass

    def settimeout(self, seconds) -> None:
        pass


class _FakeConnection:
    def __init__(self, address: str):
        self._address = address
        self.closed = False
        self.close_hook = None

    def address(self) -> str:
        return self._address

    def close(self) -> None:
        if self.close_hook is not None:
            self.close_hook()
        self.closed = True


def _bare_reconnectable_session() -> Session:
    session = Session.__new__(Session)
    session.scheduled_reconnect = None
    session.connection = _FakeConnection("old-ap.example:4070")
    session.cipher_pair = object()
    session.logger = logging.getLogger("tests.session")
    session._Session__reconnect_lock = threading.Lock()
    session._Session__auth_lock = threading.Condition()
    session._Session__auth_lock_bool = False
    session._Session__receiver = None
    session._Session__closing = False
    session._Session__closed = False
    session._Session__inner = SimpleNamespace(conf=object(), device_id="device")
    session._Session__ap_welcome = SimpleNamespace(
        reusable_auth_credentials_type=1,
        canonical_username="user",
        reusable_auth_credentials=b"credentials",
    )
    return session


def test_read_int_assembles_split_tcp_chunks():
    connection = _ConnectionHolder(
        _FakeSocket([b"\x00", b"\x00\x00", b"\x05"]),
        "ap.example:4070",
    )

    assert connection.read_int() == 5


def test_read_int_raises_connection_error_on_early_eof():
    connection = _ConnectionHolder(
        _FakeSocket([b"\x00"]),
        "ap.example:4070",
    )

    with pytest.raises(ConnectionError, match="expected 4 bytes, got 1"):
        connection.read_int()


def test_reconnect_rotates_access_points_without_replacement(monkeypatch):
    session = _bare_reconnectable_session()
    attempted: list[str] = []

    monkeypatch.setenv("LIBRESPOT_RETRY_ATTEMPTS", "3")
    monkeypatch.setattr(
        ApResolver,
        "get_accesspoint_pool",
        staticmethod(
            lambda: [
                "ap1.example:4070",
                "ap2.example:4070",
                "ap3.example:4070",
            ]
        ),
    )

    def create_connection(address, conf):
        attempted.append(address)
        if len(attempted) < 3:
            raise ConnectionRefusedError(address)
        return _FakeConnection(address)

    def connect(self):
        with self._Session__auth_lock:
            self.cipher_pair = object()
            self._Session__auth_lock_bool = True

    def authenticate_partial(self, credential, remove_lock):
        with self._Session__auth_lock:
            self._Session__auth_lock_bool = False
            self._Session__auth_lock.notify_all()

    monkeypatch.setattr(_ConnectionHolder, "create", staticmethod(create_connection))
    monkeypatch.setattr(Session, "connect", connect)
    monkeypatch.setattr(Session, "_Session__authenticate_partial", authenticate_partial)

    session.reconnect()

    assert attempted == [
        "ap1.example:4070",
        "ap2.example:4070",
        "ap3.example:4070",
    ]
    assert session.connection.address() == "ap3.example:4070"


def test_reconnect_blocks_sends_before_closing_old_connection(monkeypatch):
    session = _bare_reconnectable_session()
    old_connection = session.connection

    def assert_reconnect_marked_unavailable():
        assert session.cipher_pair is None
        assert session._Session__auth_lock_bool is True

    old_connection.close_hook = assert_reconnect_marked_unavailable

    monkeypatch.setenv("LIBRESPOT_RETRY_ATTEMPTS", "1")
    monkeypatch.setattr(
        ApResolver,
        "get_accesspoint_pool",
        staticmethod(lambda: ["ap1.example:4070"]),
    )

    def fail_create(address, conf):
        raise ConnectionRefusedError(address)

    monkeypatch.setattr(_ConnectionHolder, "create", staticmethod(fail_create))

    with pytest.raises(ConnectionRefusedError):
        session.reconnect()

    assert old_connection.closed is True
    assert session.connection is None
    assert session._Session__auth_lock_bool is False
    with pytest.raises(RuntimeError, match="Session is disconnected"):
        session.send(b"\x04", b"")
