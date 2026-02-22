from __future__ import annotations

import logging
import threading
import time
import typing

import requests
from requests.structures import CaseInsensitiveDict

from librespot.mercury import MercuryRequests

if typing.TYPE_CHECKING:
    from librespot.core import Session

from librespot.proto.spotify.login5.v3 import Login5_pb2 as Login5
from librespot.proto.spotify.login5.v3.credentials import Credentials_pb2 as Login5Credentials

_LOGIN5_URL = "https://login5.spotify.com/v3/login"
_HTTP_TIMEOUT_SECONDS = 10
_TOKEN_EXPIRE_THRESHOLD_SECONDS = 10


class TokenProvider:
    """ """
    logger = logging.getLogger("Librespot:TokenProvider")
    token_expire_threshold = _TOKEN_EXPIRE_THRESHOLD_SECONDS
    __session: Session
    __tokens: typing.List[StoredToken]

    def __init__(self, session: Session):
        self.__session = session
        self.__tokens = []
        self.__tokens_lock = threading.Lock()

    def find_token_with_all_scopes(
            self, scopes: typing.List[str]) -> typing.Union[StoredToken, None]:
        """

        :param scopes: typing.List[str]:

        """
        for token in self.__tokens:
            if token.has_scopes(scopes):
                return token
        return None

    def get(self, scope: str) -> str:
        """

        :param scope: str:

        """
        token = self.get_token(scope)
        if token is None:
            raise RuntimeError("Failed to get token for scope: {}".format(scope))
        return token.access_token

    def get_token(self, *scopes) -> typing.Optional[StoredToken]:
        """

        :param *scopes:

        """
        scopes = list(scopes)
        if len(scopes) == 0:
            raise RuntimeError("The token doesn't have any scope")

        with self.__tokens_lock:
            token = self.find_token_with_all_scopes(scopes)
            if token is not None:
                if token.expired():
                    self.__tokens.remove(token)
                    self.logger.debug("Login5 token expired, need to re-authenticate")
                else:
                    return token

        token = self.login5(scopes)
        if token is not None:
            with self.__tokens_lock:
                self.__tokens.append(token)
            self.logger.debug("Using Login5 access token for scopes: {}".format(scopes))
        return token

    def login5(self, scopes: typing.List[str]) -> typing.Union[StoredToken, None]:
        """Submit Login5 request for a fresh access token"""

        if self.__session.ap_welcome():
            login5_request = Login5.LoginRequest()
            login5_request.client_info.client_id = MercuryRequests.keymaster_client_id
            login5_request.client_info.device_id = self.__session.device_id()

            stored_cred = Login5Credentials.StoredCredential()
            stored_cred.username = self.__session.username()
            stored_cred.data = self.__session.ap_welcome().reusable_auth_credentials
            login5_request.stored_credential.CopyFrom(stored_cred)

            response = requests.post(
                _LOGIN5_URL,
                data=login5_request.SerializeToString(),
                headers=CaseInsensitiveDict({
                    "Content-Type": "application/x-protobuf",
                    "Accept": "application/x-protobuf"
                    }),
                timeout=_HTTP_TIMEOUT_SECONDS)

            if response.status_code == 200:
                login5_response = Login5.LoginResponse()
                login5_response.ParseFromString(response.content)

                if login5_response.HasField('ok'):
                    self.logger.info("Login5 authentication successful")
                    token = TokenProvider.StoredToken({
                        "expiresIn": login5_response.ok.access_token_expires_in, # approximately one hour
                        "accessToken": login5_response.ok.access_token,
                        "scope": scopes
                    })
                    return token
                else:
                    self.logger.warning("Login5 authentication failed: {}".format(login5_response.error))
            else:
                self.logger.warning("Login5 request failed with status: {}".format(response.status_code))
        else:
            self.logger.error("Login5 authentication failed: No APWelcome found")

    class StoredToken:
        """ """
        expires_in: int
        access_token: str
        scopes: typing.List[str]
        timestamp: int

        def __init__(self, obj):
            self.timestamp = int(time.time_ns() / 1000)
            self.expires_in = obj["expiresIn"]
            self.access_token = obj["accessToken"]
            self.scopes = obj["scope"]

        def expired(self) -> bool:
            """ """
            return self.timestamp + (self.expires_in - TokenProvider.
                                     token_expire_threshold) * 1000 * 1000 < int(
                                         time.time_ns() / 1000)

        def has_scope(self, scope: str) -> bool:
            """

            :param scope: str:

            """
            for s in self.scopes:
                if s == scope:
                    return True
            return False

        def has_scopes(self, sc: typing.List[str]) -> bool:
            """

            :param sc: typing.List[str]:

            """
            for s in sc:
                if not self.has_scope(s):
                    return False
            return True
