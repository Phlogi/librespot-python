"""E2E test fixtures and constants for librespot-python."""

import os
import pathlib
import tempfile

import pytest

from librespot.session import Session
from librespot.metadata import (
    AlbumId,
    ArtistId,
    EpisodeId,
    PlaylistId,
    ShowId,
    TrackId,
)

# ---------------------------------------------------------------------------
# Well-known Spotify content URIs
# ---------------------------------------------------------------------------

TRACK_BOHEMIAN_RHAPSODY = TrackId.from_uri("spotify:track:7tFiyTwD0nx5a1eklYtX2J")
ARTIST_QUEEN = ArtistId.from_uri("spotify:artist:1dfeR4HaWDbWqFHLkxsg1d")
ALBUM_A_NIGHT_AT_THE_OPERA = AlbumId.from_uri("spotify:album:1GbtB4zTqAsyfZEsm1RZfx")
PLAYLIST_TOP50_GLOBAL = PlaylistId.from_uri("spotify:playlist:37i9dQZEVXbMDoHDwVN2tF")
SHOW_URI = ShowId.from_uri("spotify:show:6UCtBYL29hwhw4YbTdX83N")


# ---------------------------------------------------------------------------
# Load credentials from .env.e2e-testing
# ---------------------------------------------------------------------------

def _load_dotenv():
    """Load key=value pairs from .env.e2e-testing into os.environ."""
    env_file = pathlib.Path(__file__).resolve().parent.parent / ".env.e2e-testing"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        # Strip matching surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _get_credentials_file() -> str:
    """Return path to credentials.json, or skip if not found."""
    # Env var overrides default location
    path = os.environ.get("SPOTIFY_CREDENTIALS_FILE")
    if path:
        path = str(_PROJECT_ROOT / path) if not os.path.isabs(path) else path
        if os.path.isfile(path):
            return path
        pytest.skip(f"SPOTIFY_CREDENTIALS_FILE={path} does not exist")

    # Default: credentials.json in project root
    default = _PROJECT_ROOT / "credentials.json"
    if default.is_file():
        return str(default)

    pytest.skip(
        "No credentials.json found. Create one by running:\n"
        "  uv run python -c \"\n"
        "  from librespot.core import Session\n"
        "  Session.Builder().user_pass('USER', 'PASS').create().close()\n"
        "  \"\n"
        "Or set SPOTIFY_CREDENTIALS_FILE in .env.e2e-testing"
    )


@pytest.fixture(scope="session")
def conf():
    """Session configuration with a temp cache dir, reading stored creds from file."""
    tmp = tempfile.mkdtemp(prefix="librespot_test_")
    creds_file = _get_credentials_file()
    return (
        Session.Configuration.Builder()
        .set_store_credentials(True)
        .set_stored_credential_file(os.path.join(tmp, "credentials.json"))
        .set_cache_enabled(True)
        .set_cache_dir(tmp)
        .build(),
        creds_file,
    )


@pytest.fixture(scope="session")
def session(conf):
    """Authenticated Spotify session (lives for the whole test run)."""
    config, creds_file = conf
    sess = (
        Session.Builder(config)
        .set_device_name("librespot-python-e2e")
        .stored_file(creds_file)
        .create()
    )
    yield sess
    sess.close()
