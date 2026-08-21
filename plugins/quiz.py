"""plugins/quiz.py — voice trivia quiz on any topic.

Single in-process session (this assistant talks to one user at a time).
Questions are generated on the fly via Gemini with structured JSON output
so answers can be graded reliably instead of parsing free text.
"""
import json
import sys
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

TOOL_SPEC = {
    "name": "quiz",
    "description": (
        "Runs a spoken trivia quiz on any topic. Use when the user asks to start "
        "a quiz/trivia game, answers a quiz question, wants their score, or wants "
        "to stop the quiz. Always call with action='start' first when a new topic "
        "is requested, action='answer' when the user responds to a question."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "start | answer | next | stop | status"
            },
            "topic": {
                "type": "STRING",
                "description": "Quiz topic — required for action='start' (e.g. 'World War 2', 'Python')"
            },
            "difficulty": {
                "type": "STRING",
                "description": "easy | medium | hard — optional, defaults to medium"
            },
            "answer": {
                "type": "STRING",
                "description": "The user's spoken answer — required for action='answer'"
            },
        },
        "required": ["action"]
    }
}

_STATE = {
    "active":     False,
    "topic":      None,
    "difficulty": "medium",
    "score":      0,
    "total":      0,
    "asked":      [],   # question texts already used, so we don't repeat
    "current":    None, # {"question","options","correct_index","explanation"}
}


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _generate_question(topic: str, difficulty: str, asked: list[str]) -> dict:
    from google import genai

    client = genai.Client(api_key=_get_api_key())
    avoid  = ("\nDo not repeat any of these already-asked questions: "
              + "; ".join(asked[-15:])) if asked else ""

    prompt = (
        f"Write one {difficulty} difficulty multiple-choice trivia question about '{topic}'. "
        f"Exactly 4 answer options, only one correct.{avoid}\n"
        'Respond with ONLY JSON in this exact shape: '
        '{"question": "...", "options": ["...","...","...","..."], '
        '"correct_index": 0, "explanation": "one short sentence"}'
    )
    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    text = (response.text or "").strip()
    data = json.loads(text)

    if not isinstance(data.get("options"), list) or len(data["options"]) != 4:
        raise ValueError("Malformed question from model")
    return data


def _format_question(q: dict, prefix: str = "") -> str:
    letters = ["A", "B", "C", "D"]
    opts    = "  ".join(f"{letters[i]}) {opt}" for i, opt in enumerate(q["options"]))
    return f"{prefix}{q['question']}  {opts}"


def _match_answer(answer: str, options: list[str]) -> int | None:
    """Map a free-form spoken answer to an option index, or None if no match."""
    a = answer.strip().lower().rstrip(".")
    letters = {"a": 0, "b": 1, "c": 2, "d": 3}
    if a in letters:
        return letters[a]
    if a.isdigit() and 1 <= int(a) <= 4:
        return int(a) - 1
    # substring match against option text
    for i, opt in enumerate(options):
        if a in opt.lower() or opt.lower() in a:
            return i
    return None


def run(parameters: dict, player=None, speak=None) -> str:
    action = (parameters.get("action") or "").lower().strip()

    if action == "start":
        topic      = parameters.get("topic", "").strip()
        difficulty = (parameters.get("difficulty") or "medium").lower().strip()
        if not topic:
            return "What topic would you like the quiz to be about?"

        _STATE.update(active=True, topic=topic, difficulty=difficulty,
                       score=0, total=0, asked=[], current=None)
        try:
            q = _generate_question(topic, difficulty, [])
        except Exception as e:
            _STATE["active"] = False
            return f"Couldn't start the quiz: {e}"

        _STATE["current"] = q
        _STATE["asked"].append(q["question"])
        return _format_question(q, prefix=f"Starting a {difficulty} quiz on {topic}. First question: ")

    if not _STATE["active"]:
        return "No quiz is currently running. Say a topic to start one."

    if action == "status":
        return f"Score so far: {_STATE['score']} out of {_STATE['total']} on {_STATE['topic']}."

    if action == "stop":
        score, total, topic = _STATE["score"], _STATE["total"], _STATE["topic"]
        _STATE.update(active=False, current=None)
        if total == 0:
            return f"Quiz on {topic} ended with no questions answered."
        return f"Quiz on {topic} ended. Final score: {score} out of {total}."

    if action == "next":
        try:
            q = _generate_question(_STATE["topic"], _STATE["difficulty"], _STATE["asked"])
        except Exception as e:
            return f"Couldn't get the next question: {e}"
        _STATE["current"] = q
        _STATE["asked"].append(q["question"])
        return _format_question(q, prefix="Next question: ")

    if action == "answer":
        current = _STATE["current"]
        if not current:
            return "There's no active question — say 'next' to get one."
        user_answer = parameters.get("answer", "")
        idx = _match_answer(user_answer, current["options"])
        _STATE["total"] += 1

        if idx == current["correct_index"]:
            _STATE["score"] += 1
            feedback = f"Correct! {current.get('explanation', '')}"
        else:
            correct_text = current["options"][current["correct_index"]]
            feedback = f"Not quite — the answer was {correct_text}. {current.get('explanation', '')}"

        try:
            q = _generate_question(_STATE["topic"], _STATE["difficulty"], _STATE["asked"])
            _STATE["current"] = q
            _STATE["asked"].append(q["question"])
            return _format_question(q, prefix=f"{feedback} Score: {_STATE['score']}/{_STATE['total']}. Next question: ")
        except Exception:
            return f"{feedback} Score: {_STATE['score']}/{_STATE['total']}."

    return f"Unknown quiz action: {action}"
