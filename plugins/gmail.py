"""plugins/gmail.py — Gmail by voice: check unread, search, read, send, draft.

Needs the one-time Google OAuth setup described in core/google_auth.py.
Shares the same OAuth token as plugins/calendar.py.
"""
import base64
from email.mime.text import MIMEText

from core.google_auth import get_credentials, GoogleAuthNotConfigured

TOOL_SPEC = {
    "name": "email",
    "description": (
        "Reads, searches, and sends Gmail. Use when the user asks about their "
        "inbox/unread email, wants a specific message read out, or wants to "
        "send or draft an email."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "unread | search | read | send | draft"
            },
            "query": {
                "type": "STRING",
                "description": "Gmail search query for 'search'/'read', e.g. 'from:john subject:invoice' or a topic"
            },
            "to":      {"type": "STRING", "description": "Recipient address — required for 'send'/'draft'"},
            "subject": {"type": "STRING", "description": "Subject line — for 'send'/'draft'"},
            "body":    {"type": "STRING", "description": "Message body — required for 'send'/'draft'"},
            "max_results": {"type": "NUMBER", "description": "Max messages to list for 'unread'/'search', default 5"},
        },
        "required": ["action"]
    }
}


def _service():
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=get_credentials())


def _header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _decode_body(payload: dict) -> str:
    def _walk(part):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        for sub in part.get("parts") or []:
            r = _walk(sub)
            if r:
                return r
        return None
    return _walk(payload) or ""


def run(parameters: dict, player=None, speak=None) -> str:
    action = (parameters.get("action") or "").lower().strip()
    try:
        service = _service()
    except GoogleAuthNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Email auth failed: {e}"

    if action in ("unread", "search"):
        q = "is:unread" if action == "unread" else parameters.get("query", "").strip()
        if action == "search" and not q:
            return "What should I search your email for?"
        max_results = int(parameters.get("max_results") or 5)

        msgs = service.users().messages().list(
            userId="me", q=q, maxResults=max_results
        ).execute().get("messages", [])
        if not msgs:
            return "No unread emails." if action == "unread" else f"No emails found for '{q}'."

        lines = []
        for m in msgs:
            full    = service.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject"],
            ).execute()
            headers = full.get("payload", {}).get("headers", [])
            lines.append(f"From {_header(headers, 'From')}: {_header(headers, 'Subject')}")

        label = "Unread" if action == "unread" else f"Results for '{q}'"
        return f"{label} — " + "; ".join(lines)

    if action == "read":
        q = parameters.get("query", "").strip()
        if not q:
            return "Which email should I read?"
        msgs = service.users().messages().list(userId="me", q=q, maxResults=1).execute().get("messages", [])
        if not msgs:
            return f"Couldn't find an email matching '{q}'."
        full    = service.users().messages().get(userId="me", id=msgs[0]["id"], format="full").execute()
        headers = full.get("payload", {}).get("headers", [])
        body    = _decode_body(full.get("payload", {})).strip()
        if len(body) > 1200:
            body = body[:1200] + "…"
        return f"From {_header(headers, 'From')}, subject '{_header(headers, 'Subject')}': {body}"

    if action in ("send", "draft"):
        to      = parameters.get("to", "").strip()
        subject = parameters.get("subject", "").strip()
        body    = parameters.get("body", "").strip()
        if not to or not body:
            return "I need a recipient and a message body."

        msg            = MIMEText(body)
        msg["to"]      = to
        msg["subject"] = subject or "(no subject)"
        raw            = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        if action == "send":
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            return f"Email sent to {to}."
        service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        return f"Draft saved for {to} — not sent yet."

    return f"Unknown email action: {action}"
