"""Dark 808 - a track with tempo changes and a 5/4 section, built only via MCP.

Tempo changes are baked into note times: every section is written in its own
"musical" tempo and converted to ticks of the fixed project tempo (BASE_BPM).
So speed-ups, slow-downs and ramps are part of the project (they survive an
export), no tempo automation clip needed. All parts are piano-roll notes.

Channels (project "808 Beat"): 0 Kick, 1 Clap, 2 HiHat, 3 Snare,
4 808 Astronomic (808 bass sample), 5 FL Keys (dirty), 6 PoiZone (saw synth).

Run:  uv run python tracks/dark_808.py [part ...]
      parts: kick clap hat snare bass keys saw (default: all)
"""

from __future__ import annotations

import json
import random
import sys
import time

from fl_studio_mcp.tools.piano_roll import (
    _clear_request_file,
    _get_response_file,
    _get_state_file,
    _write_request,
)
from fl_studio_mcp.utils.connection import get_connection
from fl_studio_mcp.utils.fl_trigger import trigger_fl_studio

BASE_BPM = 85.0
KICK, CLAP, HAT, SNARE, BASS, KEYS, SAW = range(7)

# (name, bars, beats per bar, tempo at start, tempo at end)
SECTIONS = [
    ("intro", 4, 4, 85, 85),
    ("verse", 8, 4, 85, 85),
    ("odd", 12, 5, 95, 95),     # 5/4, faster
    ("break", 4, 4, 70, 70),    # slows right down
    ("build", 2, 4, 70, 140),   # accelerando with a snare roll
    ("drop", 16, 4, 140, 140),  # double speed, grows every 4 bars
    ("outro", 4, 5, 85, 85),    # back to 5/4
]

# F minor / phrygian. Chords for the saw and keys, roots for the 808.
CH = {"Fm": [53, 56, 60], "Gb": [54, 58, 61], "Ebm": [51, 54, 58],
      "C": [52, 55, 60], "Db": [53, 56, 61]}
ROOT_808 = {"Fm": 53, "Gb": 54, "Ebm": 51, "C": 48, "Db": 49}
PROG = {"intro": ["Fm", "Fm", "Db", "C"], "verse": ["Fm", "Fm", "Db", "C"],
        "odd": ["Fm", "Gb", "Ebm", "C"], "break": ["Db", "C", "Fm", "Fm"],
        "build": ["Gb", "C"], "drop": ["Fm", "Fm", "Gb", "C"],
        "outro": ["Fm", "Gb", "Fm", "Fm"]}

LEAD = [(72, 0, .5), (75, .5, .5), (77, 1, .75), (78, 1.75, .25), (77, 2, .5),
        (75, 2.5, .5), (72, 3, 1), (68, 4.5, .5), (70, 5, .5), (72, 5.5, 1.5),
        (66, 7, .5), (65, 7.5, .5)]

rnd = random.Random(1808)


# ---------------------------------------------------------------- tempo map
def build_timeline():
    """Return {section: [start of each beat in project beats]} + total."""
    starts, pos = {}, 0.0
    for name, bars, bpb, t0, t1 in SECTIONS:
        n = bars * bpb
        beats = []
        for i in range(n + 1):
            beats.append(pos)
            if i < n:
                tempo = t0 + (t1 - t0) * (i / max(1, n - 1))
                pos += BASE_BPM / tempo
        starts[name] = beats
    return starts, pos


TL, TOTAL = build_timeline()
INFO = {name: (bars, bpb) for name, bars, bpb, _, _ in SECTIONS}


def at(sec: str, bar: int, beat: float) -> float:
    """Project time (in project beats) of a musical position."""
    bpb = INFO[sec][1]
    b = bar * bpb + beat
    i = min(int(b), len(TL[sec]) - 2)
    frac = b - i
    return TL[sec][i] + frac * (TL[sec][i + 1] - TL[sec][i])


def note(out, midi, sec, bar, beat, dur, vel):
    t0 = at(sec, bar, beat)
    t1 = at(sec, bar, beat + dur)
    out.append({"midi": midi, "time": round(t0, 4),
                "duration": round(max(0.02, t1 - t0 - 0.005), 4),
                "velocity": round(max(0.05, min(1.0, vel)), 3)})


def h(v, s=0.05):
    return v + rnd.uniform(-s, s)


# ---------------------------------------------------------------- parts
def parts():
    P = {k: [] for k in (KICK, CLAP, HAT, SNARE, BASS, KEYS, SAW)}
    for sec, (bars, bpb) in INFO.items():
        for bar in range(bars):
            chord = PROG[sec][bar % len(PROG[sec])]
            root = ROOT_808[chord]
            last4 = bar % 4 == 3
            # ---- kick + 808 pattern (beats)
            if sec == "verse":
                kicks = [0, 0.75, 2.5] if bar % 2 == 0 else [0, 1.75, 2.5, 3.25]
            elif sec == "odd":
                kicks = [0, 0.75, 3] if bar % 2 == 0 else [0, 1.5, 3, 4.25]
            elif sec == "drop":
                kicks = [0, 0.75, 2.5] if bar % 2 == 0 else [0, 1.5, 2.75, 3.5]
                if bar >= 8:
                    kicks = sorted(set(kicks + [3.75 if bar % 2 == 0 else 2.25]))
            elif sec == "build":
                kicks = [0]
            elif sec == "outro":
                kicks = [0, 3] if bar < 3 else [0]
            else:
                kicks = []
            for k in kicks:
                note(P[KICK], 60, sec, bar, k, 0.25, h(0.85 if k == 0 else 0.72))
            # 808: follow the kick, hold until the next hit
            if sec in ("verse", "odd", "drop", "outro"):
                hits = kicks + [bpb]
                for a, b in zip(hits, hits[1:]):
                    note(P[BASS], root, sec, bar, a, b - a, h(0.8 if a == 0 else 0.65))
            elif sec in ("intro", "break"):
                # the 808 sample decays fast: re-hit every 2 beats, no silent holes
                for a in (0, 2):
                    note(P[BASS], root, sec, bar, a, 2, h(0.62 if a == 0 else 0.5))
            elif sec == "build" and bar == 1:
                note(P[BASS], root, sec, bar, 0, 4, 0.7)
            # ---- clap
            if sec == "verse":
                note(P[CLAP], 60, sec, bar, 2, 0.25, h(0.9))
            elif sec == "odd":
                for c in (2, 4):
                    note(P[CLAP], 60, sec, bar, c, 0.25, h(0.85))
            elif sec == "drop":
                for c in (1, 3):
                    note(P[CLAP], 60, sec, bar, c, 0.25, h(0.95))
                    if bar >= 8:
                        note(P[SNARE], 60, sec, bar, c, 0.1, h(0.55))
                if bar >= 8 and bar % 2 == 1:
                    note(P[SNARE], 60, sec, bar, 3.75, 0.1, h(0.45))
            elif sec == "outro" and bar < 3:
                note(P[CLAP], 60, sec, bar, 2, 0.25, h(0.6))
            # ---- hats
            if sec in ("verse", "odd", "drop"):
                step = 0.25 if sec == "drop" and bar >= 8 else 0.5
                for i in range(int(bpb / step)):
                    b = i * step
                    acc = b in (0, 3) if sec == "odd" else b % 1 == 0
                    if step == 0.25 and b % 0.5:
                        note(P[HAT], 60, sec, bar, b, 0.06, h(0.3))
                        continue
                    note(P[HAT], 60, sec, bar, b, 0.1, h(0.62 if acc else 0.42))
                if last4 or (sec == "drop" and (bar % 2 == 1 or bar >= 12)):
                    # 16th-triplet roll on the last beat
                    for j in range(6):
                        note(P[HAT], 60, sec, bar, bpb - 1 + j / 6, 0.08, 0.35 + j * 0.06)
            elif sec == "intro" and bar >= 2:
                for b in range(4):
                    note(P[HAT], 60, sec, bar, b, 0.1, h(0.4))
            elif sec == "break":
                for b in range(4):
                    note(P[HAT], 60, sec, bar, b + 0.5, 0.1, h(0.4))
            elif sec == "outro" and bar < 2:
                for b in range(bpb):
                    note(P[HAT], 60, sec, bar, b, 0.1, h(0.38))
            # ---- snare roll in the build (8ths -> 16ths -> 32nds, crescendo)
            if sec == "build":
                div = 2 if bar == 0 else 4
                for i in range(4 * div):
                    b = i / div
                    if bar == 1 and b >= 3:
                        continue
                    note(P[SNARE], 60, sec, bar, b, 0.08, 0.3 + 0.5 * (bar * 4 + b) / 8)
                if bar == 1:
                    for j in range(8):
                        note(P[SNARE], 60, sec, bar, 3 + j / 8, 0.05, 0.75 + j * 0.03)
            # ---- dirty keys: break and outro chords, sustained
            if sec in ("break", "outro", "build"):
                kv = 0.85 if sec == "break" else 0.72
                for k, m in enumerate(CH[chord] + [CH[chord][0] + 12]):
                    note(P[KEYS], m, sec, bar, 0.02 * k, bpb - 0.1, h(kv))
            # ---- saw
            tones = CH[chord]
            if sec == "intro":
                for b in (0, 2.5):
                    for m in tones:
                        note(P[SAW], m + 12, sec, bar, b, 0.4, h(0.55))
            elif sec == "break":  # low pad under the dirty keys
                for m in tones:
                    note(P[SAW], m, sec, bar, 0, bpb - 0.1, h(0.45))
            elif sec == "verse":
                for b, d in ((0, .25), (0.75, .25), (1.5, .5), (3, .25), (3.5, .25)):
                    for m in tones:
                        note(P[SAW], m + 12, sec, bar, b, d, h(0.62))
            elif sec == "odd":
                arp = tones + [tones[0] + 12, tones[1] + 12]
                for i in range(bpb * 2):  # eighths, accents on 3+2 grouping
                    acc = i in (0, 6)
                    note(P[SAW], arp[i % len(arp)] + 12, sec, bar, i * 0.5, 0.4,
                         h(0.75 if acc else 0.5))
            elif sec == "drop":
                octs = {0: [0], 1: [12], 2: [0], 3: [0, 12]}[bar // 4]
                for m, b, d in LEAD:
                    if (bar % 2) * 4 <= b < (bar % 2) * 4 + 4:
                        v = 0.85 if b % 1 == 0 else 0.7
                        for o in octs:
                            note(P[SAW], m + o, sec, bar, b - (bar % 2) * 4, d,
                                 h(v if len(octs) == 1 else v * 0.8))
                for b in (0.5, 1.5, 2.5, 3.5):  # offbeat stabs under the lead
                    for m in tones:
                        note(P[SAW], m, sec, bar, b, 0.2, h(0.45))
            elif sec == "outro":
                if bar < 3:
                    for m in tones:
                        note(P[SAW], m + 12, sec, bar, 0, 1.5, h(0.4))
    return P


# ---------------------------------------------------------------- sound
SAW_PARAMS = [(18, 0.6), (19, 0.42), (15, 0.7), (29, 0.62), (25, 0.22),
              (31, 1.0), (38, 0.2), (34, 0.32), (39, 1.0), (42, 0.15)]
KEYS_PARAMS = [(14, 0.55), (2, 0.8), (4, 0.75), (11, 0.6), (12, 0.5)]
VOLUMES = {KICK: 0.72, CLAP: 0.55, HAT: 0.36, SNARE: 0.45, BASS: 0.72, KEYS: 0.62, SAW: 0.55}
NAMES = {"kick": KICK, "clap": CLAP, "hat": HAT, "snare": SNARE,
         "bass": BASS, "keys": KEYS, "saw": SAW}


def send(c, action, params=None, timeout=5.0):
    r = c.send_command(action, params or {}, timeout=timeout)
    if r.get("error"):
        raise RuntimeError(f"{action}: {r['error']}")
    return r


MARK_STEP = 511  # marker step used to prove which channel the piano roll shows


def run_piano_roll(requests):
    """Queue requests, press the script hotkey, return (response, state)."""
    resp, state = _get_response_file(), _get_state_file()
    for f in (resp, state):
        if f.exists():
            f.unlink()
    _clear_request_file()  # a leftover queue would be appended to (duplicate notes)
    _write_request(requests)
    trigger_fl_studio(delay=0.3)
    for _ in range(40):
        # an empty queue produces no response file, only the state export
        if state.exists() and (resp.exists() or not requests):
            try:
                st = json.loads(state.read_text())
                return (json.loads(resp.read_text()) if requests else {}), st
            except json.JSONDecodeError:
                pass
        time.sleep(0.2)
    return None, None


def focus_piano_roll(c, ch):
    """Point the piano roll at `ch` and prove it with a marker note: FL's
    focus caption is unreliable, and once notes landed in the previous channel."""
    send(c, "channels.setSteps", {"channel": ch, "steps": [{"pos": MARK_STEP}], "clear": False})
    tick = MARK_STEP * 24
    for _ in range(6):
        send(c, "ui.focusPianoRoll", {"channel": ch})
        time.sleep(0.5)
        _, st = run_piano_roll([])
        if st and any(n["time_ticks"] == tick for n in st["notes"]):
            return True
    raise RuntimeError(f"piano roll did not switch to channel {ch}")


def write_notes(c, ch, notes):
    send(c, "channels.clearSteps", {"channel": ch, "length": 1024}, timeout=30)
    for attempt in range(3):  # idempotent: the request starts with "clear"
        focus_piano_roll(c, ch)
        r, st = run_piano_roll([{"action": "clear"}, {"action": "add_notes", "notes": notes}])
        if r is not None:
            break
        print(f"  channel {ch}: no response, retry {attempt + 1}")
    else:
        raise RuntimeError(f"channel {ch}: piano roll script did not respond")
    if st["noteCount"] != len(notes):
        raise RuntimeError(f"channel {ch}: {st['noteCount']} notes in roll, expected {len(notes)}")
    return r


def main():
    want = [NAMES[a] for a in sys.argv[1:]] or list(NAMES.values())
    c = get_connection()
    c.ensure_connected()
    send(c, "tempo.setMap", {"points": [], "enabled": False})
    print("tempo", send(c, "transport.setTempo", {"bpm": BASE_BPM})["bpm"],
          "| length", round(TOTAL, 2), "beats =", round(TOTAL * 60 / BASE_BPM, 1), "s")
    for pi, v in SAW_PARAMS:
        send(c, "plugins.setParamValue", {"plugin_index": SAW, "param_index": pi, "value": v})
    for pi, v in KEYS_PARAMS:
        send(c, "plugins.setParamValue", {"plugin_index": KEYS, "param_index": pi, "value": v})
    send(c, "channels.routeToMixer", {"channel_index": SAW, "mixer_track": 7})
    send(c, "mixer.setTrackName", {"track": 7, "name": "Saw"})
    send(c, "mixer.setTrackVolume", {"track": 6, "volume": 0.8})
    send(c, "mixer.setTrackVolume", {"track": 0, "volume": 0.66})  # master: headroom
    for ch, v in VOLUMES.items():
        send(c, "channels.setVolume", {"index": ch, "volume": v})
    P = parts()
    for ch in want:
        r = write_notes(c, ch, P[ch])
        print(ch, "notes", r["notes_added"], "deleted", r["notes_deleted"])
    print("pattern", send(c, "patterns.info"))


if __name__ == "__main__":
    main()
