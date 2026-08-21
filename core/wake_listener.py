"""
JARVIS wake listener — always-on, lightweight background process with a
menu bar status icon.

Listens continuously on the microphone for either:
  - the spoken wake word ("Jarvis" / "Hey Jarvis" / "OK Jarvis"), offline via
    Vosk — no API calls, no cost — with word-confidence filtering to cut
    down on false triggers from TV/conversation
  - a double-clap (amplitude-spike detection)

On trigger, launches the full JARVIS app (J.A.R.V.I.S.app) if it isn't
already running, then pauses listening until JARVIS closes again so the
two processes never fight over the microphone.

Self-healing: a watchdog thread force-restarts the process (via os._exit,
relying on launchd's KeepAlive) if the listening loop or the audio stream
ever stalls, so a wedged CoreAudio stream can't leave the listener silently
dead in the background.

Meant to be run as a macOS LaunchAgent (RunAtLoad + KeepAlive) so it starts
at login and stays alive in the background indefinitely.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import rumps
import sounddevice as sd
from vosk import KaldiRecognizer, Model, SetLogLevel

SetLogLevel(-1)  # silence Vosk's internal C++ debug logging

PROJECT_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH  = Path(__file__).resolve().parent / "models" / "vosk-model-small-en-us-0.15"
PYTHON      = PROJECT_DIR / ".venv" / "bin" / "python"
MAIN_PY     = PROJECT_DIR / "main.py"
LOG_PATH    = Path(__file__).resolve().parent / "wake_listener.log"

SAMPLE_RATE = 16000
BLOCK_SIZE  = 2000   # ~125ms per block — fine enough resolution for clap timing

# Vosk's grammar-constrained recognizer can only match words that exist in
# the small model's lexicon — it silently drops anything else (logs "Ignoring
# word missing in vocabulary" and never matches it, no matter how clearly
# it's said). Stylized assistant names aren't real dictionary words, so they
# need an in-vocabulary phonetic stand-in for the wake word specifically.
# Display name / spoken persona elsewhere are untouched — this only affects
# what the offline wake-word engine listens for.
_PHONETIC_ALIASES = {
    "sofii": "sophie",
}


def _load_wake_phrases() -> list[str]:
    """Wake phrases are derived from the configured assistant name (Settings ->
    assistant name), so a rename takes effect here too — not just in the HUD
    and prompt. 'jarvis' always stays in as a fallback so an old habit still
    works even after renaming."""
    configured = ""
    try:
        cfg = json.loads((PROJECT_DIR / "config" / "api_keys.json").read_text(encoding="utf-8"))
        configured = (cfg.get("assistant_name") or "").strip().lower()
    except Exception:
        pass
    configured = _PHONETIC_ALIASES.get(configured, configured)

    names = {"jarvis"}
    if configured:
        names.add(configured)

    phrases: list[str] = []
    for n in names:
        phrases.extend([n, f"hey {n}", f"ok {n}", f"okay {n}"])
    return phrases


WAKE_PHRASES        = _load_wake_phrases()
CONFIDENCE_THRESHOLD = 0.45   # avg per-word Vosk confidence required to accept a wake hit

STALL_TIMEOUT     = 15   # seconds with no audio block -> restart the stream
WATCHDOG_TIMEOUT  = 30   # seconds with zero progress at all -> hard-restart the process
STREAM_FAIL_LIMIT = 3    # consecutive open/listen failures -> hard-restart the process


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_jarvis_running() -> bool:
    try:
        out = subprocess.run(["pgrep", "-f", str(MAIN_PY)], capture_output=True, text=True)
        return bool(out.stdout.strip())
    except Exception:
        return False


def launch_jarvis() -> None:
    log("Trigger detected — launching JARVIS.")
    # Launch the live source directly instead of the packaged J.A.R.V.I.S.app
    # bundle on the Desktop. That bundle is a separately frozen build that only
    # updates when someone re-runs the packaging step — it silently drifts out
    # of sync with this source tree (confirmed live: it was a build from
    # earlier that day, missing everything fixed since, and was crashing
    # within ~10s of every launch). `open -a` also can't report a crash that
    # happens after the app has already started, since Popen only observes
    # whether the *launch itself* succeeded. Running the source directly
    # means what launches is always exactly what's on disk right now.
    subprocess.Popen([str(PYTHON), str(MAIN_PY)], cwd=str(PROJECT_DIR))


class StreamStalled(Exception):
    """Raised internally when the mic stream stops delivering audio."""


class ClapDetector:
    """Detects two sharp amplitude spikes 0.12s-1.2s apart."""

    def __init__(self) -> None:
        self.noise_floor = 200.0
        self.last_event_ts = 0.0
        self.pending_clap_ts: float | None = None

    def process(self, block: np.ndarray) -> bool:
        peak = float(np.abs(block).max())
        now = time.monotonic()
        # Floor raised from 1500 to 4000 — tonight's own heartbeat logs showed
        # ordinary ambient noise (typing, clicks) peaking at 3468 in this
        # environment, meaning background sound alone could already cross the
        # old threshold and register as a "clap" with no clap involved. Ratio
        # raised 5x -> 8x too, so it stays a clear outlier even in quieter rooms.
        is_spike = peak > max(self.noise_floor * 8, 4000)

        if not is_spike:
            self.noise_floor = 0.98 * self.noise_floor + 0.02 * peak
            return False

        if now - self.last_event_ts < 0.12:
            return False  # debounce ringing from the same clap
        self.last_event_ts = now

        if self.pending_clap_ts is not None and (now - self.pending_clap_ts) > 1.2:
            self.pending_clap_ts = None  # stale first clap, expired

        if self.pending_clap_ts is None:
            self.pending_clap_ts = now
            return False

        self.pending_clap_ts = None
        return True


class WakeListener:
    """Owns the mic-listening loop and the watchdog. UI-agnostic — talks to
    the menu bar app only through the `on_status` callback."""

    def __init__(self, on_status) -> None:
        self.on_status = on_status
        self._last_progress = time.monotonic()
        self._progress_lock = threading.Lock()

    def _touch(self) -> None:
        with self._progress_lock:
            self._last_progress = time.monotonic()

    def _stale_seconds(self) -> float:
        with self._progress_lock:
            return time.monotonic() - self._last_progress

    def start(self) -> None:
        threading.Thread(target=self._run_forever, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _watchdog(self) -> None:
        while True:
            time.sleep(5)
            stale = self._stale_seconds()
            if stale > WATCHDOG_TIMEOUT:
                log(f"WATCHDOG: no progress for {stale:.0f}s — forcing hard restart.")
                os._exit(1)  # launchd KeepAlive relaunches us fresh

    def _run_forever(self) -> None:
        log("=== JARVIS wake listener starting ===")
        if not MODEL_PATH.exists():
            log(f"FATAL: vosk model not found at {MODEL_PATH}")
            self.on_status("error", "Model missing")
            return

        model = Model(str(MODEL_PATH))
        # _touch() below marks the outer loop as "alive" on every iteration,
        # including failed ones — so the WATCHDOG_TIMEOUT stall detector never
        # fires for a stream that opens-fails-retries forever without ever
        # succeeding (observed live: PortAudio/CoreAudio errors after a
        # sleep/wake cycle retried every 5s indefinitely). Track consecutive
        # failures separately and force the same known-good remedy — a fresh
        # process, which gets fresh CoreAudio HAL state — after a few in a row.
        stream_fail_streak = 0

        while True:
            self._touch()
            if is_jarvis_running():
                self.on_status("paused", "JARVIS running")
                self._wait_for_jarvis_lifecycle()
                continue

            try:
                self._listen_once(model)
                stream_fail_streak = 0
            except StreamStalled:
                stream_fail_streak += 1
                log(f"Audio stream stalled — reopening ({stream_fail_streak}/{STREAM_FAIL_LIMIT}).")
                self.on_status("error", "Stream stalled, recovering")
                if stream_fail_streak >= STREAM_FAIL_LIMIT:
                    log("Stream repeatedly stalled — forcing hard restart for fresh CoreAudio state.")
                    os._exit(1)
                time.sleep(1)
                continue
            except Exception as e:
                stream_fail_streak += 1
                log(f"ERR: audio stream failed ({stream_fail_streak}/{STREAM_FAIL_LIMIT}) — {e}")
                self.on_status("error", str(e)[:40])
                if stream_fail_streak >= STREAM_FAIL_LIMIT:
                    log("Audio stream repeatedly failed to open — forcing hard restart for fresh CoreAudio state.")
                    os._exit(1)
                time.sleep(5)
                continue

            if is_jarvis_running():
                continue

            self.on_status("launching", "Launching JARVIS…")
            launch_jarvis()
            self._wait_for_jarvis_lifecycle()

    def _wait_for_jarvis_lifecycle(self) -> None:
        started = False
        for _ in range(20):
            self._touch()
            if is_jarvis_running():
                started = True
                break
            time.sleep(1)
        if not started:
            log("WARN: JARVIS did not appear to start within 20s.")
            self.on_status("listening", "Listening")
            return
        log("JARVIS is running — wake listener paused (mic released).")
        self.on_status("paused", "JARVIS running")
        while is_jarvis_running():
            self._touch()
            time.sleep(3)
        log("JARVIS closed — resuming wake-word / clap listening.")

    def _listen_once(self, model: Model) -> None:
        rec = KaldiRecognizer(model, SAMPLE_RATE, json.dumps(WAKE_PHRASES + ["[unk]"]))
        rec.SetWords(True)  # include per-word confidence in results
        clap = ClapDetector()
        audio_q: queue.Queue[bytes] = queue.Queue()

        def callback(indata, frames, time_info, status):
            if status:
                log(f"DEBUG: stream status flag: {status}")
            audio_q.put(bytes(indata))

        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE,
                                dtype="int16", channels=1, callback=callback):
            log(f"Listening for {' / '.join(sorted(set(WAKE_PHRASES)))} or a double-clap...")
            self.on_status("listening", "Listening")
            last_check = time.monotonic()
            last_audio = time.monotonic()
            last_heartbeat = time.monotonic()
            blocks_seen = 0
            peak_since_hb = 0

            while True:
                now = time.monotonic()
                self._touch()

                if now - last_check > 2:
                    last_check = now
                    if is_jarvis_running():
                        return

                try:
                    data = audio_q.get(timeout=1)
                except queue.Empty:
                    if time.monotonic() - last_audio > STALL_TIMEOUT:
                        raise StreamStalled()
                    continue

                last_audio = time.monotonic()
                block = np.frombuffer(data, dtype=np.int16)
                blocks_seen += 1
                peak_since_hb = max(peak_since_hb, int(np.abs(block).max()))

                if time.monotonic() - last_heartbeat > 15:
                    log(f"DEBUG: heartbeat — {blocks_seen} blocks/~15s, "
                        f"peak {peak_since_hb}, noise floor {clap.noise_floor:.0f}")
                    blocks_seen, peak_since_hb = 0, 0
                    last_heartbeat = time.monotonic()

                if clap.process(block):
                    log("Double-clap detected.")
                    return

                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    text = result.get("text", "").lower().strip()
                    if text:
                        words = result.get("result", [])
                        avg_conf = (sum(w.get("conf", 0) for w in words) / len(words)
                                    if words else 0.0)
                        log(f"DEBUG: heard '{text}' (conf={avg_conf:.2f})")
                        if any(p in text for p in WAKE_PHRASES) and avg_conf >= CONFIDENCE_THRESHOLD:
                            log(f"Wake word detected: '{text}' (conf={avg_conf:.2f})")
                            return


class WakeMenuBarApp(rumps.App):
    ICONS = {
        "starting":  "🌑",
        "listening": "🎙",
        "paused":    "🟢",
        "launching": "🚀",
        "error":     "⚠️",
    }

    def __init__(self) -> None:
        super().__init__(name="JARVIS Wake", title=self.ICONS["starting"], quit_button=None)
        self.status_item = rumps.MenuItem("Status: starting…")
        self.open_item = rumps.MenuItem("Open JARVIS Now", callback=self._open_now)
        quit_item = rumps.MenuItem("Quit Wake Listener", callback=self._quit)
        self.menu = [self.status_item, None, self.open_item, quit_item]

        self._pending_icon = self.ICONS["starting"]
        self._pending_text = "Status: starting…"
        self._lock = threading.Lock()

        self.listener = WakeListener(on_status=self._set_status)
        self.listener.start()

        self._timer = rumps.Timer(self._refresh, 1)
        self._timer.start()

    def _set_status(self, state: str, detail: str) -> None:
        with self._lock:
            self._pending_icon = self.ICONS.get(state, "🌑")
            self._pending_text = f"Status: {detail}"

    def _refresh(self, _timer) -> None:
        with self._lock:
            icon, text = self._pending_icon, self._pending_text
        self.title = icon
        self.status_item.title = text

    def _open_now(self, _sender) -> None:
        threading.Thread(target=launch_jarvis, daemon=True).start()

    def _quit(self, _sender) -> None:
        rumps.quit_application()


def main() -> None:
    try:
        from AppKit import NSApp, NSApplicationActivationPolicyAccessory
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception:
        pass  # menu bar still works even if we can't hide the Dock icon
    WakeMenuBarApp().run()


if __name__ == "__main__":
    main()
