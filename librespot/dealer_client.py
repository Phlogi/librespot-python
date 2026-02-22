from __future__ import annotations

import base64
import concurrent.futures
import enum
import gzip
import json
import logging
import threading
import typing

import websocket
from requests.structures import CaseInsensitiveDict

from librespot.apresolver import ApResolver
from librespot.core_types import MessageType
from librespot.structure import Closeable
from librespot.structure import MessageListener
from librespot.structure import RequestListener

if typing.TYPE_CHECKING:
    from librespot.core import Session

_DEALER_PING_INTERVAL_SECONDS = 30
_DEALER_PONG_TIMEOUT_SECONDS = 3
_DEALER_RECONNECT_DELAY_SECONDS = 10


class DealerClient(Closeable):
    """ """
    logger = logging.getLogger("Librespot:DealerClient")
    __connection: typing.Union[ConnectionHolder, None]
    __last_scheduled_reconnection: typing.Union[threading.Timer, None]
    __message_listeners: typing.Dict[MessageListener, typing.List[str]]
    __message_listeners_lock: threading.Condition
    __request_listeners: typing.Dict[str, RequestListener]
    __request_listeners_lock: threading.Condition
    __session: Session
    __worker: concurrent.futures.ThreadPoolExecutor

    def __init__(self, session: Session):
        self.__session = session
        self.__last_scheduled_reconnection = None
        self.__message_listeners = {}
        self.__message_listeners_lock = threading.Condition()
        self.__request_listeners = {}
        self.__request_listeners_lock = threading.Condition()
        self.__worker = concurrent.futures.ThreadPoolExecutor()

    def add_message_listener(self, listener: MessageListener,
                             uris: list[str]) -> None:
        """

        :param listener: MessageListener:
        :param uris: list[str]:

        """
        with self.__message_listeners_lock:
            if listener in self.__message_listeners:
                raise TypeError(
                    "A listener for {} has already been added.".format(uris))
            self.__message_listeners[listener] = uris
            self.__message_listeners_lock.notify_all()

    def add_request_listener(self, listener: RequestListener, uri: str):
        """

        :param listener: RequestListener:
        :param uri: str:

        """
        with self.__request_listeners_lock:
            if uri in self.__request_listeners:
                raise TypeError(
                    "A listener for '{}' has already been added.".format(uri))
            self.__request_listeners[uri] = listener
            self.__request_listeners_lock.notify_all()

    def close(self) -> None:
        """ """
        if self.__last_scheduled_reconnection is not None:
            self.__last_scheduled_reconnection.cancel()
            self.__last_scheduled_reconnection = None
        if self.__connection is not None:
            self.__connection.close()
            self.__connection = None
        self.__worker.shutdown()

    def connect(self) -> None:
        """ """
        self.__connection = DealerClient.ConnectionHolder(
            self.__session,
            self,
            "wss://{}/?access_token={}".format(
                ApResolver.get_random_dealer(),
                self.__session.tokens().get("playlist-read"),
            ),
        )

    def connection_invalided(self) -> None:
        """ """
        self.__connection = None
        self.logger.debug("Scheduled reconnection attempt in %d seconds...",
                          _DEALER_RECONNECT_DELAY_SECONDS)
        self.__last_scheduled_reconnection = threading.Timer(
            _DEALER_RECONNECT_DELAY_SECONDS, self._do_reconnect)
        self.__last_scheduled_reconnection.daemon = True
        self.__last_scheduled_reconnection.start()

    def _do_reconnect(self) -> None:
        """Callback for the reconnection timer."""
        self.__last_scheduled_reconnection = None
        self.connect()

    def handle_message(self, obj: typing.Any) -> None:
        """

        :param obj: typing.Any:

        """
        uri = obj.get("uri")
        if uri is None:
            self.logger.warning("Received message with no uri, ignoring.")
            return
        headers = self.__get_headers(obj)
        payloads = obj.get("payloads")
        decoded_payloads: typing.Any
        if payloads is not None:
            if headers.get("Content-Type") == "application/json":
                decoded_payloads = payloads
            elif headers.get("Content-Type") == "plain/text":
                decoded_payloads = payloads
            else:
                decoded_payloads = base64.b64decode(payloads)
                if headers.get("Transfer-Encoding") == "gzip":
                    decoded_payloads = gzip.decompress(decoded_payloads)
        else:
            decoded_payloads = b""
        interesting = False
        with self.__message_listeners_lock:
            for listener in self.__message_listeners:
                dispatched = False
                keys = self.__message_listeners.get(listener)
                if keys is None:
                    continue
                for key in keys:
                    if uri.startswith(key) and not dispatched:
                        interesting = True

                        def anonymous(l=listener, u=uri, h=headers, p=decoded_payloads):
                            """ """
                            l.on_message(u, h, p)

                        self.__worker.submit(anonymous)
                        dispatched = True
        if not interesting:
            self.logger.debug("Couldn't dispatch message: {}".format(uri))

    def handle_request(self, obj: typing.Any) -> None:
        """

        :param obj: typing.Any:

        """
        mid = obj.get("message_ident")
        key = obj.get("key")
        headers = self.__get_headers(obj)
        payload = obj.get("payload")
        if payload is None:
            self.logger.warning("Received request with no payload, ignoring.")
            return
        if headers.get("Transfer-Encoding") == "gzip":
            gz = base64.b64decode(payload.get("compressed"))
            payload = json.loads(gzip.decompress(gz))
        pid = payload.get("message_id")
        sender = payload.get("sent_by_device_id")
        command = payload.get("command")
        self.logger.debug(
            "Received request. [mid: {}, key: {}, pid: {}, sender: {}, command: {}]"
            .format(mid, key, pid, sender, command))
        interesting = False
        with self.__request_listeners_lock:
            for mid_prefix in self.__request_listeners:
                if mid.startswith(mid_prefix):
                    listener = self.__request_listeners.get(mid_prefix)
                    if listener is None:
                        continue
                    interesting = True

                    _listener = listener  # bind for closure

                    def anonymous(_listener=_listener):
                        """ """
                        result = _listener.on_request(mid, pid, sender, command)
                        conn = self.__connection
                        if conn is not None:
                            conn.send_reply(key, result)
                        self.logger.warning(
                            "Handled request. [key: {}, result: {}]".format(
                                key, result))

                    self.__worker.submit(anonymous)
        if not interesting:
            self.logger.debug("Couldn't dispatch request: {}".format(mid))

    def remove_message_listener(self, listener: MessageListener) -> None:
        """

        :param listener: MessageListener:

        """
        with self.__message_listeners_lock:
            self.__message_listeners.pop(listener)

    def remove_request_listener(self, listener: RequestListener) -> None:
        """

        :param listener: RequestListener:

        """
        with self.__request_listeners_lock:
            request_listeners = {}
            for key, value in self.__request_listeners.items():
                if value != listener:
                    request_listeners[key] = value
            self.__request_listeners = request_listeners

    def wait_for_listener(self) -> None:
        """ """
        with self.__message_listeners_lock:
            while not self.__message_listeners:
                self.__message_listeners_lock.wait()

    def __get_headers(self, obj: typing.Any) -> CaseInsensitiveDict[str]:
        headers = obj.get("headers")
        if headers is None:
            return CaseInsensitiveDict()
        return headers

    class ConnectionHolder(Closeable):
        """ """
        __closed: bool
        __dealer_client: DealerClient
        __last_scheduled_ping: typing.Union[threading.Timer, None]
        __received_pong: bool
        __session: Session
        __url: str
        __ws: websocket.WebSocketApp

        def __init__(self, session: Session, dealer_client: DealerClient,
                     url: str):
            self.__closed = False
            self.__received_pong = False
            self.__last_scheduled_ping = None
            self.__session = session
            self.__dealer_client = dealer_client
            self.__url = url
            self.__ws = websocket.WebSocketApp(
                url,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_failure,
            )
            self.__ws_thread = threading.Thread(
                target=self.__ws.run_forever,
                name="dealer-websocket",
                daemon=True,
            )
            self.__ws_thread.start()

        def close(self):
            """ """
            if not self.__closed:
                self.__ws.close()
                self.__closed = True
            if self.__last_scheduled_ping is not None:
                self.__last_scheduled_ping.cancel()

        def on_failure(self, ws: websocket.WebSocketApp, error):
            """

            :param ws: websocket.WebSocketApp:
            :param error:

            """
            if self.__closed:
                return
            self.__dealer_client.logger.warning(
                "An exception occurred. Reconnecting...")
            self.close()
            self.__dealer_client.connection_invalided()

        def on_message(self, ws: websocket.WebSocketApp, text: str):
            """

            :param ws: websocket.WebSocketApp:
            :param text: str:

            """
            obj = json.loads(text)
            self.__dealer_client.wait_for_listener()
            typ = MessageType.parse(obj.get("type"))
            if typ == MessageType.MESSAGE:
                self.__dealer_client.handle_message(obj)
            elif typ == MessageType.REQUEST:
                self.__dealer_client.handle_request(obj)
            elif typ == MessageType.PONG:
                self.__received_pong = True
            elif typ == MessageType.PING:
                pass
            else:
                raise RuntimeError("Unknown message type for {}".format(
                    typ.value))

        def on_open(self, ws: websocket.WebSocketApp):
            """

            :param ws: websocket.WebSocketApp:

            """
            if self.__closed:
                self.__dealer_client.logger.critical(
                    "I wonder what happened here... Terminating. [closed: {}]".
                    format(self.__closed))
            self.__dealer_client.logger.debug(
                "Dealer connected! [url: {}]".format(self.__url))
            self._schedule_ping()

        def _schedule_ping(self) -> None:
            """Schedule the next ping after DEALER_PING_INTERVAL_SECONDS."""
            self.__last_scheduled_ping = threading.Timer(
                _DEALER_PING_INTERVAL_SECONDS, self._ping_and_check)
            self.__last_scheduled_ping.daemon = True
            self.__last_scheduled_ping.start()

        def _ping_and_check(self) -> None:
            """Send a ping and schedule a pong timeout check."""
            self.send_ping()
            self.__received_pong = False
            pong_timer = threading.Timer(
                _DEALER_PONG_TIMEOUT_SECONDS, self._check_pong)
            pong_timer.daemon = True
            pong_timer.start()

        def _check_pong(self) -> None:
            """Check if a pong was received; reconnect if not."""
            if self.__last_scheduled_ping is None:
                return
            if not self.__received_pong:
                self.__dealer_client.logger.warning(
                    "Did not receive pong in %d seconds. Reconnecting...",
                    _DEALER_PONG_TIMEOUT_SECONDS
                )
                self.close()
                self.__dealer_client.connection_invalided()
                return
            self.__received_pong = False
            self._schedule_ping()

        def send_ping(self):
            """ """
            self.__ws.send('{"type":"ping"}')

        def send_reply(self, key: str, result: DealerClient.RequestResult):
            """

            :param key: str:
            :param result: DealerClient.RequestResult:

            """
            self.__ws.send(
                json.dumps({"type": "reply", "key": key, "payload": {"success": result == DealerClient.RequestResult.SUCCESS}}))

    class RequestResult(enum.Enum):
        """ """
        UNKNOWN_SEND_COMMAND_RESULT = 0
        SUCCESS = 1
        DEVICE_NOT_FOUND = 2
        CONTEXT_PLAYER_ERROR = 3
        DEVICE_DISAPPEARED = 4
        UPSTREAM_ERROR = 5
        DEVICE_DOES_NOT_SUPPORT_COMMAND = 6
        RATE_LIMITED = 7
