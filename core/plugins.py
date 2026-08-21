"""core/plugins.py — auto-discovers voice tools from the plugins/ directory.

Drop a new capability into plugins/<name>.py and Sofii picks it up on next
launch — no hand-editing TOOL_DECLARATIONS or the dispatch chain in main.py.

Each plugin module must define:

    TOOL_SPEC: dict
        Same shape as a Gemini function_declaration entry, e.g.
        {"name": "quiz", "description": "...", "parameters": {...}}

    def run(parameters: dict, player=None, speak=None) -> str
        parameters — the args Gemini filled in for the call
        player     — the JarvisUI instance (for on-screen panels, TTS state)
        speak      — callable(text) to say something out loud mid-tool
        Returns the string result to hand back to the model.

A plugin that fails to import, or is missing either of the above, is
skipped with a warning — it never blocks the rest of Sofii from starting.
"""
import importlib
import traceback
from pathlib import Path

PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"


def load_plugins() -> tuple[list[dict], dict[str, callable]]:
    """Scan plugins/*.py, return (tool_declarations, {name: run_fn})."""
    tool_declarations: list[dict] = []
    registry: dict[str, callable] = {}

    if not PLUGINS_DIR.exists():
        return tool_declarations, registry

    for path in sorted(PLUGINS_DIR.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        module_name = f"plugins.{path.stem}"
        try:
            module = importlib.import_module(module_name)
            spec   = getattr(module, "TOOL_SPEC", None)
            run_fn = getattr(module, "run", None)

            if not isinstance(spec, dict) or "name" not in spec:
                print(f"[Plugins] ⚠️  Skipped {path.name}: missing/invalid TOOL_SPEC")
                continue
            if not callable(run_fn):
                print(f"[Plugins] ⚠️  Skipped {path.name}: missing run()")
                continue

            name = spec["name"]
            if name in registry:
                print(f"[Plugins] ⚠️  Skipped {path.name}: duplicate tool name '{name}'")
                continue

            tool_declarations.append(spec)
            registry[name] = run_fn
            print(f"[Plugins] ✅ Loaded '{name}' from {path.name}")

        except Exception as e:
            print(f"[Plugins] ⚠️  Failed to load {path.name}: {e}")
            traceback.print_exc()

    return tool_declarations, registry
