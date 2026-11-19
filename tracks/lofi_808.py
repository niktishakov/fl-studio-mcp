"""Build "808 Beat" as a 32-bar lo-fi background track - no mouse needed.

Channels (project "808 Beat"): 0 Kick, 1 Clap, 2 HiHat, 3 Snare, 4 808 Astronomic,
5 FL Keys. One long pattern holds the whole arrangement and loops.

Run:  uv run python tracks/lofi_808.py            (everything)
      uv run python tracks/lofi_808.py drums      (drums only)
      uv run python tracks/lofi_808.py keys       (keys only)
"""

from __future__ import annotations

import json
import random
import sys
import time

from fl_studio_mcp.tools.piano_roll import _get_response_file, _write_request
from fl_studio_mcp.utils.connection import get_connection
from fl_studio_mcp.utils.fl_trigger import trigger_fl_studio

BPM = 80
BARS = 32
STEPS = BARS * 16
KICK, CLAP, HAT, SNARE, ASTRO, KEYS = range(6)

# Sections: (first bar, last bar inclusive, name)
SECTIONS = [
    (0, 3, "intro"),       # keys only, hats come in on bar 3
    (4, 11, "groove_a"),   # full beat, melody A from bar 8
    (12, 15, "groove_b"),  # second progression + melody B
    (16, 19, "break"),     # no kick, soft hats, keys
    (20, 23, "groove_a2"), # beat back, melody A variant
    (24, 27, "groove_b2"), # progression B + melody B + hat rolls
    (28, 31, "outro"),     # drums thin out, last chord rings
]

# Chords: (bass root, upper voicing) - MIDI numbers, C minor
PROG_A = [(44, [56, 60, 63, 67]),  # Abmaj7
          (43, [55, 58, 62, 65]),  # Gm7
          (41, [56, 60, 63, 67]),  # Fm9
          (36, [55, 58, 62, 63])]  # Cm9
PROG_B = [(41, [56, 60, 63, 65]),  # Fm7
          (46, [56, 58, 62, 65]),  # Bb7
          (39, [55, 58, 62, 63]),  # Ebmaj7
          (43, [55, 60, 62, 65])]  # G7sus

# Melodies: (midi, time in beats from section-of-4-bars start, duration)
MEL_A = [(75, 2.0, 1.0), (74, 5.5, 0.5), (70, 6.0, 1.5),
         (72, 10.0, 1.0), (75, 11.0, 0.5), (67, 13.0, 2.0)]
MEL_A2 = [(72, 1.0, 0.5), (75, 1.5, 1.5), (77, 5.0, 1.0), (74, 6.0, 1.0),
          (70, 9.5, 0.5), (72, 10.0, 1.5), (67, 13.0, 0.5), (70, 13.5, 0.5),
          (67, 14.0, 1.5)]
MEL_B = [(72, 1.5, 0.5), (75, 2.0, 1.0), (77, 3.0, 0.75),
         (74, 4.5, 1.5), (72, 6.5, 0.5),
         (70, 9.0, 0.5), (74, 9.5, 0.5), (79, 10.0, 1.5),
         (77, 14.0, 0.5), (74, 14.5, 0.5), (71, 15.0, 1.0)]

# FL Keys sound: (param index, value). Default Muffle=100% made keys ~25 dB
# quieter than drums. Muffle 45%, vel->muffle 50%, stereo, darker treble,
# slow tremolo for a lo-fi wobble.
FL_KEYS_PARAMS = [(4, 0.55), (5, 0.25), (7, 0.25), (11, 0.42), (12, 0.56), (13, 0.45)]

rnd = random.Random(808)


def section(bar: int) -> str:
    for a, b, name in SECTIONS:
        if a <= bar <= b:
            return name
    return "outro"


def hum(v: int, spread: int = 6) -> int:
    return max(1, min(127, v + rnd.randint(-spread, spread)))


def drums() -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {KICK: [], CLAP: [], HAT: [], SNARE: [], ASTRO: []}
    for bar in range(BARS):
        sec = section(bar)
        b0 = bar * 16
        fourth = bar % 4 == 3
        # Kick
        if sec not in ("intro", "break") and not (sec == "outro" and bar >= 30):
            pattern = [0, 8, 14] if fourth else [0, 8, 10]
            for i, s in enumerate(pattern):
                out[KICK].append({"pos": b0 + s, "velocity": hum(108 if i == 0 else 88)})
        # Clap on 2 and 4, lazy (a few ticks late); ghost before bar end
        if sec not in ("intro",) and not (sec == "outro" and bar >= 29):
            if sec == "break":
                out[CLAP].append({"pos": b0 + 12, "velocity": hum(78), "shift": 4})
            else:
                for s in (4, 12):
                    out[CLAP].append({"pos": b0 + s, "velocity": hum(96), "shift": 4})
                if fourth:
                    out[CLAP].append({"pos": b0 + 15, "velocity": hum(34, 4), "shift": 2})
        # Hats
        if sec == "intro":
            if bar >= 3:
                for s in (0, 4, 8, 12):
                    out[HAT].append({"pos": b0 + s, "velocity": hum(66)})
        elif not (sec == "outro" and bar >= 30):
            soft = sec == "break"
            for s in range(0, 16, 2):
                on_beat = s % 4 == 0
                v = (74 if on_beat else 54) if soft else (84 if on_beat else 58)
                out[HAT].append({"pos": b0 + s, "velocity": hum(v, 8),
                                 "shift": 0 if on_beat else 6,
                                 "pan": 64 + rnd.randint(-6, 6)})
            if fourth and sec in ("groove_a", "groove_b2", "groove_a2"):
                for s, v in ((13, 40), (15, 52)):
                    out[HAT].append({"pos": b0 + s, "velocity": hum(v, 4), "shift": 3})
    return out


def keys() -> list[dict]:
    notes: list[dict] = []
    for bar in range(BARS):
        sec = section(bar)
        t = bar * 4.0
        prog = PROG_B if sec in ("groove_b", "groove_b2") else PROG_A
        root, chord = prog[bar % 4]
        soft = sec in ("intro", "break", "outro")
        last = bar == BARS - 1
        dur = 3.9 if last else 3.75
        notes.append({"midi": root, "time": t, "duration": dur,
                      "velocity": round(0.6 + rnd.uniform(-0.04, 0.04), 2)})
        for k, m in enumerate(chord):
            notes.append({"midi": m, "time": round(t + 0.025 * k, 3), "duration": dur - 0.05,
                          "velocity": round((0.68 if soft else 0.62) + rnd.uniform(-0.04, 0.04), 2)})
        # Comping: re-hit the top two notes on the "and" of 3 in grooves
        if not soft and bar % 2 == 1:
            for m in chord[-2:]:
                notes.append({"midi": m, "time": t + 2.5, "duration": 1.25,
                              "velocity": round(0.45 + rnd.uniform(-0.03, 0.03), 2)})
    # Melodies: (first bar, motif)
    for first_bar, motif in ((8, MEL_A), (12, MEL_B), (20, MEL_A2), (24, MEL_B)):
        for m, tt, d in motif:
            notes.append({"midi": m, "time": first_bar * 4.0 + tt, "duration": d,
                          "velocity": round(0.6 + rnd.uniform(-0.04, 0.04), 2)})
    return notes


def send(c, action: str, params: dict | None = None) -> dict:
    r = c.send_command(action, params or {})
    if r.get("error"):
        raise RuntimeError(f"{action}: {r['error']}")
    return r


def write_keys(c, notes: list[dict]) -> dict:
    send(c, "ui.focusPianoRoll", {"channel": KEYS})
    time.sleep(0.5)
    resp = _get_response_file()
    if resp.exists():
        resp.unlink()
    _write_request([{"action": "clear"}, {"action": "add_notes", "notes": notes}])
    trigger_fl_studio()
    for _ in range(30):
        if resp.exists():
            try:
                return json.loads(resp.read_text())
            except json.JSONDecodeError:
                pass
        time.sleep(0.25)
    raise RuntimeError("piano roll script did not respond")


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    c = get_connection()
    c.ensure_connected()
    print("tempo", send(c, "transport.setTempo", {"bpm": BPM})["bpm"])
    if what in ("all", "drums"):
        for ch, steps in drums().items():
            r = c.send_command("channels.clearSteps", {"channel": ch, "length": STEPS}, timeout=15)
            print(r["channel_name"], "cleared, left", r["remaining"])
            for i in range(0, len(steps), 32):  # chunks keep each call well under the timeout
                send(c, "channels.setSteps", {"channel": ch, "steps": steps[i:i + 32], "clear": False})
            got = c.send_command("channels.getSteps", {"channel": ch, "length": STEPS}, timeout=15)
            assert len(got["steps"]) == len(steps), (r["channel_name"], len(got["steps"]), len(steps))
            print(r["channel_name"], "steps", len(got["steps"]), "verified")
        for ch, vol in ((KICK, 0.62), (CLAP, 0.42), (HAT, 0.3), (SNARE, 0.0), (ASTRO, 0.0)):
            send(c, "channels.setVolume", {"index": ch, "volume": vol})
    if what in ("all", "keys"):
        for pi, v in FL_KEYS_PARAMS:
            send(c, "plugins.setParamValue", {"plugin_index": KEYS, "param_index": pi, "value": v})
        send(c, "channels.setVolume", {"index": KEYS, "volume": 0.8})
        # Own mixer insert so keys can be pushed above unity (1.0 = +5.6 dB)
        send(c, "channels.routeToMixer", {"channel_index": KEYS, "mixer_track": 6})
        send(c, "mixer.setTrackName", {"track": 6, "name": "Keys"})
        send(c, "mixer.setTrackVolume", {"track": 6, "volume": 1.0})
        print("keys", write_keys(c, keys()))
    print("pattern", send(c, "patterns.info"))


if __name__ == "__main__":
    main()
