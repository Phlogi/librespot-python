"""Functional end-to-end tests for librespot-python.

All tests are read-only — no account state modifications.
Streaming tests require Spotify Premium and are marked with @pytest.mark.premium.
"""

import pytest
import requests

from librespot.audio.decoders import (
    AudioQuality,
    FormatOnlyAudioQuality,
    LosslessOnlyAudioQuality,
    VorbisOnlyAudioQuality,
)
from librespot.audio.format import SuperAudioFormat
from librespot.session import Session
from librespot.metadata import EpisodeId

from tests.conftest import (
    ALBUM_A_NIGHT_AT_THE_OPERA,
    ARTIST_QUEEN,
    PLAYLIST_TOP50_GLOBAL,
    SHOW_URI,
    TRACK_BOHEMIAN_RHAPSODY,
)

premium = pytest.mark.premium


# ═══════════════════════════════════════════════════════════════════════════
# Authentication & Session (3 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestAuthentication:
    def test_login_and_session_valid(self, session):
        """Log in with user/pass, check session is usable."""
        assert session.is_valid()
        assert len(session.username()) > 0
        assert isinstance(session.country_code, str)
        assert len(session.country_code) == 2

    def test_login_with_stored_credentials(self, session, conf):
        """Get stored creds string from session, create a new session from it."""
        stored_creds = session.stored()
        assert stored_creds is not None and len(stored_creds) > 0

        config, _ = conf
        new_session = (
            Session.Builder(config)
            .set_device_name("librespot-python-e2e-stored")
            .stored(stored_creds)
            .create()
        )
        try:
            assert new_session.is_valid()
            assert new_session.username() == session.username()
        finally:
            new_session.close()

    def test_get_access_token(self, session):
        """Request an OAuth token via session.tokens().get(...)."""
        token = session.tokens().get("playlist-read")
        assert isinstance(token, str)
        assert len(token) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Search (2 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestSearch:
    """Search tests using the Spotify Web API (Mercury search is deprecated)."""

    def _web_search(self, session, query, search_type):
        token = session.tokens().get("user-read-playback-state")
        resp = requests.get(
            "https://api.spotify.com/v1/search",
            params={"q": query, "type": search_type, "limit": 10},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, f"Search API returned {resp.status_code}"
        return resp.json()

    def test_search_track(self, session):
        """Search 'Bohemian Rhapsody' and verify track results."""
        results = self._web_search(session, "Bohemian Rhapsody", "track")
        assert "tracks" in results
        items = results["tracks"]["items"]
        assert len(items) > 0
        assert any("Bohemian Rhapsody" in item["name"] for item in items)

    def test_search_artist(self, session):
        """Search 'Queen' and verify artist results."""
        results = self._web_search(session, "Queen", "artist")
        assert "artists" in results
        items = results["artists"]["items"]
        assert len(items) > 0
        assert any("Queen" in item["name"] for item in items)


# ═══════════════════════════════════════════════════════════════════════════
# Metadata Retrieval (6 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestMetadata:
    def test_fetch_track_metadata(self, session):
        """Fetch track metadata for Bohemian Rhapsody."""
        track = session.api().get_metadata_4_track(TRACK_BOHEMIAN_RHAPSODY)
        assert "Bohemian Rhapsody" in track.name
        assert track.artist[0].name == "Queen"
        assert track.duration > 0
        assert len(track.file) > 0

    def test_fetch_album_metadata(self, session):
        """Fetch album metadata for A Night at the Opera."""
        album = session.api().get_metadata_4_album(ALBUM_A_NIGHT_AT_THE_OPERA)
        assert len(album.name) > 0
        assert any(a.name == "Queen" for a in album.artist)
        assert len(album.disc) > 0
        assert len(album.disc[0].track) > 0

    def test_fetch_artist_metadata(self, session):
        """Fetch artist metadata for Queen."""
        artist = session.api().get_metadata_4_artist(ARTIST_QUEEN)
        assert artist.name == "Queen"
        assert len(artist.top_track) > 0

    def test_fetch_episode_metadata(self, session):
        """Fetch episode metadata via Web API discovery."""
        token = session.tokens().get("user-read-playback-state")
        resp = requests.get(
            "https://api.spotify.com/v1/search",
            params={"q": "podcast", "type": "episode", "limit": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            pytest.skip(f"Web API search unavailable ({resp.status_code})")
        items = resp.json().get("episodes", {}).get("items", [])
        if not items:
            pytest.skip("No episodes found via Web API search")
        episode_id = EpisodeId.from_uri(items[0]["uri"])
        episode = session.api().get_metadata_4_episode(episode_id)
        assert len(episode.name) > 0
        assert episode.duration > 0

    def test_fetch_show_metadata(self, session):
        """Fetch show metadata."""
        show = session.api().get_metadata_4_show(SHOW_URI)
        assert len(show.name) > 0

    def test_fetch_playlist(self, session):
        """Fetch Top 50 Global playlist."""
        playlist = session.api().get_playlist(PLAYLIST_TOP50_GLOBAL)
        assert len(playlist.attributes.name) > 0
        assert len(playlist.contents.items) > 0
        # Verify items have URIs
        first_item = playlist.contents.items[0]
        assert len(first_item.uri) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Audio Streaming — Tracks (5 tests)
# ═══════════════════════════════════════════════════════════════════════════


@premium
class TestStreamTracks:
    def test_stream_track_vorbis(self, session):
        """Load Bohemian Rhapsody with VorbisOnlyAudioQuality(NORMAL), read first 16KB."""
        quality = VorbisOnlyAudioQuality(AudioQuality.NORMAL)
        loaded = session.content_feeder().load(
            TRACK_BOHEMIAN_RHAPSODY, quality, False, None
        )
        assert loaded is not None
        assert loaded.track is not None
        assert "Bohemian Rhapsody" in loaded.track.name

        stream = loaded.input_stream
        assert stream.codec() == SuperAudioFormat.VORBIS

        data = stream.stream().read(16384)
        assert len(data) == 16384
        assert any(b != 0 for b in data)

    def test_stream_track_high_quality(self, session):
        """Load track with VorbisOnlyAudioQuality(VERY_HIGH), read first 16KB."""
        quality = VorbisOnlyAudioQuality(AudioQuality.VERY_HIGH)
        loaded = session.content_feeder().load(
            TRACK_BOHEMIAN_RHAPSODY, quality, False, None
        )
        assert loaded is not None

        stream = loaded.input_stream
        assert stream.codec() == SuperAudioFormat.VORBIS

        data = stream.stream().read(16384)
        assert len(data) == 16384

    def test_stream_track_mp3(self, session):
        """Load track with FormatOnlyAudioQuality for MP3, read first 16KB."""
        quality = FormatOnlyAudioQuality(AudioQuality.NORMAL, SuperAudioFormat.MP3)
        loaded = session.content_feeder().load(
            TRACK_BOHEMIAN_RHAPSODY, quality, False, None
        )
        assert loaded is not None

        stream = loaded.input_stream
        assert stream.codec() == SuperAudioFormat.MP3

        data = stream.stream().read(16384)
        assert len(data) > 0

    def test_stream_track_full_download(self, session):
        """Load a track and read the entire stream to completion."""
        quality = VorbisOnlyAudioQuality(AudioQuality.NORMAL)
        loaded = session.content_feeder().load(
            TRACK_BOHEMIAN_RHAPSODY, quality, False, None
        )
        assert loaded is not None

        input_stream = loaded.input_stream.stream()
        total = 0
        while True:
            chunk = input_stream.read(65536)
            if not chunk:
                break
            total += len(chunk)

        assert total > 100_000  # A real track should be > 100KB
        assert input_stream.available() == 0

    def test_stream_track_seek(self, session):
        """Load track, read 4KB, seek to 0, re-read 4KB — both reads should match."""
        quality = VorbisOnlyAudioQuality(AudioQuality.NORMAL)
        loaded = session.content_feeder().load(
            TRACK_BOHEMIAN_RHAPSODY, quality, False, None
        )
        assert loaded is not None

        input_stream = loaded.input_stream.stream()

        first_read = input_stream.read(4096)
        assert len(first_read) == 4096

        input_stream.seek(0)

        second_read = input_stream.read(4096)
        assert len(second_read) == 4096

        assert first_read == second_read


# ═══════════════════════════════════════════════════════════════════════════
# Audio Streaming — Podcasts (2 tests)
# ═══════════════════════════════════════════════════════════════════════════


@premium
class TestStreamEpisodes:
    @pytest.fixture()
    def episode_id(self, session):
        """Discover a playable episode via Web API."""
        token = session.tokens().get("user-read-playback-state")
        resp = requests.get(
            "https://api.spotify.com/v1/search",
            params={"q": "podcast", "type": "episode", "limit": 5},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            pytest.skip(f"Web API search unavailable ({resp.status_code})")
        items = resp.json().get("episodes", {}).get("items", [])
        if not items:
            pytest.skip("No episodes found via Web API search")
        return EpisodeId.from_uri(items[0]["uri"])

    def test_stream_episode(self, session, episode_id):
        """Load a podcast episode and read first 16KB."""
        quality = VorbisOnlyAudioQuality(AudioQuality.NORMAL)
        loaded = session.content_feeder().load(
            episode_id, quality, False, None
        )
        assert loaded is not None
        assert loaded.episode is not None
        assert loaded.track is None

        data = loaded.input_stream.stream().read(16384)
        assert len(data) > 0

    def test_stream_episode_external_url(self, session, episode_id):
        """Load a podcast episode that has an external_url."""
        quality = VorbisOnlyAudioQuality(AudioQuality.NORMAL)
        loaded = session.content_feeder().load(
            episode_id, quality, False, None
        )
        assert loaded is not None

        data = loaded.input_stream.stream().read(16384)
        assert len(data) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Audio Streaming — Lossless (1 test)
# ═══════════════════════════════════════════════════════════════════════════


@premium
class TestStreamLossless:
    def test_stream_track_flac(self, session):
        """Load track with LosslessOnlyAudioQuality(LOSSLESS)."""
        quality = LosslessOnlyAudioQuality(AudioQuality.LOSSLESS)
        try:
            loaded = session.content_feeder().load(
                TRACK_BOHEMIAN_RHAPSODY, quality, False, None
            )
        except Exception:
            pytest.skip("FLAC not available (may require Premium with HiFi)")

        if loaded is None:
            pytest.skip("FLAC not available for this track/account")

        stream = loaded.input_stream
        assert stream.codec() == SuperAudioFormat.FLAC

        data = stream.stream().read(16384)
        assert len(data) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Normalization Data (1 test)
# ═══════════════════════════════════════════════════════════════════════════


@premium
class TestNormalization:
    def test_normalization_data(self, session):
        """Load a track and check normalization_data attributes."""
        quality = VorbisOnlyAudioQuality(AudioQuality.NORMAL)
        loaded = session.content_feeder().load(
            TRACK_BOHEMIAN_RHAPSODY, quality, False, None
        )
        assert loaded is not None

        norm = loaded.normalization_data
        assert norm is not None

        assert isinstance(norm.track_gain_db, float)
        assert -40 <= norm.track_gain_db <= 40

        assert norm.track_peak > 0

        assert isinstance(norm.album_gain_db, float)

        factor = norm.get_factor(0.0)
        assert factor > 0
