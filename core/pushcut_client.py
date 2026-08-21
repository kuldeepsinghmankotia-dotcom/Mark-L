"""core/pushcut_client.py — thin client for Pushcut's Automation Server API.

Pushcut (iOS app, pushcut.io) is the bridge that lets a laptop trigger a
Shortcut on an iPhone without jailbreaking — Apple gives no direct way to
push a command to a phone. This wraps the two endpoints documented at
https://www.pushcut.io/support/automation-server and
https://www.pushcut.io/support/notifications :

    POST https://api.pushcut.io/{secret}/execute?shortcut={name}
    POST https://api.pushcut.io/{secret}/notifications/{notification_name}

One-time setup (only you can do this — it's tied to your iPhone):
  1. Install "Pushcut" from the App Store on your iPhone
  2. Open it -> Settings -> copy your account secret URL (or just the secret)
  3. Save it as "pushcut_secret" in config/api_keys.json
  4. Build the starter Shortcuts described in plugins/ios_control.py in the
     Shortcuts app on your phone — Pushcut can only run Shortcuts that
     already exist, it can't create them remotely.
"""
import json
import sys
from pathlib import Path

import requests

API_BASE = "https://api.pushcut.io"


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


class PushcutNotConfigured(Exception):
    pass


def _get_secret() -> str:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            secret = json.load(f).get("pushcut_secret", "").strip()
    except Exception:
        secret = ""
    if not secret:
        raise PushcutNotConfigured(
            "Your iPhone isn't linked yet. Install Pushcut on your phone, copy your "
            "account secret from its Settings, and save it as \"pushcut_secret\" in "
            "config/api_keys.json."
        )
    # Accept either the bare secret or a full copied URL like
    # https://api.pushcut.io/<secret> — extract just the secret either way.
    if secret.startswith("http"):
        secret = secret.rstrip("/").split("/")[-1]
    return secret


def run_shortcut(name: str, input_text: str = "", wait: bool = True, timeout: int = 20) -> str:
    """Runs a named Shortcut on the phone. Returns the shortcut's output text
    if wait=True (blocks until it finishes or `timeout` elapses), otherwise
    fires it and returns immediately (202 Accepted)."""
    secret = _get_secret()
    url    = f"{API_BASE}/{secret}/execute"
    params = {"shortcut": name}
    if input_text:
        params["input"] = input_text
    params["timeout"] = "nowait" if not wait else str(timeout)

    resp = requests.post(url, params=params, timeout=timeout + 10)
    if resp.status_code == 202:
        return ""
    resp.raise_for_status()
    return resp.text.strip()


def send_notification(name: str, title: str = "", text: str = "", input_text: str = "") -> None:
    """Triggers a pre-built Pushcut notification (which can itself run a
    Shortcut/HomeKit scene on the phone, per how the notification is configured)."""
    secret  = _get_secret()
    url     = f"{API_BASE}/{secret}/notifications/{name}"
    body    = {k: v for k, v in {"title": title, "text": text, "input": input_text}.items() if v}
    resp    = requests.post(url, json=body, timeout=15)
    resp.raise_for_status()
