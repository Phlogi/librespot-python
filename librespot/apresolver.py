from __future__ import annotations

import json
import logging
import random
import time
import typing

import requests

_APRESOLVE_URL = "https://apresolve.spotify.com/"
_HTTP_TIMEOUT_SECONDS = 10
_AP_RESOLVE_MAX_RETRIES = 3
_AP_RESOLVE_RETRY_DELAY = 1

_logger = logging.getLogger("Librespot:ApResolver")


class ApResolver:
    """ """
    base_url = _APRESOLVE_URL

    @staticmethod
    def request(service_type: str) -> typing.Any:
        """Gets the specified ApResolve

        :param service_type: str:
        :returns: The resulting object will be returned

        """
        last_exception: typing.Optional[Exception] = None
        for attempt in range(1, _AP_RESOLVE_MAX_RETRIES + 1):
            try:
                response = requests.get(
                    "{}?type={}".format(ApResolver.base_url, service_type),
                    timeout=_HTTP_TIMEOUT_SECONDS,
                )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"ApResolve request failed with status {response.status_code}"
                    )
                return response.json()
            except (requests.RequestException, json.JSONDecodeError, RuntimeError) as ex:
                last_exception = ex
                if attempt < _AP_RESOLVE_MAX_RETRIES:
                    _logger.warning(
                        "ApResolve attempt %d/%d failed: %s. Retrying in %ds...",
                        attempt, _AP_RESOLVE_MAX_RETRIES, ex, _AP_RESOLVE_RETRY_DELAY,
                    )
                    time.sleep(_AP_RESOLVE_RETRY_DELAY)
        raise RuntimeError(
            f"ApResolve failed after {_AP_RESOLVE_MAX_RETRIES} attempts: {last_exception}"
        ) from last_exception

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
