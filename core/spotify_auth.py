"""core/spotify_auth.py — shared Spotify Web API client via spotipy.

One-time setup (only you can do this — it's tied to your Spotify account):
  1. https://developer.spotify.com/dashboard -> Create app
  2. Redirect URI: add exactly  http://127.0.0.1:8888/callback
  3. Save the app's Client ID and Client Secret as "spotify_client_id" and
     "spotify_client_secret" in config/api_keys.json

The first Spotify command opens a browser for one-time consent, then caches
a refresh token in config/spotify_token_cache.json — every call after that
is silent.

Important: the Spotify Web API controls an *active* Spotify Connect device
(phone, desktop app, speaker) — it can't launch Spotify itself. If nothing
is playing anywhere, open the desktop app first (e.g. via the open_app tool)
before trying to play something.
"""
import json
import sys
from pathlib import Path

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = (
    "user-read-playback-state user-modify-playback-state "
    "user-read-currently-playing user-read-recently-played "
    "playlist-read-private"
)


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
CACHE_PATH      = BASE_DIR / "config" / "spotify_token_cache.json"


class SpotifyNotConfigured(Exception):
    pass


def get_client():
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    try:
        cfg = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}

    client_id     = (cfg.get("spotify_client_id") or "").strip()
    client_secret = (cfg.get("spotify_client_secret") or "").strip()
    if not client_id or not client_secret:
        raise SpotifyNotConfigured(
            "Spotify isn't linked yet. Create an app at developer.spotify.com/dashboard, "
            "add redirect URI http://127.0.0.1:8888/callback, and save the Client ID / "
            "Secret as \"spotify_client_id\" / \"spotify_client_secret\" in config/api_keys.json."
        )

    auth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPES,
        cache_path=str(CACHE_PATH),
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth)
