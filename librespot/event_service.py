from __future__ import annotations

import concurrent.futures
import enum
import io
import logging
import time
import typing

from librespot.mercury import RawMercuryRequest
from librespot.structure import Closeable

if typing.TYPE_CHECKING:
    from librespot.core import Session

_EVENT_SERVICE_URI = "hm://event-service/v1/events"


class EventService(Closeable):
    """ """
    logger = logging.getLogger("Librespot:EventService")
    __session: Session
    __worker: concurrent.futures.ThreadPoolExecutor

    def __init__(self, session: Session):
        self.__session = session
        self.__worker = concurrent.futures.ThreadPoolExecutor()

    def __worker_callback(self, event_builder: EventBuilder):
        try:
            body = event_builder.to_array()
            resp = self.__session.mercury().send_sync(
                RawMercuryRequest.Builder().set_uri(
                    _EVENT_SERVICE_URI).set_method("POST").
                add_user_field("Accept-Language", "en").add_user_field(
                    "X-ClientTimeStamp",
                    str(int(time.time() * 1000))).add_payload_part(body).build())
            self.logger.debug("Event sent. body: {}, result: {}".format(
                body, resp.status_code))
        except IOError as ex:
            self.logger.error("Failed sending event: {} {}".format(
                event_builder, ex))

    def send_event(self, event_or_builder: typing.Union[GenericEvent,
                                                        EventBuilder]):
        """

        :param event_or_builder: typing.Union[GenericEvent:
        :param EventBuilder]:

        """
        if type(event_or_builder) is EventService.GenericEvent:
            builder = event_or_builder.build()
        elif type(event_or_builder) is EventService.EventBuilder:
            builder = event_or_builder
        else:
            raise TypeError()
        self.__worker.submit(lambda: self.__worker_callback(builder))

    def language(self, lang: str):
        """

        :param lang: str:

        """
        event = EventService.EventBuilder(EventService.Type.LANGUAGE)
        event.append(s=lang)
        self.send_event(event)

    def close(self):
        """ """
        self.__worker.shutdown()

    class Type(enum.Enum):
        """ """
        LANGUAGE = ("812", 1)
        FETCHED_FILE_ID = ("274", 3)
        NEW_SESSION_ID = ("557", 3)
        NEW_PLAYBACK_ID = ("558", 1)
        TRACK_PLAYED = ("372", 1)
        TRACK_TRANSITION = ("12", 37)
        CDN_REQUEST = ("10", 20)

        eventId: str
        unknown: int

        def __init__(self, event_id: str, unknown: int):
            self.eventId = event_id
            self.unknown = unknown

    class GenericEvent:
        """ """

        def build(self) -> EventService.EventBuilder:
            """ """
            raise NotImplementedError

    class EventBuilder:
        """ """
        body: io.BytesIO

        def __init__(self, event_type: EventService.Type):
            self.body = io.BytesIO()
            self.append_no_delimiter(event_type.value[0])
            self.append(event_type.value[1])

        def append_no_delimiter(self, s: typing.Optional[str] = None) -> None:
            """

            :param s: str:  (Default value = None)

            """
            if s is None:
                s = ""
            self.body.write(s.encode())

        def append(self,
                   c: typing.Optional[int] = None,
                   s: typing.Optional[str] = None) -> EventService.EventBuilder:
            """

            :param c: int:  (Default value = None)
            :param s: str:  (Default value = None)

            """
            if (c is None and s is None) or (c is not None and s is not None):
                raise TypeError()
            if c is not None:
                self.body.write(b"\x09")
                self.body.write(bytes([c]))
                return self
            assert s is not None
            self.body.write(b"\x09")
            self.append_no_delimiter(s)
            return self

        def to_array(self) -> bytes:
            """ """
            pos = self.body.tell()
            self.body.seek(0)
            data = self.body.read()
            self.body.seek(pos)
            return data
