# Contributing

## What this library is

- A headless Spotify client, allowing you to **authenticate and retrieve a decrypted audio stream for any track**.
- *Not* a standalone audio player: the **provided stream must be piped to another application** (like `ffplay`) or handled by a server to be played.

## Environment setup

### Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

### Install dependencies

```sh
uv sync --extra dev
```

Run the checks with:

```sh
uv run pytest
uv run ruff check
uv run pyright
```

### Protocol buffer generation

> This is **only needed if you change any `.proto` serialization schema files**.

`grpcio-tools` is pinned in the `proto` extra and bundles the matching `protoc`.
The generator emits both the runtime `*_pb2.py` modules and the `*_pb2.pyi` type
stubs, normalises protoc's snake_case module names to the CamelCase names used by
this package, and rewrites imports to absolute `librespot.proto.*` paths.

After changing a `.proto` file, regenerate everything with:

```bash
uv run --extra proto python scripts/generate_proto_stubs.py
uv run --extra proto python scripts/generate_proto_stubs.py --check
```

Commit the source `.proto` and the regenerated Python files **together** so they
can be compared easily. Regenerate with the pinned `grpcio-tools` version; a
different `protoc` produces different output and makes `--check` fail.

## Testing

### Unit tests

```bash
uv run pytest -m "not premium" --ignore=tests/test_e2e.py
```

### Spotify end-to-end tests

`tests/test_e2e.py` logs in with stored credentials and exercises search,
metadata, and (with `-m premium`) audio streaming. It skips itself when no
credentials file is available.

```bash
SPOTIFY_CREDENTIALS_FILE=/path/to/credentials.json uv run pytest tests/test_e2e.py -m "not premium"
SPOTIFY_CREDENTIALS_FILE=/path/to/credentials.json uv run pytest tests/test_e2e.py -m premium
```

The account can return `429` for search calls; that is Spotify rate limiting,
not a librespot failure.

### Full-stack zotify download

This is the strongest integration check: latest `better-zotify` driving the
local librespot checkout, downloading a real track.

```bash
uv venv --python 3.14 /tmp/zotify-it/venv
uv pip install --python /tmp/zotify-it/venv/bin/python .
uv pip install --python /tmp/zotify-it/venv/bin/python --no-deps \
  "zotify @ git+ssh://git@github.com/Phlogi/better-zotify.git@throttle"
uv pip install --python /tmp/zotify-it/venv/bin/python \
  ffmpy music_tag Pillow pkce 'protobuf>=6.33.5,<7' pwinput \
  'requests>=2.32.3' 'tabulate[widechars]' tqdm 'urllib3>=2'

mkdir -p /tmp/zotify-it/creds /tmp/zotify-it/out
cp /path/to/credentials.json /tmp/zotify-it/creds/credentials.json

SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... SAVE_CREDENTIALS=False \
  /tmp/zotify-it/venv/bin/zotify --no-splash \
  --creds /tmp/zotify-it/creds --root-path /tmp/zotify-it/out \
  'spotify:track:4cOdK2wGLETKBW3PvgPWqT'

ffprobe -v error -show_entries format=format_name,duration \
  /tmp/zotify-it/out/*/*/*.ogg
```

`--no-deps` keeps zotify from re-installing librespot from git; the runtime
dependencies are installed explicitly in the next step because current zotify
requires Spotify OAuth client credentials in addition to `credentials.json`.

## Architecture

The main components are:

- **`Session` class** *(entrypoint)*

    - `Session.Builder` is used to configure and create a session, via one of:

        - username/password
        - stored credentials
        - OAuth
 
    - An active session is **required** for all other operations.

- **`ApiClient` class**

    - A high-level client for making standard HTTPS requests to Spotify's Web API endpoints (e.g., `https://spclient.wg.spotify.com`).
    - Accessed via `session.api()`, it provides convenient methods like `get_metadata_4_track()` and handles client tokens automatically.

- **`MercuryClient` class**

    - The low-level client for Spotify's proprietary `mercury` protocol, which uses `hm://` URIs.
    - Accessed via `session.mercury()`, it handles sending and receiving messages over the main session connection for metadata lookups and subscriptions that are not available via the standard Web API.

- **`DealerClient` class**

    - Manages the persistent WebSocket (`wss://`) connection to Spotify's `dealer` service.
    - Accessed via `session.dealer()`, it listens for and dispatches real-time, asynchronous JSON-based events, such as remote player state changes or notifications from other connected devices.

- **`Session.Receiver` thread**

    - Spawned after authentication to read every encrypted packet coming from the access point.
    - Routes decoded commands to subsystems (`MercuryClient`, `AudioKeyManager`, `ChannelManager`, etc.) and responds to keep-alive pings to hold the session open.

- **Metadata types**

    - The `librespot.metadata` module provides typed identifiers (`TrackId`, `AlbumId`, `PlaylistId`, `EpisodeId`, etc.) used to reference Spotify content throughout the API.
    - They are constructed from Spotify identifiers, typically using one of the following methods:

        - `from_uri()`: For all ID types.
        - `from_base62()`: For most ID types (e.g., tracks, albums, artists).

- **`PlayableContentFeeder` class**

    - Retrieves audio streams; is accessed via `session.content_feeder()`.
    - `load(playable_id, audio_quality_picker, preload, halt_listener)`:

        - Accepts:

            - a `TrackId` or `EpisodeId` (any `PlayableId`)
            - an `AudioQualityPicker`
            - a `preload` flag
            - an optional `HaltListener` callback (pass `None` if unneeded).

        - Returns a `LoadedStream` that contains the decrypted stream together with:

            - track/episode metadata
            - normalization data
            - transfer metrics

- **`audio` module**
    
    - Contains tools for format selection, quality management, streaming, and decryption.
    - `VorbisOnlyAudioQuality` and `LosslessOnlyAudioQuality` choose the best matching `Metadata.AudioFile` for a preferred container/quality combination.
    - `CdnManager` acquires and refreshes signed CDN URLs, feeding a `Streamer` that decrypts chunks on the fly while staying seekable.

- **`AudioKeyManager` and `ChannelManager`**

    - Handle the low-level transport for protected audio: `AudioKeyManager` requests AES keys, and `ChannelManager` can stream encrypted chunks directly from the access point when CDN delivery is unavailable.
    - Both are driven transparently by `PlayableContentFeeder`/`CdnManager`, so callers only interact with the decrypted `LoadedStream`.

- **`EventService` class**

    - Asynchronous publisher that emits telemetry (e.g., fetch metrics, playback events) to `hm://event-service/v1/events` via Mercury.
    - Accessible through `session.event_service()` for consumers that need to forward custom events.

- **`TokenProvider` class**

    - Caches Login5 access tokens per scope, refreshing them proactively as they near expiry.
    - Used by `ApiClient` to supply the correct `Authorization` headers for Spotify Web API calls.

- **`SearchManager` class**

    - High-level wrapper around `hm://searchview/km/v4/search/...` requests sent over Mercury.
    - Fills in username, locale, and country defaults from the current session before dispatching the call.

- **OAuth tokens for Spotify Web API**

    - Can be obtained via `session.tokens().get(scope)`
    - Enable authenticated API calls for operations like search, playlist management, and user data access
