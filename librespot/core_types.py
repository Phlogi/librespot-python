from __future__ import annotations

import enum


class MessageType(enum.Enum):
    """ """
    MESSAGE = "message"
    PING = "ping"
    PONG = "pong"
    REQUEST = "request"

    @staticmethod
    def parse(_typ: str):
        """

        :param _typ: str:

        """
        if _typ == MessageType.MESSAGE.value:
            return MessageType.MESSAGE
        if _typ == MessageType.PING.value:
            return MessageType.PING
        if _typ == MessageType.PONG.value:
            return MessageType.PONG
        if _typ == MessageType.REQUEST.value:
            return MessageType.REQUEST
        raise TypeError("Unknown MessageType: {}".format(_typ))
