from __future__ import annotations

import json
import typing
import urllib.parse

from librespot.mercury import RawMercuryRequest

if typing.TYPE_CHECKING:
    from librespot.core import Session

_SEARCH_BASE_URI = "hm://searchview/km/v4/search/"


class SearchManager:
    """ """
    base_url = _SEARCH_BASE_URI
    __session: Session

    def __init__(self, session: Session):
        self.__session = session

    def request(self, request: SearchRequest) -> typing.Any:
        """

        :param request: SearchRequest:

        """
        if request.get_username() == "":
            request.set_username(self.__session.username())
        if request.get_country() == "":
            request.set_country(self.__session.country_code)
        if request.get_locale() == "":
            request.set_locale(self.__session.preferred_locale())
        response = self.__session.mercury().send_sync(
            RawMercuryRequest.new_builder().set_method("GET").set_uri(
                request.build_url()).build())
        if response.status_code != 200:
            raise SearchManager.SearchException(response.status_code)
        return json.loads(response.payload)

    class SearchException(Exception):
        """ """

        def __init__(self, status_code: int):
            super().__init__("Search failed with code {}.".format(status_code))

    class SearchRequest:
        """ """
        query: typing.Final[str]
        __catalogue = ""
        __country = ""
        __image_size = ""
        __limit = 10
        __locale = ""
        __username = ""

        def __init__(self, query: str):
            self.query = query
            if query == "":
                raise TypeError

        def build_url(self) -> str:
            """ """
            url = SearchManager.base_url + urllib.parse.quote(self.query)
            url += "?entityVersion=2"
            url += "&catalogue=" + urllib.parse.quote(self.__catalogue)
            url += "&country=" + urllib.parse.quote(self.__country)
            url += "&imageSize=" + urllib.parse.quote(self.__image_size)
            url += "&limit=" + str(self.__limit)
            url += "&locale=" + urllib.parse.quote(self.__locale)
            url += "&username=" + urllib.parse.quote(self.__username)
            return url

        def get_catalogue(self) -> str:
            """ """
            return self.__catalogue

        def get_country(self) -> str:
            """ """
            return self.__country

        def get_image_size(self) -> str:
            """ """
            return self.__image_size

        def get_limit(self) -> int:
            """ """
            return self.__limit

        def get_locale(self) -> str:
            """ """
            return self.__locale

        def get_username(self) -> str:
            """ """
            return self.__username

        def set_catalogue(self, catalogue: str) -> SearchManager.SearchRequest:
            """

            :param catalogue: str:

            """
            self.__catalogue = catalogue
            return self

        def set_country(self, country: str) -> SearchManager.SearchRequest:
            """

            :param country: str:

            """
            self.__country = country
            return self

        def set_image_size(self,
                           image_size: str) -> SearchManager.SearchRequest:
            """

            :param image_size: str:

            """
            self.__image_size = image_size
            return self

        def set_limit(self, limit: int) -> SearchManager.SearchRequest:
            """

            :param limit: int:

            """
            self.__limit = limit
            return self

        def set_locale(self, locale: str) -> SearchManager.SearchRequest:
            """

            :param locale: str:

            """
            self.__locale = locale
            return self

        def set_username(self, username: str) -> SearchManager.SearchRequest:
            """

            :param username: str:

            """
            self.__username = username
            return self
