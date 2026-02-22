"""Compatibility facade — re-exports all public names from their new modules.

Deprecated: import from the specific modules instead:
    from librespot.session import Session
    from librespot.api import ApiClient
    from librespot.apresolver import ApResolver
    from librespot.core_types import MessageType
    from librespot.dealer_client import DealerClient
    from librespot.event_service import EventService
    from librespot.search import SearchManager
    from librespot.token_provider import TokenProvider

These re-exports will be removed in a future version.
"""
from __future__ import annotations

# Deprecated: import from librespot.api instead
from librespot.api import ApiClient  # noqa: F401
# Deprecated: import from librespot.apresolver instead
from librespot.apresolver import ApResolver  # noqa: F401
# Deprecated: import from librespot.core_types instead
from librespot.core_types import MessageType  # noqa: F401
# Deprecated: import from librespot.dealer_client instead
from librespot.dealer_client import DealerClient  # noqa: F401
# Deprecated: import from librespot.event_service instead
from librespot.event_service import EventService  # noqa: F401
# Deprecated: import from librespot.search instead
from librespot.search import SearchManager  # noqa: F401
# Deprecated: import from librespot.session instead
from librespot.session import Session  # noqa: F401
# Deprecated: import from librespot.token_provider instead
from librespot.token_provider import TokenProvider  # noqa: F401
