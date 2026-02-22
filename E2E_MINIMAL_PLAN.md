# Functional End-to-End Test Suite for librespot-python

## Context
Create a focused e2e test suite that exercises the library from an **end-user perspective** — authenticate, search, fetch metadata, stream audio. No low-level protocol tests, no unit tests for ID classes or packet types. Just: "can I log in, find music, and download it?"

## Structure

```
tests/
    conftest.py          # session fixture, constants, helpers
    test_e2e.py          # all functional tests in one file
pyproject.toml           # pytest config
```

## Setup

### `pyproject.toml`
```toml
[tool.pytest.ini_options]
markers = [
    "premium: requires Spotify Premium",
]
```

### `conftest.py`
- **`session` fixture** (scope=`session`): auth via `SPOTIFY_USERNAME` + `SPOTIFY_PASSWORD` env vars using `Session.Builder().user_pass(...).create()`. Teardown calls `session.close()`. Skip collection if env vars missing.
- **`conf` fixture**: `Session.Configuration` with `store_credentials=False`, temp cache dir.
- **Constants**: well-known Spotify content URIs (Bohemian Rhapsody track, Queen artist, A Kind of Magic album, Top 50 Global playlist, a podcast episode+show).

---

## Test Plan: `test_e2e.py`

### Authentication & Session (3 tests)

| # | Test | What it does | Key assertions |
|---|------|-------------|----------------|
| 1 | `test_login_and_session_valid` | Log in with user/pass, check session is usable | `session.is_valid()`, `session.username()` non-empty, `session.country_code` is 2-letter string |
| 2 | `test_login_with_stored_credentials` | Get stored creds string from session, create a new session from it | New session is valid, same username |
| 3 | `test_get_access_token` | Request an OAuth token via `session.tokens().get("playlist-read")` | Returns non-empty string, token object not expired |

### Search (2 tests)

| # | Test | What it does | Key assertions |
|---|------|-------------|----------------|
| 4 | `test_search_track` | Search "Bohemian Rhapsody" | Returns dict with `"tracks"` key, hits list non-empty, first hit name contains "Bohemian Rhapsody" |
| 5 | `test_search_artist` | Search "Queen" | Returns dict with `"artists"` key, hits contain "Queen" |

### Metadata Retrieval (6 tests)

| # | Test | What it does | Key assertions |
|---|------|-------------|----------------|
| 6 | `test_fetch_track_metadata` | `api().get_metadata_4_track(TrackId)` for Bohemian Rhapsody | `track.name == "Bohemian Rhapsody"`, `track.artist[0].name == "Queen"`, `track.duration > 0`, `len(track.file) > 0` |
| 7 | `test_fetch_album_metadata` | `api().get_metadata_4_album(AlbumId)` for A Kind of Magic | `album.name` non-empty, has artist "Queen", has discs with tracks |
| 8 | `test_fetch_artist_metadata` | `api().get_metadata_4_artist(ArtistId)` for Queen | `artist.name == "Queen"`, has top tracks |
| 9 | `test_fetch_episode_metadata` | `api().get_metadata_4_episode(EpisodeId)` | `episode.name` non-empty, `episode.duration > 0` |
| 10 | `test_fetch_show_metadata` | `api().get_metadata_4_show(ShowId)` | `show.name` non-empty |
| 11 | `test_fetch_playlist` | `api().get_playlist(PlaylistId)` for Top 50 Global | Has name, items list with track URIs |

### Audio Streaming — Tracks (5 tests, `@premium`)

| # | Test | What it does | Key assertions |
|---|------|-------------|----------------|
| 12 | `test_stream_track_vorbis` | Load Bohemian Rhapsody with `VorbisOnlyAudioQuality(NORMAL)`, read first 16KB | Returns `LoadedStream`, `stream.track.name` matches, read returns 16384 non-zero bytes, codec is VORBIS |
| 13 | `test_stream_track_high_quality` | Load same track with `VorbisOnlyAudioQuality(VERY_HIGH)`, read first 16KB | Returns `LoadedStream`, read succeeds, codec is VORBIS |
| 14 | `test_stream_track_mp3` | Load track with `FormatOnlyAudioQuality(NORMAL, SuperAudioFormat.MP3)`, read first 16KB | Returns `LoadedStream`, codec is MP3, read returns bytes |
| 15 | `test_stream_track_full_download` | Load track, read entire stream to completion | Total bytes > 100KB (a real track), stream exhausted (`available() == 0`) |
| 16 | `test_stream_track_seek` | Load track, read 4KB, seek to 0, re-read 4KB | Both reads return identical bytes |

### Audio Streaming — Podcasts (2 tests, `@premium`)

| # | Test | What it does | Key assertions |
|---|------|-------------|----------------|
| 17 | `test_stream_episode` | Load a podcast episode, read first 16KB | Returns `LoadedStream`, `stream.episode` is set, `stream.track` is None, read returns bytes |
| 18 | `test_stream_episode_external_url` | Load a podcast episode that has `external_url` (many podcasts do) | Returns `LoadedStream`, read returns bytes |

### Audio Streaming — Lossless (1 test, `@premium`)

| # | Test | What it does | Key assertions |
|---|------|-------------|----------------|
| 19 | `test_stream_track_flac` | Load track with `LosslessOnlyAudioQuality(LOSSLESS)` | If FLAC available: `LoadedStream` with codec FLAC, read returns bytes. If not available (free tier): `get_file()` returns None — skip with message |

### Normalization Data (1 test, `@premium`)

| # | Test | What it does | Key assertions |
|---|------|-------------|----------------|
| 20 | `test_normalization_data` | Load a track, check `loaded.normalization_data` | `track_gain_db` is a float in [-40, 40], `track_peak` > 0, `album_gain_db` is a float, `get_factor(0.0)` > 0 |

---

## Total: 20 tests

- **3** auth/session
- **2** search
- **6** metadata
- **9** audio streaming (`@premium`)

All tests are **read-only**. No account state modifications.

---

## Key Source Files

- `librespot/core.py` — `Session`, `Session.Builder`, `Session.Configuration`, `ApiClient`, `TokenProvider`, `SearchManager`
- `librespot/audio/__init__.py` — `PlayableContentFeeder`, `LoadedStream`, `NormalizationData`, `CdnManager`
- `librespot/audio/decoders.py` — `AudioQuality`, `VorbisOnlyAudioQuality`, `LosslessOnlyAudioQuality`, `FormatOnlyAudioQuality`
- `librespot/audio/format.py` — `SuperAudioFormat`
- `librespot/metadata.py` — `TrackId`, `EpisodeId`, `AlbumId`, `ArtistId`, `ShowId`, `PlaylistId`

## Verification

```bash
uv add --dev pytest

# All tests:
SPOTIFY_USERNAME=... SPOTIFY_PASSWORD=... uv run pytest tests/test_e2e.py -v

# Skip streaming (free accounts):
SPOTIFY_USERNAME=... SPOTIFY_PASSWORD=... uv run pytest tests/test_e2e.py -v -m "not premium"
```
