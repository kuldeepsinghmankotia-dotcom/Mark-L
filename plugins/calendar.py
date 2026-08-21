"""plugins/calendar.py — Google Calendar by voice: list, create, delete events.

Needs the one-time Google OAuth setup described in core/google_auth.py.
"""
from datetime import datetime, timedelta

from core.google_auth import get_credentials, GoogleAuthNotConfigured

TOOL_SPEC = {
    "name": "calendar_events",
    "description": (
        "Reads and manages Google Calendar events. Use when the user asks what's "
        "on their schedule/calendar, wants to book/create an event, or wants to "
        "delete/cancel one."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "list | create | delete"
            },
            "date": {
                "type": "STRING",
                "description": "YYYY-MM-DD — for 'list', defaults to today"
            },
            "days_ahead": {
                "type": "NUMBER",
                "description": "For 'list' — how many days forward from 'date' to include, default 1"
            },
            "summary": {
                "type": "STRING",
                "description": "Event title — required for 'create'; search text for 'delete'"
            },
            "start_time": {
                "type": "STRING",
                "description": "ISO 8601 datetime for 'create', e.g. 2026-08-06T15:00:00"
            },
            "end_time": {
                "type": "STRING",
                "description": "ISO 8601 datetime for 'create' — optional, defaults to start_time + 1 hour"
            },
            "description": {"type": "STRING", "description": "Optional event description"},
            "location":    {"type": "STRING", "description": "Optional event location"},
        },
        "required": ["action"]
    }
}


def _service():
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=get_credentials())


def run(parameters: dict, player=None, speak=None) -> str:
    action = (parameters.get("action") or "").lower().strip()
    try:
        service = _service()
    except GoogleAuthNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Calendar auth failed: {e}"

    if action in ("list", "today"):
        date_str   = parameters.get("date") or datetime.now().strftime("%Y-%m-%d")
        days_ahead = int(parameters.get("days_ahead") or 1)
        try:
            start = datetime.strptime(date_str, "%Y-%m-%d").astimezone()
        except ValueError:
            return "Date should be in YYYY-MM-DD format."
        end = start + timedelta(days=days_ahead)

        events = service.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute().get("items", [])

        if not events:
            return f"No events found for {date_str}."
        lines = []
        for e in events:
            when = e["start"].get("dateTime", e["start"].get("date"))
            lines.append(f"{when} — {e.get('summary', '(no title)')}")
        return f"Events for {date_str}: " + "; ".join(lines)

    if action == "create":
        summary    = parameters.get("summary", "").strip()
        start_time = parameters.get("start_time", "").strip()
        if not summary or not start_time:
            return "I need at least an event title and a start time to create it."
        try:
            start_dt = datetime.fromisoformat(start_time)
        except ValueError:
            return "start_time should be ISO 8601, e.g. 2026-08-06T15:00:00."
        end_time = parameters.get("end_time")
        if not end_time:
            end_time = (start_dt + timedelta(hours=1)).isoformat()

        body = {
            "summary": summary,
            "start":   {"dateTime": start_time},
            "end":     {"dateTime": end_time},
        }
        if parameters.get("description"):
            body["description"] = parameters["description"]
        if parameters.get("location"):
            body["location"] = parameters["location"]

        service.events().insert(calendarId="primary", body=body).execute()
        return f"Created '{summary}' on your calendar."

    if action == "delete":
        summary = parameters.get("summary", "").strip()
        if not summary:
            return "Which event should I delete?"
        now = datetime.now().astimezone().isoformat()
        events = service.events().list(
            calendarId="primary", timeMin=now, q=summary,
            singleEvents=True, orderBy="startTime", maxResults=5,
        ).execute().get("items", [])
        if not events:
            return f"Couldn't find an upcoming event matching '{summary}'."
        target = events[0]
        service.events().delete(calendarId="primary", eventId=target["id"]).execute()
        return f"Deleted '{target.get('summary')}' from your calendar."

    return f"Unknown calendar_events action: {action}"
