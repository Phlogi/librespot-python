"""Unit tests for DealerClient listener management and dispatch routing."""
from __future__ import annotations

import threading
import typing
from unittest.mock import MagicMock, patch

import pytest
from requests.structures import CaseInsensitiveDict

from librespot.dealer_client import DealerClient
from librespot.structure import MessageListener, RequestListener


class _StubSession:
    """Minimal session stub for DealerClient tests."""

    def tokens(self):
        tok = MagicMock()
        tok.get.return_value = "fake-token"
        return tok


class _TestMessageListener(MessageListener):
    """Collects messages for assertions."""

    def __init__(self):
        self.messages: list[tuple[str, CaseInsensitiveDict, typing.Any]] = []

    def on_message(self, uri: str, headers: CaseInsensitiveDict[str],
                   payload: bytes):
        self.messages.append((uri, headers, payload))


class _TestRequestListener(RequestListener):
    """Collects requests for assertions."""

    def __init__(self, result=DealerClient.RequestResult.SUCCESS):
        self.requests: list[tuple] = []
        self._result = result

    def on_request(self, mid: str, pid: int, sender: str, command: dict):
        self.requests.append((mid, pid, sender, command))
        return self._result


@pytest.fixture
def dealer():
    session = _StubSession()
    dc = DealerClient(session)
    yield dc
    # Don't call close() — worker may already be shut down in some tests


class TestListenerRegistration:
    def test_add_and_remove_message_listener(self, dealer: DealerClient):
        listener = _TestMessageListener()
        dealer.add_message_listener(listener, ["hm://foo"])

        # Adding the same listener again should raise
        with pytest.raises(TypeError):
            dealer.add_message_listener(listener, ["hm://bar"])

        dealer.remove_message_listener(listener)
        # After removal, re-adding should work
        dealer.add_message_listener(listener, ["hm://baz"])

    def test_add_and_remove_request_listener(self, dealer: DealerClient):
        listener = _TestRequestListener()
        dealer.add_request_listener(listener, "hm://connect")

        # Adding same URI again should raise
        with pytest.raises(TypeError):
            dealer.add_request_listener(listener, "hm://connect")

        dealer.remove_request_listener(listener)
        # After removal, re-adding should work
        dealer.add_request_listener(listener, "hm://connect")

    def test_wait_for_listener_unblocks(self, dealer: DealerClient):
        """wait_for_listener should block until a listener is added."""
        result = threading.Event()

        def waiter():
            dealer.wait_for_listener()
            result.set()

        t = threading.Thread(target=waiter, daemon=True)
        t.start()

        # Should not be set yet
        assert not result.wait(timeout=0.1)

        # Add a listener — should unblock
        dealer.add_message_listener(_TestMessageListener(), ["hm://x"])
        assert result.wait(timeout=1.0)


class TestMessageDispatch:
    def test_dispatch_to_matching_listener(self, dealer: DealerClient):
        listener = _TestMessageListener()
        dealer.add_message_listener(listener, ["hm://connect-state/"])

        msg = {
            "uri": "hm://connect-state/v1/connect/update",
            "headers": {"Content-Type": "application/json"},
            "payloads": '{"test": true}',
        }
        dealer.handle_message(msg)
        # Worker dispatches asynchronously — give it time
        dealer._DealerClient__worker.shutdown(wait=True)

        assert len(listener.messages) == 1
        uri, headers, payload = listener.messages[0]
        assert uri == "hm://connect-state/v1/connect/update"

    def test_no_dispatch_for_non_matching_uri(self, dealer: DealerClient):
        listener = _TestMessageListener()
        dealer.add_message_listener(listener, ["hm://connect-state/"])

        msg = {
            "uri": "hm://other/something",
            "headers": {},
            "payloads": "data",
        }
        dealer.handle_message(msg)
        dealer._DealerClient__worker.shutdown(wait=True)

        assert len(listener.messages) == 0

    def test_missing_uri_is_ignored(self, dealer: DealerClient):
        listener = _TestMessageListener()
        dealer.add_message_listener(listener, ["hm://test/"])

        # Message with no uri key
        dealer.handle_message({"headers": {}})
        dealer._DealerClient__worker.shutdown(wait=True)

        assert len(listener.messages) == 0


class TestRequestDispatch:
    def test_dispatch_to_matching_request_listener(self, dealer: DealerClient):
        listener = _TestRequestListener()
        dealer.add_request_listener(listener, "hm://connect-state")

        obj = {
            "message_ident": "hm://connect-state/v1/request/123",
            "key": "req-key-1",
            "headers": {},
            "payload": {
                "message_id": 42,
                "sent_by_device_id": "device-1",
                "command": {"endpoint": "play"},
            },
        }
        # ConnectionHolder is None, so send_reply won't be called
        dealer.handle_request(obj)
        dealer._DealerClient__worker.shutdown(wait=True)

        assert len(listener.requests) == 1

    def test_no_dispatch_for_non_matching_request(self, dealer: DealerClient):
        listener = _TestRequestListener()
        dealer.add_request_listener(listener, "hm://connect-state")

        obj = {
            "message_ident": "hm://other/request",
            "key": "req-key-2",
            "headers": {},
            "payload": {
                "message_id": 1,
                "sent_by_device_id": "d",
                "command": {},
            },
        }
        dealer.handle_request(obj)
        dealer._DealerClient__worker.shutdown(wait=True)

        assert len(listener.requests) == 0
