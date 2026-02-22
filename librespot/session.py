from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import os
import socket
import struct
import threading
import time
import typing

import defusedxml.ElementTree
import requests
from Cryptodome import Random
from Cryptodome.Cipher import AES
from Cryptodome.Hash import HMAC
from Cryptodome.Hash import SHA1
from Cryptodome.Protocol.KDF import PBKDF2
from Cryptodome.PublicKey import RSA
from Cryptodome.Signature import PKCS1_v1_5
from requests.structures import CaseInsensitiveDict

from librespot import util
from librespot import Version
from librespot.audio import AudioKeyManager
from librespot.audio import CdnManager
from librespot.audio import PlayableContentFeeder
from librespot.audio.storage import ChannelManager
from librespot.cache import CacheManager
from librespot.crypto import CipherPair
from librespot.crypto import DiffieHellman
from librespot.crypto import Packet
from librespot.mercury import MercuryClient
from librespot.mercury import MercuryRequests
from librespot.oauth import OAuth
from librespot.proto import Authentication_pb2 as Authentication
from librespot.proto import Connect_pb2 as Connect
from librespot.proto import Keyexchange_pb2 as Keyexchange
from librespot.proto.ExplicitContentPubsub_pb2 import UserAttributesUpdate
from librespot.structure import Closeable
from librespot.structure import MessageListener
from librespot.structure import SubListener

from librespot.api import ApiClient
from librespot.apresolver import ApResolver
from librespot.dealer_client import DealerClient
from librespot.event_service import EventService
from librespot.search import SearchManager
from librespot.token_provider import TokenProvider

# ── Module-level constants ──────────────────────────────────────────────────────

_CONNECT_LOGOUT_URI = "hm://connect-state/v1/connect/logout"
_OAUTH_REDIRECT_URI = "http://127.0.0.1:5588/login"
_RETRY_ATTEMPTS_ENV = "LIBRESPOT_RETRY_ATTEMPTS"
_DEFAULT_RETRY_ATTEMPTS = 5
_BACKOFF_CAP_SECONDS = 30
_SESSION_PING_TIMEOUT_SECONDS = 2 * 60 + 5


# ── Promoted from Session nested classes ────────────────────────────────────────

class _Accumulator:
    """ """
    __buffer: io.BytesIO

    def __init__(self):
        self.__buffer = io.BytesIO()

    def read(self) -> bytes:
        """Read all buffer


        :returns: All buffer

        """
        pos = self.__buffer.tell()
        self.__buffer.seek(0)
        data = self.__buffer.read()
        self.__buffer.seek(pos)
        return data

    def write(self, data: bytes) -> None:
        """Write data to buffer

        :param data: Bytes to be written
        :param data: bytes:

        """
        self.__buffer.write(data)

    def write_int(self, data: int) -> None:
        """Write data to buffer

        :param data: Integer to be written
        :param data: int:

        """
        self.write(struct.pack(">i", data))

    def write_short(self, data: int) -> None:
        """Write data to buffer

        :param data: Short integer to be written
        :param data: int:

        """
        self.write(struct.pack(">h", data))


class _ConnectionHolder:
    """ """
    __buffer: io.BytesIO
    __socket: socket.socket

    def __init__(self, sock: socket.socket):
        self.__buffer = io.BytesIO()
        self.__socket = sock

    @staticmethod
    def create(address: str, conf) -> _ConnectionHolder:
        """Create the ConnectionHolder instance

        :param address: Address to connect
        :param address: str:
        :param conf:
        :returns: ConnectionHolder instance

        """
        ap_address = address.split(":")[0]
        ap_port = int(address.split(":")[1])
        sock = socket.socket()
        sock.connect((ap_address, ap_port))
        return _ConnectionHolder(sock)

    def close(self) -> None:
        """Close the connection"""
        self.__socket.close()

    def flush(self) -> None:
        """Flush data to socket"""
        self.__buffer.seek(0)
        self.__socket.sendall(self.__buffer.read())
        self.__buffer = io.BytesIO()

    def read(self, length: int) -> bytes:
        """Read exactly *length* bytes from the socket.

        Loops over recv calls until the full amount has been
        collected.  Raises ConnectionError if the remote end
        closes the connection before all bytes arrive.

        :param length: Number of bytes to read.
        :returns: Exactly *length* bytes.

        """
        pieces: list[bytes] = []
        remaining = length
        while remaining > 0:
            chunk = self.__socket.recv(remaining)
            if not chunk:
                received = length - remaining
                raise ConnectionError(
                    "Connection closed: expected {} bytes, "
                    "got {}".format(length, received))
            pieces.append(chunk)
            remaining -= len(chunk)
        return b"".join(pieces)

    def read_int(self) -> int:
        """Read integer from socket


        :returns: integer from socket

        """
        return struct.unpack(">i", self.read(4))[0]

    def read_short(self) -> int:
        """Read short integer from socket


        :returns: short integer from socket

        """
        return struct.unpack(">h", self.read(2))[0]

    def set_timeout(self, seconds: float) -> None:
        """Set socket's timeout

        :param seconds: Number of seconds until timeout
        :param seconds: float:

        """
        self.__socket.settimeout(None if seconds == 0 else seconds)

    def write(self, data: bytes) -> None:
        """Write data to buffer

        :param data: Bytes to be written
        :param data: bytes:

        """
        self.__buffer.write(data)

    def write_int(self, data: int) -> None:
        """Write data to buffer

        :param data: Integer to be written
        :param data: int:

        """
        self.write(struct.pack(">i", data))

    def write_short(self, data: int) -> None:
        """Write data to buffer

        :param data: Short integer to be written
        :param data: int:

        """
        self.write(struct.pack(">h", data))


class _Inner:
    """ """
    device_type: typing.Optional[Connect.DeviceType] = None
    device_name: str
    device_id: str
    conf: typing.Optional[_Configuration] = None
    preferred_locale: str

    def __init__(
        self,
        device_type: Connect.DeviceType,
        device_name: str,
        preferred_locale: str,
        conf: _Configuration,
        device_id: typing.Optional[str] = None,
    ):
        self.preferred_locale = preferred_locale
        self.conf = conf
        self.device_type = device_type
        self.device_name = device_name
        self.device_id = (device_id if device_id is not None else
                          util.random_hex_string(40))


class _Configuration:
    """ """
    # Cache
    cache_enabled: bool
    cache_dir: str
    do_cache_clean_up: bool

    # Stored credentials
    store_credentials: bool
    stored_credentials_file: str

    # Fetching
    retry_on_chunk_error: bool

    def __init__(
        self,
        cache_enabled: bool,
        cache_dir: str,
        do_cache_clean_up: bool,
        store_credentials: bool,
        stored_credentials_file: str,
        retry_on_chunk_error: bool,
    ):
        self.cache_enabled = cache_enabled
        self.cache_dir = cache_dir
        self.do_cache_clean_up = do_cache_clean_up
        self.store_credentials = store_credentials
        self.stored_credentials_file = stored_credentials_file
        self.retry_on_chunk_error = retry_on_chunk_error

    class Builder:
        """ """
        # Cache
        cache_enabled: bool = True
        cache_dir: str = os.path.join(os.getcwd(), "cache")
        do_cache_clean_up: bool = True

        # Stored credentials
        store_credentials: bool = True
        stored_credentials_file: str = os.path.join(
            os.getcwd(), "credentials.json")

        # Fetching
        retry_on_chunk_error: bool = True

        def set_cache_enabled(
                self,
                cache_enabled: bool) -> _Configuration.Builder:
            """Set cache_enabled

            :param cache_enabled: bool:
            :returns: Builder

            """
            self.cache_enabled = cache_enabled
            return self

        def set_cache_dir(self,
                          cache_dir: str) -> _Configuration.Builder:
            """Set cache_dir

            :param cache_dir: str:
            :returns: Builder

            """
            self.cache_dir = cache_dir
            return self

        def set_do_cache_clean_up(
                self,
                do_cache_clean_up: bool) -> _Configuration.Builder:
            """Set do_cache_clean_up

            :param do_cache_clean_up: bool:
            :returns: Builder

            """
            self.do_cache_clean_up = do_cache_clean_up
            return self

        def set_store_credentials(
                self,
                store_credentials: bool) -> _Configuration.Builder:
            """Set store_credentials

            :param store_credentials: bool:
            :returns: Builder

            """
            self.store_credentials = store_credentials
            return self

        def set_stored_credential_file(
                self, stored_credential_file: str
        ) -> _Configuration.Builder:
            """Set stored_credential_file

            :param stored_credential_file: str:
            :returns: Builder

            """
            self.stored_credentials_file = stored_credential_file
            return self

        def set_retry_on_chunk_error(
                self, retry_on_chunk_error: bool
        ) -> _Configuration.Builder:
            """Set retry_on_chunk_error

            :param retry_on_chunk_error: bool:
            :returns: Builder

            """
            self.retry_on_chunk_error = retry_on_chunk_error
            return self

        def build(self) -> _Configuration:
            """Build Configuration instance


            :returns: _Configuration

            """
            return _Configuration(
                self.cache_enabled,
                self.cache_dir,
                self.do_cache_clean_up,
                self.store_credentials,
                self.stored_credentials_file,
                self.retry_on_chunk_error,
            )


class _Receiver:
    """ """
    __session: Session
    __thread: threading.Thread
    __running: bool = True

    def __init__(self, session):
        self.__session = session
        self.__thread = threading.Thread(target=self.run)
        self.__thread.daemon = True
        self.__thread.name = "session-packet-receiver"
        self.__thread.start()

    def stop(self) -> None:
        """ """
        self.__running = False

    def run(self) -> None:
        """Receive Packet thread function"""
        self.__session.logger.info("Session.Receiver started")
        while self.__running:
            packet: Packet
            cmd: typing.Optional[bytes]
            try:
                cp = self.__session.cipher_pair
                conn = self.__session.connection
                if cp is None:
                    raise ConnectionError("cipher_pair is None")
                if conn is None:
                    raise ConnectionError("connection is None")
                packet = cp.receive_encoded(conn)
                cmd = Packet.Type.parse(packet.cmd)
                if cmd is None:
                    self.__session.logger.info(
                        "Skipping unknown command cmd: 0x{}, payload: {}".
                        format(util.bytes_to_hex(packet.cmd),
                               packet.payload))
                    continue
            except (RuntimeError, ConnectionError) as ex:
                if self.__running:
                    self.__session.logger.critical(
                        "Failed reading packet! {}".format(ex))
                    self.__session.reconnect()
                break
            if not self.__running:
                break
            if cmd == Packet.Type.ping:
                if self.__session.scheduled_reconnect is not None:
                    self.__session.scheduled_reconnect.cancel()

                def anonymous():
                    """ """
                    self.__session.logger.warning(
                        "Socket timed out. Reconnecting...")
                    self.__session.reconnect()

                self.__session.scheduled_reconnect = threading.Timer(
                    _SESSION_PING_TIMEOUT_SECONDS, anonymous)
                self.__session.scheduled_reconnect.daemon = True
                self.__session.scheduled_reconnect.start()
                self.__session.send(Packet.Type.pong, packet.payload)
            elif cmd == Packet.Type.pong_ack:
                continue
            elif cmd == Packet.Type.country_code:
                self.__session.country_code = packet.payload.decode()
                self.__session.logger.info(
                    "Received country_code: {}".format(
                        self.__session.country_code))
            elif cmd == Packet.Type.license_version:
                license_version = io.BytesIO(packet.payload)
                license_id = struct.unpack(">h",
                                           license_version.read(2))[0]
                if license_id != 0:
                    buffer = license_version.read()
                    self.__session.logger.info(
                        "Received license_version: {}, {}".format(
                            license_id, buffer.decode()))
                else:
                    self.__session.logger.info(
                        "Received license_version: {}".format(license_id))
            elif cmd == Packet.Type.unknown_0x10:
                self.__session.logger.debug("Received 0x10: {}".format(
                    util.bytes_to_hex(packet.payload)))
            elif cmd in [
                    Packet.Type.mercury_sub,
                    Packet.Type.mercury_unsub,
                    Packet.Type.mercury_event,
                    Packet.Type.mercury_req,
            ]:
                self.__session.mercury().dispatch(packet)
            elif cmd in [Packet.Type.aes_key, Packet.Type.aes_key_error]:
                self.__session.audio_key().dispatch(packet)
            elif cmd in [
                    Packet.Type.channel_error, Packet.Type.stream_chunk_res
            ]:
                self.__session.channel().dispatch(packet)
            elif cmd == Packet.Type.product_info:
                self.__session.parse_product_info(packet.payload)
            else:
                self.__session.logger.info("Skipping {}".format(
                    util.bytes_to_hex(cmd)))


class _SpotifyAuthenticationException(Exception):
    """ """

    def __init__(self, login_failed: Keyexchange.APLoginFailed):
        super().__init__(
            Keyexchange.ErrorCode.Name(login_failed.error_code))


class _AbsBuilder:
    """ """
    conf: typing.Optional[_Configuration] = None
    device_id: typing.Optional[str] = None
    device_name: str = "librespot-python"
    device_type = Connect.DeviceType.COMPUTER
    preferred_locale: str = "en"

    def __init__(self, conf: typing.Optional[_Configuration] = None):
        if conf is None:
            self.conf = _Configuration.Builder().build()
        else:
            self.conf = conf

    def set_preferred_locale(self, locale: str) -> _AbsBuilder:
        """

        :param locale: str:

        """
        if len(locale) != 2:
            raise TypeError("Invalid locale: {}".format(locale))
        self.preferred_locale = locale
        return self

    def set_device_name(self, device_name: str) -> _AbsBuilder:
        """

        :param device_name: str:

        """
        self.device_name = device_name
        return self

    def set_device_id(self, device_id: str) -> _AbsBuilder:
        """

        :param device_id: str:

        """
        if len(device_id) != 40:
            raise TypeError("Device ID must be 40 chars long.")
        self.device_id = device_id
        return self

    def set_device_type(
            self, device_type: Connect.DeviceType) -> _AbsBuilder:
        """

        :param device_type: Connect.DeviceType:

        """
        self.device_type = device_type
        return self


class _Builder(_AbsBuilder):
    """ """
    login_credentials: typing.Optional[Authentication.LoginCredentials] = None

    def blob(self, username: str, blob: bytes) -> _Builder:
        """

        :param username: str:
        :param blob: bytes:

        """
        if self.device_id is None:
            raise TypeError("You must specify the device ID first.")
        self.login_credentials = self.decrypt_blob(self.device_id,
                                                   username, blob)
        return self

    def decrypt_blob(
            self, device_id: str, username: str,
            encrypted_blob: bytes) -> Authentication.LoginCredentials:
        """

        :param device_id: str:
        :param username: str:
        :param encrypted_blob: bytes:

        """
        encrypted_blob = base64.b64decode(encrypted_blob)
        sha1 = SHA1.new()
        sha1.update(device_id.encode())
        secret = sha1.digest()
        base_key = PBKDF2(secret,
                          username.encode(),
                          20,
                          0x100,
                          hmac_hash_module=SHA1)
        sha1 = SHA1.new()
        sha1.update(base_key)
        key = sha1.digest() + b"\x00\x00\x00\x14"
        aes = AES.new(key, AES.MODE_ECB)
        decrypted_blob = bytearray(aes.decrypt(encrypted_blob))
        l = len(decrypted_blob)
        for i in range(0, l - 0x10):
            decrypted_blob[l - i - 1] ^= decrypted_blob[l - i - 0x11]
        blob = io.BytesIO(decrypted_blob)
        blob.read(1)
        le = self.read_blob_int(blob)
        blob.read(le)
        blob.read(1)
        type_int = self.read_blob_int(blob)
        type_ = Authentication.AuthenticationType.Name(type_int)
        if type_ is None:
            raise IOError(
                TypeError(
                    "Unknown AuthenticationType: {}".format(type_int)))
        blob.read(1)
        l = self.read_blob_int(blob)
        auth_data = blob.read(l)
        return Authentication.LoginCredentials(
            auth_data=auth_data,
            typ=type_,
            username=username,
        )

    def read_blob_int(self, buffer: io.BytesIO) -> int:
        """

        :param buffer: io.BytesIO:

        """
        lo = buffer.read(1)
        if (int(lo[0]) & 0x80) == 0:
            return int(lo[0])
        hi = buffer.read(1)
        return int(lo[0]) & 0x7F | int(hi[0]) << 7

    def stored(self, stored_credentials_str: str):
        """Create credential from stored string

        :param stored_credentials_str: str:
        :returns: Builder

        """
        try:
            obj = json.loads(base64.b64decode(stored_credentials_str))
        except binascii.Error:
            pass
        except json.JSONDecodeError:
            pass
        else:
            try:
                self.login_credentials = Authentication.LoginCredentials(
                    typ=Authentication.AuthenticationType.Value(
                        obj["type"]),
                    username=obj["username"],
                    auth_data=base64.b64decode(obj["credentials"]),
                )
            except KeyError:
                pass
        return self

    def stored_file(self,
                    stored_credentials: typing.Optional[str] = None) -> _Builder:
        """Create credential from stored file

        :param stored_credentials: str:  (Default value = None)
        :returns: Builder

        """
        if stored_credentials is None:
            if self.conf is not None:
                stored_credentials = self.conf.stored_credentials_file
        if stored_credentials is not None and os.path.isfile(stored_credentials):
            try:
                with open(stored_credentials) as f:
                    obj = json.load(f)
            except json.JSONDecodeError:
                pass
            else:
                try:
                    # Try Python librespot format first
                    self.login_credentials = Authentication.LoginCredentials(
                        typ=Authentication.AuthenticationType.Value(
                            obj["type"]),
                        username=obj["username"],
                        auth_data=base64.b64decode(obj["credentials"]),
                    )
                except KeyError:
                    # Try Rust librespot format (auth_type as int, auth_data instead of credentials)
                    try:
                        self.login_credentials = Authentication.LoginCredentials(
                            typ=obj["auth_type"],
                            username=obj["username"],
                            auth_data=base64.b64decode(obj["auth_data"]),
                        )
                    except KeyError:
                        pass
        return self

    def oauth(self, oauth_url_callback, success_page_content = None) -> _Builder:
        """
        Login via OAuth

        You can supply an oauth_url_callback method that takes a string and returns the OAuth URL.
        When oauth_url_callback is None, this will only log the auth url to the console.
        """
        if self.conf is not None and self.conf.stored_credentials_file is not None and os.path.isfile(self.conf.stored_credentials_file):
            return self.stored_file(None)
        self.login_credentials = OAuth(MercuryRequests.keymaster_client_id, _OAUTH_REDIRECT_URI, oauth_url_callback).set_success_page_content(success_page_content).flow()
        return self

    def user_pass(self, username: str, password: str) -> _Builder:
        """Create credential from username and password

        :param username: Spotify's account username
        :param username: str:
        :param password: str:
        :returns: Builder

        """
        self.login_credentials = Authentication.LoginCredentials(
            username=username,
            typ=Authentication.AuthenticationType.AUTHENTICATION_USER_PASS,
            auth_data=password.encode(),
        )
        return self

    def create(self) -> Session:
        """Create the Session instance.

        Retries connection attempts with exponential backoff when
        the access-point is unreachable or drops the connection.
        Authentication failures (bad credentials) are never retried.

        :returns: Session instance

        """
        if self.login_credentials is None:
            raise RuntimeError("You must select an authentication method.")

        max_attempts = int(os.getenv(_RETRY_ATTEMPTS_ENV, str(_DEFAULT_RETRY_ATTEMPTS)))
        last_exception: typing.Optional[Exception] = None
        logger = logging.getLogger("Librespot:Session")

        for attempt in range(1, max_attempts + 1):
            session: typing.Optional[Session] = None
            try:
                assert self.conf is not None, "Configuration not set"
                session = Session(
                    _Inner(
                        self.device_type,
                        self.device_name,
                        self.preferred_locale,
                        self.conf,
                        self.device_id,
                    ),
                    ApResolver.get_random_accesspoint(),
                )
                session.connect()
                session.authenticate(self.login_credentials)
                return session
            except _SpotifyAuthenticationException:
                raise
            except Exception as ex:
                last_exception = ex
                if session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass
                if attempt < max_attempts:
                    delay = min(2 ** attempt, _BACKOFF_CAP_SECONDS)
                    logger.warning(
                        "Connection attempt %d/%d failed: %s. "
                        "Retrying in %ds...",
                        attempt, max_attempts, ex, delay,
                    )
                    time.sleep(delay)

        if last_exception is not None:
            raise last_exception
        raise RuntimeError("Failed to connect")


# ── Session ─────────────────────────────────────────────────────────────────────

class Session(Closeable, MessageListener, SubListener):
    """ """
    # Backward-compatible aliases for promoted nested classes
    Accumulator = _Accumulator
    AbsBuilder = _AbsBuilder
    Builder = _Builder
    Configuration = _Configuration
    ConnectionHolder = _ConnectionHolder
    Inner = _Inner
    Receiver = _Receiver
    SpotifyAuthenticationException = _SpotifyAuthenticationException

    cipher_pair: typing.Union[CipherPair, None]
    country_code: str = "EN"
    connection: typing.Union[_ConnectionHolder, None]
    logger = logging.getLogger("Librespot:Session")
    scheduled_reconnect: typing.Union[threading.Timer, None]
    __api: ApiClient
    __ap_welcome: Authentication.APWelcome
    __audio_key_manager: typing.Union[AudioKeyManager, None] = None
    __auth_lock: threading.Condition
    __auth_lock_bool: bool
    __cache_manager: typing.Union[CacheManager, None]
    __cdn_manager: typing.Union[CdnManager, None]
    __channel_manager: typing.Union[ChannelManager, None] = None
    __client: typing.Union[requests.Session, None]
    __closed: bool
    __closing: bool
    __content_feeder: typing.Union[PlayableContentFeeder, None]
    __dealer_client: typing.Union[DealerClient, None] = None
    __event_service: typing.Union[EventService, None] = None
    __keys: DiffieHellman
    __mercury_client: MercuryClient
    __receiver: typing.Union[_Receiver, None] = None
    __search: typing.Union[SearchManager, None]
    __server_key = (b"\xac\xe0F\x0b\xff\xc20\xaf\xf4k\xfe\xc3\xbf\xbf\x86="
                    b"\xa1\x91\xc6\xcc3l\x93\xa1O\xb3\xb0\x16\x12\xac\xacj"
                    b"\xf1\x80\xe7\xf6\x14\xd9B\x9d\xbe.4fC\xe3b\xd22z\x1a"
                    b"\r\x92;\xae\xdd\x14\x02\xb1\x81U\x05a\x04\xd5,\x96\xa4"
                    b"L\x1e\xcc\x02J\xd4\xb2\x0c\x00\x1f\x17\xed\xc2/\xc45"
                    b"!\xc8\xf0\xcb\xae\xd2\xad\xd7+\x0f\x9d\xb3\xc52\x1a*"
                    b"\xfeY\xf3Z\r\xach\xf1\xfab\x1e\xfb,\x8d\x0c\xb79-\x92"
                    b"G\xe3\xd75\x1am\xbd$\xc2\xae%[\x88\xff\xabs)\x8a\x0b"
                    b"\xcc\xcd\x0cXg1\x89\xe8\xbd4\x80xJ_\xc9k\x89\x9d\x95k"
                    b"\xfc\x86\xd7O3\xa6x\x17\x96\xc9\xc3-\r2\xa5\xab\xcd\x05'"
                    b"\xe2\xf7\x10\xa3\x96\x13\xc4/\x99\xc0'\xbf\xed\x04\x9c"
                    b"<'X\x04\xb6\xb2\x19\xf9\xc1/\x02\xe9Hc\xec\xa1\xb6B\xa0"
                    b"\x9dH%\xf8\xb3\x9d\xd0\xe8j\xf9HM\xa1\xc2\xba\x860B\xea"
                    b"\x9d\xb3\x08l\x19\x0eH\xb3\x9df\xeb\x00\x06\xa2Z\xee\xa1"
                    b"\x1b\x13\x87<\xd7\x19\xe6U\xbd")
    __stored_str: str
    __token_provider: typing.Union[TokenProvider, None]
    __user_attributes: typing.Dict[str, str]

    def __init__(self, inner: _Inner, address: str) -> None:
        assert inner.conf is not None, "Configuration not set"
        self.scheduled_reconnect = None
        self.__reconnect_lock = threading.Lock()
        self.__auth_lock = threading.Condition()
        self.__auth_lock_bool = False
        self.__closed = False
        self.__closing = False
        self.__stored_str = ""
        self.__user_attributes = {}
        self.__client = Session.create_client(inner.conf)
        self.connection = _ConnectionHolder.create(address, None)
        self.__inner = inner
        self.__keys = DiffieHellman()
        self.logger.info("Created new session! device_id: {}, ap: {}".format(
            inner.device_id, address))

    def api(self) -> ApiClient:
        """ """
        self.__wait_auth_lock()
        if self.__api is None:
            raise RuntimeError("Session isn't authenticated!")
        return self.__api

    def ap_welcome(self):
        """ """
        self.__wait_auth_lock()
        if self.__ap_welcome is None:
            raise RuntimeError("Session isn't authenticated!")
        return self.__ap_welcome

    def audio_key(self) -> AudioKeyManager:
        """ """
        self.__wait_auth_lock()
        if self.__audio_key_manager is None:
            raise RuntimeError("Session isn't authenticated!")
        return self.__audio_key_manager

    def authenticate(self,
                     credential: Authentication.LoginCredentials) -> None:
        """Log in to Spotify

        :param credential: Spotify account login information
        :param credential: Authentication.LoginCredentials:

        """
        self.__authenticate_partial(credential, False)
        with self.__auth_lock:
            self.__mercury_client = MercuryClient(self)
            self.__token_provider = TokenProvider(self)
            self.__audio_key_manager = AudioKeyManager(self)
            self.__channel_manager = ChannelManager(self)
            self.__api = ApiClient(self)
            self.__cdn_manager = CdnManager(self)
            self.__content_feeder = PlayableContentFeeder(self)
            self.__cache_manager = CacheManager(self)
            self.__dealer_client = DealerClient(self)
            self.__search = SearchManager(self)
            self.__event_service = EventService(self)
            self.__auth_lock_bool = False
            self.__auth_lock.notify_all()
        self.dealer().connect()
        self.logger.info("Authenticated as {}!".format(
            self.__ap_welcome.canonical_username))
        self.mercury().interested_in("spotify:user:attributes:update", self)
        self.dealer().add_message_listener(
            self, [_CONNECT_LOGOUT_URI])

    def cache(self) -> CacheManager:
        """ """
        self.__wait_auth_lock()
        if self.__cache_manager is None:
            raise RuntimeError("Session isn't authenticated!")
        return self.__cache_manager

    def cdn(self) -> CdnManager:
        """ """
        self.__wait_auth_lock()
        if self.__cdn_manager is None:
            raise RuntimeError("Session isn't authenticated!")
        return self.__cdn_manager

    def channel(self) -> ChannelManager:
        """ """
        self.__wait_auth_lock()
        if self.__channel_manager is None:
            raise RuntimeError("Session isn't authenticated!")
        return self.__channel_manager

    def client(self) -> requests.Session:
        """ """
        if self.__client is None:
            raise RuntimeError("Session client is not initialized!")
        return self.__client

    def close(self) -> None:
        """Close instance"""
        self.logger.info("Closing session. device_id: {}".format(
            self.__inner.device_id))
        self.__closing = True
        if self.scheduled_reconnect is not None:
            self.scheduled_reconnect.cancel()
            self.scheduled_reconnect = None
        if self.__dealer_client is not None:
            self.__dealer_client.close()
            self.__dealer_client = None
        if self.__audio_key_manager is not None:
            self.__audio_key_manager = None
        if self.__channel_manager is not None:
            self.__channel_manager.close()
            self.__channel_manager = None
        if self.__event_service is not None:
            self.__event_service.close()
            self.__event_service = None
        if self.__receiver is not None:
            self.__receiver.stop()
            self.__receiver = None
        if self.__client is not None:
            self.__client.close()
            self.__client = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        with self.__auth_lock:
            self.__ap_welcome = None
            self.cipher_pair = None
            self.__closed = True
            self.__auth_lock.notify_all()
        self.logger.info("Closed session. device_id: {}".format(
            self.__inner.device_id))

    def connect(self) -> None:
        """Connect to the Spotify Server"""
        assert self.connection is not None, "Connection not established"
        acc = _Accumulator()
        # Send ClientHello
        nonce = Random.get_random_bytes(0x10)
        client_hello_proto = Keyexchange.ClientHello(
            build_info=Version.standard_build_info(),
            client_nonce=nonce,
            cryptosuites_supported=[
                Keyexchange.Cryptosuite.CRYPTO_SUITE_SHANNON
            ],
            login_crypto_hello=Keyexchange.LoginCryptoHelloUnion(
                diffie_hellman=Keyexchange.LoginCryptoDiffieHellmanHello(
                    gc=self.__keys.public_key_bytes(), server_keys_known=1), ),
            padding=b"\x1e",
        )
        client_hello_bytes = client_hello_proto.SerializeToString()
        self.connection.write(b"\x00\x04")
        self.connection.write_int(2 + 4 + len(client_hello_bytes))
        self.connection.write(client_hello_bytes)
        self.connection.flush()
        acc.write(b"\x00\x04")
        acc.write_int(2 + 4 + len(client_hello_bytes))
        acc.write(client_hello_bytes)
        # Read APResponseMessage
        ap_response_message_length = self.connection.read_int()
        acc.write_int(ap_response_message_length)
        ap_response_message_bytes = self.connection.read(
            ap_response_message_length - 4)
        acc.write(ap_response_message_bytes)
        ap_response_message_proto = Keyexchange.APResponseMessage()
        ap_response_message_proto.ParseFromString(ap_response_message_bytes)
        shared_key = util.int_to_bytes(
            self.__keys.compute_shared_key(
                ap_response_message_proto.challenge.login_crypto_challenge.
                diffie_hellman.gs))
        # Check gs_signature
        rsa = RSA.construct((int.from_bytes(self.__server_key, "big"), 65537))
        pkcs1_v1_5 = PKCS1_v1_5.new(rsa)
        sha1 = SHA1.new()
        sha1.update(ap_response_message_proto.challenge.login_crypto_challenge.
                    diffie_hellman.gs)
        if not pkcs1_v1_5.verify(
                sha1,
                ap_response_message_proto.challenge.login_crypto_challenge.
                diffie_hellman.gs_signature,
        ):
            raise RuntimeError("Failed signature check!")
        # Solve challenge
        buffer = io.BytesIO()
        for i in range(1, 6):
            mac = HMAC.new(shared_key, digestmod=SHA1)
            mac.update(acc.read())
            mac.update(bytes([i]))
            buffer.write(mac.digest())
        buffer.seek(0)
        mac = HMAC.new(buffer.read(20), digestmod=SHA1)
        mac.update(acc.read())
        challenge = mac.digest()
        client_response_plaintext_proto = Keyexchange.ClientResponsePlaintext(
            crypto_response=Keyexchange.CryptoResponseUnion(),
            login_crypto_response=Keyexchange.LoginCryptoResponseUnion(
                diffie_hellman=Keyexchange.LoginCryptoDiffieHellmanResponse(
                    hmac=challenge)),
            pow_response=Keyexchange.PoWResponseUnion(),
        )
        client_response_plaintext_bytes = (
            client_response_plaintext_proto.SerializeToString())
        self.connection.write_int(4 + len(client_response_plaintext_bytes))
        self.connection.write(client_response_plaintext_bytes)
        self.connection.flush()
        try:
            self.connection.set_timeout(1)
            scrap = self.connection.read(4)
            if len(scrap) == 4:
                payload = self.connection.read(
                    struct.unpack(">i", scrap)[0] - 4)
                failed = Keyexchange.APResponseMessage()
                failed.ParseFromString(payload)
                raise RuntimeError(failed)
        except socket.timeout:
            pass
        finally:
            self.connection.set_timeout(0)
        buffer.seek(20)
        with self.__auth_lock:
            self.cipher_pair = CipherPair(buffer.read(32), buffer.read(32))
            self.__auth_lock_bool = True
        self.logger.info("Connection successfully!")

    def content_feeder(self) -> PlayableContentFeeder:
        """ """
        self.__wait_auth_lock()
        if self.__content_feeder is None:
            raise RuntimeError("Session isn't authenticated!")
        return self.__content_feeder

    @staticmethod
    def create_client(conf: _Configuration) -> requests.Session:
        """

        :param conf: Configuration:

        """
        client = requests.Session()
        return client

    def dealer(self) -> DealerClient:
        """ """
        self.__wait_auth_lock()
        if self.__dealer_client is None:
            raise RuntimeError("Session isn't authenticated!")
        return self.__dealer_client

    def device_id(self) -> str:
        """ """
        return self.__inner.device_id

    def device_name(self) -> str:
        """ """
        return self.__inner.device_name

    def device_type(self) -> Connect.DeviceType:
        """ """
        return self.__inner.device_type

    def event(self, resp: MercuryClient.Response) -> None:
        """

        :param resp: MercuryClient.Response:

        """
        if resp.uri == "spotify:user:attributes:update":
            attributes_update = UserAttributesUpdate()
            attributes_update.ParseFromString(resp.payload)
            for pair in attributes_update.pairs_list:
                self.__user_attributes[pair.key] = pair.value
                self.logger.info("Updated user attribute: {} -> {}".format(
                    pair.key, pair.value))

    def get_user_attribute(self, key: str, fallback: typing.Optional[str] = None) -> typing.Optional[str]:
        """

        :param key: str:
        :param fallback: str:  (Default value = None)

        """
        return (self.__user_attributes.get(key)
                if self.__user_attributes.get(key) is not None else fallback)

    def is_valid(self) -> bool:
        """ """
        if self.__closed:
            return False
        self.__wait_auth_lock()
        return self.__ap_welcome is not None and self.connection is not None

    def mercury(self) -> MercuryClient:
        """ """
        self.__wait_auth_lock()
        if self.__mercury_client is None:
            raise RuntimeError("Session isn't authenticated!")
        return self.__mercury_client

    def on_message(self, uri: str, headers: CaseInsensitiveDict[str],
                   payload: bytes):
        """

        :param uri: str:
        :param headers: CaseInsensitiveDict[str:
        :param str]:
        :param payload: bytes:

        """
        if uri == _CONNECT_LOGOUT_URI:
            self.close()

    def parse_product_info(self, data) -> None:
        """Parse product information

        :param data: Raw product information

        """
        products = defusedxml.ElementTree.fromstring(data)
        if products is None:
            return
        product = products[0]
        if product is None:
            return
        for i in range(len(product)):
            self.__user_attributes[product[i].tag] = product[i].text
        self.logger.debug("Parsed product info: {}".format(
            self.__user_attributes))

    def preferred_locale(self) -> str:
        """ """
        return self.__inner.preferred_locale

    def reconnect(self) -> None:
        """Reconnect to the Spotify Server.

        Retries with exponential backoff when the access-point is
        unreachable or drops the connection.
        """
        if not self.__reconnect_lock.acquire(blocking=False):
            self.logger.debug("Reconnect already in progress, skipping.")
            return
        try:
            if self.scheduled_reconnect is not None:
                self.scheduled_reconnect.cancel()
                self.scheduled_reconnect = None

            if self.__receiver is not None:
                self.__receiver.stop()
            if self.connection is not None:
                self.connection.close()

            max_attempts = int(os.getenv(_RETRY_ATTEMPTS_ENV, str(_DEFAULT_RETRY_ATTEMPTS)))
            last_exception: typing.Optional[Exception] = None

            for attempt in range(1, max_attempts + 1):
                try:
                    self.connection = _ConnectionHolder.create(
                        ApResolver.get_random_accesspoint(), self.__inner.conf)
                    self.connect()
                    self.__authenticate_partial(
                        Authentication.LoginCredentials(
                            typ=self.__ap_welcome.reusable_auth_credentials_type,
                            username=self.__ap_welcome.canonical_username,
                            auth_data=self.__ap_welcome.reusable_auth_credentials,
                        ),
                        True,
                    )
                    self.logger.info("Re-authenticated as {}!".format(
                        self.__ap_welcome.canonical_username))
                    return
                except Exception as ex:
                    last_exception = ex
                    if self.connection is not None:
                        try:
                            self.connection.close()
                        except Exception:
                            pass
                        self.connection = None
                    if attempt < max_attempts:
                        delay = min(2 ** attempt, _BACKOFF_CAP_SECONDS)
                        self.logger.warning(
                            "Reconnection attempt %d/%d failed: %s. "
                            "Retrying in %ds...",
                            attempt, max_attempts, ex, delay,
                        )
                        time.sleep(delay)

            self.logger.critical(
                "Failed to reconnect after %d attempts: %s",
                max_attempts, last_exception,
            )
            if last_exception is not None:
                raise last_exception
            raise RuntimeError("Failed to reconnect")
        finally:
            self.__reconnect_lock.release()

    def reconnecting(self) -> bool:
        """ """
        return not self.__closing and not self.__closed and self.connection is None

    def search(self) -> SearchManager:
        """ """
        self.__wait_auth_lock()
        if self.__search is None:
            raise RuntimeError("Session isn't authenticated!")
        return self.__search

    def send(self, cmd: bytes, payload: bytes):
        """Send data to socket using send_unchecked

        :param cmd: Command
        :param payload: Payload
        :param cmd: bytes:
        :param payload: bytes:

        """
        if self.__closing and self.connection is None:
            self.logger.debug("Connection was broken while closing.")
            return
        if self.__closed:
            raise RuntimeError("Session is closed!")
        with self.__auth_lock:
            while self.cipher_pair is None or self.__auth_lock_bool:
                self.__auth_lock.wait()
            self.__send_unchecked(cmd, payload)

    def tokens(self) -> TokenProvider:
        """ """
        self.__wait_auth_lock()
        if self.__token_provider is None:
            raise RuntimeError("Session isn't authenticated!")
        return self.__token_provider

    def username(self):
        """ """
        return self.__ap_welcome.canonical_username

    def stored(self):
        """ """
        return self.__stored_str

    def __authenticate_partial(self,
                               credential: Authentication.LoginCredentials,
                               remove_lock: bool) -> None:
        """
        Login to Spotify
        Args:
            credential: Spotify account login information
        """
        assert self.__inner.conf is not None, "Configuration not set"
        assert self.connection is not None, "Connection not established"
        if self.cipher_pair is None:
            raise RuntimeError("Connection not established!")
        client_response_encrypted_proto = Authentication.ClientResponseEncrypted(
            login_credentials=credential,
            system_info=Authentication.SystemInfo(
                os=Authentication.Os.OS_UNKNOWN,
                cpu_family=Authentication.CpuFamily.CPU_UNKNOWN,
                system_information_string=Version.system_info_string(),
                device_id=self.__inner.device_id,
            ),
            version_string=Version.version_string(),
        )
        self.__send_unchecked(
            Packet.Type.login,
            client_response_encrypted_proto.SerializeToString())
        packet = self.cipher_pair.receive_encoded(self.connection)
        if packet.is_cmd(Packet.Type.ap_welcome):
            self.__ap_welcome = Authentication.APWelcome()
            self.__ap_welcome.ParseFromString(packet.payload)
            self.__receiver = _Receiver(self)
            bytes0x0f = Random.get_random_bytes(0x14)
            self.__send_unchecked(Packet.Type.unknown_0x0f, bytes0x0f)
            preferred_locale = io.BytesIO()
            preferred_locale.write(b"\x00\x00\x10\x00\x02preferred-locale" +
                                   self.__inner.preferred_locale.encode())
            preferred_locale.seek(0)
            self.__send_unchecked(Packet.Type.preferred_locale,
                                  preferred_locale.read())
            if remove_lock:
                with self.__auth_lock:
                    self.__auth_lock_bool = False
                    self.__auth_lock.notify_all()
            if self.__inner.conf.store_credentials:
                reusable = self.__ap_welcome.reusable_auth_credentials
                reusable_type = Authentication.AuthenticationType.Name(
                    self.__ap_welcome.reusable_auth_credentials_type)
                if self.__inner.conf.stored_credentials_file is None:
                    raise TypeError(
                        "The file path to be saved is not specified")
                self.__stored_str = base64.b64encode(
                    json.dumps({
                        "username":
                        self.__ap_welcome.canonical_username,
                        "credentials":
                        base64.b64encode(reusable).decode(),
                        "type":
                        reusable_type,
                    }).encode()).decode()
                try:
                    with open(self.__inner.conf.stored_credentials_file, "w") as f:
                        json.dump(
                            {
                                "username": self.__ap_welcome.canonical_username,
                                "credentials": base64.b64encode(reusable).decode(),
                                "type": reusable_type,
                            },
                            f,
                        )
                except OSError as ex:
                    self.logger.warning(
                        "Failed to save credentials to file: {}".format(ex))

        elif packet.is_cmd(Packet.Type.auth_failure):
            ap_login_failed = Keyexchange.APLoginFailed()
            ap_login_failed.ParseFromString(packet.payload)
            self.close()
            raise _SpotifyAuthenticationException(ap_login_failed)
        else:
            raise RuntimeError("Unknown CMD 0x" + packet.cmd.hex())

    def __send_unchecked(self, cmd: bytes, payload: bytes) -> None:
        assert self.cipher_pair is not None, "Cipher pair not initialized"
        assert self.connection is not None, "Connection not established"
        self.cipher_pair.send_encoded(self.connection, cmd, payload)

    def __wait_auth_lock(self) -> None:
        if self.__closing and self.connection is None:
            self.logger.debug("Connection was broken while closing.")
            return
        if self.__closed:
            raise RuntimeError("Session is closed!")
        with self.__auth_lock:
            while self.cipher_pair is None or self.__auth_lock_bool:
                self.__auth_lock.wait()
