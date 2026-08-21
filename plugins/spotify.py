"""plugins/spotify.py — voice control of Spotify playback via the Web API.

Requires the one-time OAuth setup described in core/spotify_auth.py.
"""
from core.spotify_auth import get_client, SpotifyNotConfigured

TOOL_SPEC = {
    "name": "spotify",
    "description": (
        "Controls Spotify playback. Use to play a song/artist/album/playlist by name, "
        "pause/resume, skip tracks, adjust volume, check what's currently playing, add "
        "a track to the queue, or list active devices. Needs Spotify already open "
        "somewhere (phone, desktop app, speaker) — pair with open_app to launch the "
        "desktop app first if nothing is playing anywhere."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "play | pause | resume | next | previous | volume | current | queue_add | devices"
            },
            "query": {
                "type": "STRING",
                "description": "Song/artist/album/playlist name — for 'play' or 'queue_add'"
            },
            "type": {
                "type": "STRING",
                "description": "track | artist | album | playlist — what 'query' refers to, default track"
            },
            "level": {"type": "NUMBER", "description": "Volume 0-100 — for 'volume'"},
        },
        "required": ["action"]
    }
}


def _no_device_message() -> str:
    return "No active Spotify device found — open Spotify on your phone, desktop app, or a speaker first."


def run(parameters: dict, player=None, speak=None) -> str:
    action = (parameters.get("action") or "").lower().strip()
    try:
        sp = get_client()
    except SpotifyNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Spotify auth failed: {e}"

    try:
        if action == "play":
            query = parameters.get("query", "").strip()
            kind  = (parameters.get("type") or "track").lower().strip()
            if not query:
                sp.start_playback()
                return "Resumed playback."

            results = sp.search(q=query, type=kind, limit=1)
            items = results.get(f"{kind}s", {}).get("items", [])
            if not items:
                return f"Couldn't find '{query}' on Spotify."
            item = items[0]
            name = item.get("name", query)

            if kind == "track":
                sp.start_playback(uris=[item["uri"]])
                artist = item["artists"][0]["name"] if item.get("artists") else ""
                return f"Playing {name}{f' by {artist}' if artist else ''} on Spotify."
            sp.start_playback(context_uri=item["uri"])
            return f"Playing {name} on Spotify."

        if action == "pause":
            sp.pause_playback()
            return "Paused."

        if action == "resume":
            sp.start_playback()
            return "Resumed."

        if action in ("next", "skip"):
            sp.next_track()
            return "Skipped to the next track."

        if action == "previous":
            sp.previous_track()
            return "Went back to the previous track."

        if action == "volume":
            level = parameters.get("level")
            if level is None:
                return "What volume level, 0 to 100?"
            level = max(0, min(100, int(level)))
            sp.volume(level)
            return f"Spotify volume set to {level}%."

        if action == "current":
            cur = sp.current_playback()
            if not cur or not cur.get("item"):
                return "Nothing is currently playing on Spotify."
            item    = cur["item"]
            artists = ", ".join(a["name"] for a in item.get("artists", []))
            state   = "Playing" if cur.get("is_playing") else "Paused"
            return f"{state}: {item['name']}{f' by {artists}' if artists else ''}."

        if action == "queue_add":
            query = parameters.get("query", "").strip()
            if not query:
                return "What should I add to the queue?"
            results = sp.search(q=query, type="track", limit=1)
            items = results.get("tracks", {}).get("items", [])
            if not items:
                return f"Couldn't find '{query}' on Spotify."
            sp.add_to_queue(items[0]["uri"])
            artist = items[0]["artists"][0]["name"] if items[0].get("artists") else ""
            return f"Added {items[0]['name']}{f' by {artist}' if artist else ''} to the queue."

        if action == "devices":
            devices = sp.devices().get("devices", [])
            if not devices:
                return _no_device_message()
            names = ", ".join(
                f"{d['name']} ({'active' if d['is_active'] else 'idle'})" for d in devices
            )
            return f"Spotify devices: {names}."

        return f"Unknown spotify action: {action}"

    except Exception as e:
        msg = str(e)
        if "NO_ACTIVE_DEVICE" in msg or "No active device" in msg or "404" in msg:
            return _no_device_message()
        return f"Spotify control failed: {e}"
