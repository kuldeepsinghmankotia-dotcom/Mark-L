"""core/google_auth.py — shared OAuth2 flow for Google Calendar + Gmail.

One-time setup (this part only you can do — it's tied to your Google account,
Sofii has no way to create it on your behalf):

  1. https://console.cloud.google.com/ -> create or select a project
  2. APIs & Services -> Library -> enable "Google Calendar API" and "Gmail API"
  3. APIs & Services -> OAuth consent screen -> External -> add yourself as a test user
  4. APIs & Services -> Credentials -> Create Credentials -> OAuth client ID
     -> Application type: "Desktop app"
  5. Download the JSON, save it as  config/google_client_secret.json

The first Calendar/Email command opens a browser for one-time consent, then
caches a refresh token in config/google_token.json — every call after that
is silent, no browser involved.
"""
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR            = _get_base_dir()
CLIENT_SECRET_PATH  = BASE_DIR / "config" / "google_client_secret.json"
TOKEN_PATH           = BASE_DIR / "config" / "google_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


class GoogleAuthNotConfigured(Exception):
    pass


def get_credentials() -> Credentials:
    if not CLIENT_SECRET_PATH.exists():
        raise GoogleAuthNotConfigured(
            "Your Google account isn't linked yet. See core/google_auth.py for the "
            "one-time setup steps, then save the OAuth client JSON as "
            "config/google_client_secret.json."
        )

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow  = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    return creds
