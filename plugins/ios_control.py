"""plugins/ios_control.py — iPhone control via Pushcut-triggered Shortcuts.

No jailbreak equivalent of Android's Accessibility Service exists on iOS, so
this can't see the phone's screen or tap arbitrary coordinates. What it CAN
do reliably: run a named Shortcut on the phone, remotely, with input text —
which covers opening apps, sending messages, toggling settings, and anything
else you can build as a Shortcut.

Requires:
  1. Pushcut app installed on the iPhone (App Store), secret saved as
     "pushcut_secret" in config/api_keys.json — see core/pushcut_client.py.
  2. A small set of Shortcuts built once in the iOS Shortcuts app:

     "OpenApp"       — Input -> If/Otherwise chain per app -> Open URLs
                        (e.g. If Input is "spotify", Open URL "spotify://")
     "SendMessage"   — Input -> Split Text by "|" -> Send Message
                        (first part = recipient, second = message body)
     "Toggle"        — Input -> If/Otherwise on wifi_on/wifi_off/dnd_on/
                        dnd_off/flashlight_on/flashlight_off -> matching
                        Set Wi-Fi / Set Focus / Set Torch action
     "GetLocation"   — Get Current Location -> Get Address -> Stop and
                        Output that text
     "ReadClipboard" — Get Clipboard -> Stop and Output that text

  Any other Shortcut you build can be triggered ad hoc via action='run_shortcut'.
"""
from core.pushcut_client import run_shortcut, PushcutNotConfigured

_TOGGLE_TARGETS = {
    "wifi_on", "wifi_off", "dnd_on", "dnd_off", "flashlight_on", "flashlight_off"
}

TOOL_SPEC = {
    "name": "ios_control",
    "description": (
        "Controls the user's iPhone remotely via Shortcuts. Use to open an app on "
        "the phone, send a text message from the phone, toggle wifi/DND/flashlight, "
        "get the phone's current location, read its clipboard, or run any other "
        "named Shortcut the user has set up."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "open_app | send_message | toggle | get_location | read_clipboard | run_shortcut"
            },
            "target": {
                "type": "STRING",
                "description": (
                    "For open_app: app name. For toggle: one of "
                    + ", ".join(sorted(_TOGGLE_TARGETS))
                    + ". For run_shortcut: the exact Shortcut name on the phone."
                )
            },
            "to":      {"type": "STRING", "description": "Recipient for send_message"},
            "message": {"type": "STRING", "description": "Message body for send_message"},
            "input":   {"type": "STRING", "description": "Free-form input text for run_shortcut"},
        },
        "required": ["action"]
    }
}


def run(parameters: dict, player=None, speak=None) -> str:
    action = (parameters.get("action") or "").lower().strip()
    try:
        if action == "open_app":
            target = parameters.get("target", "").strip()
            if not target:
                return "Which app should I open?"
            out = run_shortcut("OpenApp", input_text=target)
            return out or f"Opened {target} on your phone."

        if action == "send_message":
            to      = parameters.get("to", "").strip()
            message = parameters.get("message", "").strip()
            if not to or not message:
                return "I need a recipient and a message to send."
            run_shortcut("SendMessage", input_text=f"{to}|{message}", wait=False)
            return f"Sent to {to} on your phone."

        if action == "toggle":
            target = parameters.get("target", "").lower().strip()
            if target not in _TOGGLE_TARGETS:
                return "Toggle target should be one of: " + ", ".join(sorted(_TOGGLE_TARGETS))
            run_shortcut("Toggle", input_text=target, wait=False)
            return f"Done — {target.replace('_', ' ')}."

        if action == "get_location":
            out = run_shortcut("GetLocation", wait=True, timeout=20)
            return out or "Couldn't get the phone's location."

        if action == "read_clipboard":
            out = run_shortcut("ReadClipboard", wait=True, timeout=15)
            return out or "Phone clipboard is empty."

        if action == "run_shortcut":
            name = parameters.get("target", "").strip()
            if not name:
                return "Which Shortcut should I run?"
            out = run_shortcut(name, input_text=parameters.get("input", ""), wait=True, timeout=20)
            return out or f"Ran '{name}' on your phone."

        return f"Unknown ios_control action: {action}"

    except PushcutNotConfigured as e:
        return str(e)
    except Exception as e:
        return f"Phone control failed: {e}"
