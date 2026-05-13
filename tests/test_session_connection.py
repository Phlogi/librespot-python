"""Unit tests for session socket read handling."""
from __future__ import annotations

import pytest

from librespot.session import _ConnectionHolder


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
