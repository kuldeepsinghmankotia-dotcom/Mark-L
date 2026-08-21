"""plugins/calorie_counter.py — voice meal logging with running calorie totals.

Storage: memory/calorie_log.json, keyed by date (YYYY-MM-DD). Calories are
estimated via Gemini when the user doesn't state a number directly.
"""
import json
import sys
from datetime import datetime
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
LOG_PATH        = BASE_DIR / "memory" / "calorie_log.json"

TOOL_SPEC = {
    "name": "calorie_counter",
    "description": (
        "Logs food/meals and tracks daily calorie totals. Use when the user "
        "mentions eating or drinking something and wants it logged, asks for "
        "their calorie total today (or a past date), asks for recent history, "
        "or wants to reset a day's log."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "log | total | history | reset"
            },
            "food": {
                "type": "STRING",
                "description": "Description of the food/meal — required for action='log' (e.g. '2 eggs and toast')"
            },
            "calories": {
                "type": "NUMBER",
                "description": "Explicit calorie count if the user stated it directly; omit to have Sofii estimate it"
            },
            "date": {
                "type": "STRING",
                "description": "YYYY-MM-DD — optional, defaults to today. Used with 'total' or 'reset' for a past day."
            },
        },
        "required": ["action"]
    }
}


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load() -> dict:
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"days": {}}


def _save(data: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _estimate_calories(food: str) -> tuple[int, str]:
    from google import genai

    client = genai.Client(api_key=_get_api_key())
    prompt = (
        f"Estimate the calorie count for this food/meal: '{food}'. "
        "Give your best single estimate for a typical portion. "
        'Respond with ONLY JSON: {"calories": 350, "item": "short normalized name"}'
    )
    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    data = json.loads((response.text or "").strip())
    return int(round(float(data["calories"]))), data.get("item", food)


def run(parameters: dict, player=None, speak=None) -> str:
    action = (parameters.get("action") or "").lower().strip()
    data   = _load()

    if action == "log":
        food = parameters.get("food", "").strip()
        if not food:
            return "What did you eat?"

        explicit = parameters.get("calories")
        if explicit is not None:
            calories, item = int(round(float(explicit))), food
        else:
            try:
                calories, item = _estimate_calories(food)
            except Exception as e:
                return f"Couldn't estimate calories for that: {e}"

        date = _today()
        day  = data["days"].setdefault(date, {"entries": [], "total": 0})
        day["entries"].append({
            "food":     item,
            "calories": calories,
            "time":     datetime.now().strftime("%H:%M"),
        })
        day["total"] += calories
        _save(data)
        return f"Logged {item} at about {calories} calories. Today's total: {day['total']} calories."

    date = parameters.get("date") or _today()

    if action == "total":
        day = data["days"].get(date)
        if not day or not day["entries"]:
            return f"No meals logged for {date}."
        return f"{date}: {day['total']} calories across {len(day['entries'])} logged item(s)."

    if action == "history":
        days = sorted(data["days"].items())[-7:]
        if not days:
            return "No calorie history yet."
        parts = [f"{d}: {info['total']} cal" for d, info in days]
        return "Last 7 logged days — " + "; ".join(parts)

    if action == "reset":
        if date in data["days"]:
            del data["days"][date]
            _save(data)
            return f"Cleared the calorie log for {date}."
        return f"No log to clear for {date}."

    return f"Unknown calorie_counter action: {action}"
