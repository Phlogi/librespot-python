from __future__ import annotations

import logging
import typing

import requests
from requests.structures import CaseInsensitiveDict

from librespot import Version
from librespot.apresolver import ApResolver
from librespot.mercury import MercuryRequests
from librespot.metadata import AlbumId
from librespot.metadata import ArtistId
from librespot.metadata import EpisodeId
from librespot.metadata import PlaylistId
from librespot.metadata import ShowId
from librespot.metadata import TrackId
from librespot.proto import ClientToken_pb2 as ClientToken
from librespot.proto import Connect_pb2 as Connect
from librespot.proto import Connectivity_pb2 as Connectivity
from librespot.proto import Metadata_pb2 as Metadata
from librespot.proto import Playlist4External_pb2 as Playlist4External
from librespot.proto.ExtendedMetadata_pb2 import EntityRequest, BatchedEntityRequest, ExtensionQuery, BatchedExtensionResponse
from librespot.proto.ExtensionKind_pb2 import ExtensionKind
from librespot.structure import Closeable

if typing.TYPE_CHECKING:
    from librespot.core import Session

_CLIENTTOKEN_URL = "https://clienttoken.spotify.com/v1/clienttoken"
_HTTP_TIMEOUT_SECONDS = 10


class ApiClient(Closeable):
    """ """
    logger = logging.getLogger("Librespot:ApiClient")
    __base_url: str
    __client_token_str: typing.Optional[str] = None
    __session: Session

    def __init__(self, session: Session):
        self.__session = session
        self.__base_url = "https://{}".format(ApResolver.get_random_spclient())

    def build_request(
        self,
        method: str,
        suffix: str,
        headers: typing.Union[None, CaseInsensitiveDict[str]],
        body: typing.Union[None, bytes],
        url: typing.Union[None, str],
    ) -> requests.PreparedRequest:
        """

        :param method: str:
        :param suffix: str:
        :param headers: typing.Union[None:
        :param CaseInsensitiveDict[str:
        :param str]]:
        :param body: typing.Union[None:
        :param bytes]:
        :param url: typing.Union[None:
        :param str]:

        """
        if self.__client_token_str is None:
            resp = self.__client_token()
            self.__client_token_str = resp.granted_token.token
            self.logger.debug("Updated client token")

        if url is None:
            url = self.__base_url + suffix
        else:
            url = url + suffix

        if headers is None:
            headers = CaseInsensitiveDict()
        headers["Authorization"] = "Bearer {}".format(
            self.__session.tokens().get("playlist-read"))
        if self.__client_token_str is not None:
            headers["client-token"] = self.__client_token_str

        request = requests.Request(method, url, headers=headers, data=body)

        return request.prepare()

    def send(
        self,
        method: str,
        suffix: str,
        headers: typing.Union[None, CaseInsensitiveDict[str]],
        body: typing.Union[None, bytes],
    ) -> requests.Response:
        """

        :param method: str:
        :param suffix: str:
        :param headers: typing.Union[None:
        :param CaseInsensitiveDict[str:
        :param str]]:
        :param body: typing.Union[None:
        :param bytes]:

        """
        response = self.__session.client().send(
            self.build_request(method, suffix, headers, body, None))
        return response

    def send_to_url(
        self,
        method: str,
        url: str,
        suffix: str,
        headers: typing.Union[None, CaseInsensitiveDict[str]],
        body: typing.Union[None, bytes],
    ) -> requests.Response:
        """

        :param method: str:
        :param url: str:
        :param suffix: str:
        :param headers: typing.Union[None:
        :param CaseInsensitiveDict[str:
        :param str]]:
        :param body: typing.Union[None:
        :param bytes]:

        """
        response = self.__session.client().send(
            self.build_request(method, suffix, headers, body, url))
        return response

    # Deprecated: use send_to_url instead
    sendToUrl = send_to_url

    def put_connect_state(self, connection_id: str,
                          proto: Connect.PutStateRequest) -> None:
        """

        :param connection_id: str:
        :param proto: Connect.PutStateRequest:

        """
        response = self.send(
            "PUT",
            "/connect-state/v1/devices/{}".format(self.__session.device_id()),
            CaseInsensitiveDict({
                "Content-Type": "application/protobuf",
                "X-Spotify-Connection-Id": connection_id,
            }),
            proto.SerializeToString(),
        )
        if response.status_code == 413:
            self.logger.warning(
                "PUT state payload is too large: {} bytes uncompressed.".
                format(len(proto.SerializeToString())))
        elif response.status_code != 200:
            self.logger.warning("PUT state returned {}. headers: {}".format(
                response.status_code, response.headers))

    def get_ext_metadata(self, extension_kind: ExtensionKind, uri: str):
        headers = CaseInsensitiveDict({"content-type": "application/x-protobuf"})
        req = EntityRequest(entity_uri=uri, query=[ExtensionQuery(extension_kind=extension_kind),])

        response = self.send("POST", "/extended-metadata/v0/extended-metadata",
                             headers, BatchedEntityRequest(entity_request=[req,]).SerializeToString())
        ApiClient.StatusCodeException.check_status(response)

        body = response.content
        if body is None:
            raise ConnectionError("Extended Metadata request failed: No response body")

        proto = BatchedExtensionResponse()
        proto.ParseFromString(body)
        entityextd = proto.extended_metadata.pop().extension_data.pop()
        if entityextd.header.status_code != 200:
            raise ConnectionError("Extended Metadata request failed: Status code {}".format(entityextd.header.status_code))
        mdb: bytes = entityextd.extension_data.value
        return mdb

    def get_metadata_4_track(self, track: TrackId) -> Metadata.Track:
        """

        :param track: TrackId:

        """
        mdb = self.get_ext_metadata(ExtensionKind.TRACK_V4, track.to_spotify_uri())
        md = Metadata.Track()
        md.ParseFromString(mdb)
        return md

    def get_metadata_4_episode(self, episode: EpisodeId) -> Metadata.Episode:
        """

        :param episode: EpisodeId:

        """
        mdb = self.get_ext_metadata(ExtensionKind.EPISODE_V4, episode.to_spotify_uri())
        md = Metadata.Episode()
        md.ParseFromString(mdb)
        return md

    def get_metadata_4_album(self, album: AlbumId) -> Metadata.Album:
        """

        :param album: AlbumId:

        """
        mdb = self.get_ext_metadata(ExtensionKind.ALBUM_V4, album.to_spotify_uri())
        md = Metadata.Album()
        md.ParseFromString(mdb)
        return md

    def get_metadata_4_artist(self, artist: ArtistId) -> Metadata.Artist:
        """

        :param artist: ArtistId:

        """
        mdb = self.get_ext_metadata(ExtensionKind.ARTIST_V4, artist.to_spotify_uri())
        md = Metadata.Artist()
        md.ParseFromString(mdb)
        return md

    def get_metadata_4_show(self, show: ShowId) -> Metadata.Show:
        """

        :param show: ShowId:

        """
        mdb = self.get_ext_metadata(ExtensionKind.SHOW_V4, show.to_spotify_uri())
        md = Metadata.Show()
        md.ParseFromString(mdb)
        return md

    def get_playlist(self,
                     _id: PlaylistId) -> Playlist4External.SelectedListContent:
        """

        :param _id: PlaylistId:

        """
        response = self.send("GET",
                             "/playlist/v2/playlist/{}".format(_id.id()), None,
                             None)
        ApiClient.StatusCodeException.check_status(response)
        body = response.content
        if body is None:
            raise IOError()
        proto = Playlist4External.SelectedListContent()
        proto.ParseFromString(body)
        return proto

    def set_client_token(self, client_token):
        """

        :param client_token:

        """
        self.__client_token_str = client_token

    def __client_token(self):
        proto_req = ClientToken.ClientTokenRequest(
            request_type=ClientToken.ClientTokenRequestType.
            REQUEST_CLIENT_DATA_REQUEST,
            client_data=ClientToken.ClientDataRequest(
                client_id=MercuryRequests.keymaster_client_id,
                client_version=Version.version_name,
                connectivity_sdk_data=Connectivity.ConnectivitySdkData(
                    device_id=self.__session.device_id(),
                    platform_specific_data=Connectivity.PlatformSpecificData(
                        windows=Connectivity.NativeWindowsData(
                            something1=10,
                            something3=21370,
                            something4=2,
                            something6=9,
                            something7=332,
                            something8=33404,
                            something10=True,
                        ), ),
                ),
            ),
        )

        resp = requests.post(
            _CLIENTTOKEN_URL,
            proto_req.SerializeToString(),
            headers=CaseInsensitiveDict({
                "Accept": "application/x-protobuf",
                "Content-Encoding": "",
            }),
            timeout=_HTTP_TIMEOUT_SECONDS,
        )

        ApiClient.StatusCodeException.check_status(resp)

        proto_resp = ClientToken.ClientTokenResponse()
        proto_resp.ParseFromString(resp.content)
        return proto_resp

    class StatusCodeException(IOError):
        """ """
        code: int

        def __init__(self, response: requests.Response):
            super().__init__(response.status_code)
            self.code = response.status_code

        @staticmethod
        def check_status(response: requests.Response) -> None:
            """

            :param response: requests.Response:

            """
            if response.status_code != 200:
                raise ApiClient.StatusCodeException(response)
