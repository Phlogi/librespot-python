# Migration Guide: `librespot.core` Module Split

## What changed

The monolithic `librespot/core.py` module has been split into focused modules:

| Old import (`librespot.core`)        | New import                          |
| ------------------------------------ | ----------------------------------- |
| `from librespot.core import Session` | `from librespot.session import Session` |
| `from librespot.core import ApiClient` | `from librespot.api import ApiClient` |
| `from librespot.core import ApResolver` | `from librespot.apresolver import ApResolver` |
| `from librespot.core import DealerClient` | `from librespot.dealer_client import DealerClient` |
| `from librespot.core import EventService` | `from librespot.event_service import EventService` |
| `from librespot.core import MessageType` | `from librespot.core_types import MessageType` |
| `from librespot.core import SearchManager` | `from librespot.search import SearchManager` |
| `from librespot.core import TokenProvider` | `from librespot.token_provider import TokenProvider` |

## Compatibility

All existing `from librespot.core import ...` imports **continue to work** — `librespot/core.py` is now a compatibility facade that re-exports every public name from the new modules.

### `Session` nested classes

`Session.Builder`, `Session.Configuration`, `Session.Inner`, `Session.Receiver`, `Session.ConnectionHolder`, `Session.Accumulator`, and `Session.SpotifyAuthenticationException` remain accessible as `Session.Xxx` — they are now module-level classes within `librespot/session.py` with aliases on `Session`.

### `ApiClient.sendToUrl`

Renamed to `send_to_url` (snake_case). The old `sendToUrl` name remains as a compatibility alias.

## Timeline

- **Now**: Both old and new imports work. Use the new imports for new code.
- **Next minor version**: `librespot.core` re-exports will emit deprecation warnings.
- **Next major version**: `librespot.core` compatibility facade will be removed.
