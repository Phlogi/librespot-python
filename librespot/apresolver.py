from __future__ import annotations

import random
import typing

import requests

_APRESOLVE_URL = "https://apresolve.spotify.com/"
_HTTP_TIMEOUT_SECONDS = 10


class ApResolver:
    """ """
    base_url = _APRESOLVE_URL

    @staticmethod
    def request(service_type: str) -> typing.Any:
        """Gets the specified ApResolve

        :param service_type: str:
        :returns: The resulting object will be returned

        """
        response = requests.get("{}?type={}".format(ApResolver.base_url,
                                                    service_type),
                                timeout=_HTTP_TIMEOUT_SECONDS)
        if response.status_code != 200:
            raise RuntimeError(
                f"ApResolve request failed with status {response.status_code}: {response.content}"
            )
        return response.json()

    @staticmethod
    def get_random_of(service_type: str) -> str:
        """Gets the specified random ApResolve url

        :param service_type: str:
        :returns: A random ApResolve url will be returned

        """
        pool = ApResolver.request(service_type)
        urls = pool.get(service_type)
        if urls is None or len(urls) == 0:
            raise RuntimeError("No ApResolve url available")
        return random.choice(urls)

    @staticmethod
    def get_random_dealer() -> str:
        """Get dealer endpoint url


        :returns: dealer endpoint url

        """
        return ApResolver.get_random_of("dealer")

    @staticmethod
    def get_random_spclient() -> str:
        """Get spclient endpoint url


        :returns: spclient endpoint url

        """
        return ApResolver.get_random_of("spclient")

    @staticmethod
    def get_random_accesspoint() -> str:
        """Get accesspoint endpoint url


        :returns: accesspoint endpoint url

        """
        return ApResolver.get_random_of("accesspoint")
