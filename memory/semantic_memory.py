"""memory/semantic_memory.py — durable, searchable archive of everything Sofii is told.

long_term.json is deliberately lossy by design: session summaries are capped
at 3 and consumed on read (see pop_last_session), and _trim_to_limit silently
deletes the oldest facts once the file grows past MEMORY_MAX_CHARS. That's the
right behavior for what goes into the live prompt — but it means anything
older eventually disappears with no way to ask "what did I tell you about X
a few weeks ago".

This module keeps a separate, append-only archive (memory/archive.jsonl) with
a Gemini embedding per entry, and answers semantic queries against it with
brute-force cosine similarity — plenty fast at personal-assistant scale
(thousands of entries).
"""
import json
import sys
import time
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
ARCHIVE_PATH    = BASE_DIR / "memory" / "archive.jsonl"
EMBED_MODEL     = "gemini-embedding-001"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _embed(text: str, task_type: str) -> list[float] | None:
    try:
        from google import genai
        client = genai.Client(api_key=_get_api_key())
        resp = client.models.embed_content(
            model=EMBED_MODEL,
            contents=text,
            config={"task_type": task_type},
        )
        return list(resp.embeddings[0].values)
    except Exception as e:
        print(f"[SemanticMemory] ⚠️ embed failed: {e}")
        return None


def archive_add(text: str, category: str = "note", source: str = "") -> None:
    """Append text to the durable archive with its embedding. Never raises —
    a failed archive write should never break the caller (memory save, trim, etc)."""
    text = (text or "").strip()
    if not text:
        return
    try:
        embedding = _embed(text, "RETRIEVAL_DOCUMENT")
        if embedding is None:
            return
        entry = {
            "text":      text,
            "category":  category,
            "source":    source,
            "date":      time.strftime("%Y-%m-%d"),
            "embedding": embedding,
        }
        ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ARCHIVE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[SemanticMemory] ⚠️ archive_add failed: {e}")


def _load_archive() -> list[dict]:
    if not ARCHIVE_PATH.exists():
        return []
    entries = []
    with open(ARCHIVE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    return entries


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def archive_search(query: str, top_k: int = 5) -> list[dict]:
    """Returns up to top_k archive entries most semantically similar to query,
    each with text/category/date/score, best match first."""
    entries = _load_archive()
    if not entries:
        return []
    q_emb = _embed(query, "RETRIEVAL_QUERY")
    if q_emb is None:
        return []

    scored = []
    for e in entries:
        emb = e.get("embedding")
        if not emb:
            continue
        score = _cosine(q_emb, emb)
        scored.append({
            "text":     e["text"],
            "category": e.get("category", "note"),
            "date":     e.get("date", ""),
            "score":    score,
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
