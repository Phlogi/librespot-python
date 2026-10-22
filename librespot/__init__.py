from __future__ import annotations

import platform

from librespot._version import __version__
from librespot.crypto import DiffieHellman
from librespot.proto.Keyexchange_pb2 import BuildInfo, Platform, Product, ProductFlags
from librespot.structure import Closeable, Runnable

__all__ = [
    "Closeable",
    "DiffieHellman",
    "Product",
    "Runnable",
    "Version",
    "__version__",
]


class Version:
    version_name = __version__

    @staticmethod
    def platform() -> Platform:
        if platform.system() == "Windows":
            return Platform.PLATFORM_WIN32_X86
        if platform.system() == "Darwin":
            return Platform.PLATFORM_OSX_X86
        return Platform.PLATFORM_LINUX_X86

    @staticmethod
    def version_string() -> str:
        return f"librespot-python {Version.version_name}"

    @staticmethod
    def system_info_string() -> str:
        return f"{Version.version_string()}; Python {platform.python_version()}; {platform.system()}"

    @staticmethod
    def standard_build_info() -> BuildInfo:
        return BuildInfo(
            product=Product.PRODUCT_CLIENT,
            product_flags=[ProductFlags.PRODUCT_FLAG_NONE],
            platform=Version.platform(),
            version=117300517,
        )
