"""plugins/recall.py — semantic search over Sofii's durable memory archive.

Long-term memory (long_term.json) only keeps the most recent/most-fitting
facts and the last few session summaries — this plugin searches the full
history in memory/archive.jsonl (see memory/semantic_memory.py), so the user
can ask about things that scrolled out of the live context weeks ago.
"""
from memory.semantic_memory import archive_search

TOOL_SPEC = {
    "name": "recall",
    "description": (
        "Searches Sofii's full memory history — including old session summaries "
        "and facts no longer in active memory — for anything semantically related "
        "to a query. Use when the user asks something like 'what did I tell you "
        "about X a while back', 'do you remember when we talked about...', or "
        "'what was I working on last month'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "What to search memory for, in the user's own words or a short paraphrase"
            },
        },
        "required": ["query"]
    }
}


def run(parameters: dict, player=None, speak=None) -> str:
    query = (parameters.get("query") or "").strip()
    if not query:
        return "What would you like me to recall?"

    results = archive_search(query, top_k=5)
    if not results:
        return "I don't have anything relevant in memory for that."

    # Drop weak matches — cosine similarity below this is usually noise.
    strong = [r for r in results if r["score"] >= 0.5]
    if not strong:
        return "I don't have anything relevant in memory for that."

    lines = [f"({r['date']}) {r['text']}" for r in strong]
    return "Here's what I found — " + "; ".join(lines)
