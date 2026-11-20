"""Fast, verified writing of a track into FL Studio (shared by track scripts).

- write_notes: one piano-roll run per channel. A marker step (far beyond any
  track) proves the roll shows the right channel; the check happens inside the
  same run (action "expect_marker"), so a wrong channel is never touched.
- write_changed: skips channels whose notes did not change since the last
  successful write (fingerprints in tracks/.cache/), unless force=True.
- batch: many controller commands in one round trip.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from fl_studio_mcp.tools.piano_roll import (
    _clear_request_file,
    _get_response_file,
    _get_state_file,
    _write_request,
)
from fl_studio_mcp.utils.connection import get_connection  # noqa: F401  (re-export)
from fl_studio_mcp.utils.fl_trigger import trigger_background, trigger_fl_studio

# Marker far beyond any track (128 bars): never collides with a real note.
# (Step 511 was inside the track: a real kick there passed as the marker.)
MARK_STEP = 2047
TICKS_PER_STEP = 24  # PPQ 96 / 4
MARK_TICK = MARK_STEP * TICKS_PER_STEP
CACHE_DIR = Path(__file__).resolve().parent / ".cache"


def send(c, action, params=None, timeout=5.0):
    r = c.send_command(action, params or {}, timeout=timeout)
    if r.get("error"):
        raise RuntimeError(f"{action}: {r['error']}")
    return r


def batch(c, commands, timeout=10.0):
    """[(action, params), ...] in one round trip; raise on any error."""
    r = send(c, "batch", {"commands": [{"action": a, "params": p} for a, p in commands]},
             timeout=timeout)
    errors = [(commands[i][0], x["error"]) for i, x in enumerate(r["results"]) if x.get("error")]
    if errors:
        raise RuntimeError(f"batch errors: {errors}")
    return r["results"]


def run_piano_roll(requests):
    """Queue requests, press the script hotkey, return (response, state)."""
    resp, state = _get_response_file(), _get_state_file()
    for f in (resp, state):
        if f.exists():
            f.unlink()
    _clear_request_file()  # a leftover queue would be appended to (duplicate notes)
    _write_request(requests)

    def wait(ticks):
        for _ in range(ticks):
            # an empty queue produces no response file, only the state export
            if state.exists() and (resp.exists() or not requests):
                try:
                    st = json.loads(state.read_text())
                    return (json.loads(resp.read_text()) if requests else {}), st
                except json.JSONDecodeError:
                    pass
            time.sleep(0.1)
        return None, None

    global BG_OK
    if BG_OK is not False and trigger_background():
        # the script starts at once if FL takes the key in the background
        r = wait(15 if BG_OK is None else 80)
        if r[0] is not None or r[1] is not None:
            BG_OK = True
            return r
        if MODE == "bgonly":  # never bring FL forward: fail instead
            return None, None
        BG_OK = False  # FL ignores background keys: use the focus trigger from now on
        print("  background trigger did not work, using the focus trigger")
    if MODE == "bgonly":
        return None, None
    trigger_fl_studio(delay=0.2)
    return wait(80)  # up to 8 s: 400+ notes take longer than 5 s


# None = not tried yet in this run; True/False = background trigger works or not
# FL_TRIGGER: bg (default: background key, focus fallback), bgonly (never
# bring FL forward), focus (always the old focus trigger)
MODE = os.environ.get("FL_TRIGGER", "bg")
BG_OK = None if MODE in ("bg", "bgonly") else False


def write_notes(c, ch, notes, attempts=4):
    """Replace all notes of channel `ch` (current pattern) in one verified run."""
    send(c, "channels.setGridBit", {"channel": ch, "position": MARK_STEP, "value": True})
    requests = [{"action": "expect_marker", "time_ticks": MARK_TICK},
                {"action": "clear"}, {"action": "add_notes", "notes": notes}]
    for attempt in range(attempts):
        send(c, "ui.focusPianoRoll", {"channel": ch})
        time.sleep(0.3)
        r, st = run_piano_roll(requests)
        if r is None:
            print(f"  channel {ch}: no response, retry {attempt + 1}")
            continue
        if r.get("status") == "wrong_channel":
            print(f"  channel {ch}: roll showed another channel, retry {attempt + 1}")
            continue
        if st["noteCount"] != len(notes):
            raise RuntimeError(f"channel {ch}: {st['noteCount']} notes in roll, "
                               f"expected {len(notes)}")
        if any(n["time_ticks"] == MARK_TICK for n in st["notes"]):
            raise RuntimeError(f"channel {ch}: marker still present")
        return r
    raise RuntimeError(f"channel {ch}: not written after {attempts} attempts")


def fingerprint(notes):
    """Order-independent: edits re-sort notes, the content is what matters."""
    rows = sorted(json.dumps(n, sort_keys=True) for n in notes)
    return hashlib.sha1("\n".join(rows).encode()).hexdigest()


def write_changed(c, name, pattern, parts, channels, force=False):
    """Write only channels whose notes changed; returns list of written channels."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{name}.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    written = []
    for ch in channels:
        key, fp = f"p{pattern}/ch{ch}", fingerprint(parts[ch])
        if not force and cache.get(key) == fp:
            continue
        t0 = time.time()
        r = write_notes(c, ch, parts[ch])
        cache[key] = fp
        cache_file.write_text(json.dumps(cache, indent=1))  # save after every channel
        written.append(ch)
        print(f"{ch} notes {r['notes_added']} deleted {r['notes_deleted']} "
              f"({time.time() - t0:.1f} s)")
    return written


def verify(c, parts, channels, ppq=96):
    """Read every channel back from FL and compare with the computed notes
    (pitch, start tick, length tick). Returns {channel: (ok, fl_count, expected)}."""
    out = {}
    for ch in channels:
        send(c, "channels.setGridBit", {"channel": ch, "position": MARK_STEP, "value": True})
        st = None
        for _ in range(6):
            send(c, "ui.focusPianoRoll", {"channel": ch})
            time.sleep(0.3)
            r, st = run_piano_roll([{"action": "expect_marker", "time_ticks": MARK_TICK}])
            if r is not None and st is not None and r.get("status") != "wrong_channel":
                break
            st = None
        send(c, "channels.setGridBit", {"channel": ch, "position": MARK_STEP, "value": False})
        if st is None:
            out[ch] = (False, "no answer from the piano roll", len(parts[ch]))
            continue
        got = sorted((n["number"], n["time_ticks"], n["length_ticks"])
                     for n in st["notes"] if n["time_ticks"] != MARK_TICK)
        exp = sorted((n["midi"], int(ppq * n["time"]), int(ppq * n["duration"]))
                     for n in parts[ch])
        out[ch] = (got == exp, len(got), len(exp))
    return out


def goto_preset(c, ch, k):
    """Select preset number k of the plugin on channel ch. The preset name only
    updates after FL's event loop runs and multi-steps overshoot, so step one
    preset at a time and re-read the name after each step."""
    info = send(c, "plugins.preset", {"channel": ch, "list": True})
    names = info["presets"]
    target = names[k]
    for _ in range(len(names) + 2):
        cur = send(c, "plugins.preset", {"channel": ch})["preset"]
        if cur == target:
            return cur
        idx = names.index(cur) if cur in names else 0
        step = 1 if (k - idx) % len(names) <= len(names) // 2 else -1
        send(c, "plugins.preset", {"channel": ch, "step": step})
        time.sleep(0.35)
    raise RuntimeError(f"preset {k} ({target!r}) not reached")
