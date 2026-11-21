"""Study: the chorus of a hit, rebuilt in FL from an analysis (not a copy to
release - patterns to learn from). Source: chorus 2 (1:16-1:35) of a pop
track Nik provided, split with Demucs, notes with basic-pitch, drums with an
NMF decomposition folded on the 16th grid (see CLAUDE.md, 2026-11-21).

Measured: 135 BPM, key Eb, a 2-bar cycle:
  808   Eb1 for 6 beats, then G1 for 2 (a 16th pick-up before it), distorted
  clap  on 2 and 4 of every bar (all cycles)
  kick  a dense cluster in bar 1: 2+, 2a, 3, 3+, 3a (all cycles);
        1, 5+, 7+ in half the cycles
  hats  straight 16ths
  synth short stabs on Eb3 / Eb4 / G3

Project: FL's "Basic with limiter" template (0 Kick, 1 Clap, 2 HiHat,
3 Snare, 4 808 FLEX) + 5 PoiZone (synth). Run:
  bin/fl write --track sympathy_study --settings
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from fl_io import batch, send  # noqa: E402

BASE_BPM = 135.0
EPS = 1e-3
KICK, CLAP, HAT, SNARE, BASS, SYNTH = range(6)
NAMES = {"kick": KICK, "clap": CLAP, "hat": HAT, "snare": SNARE, "bass": BASS,
         "synth": SYNTH}
CYCLES = 8  # 8 x 2 bars = 16 bars
TL = {"loop": [float(i) for i in range(CYCLES * 8 + 1)]}  # beats; for listen.where

KICK_ALWAYS = [1.75, 2.0, 2.5, 2.75]       # beats inside the 2-bar cycle (0-based)
KICK_HALF = [0.0, 1.5, 4.5, 6.5]           # every other cycle
CLAP_AT = [1, 3, 5, 7]
# FLEX 808: midi 53 sounds F1 (measured on this kit) -> standard midi + 24
BASS_LINE = [(27, 0.0, 5.75), (31, 5.75, 0.25), (31, 6.0, 2.0)]  # (std midi, beat, len)
BASS_OFFSET = 24
SYNTH_STABS = [(51, 1.25, .25), (63, 1.5, .25), (55, 1.75, .25), (51, 2.5, .5),
               (51, 3.0, .5), (51, 3.75, .25), (51, 4.25, .5), (55, 6.5, .25),
               (55, 7.25, .25), (55, 7.5, .25)]
# PoiZone as FL adds it has the arpeggiator on and transposes -24: off / 0
SYNTH_PARAMS = [(17, 0.5), (69, 0.0), (47, 0.0), (11, 0.0)]
VOLUMES = {KICK: 0.78, CLAP: 0.7, HAT: 0.45, SNARE: 0.0, BASS: 0.8, SYNTH: 0.55}
# measured (fl stems 1-2): the 808 was 15-40 dB over everything and the mix
# clipped (Nik: "it crackles") -> channel volume in dB, mixer tracks up
# target as in Dark 808: kick ~ 808, clap and synth -8 dB, hats -15 dB
MIX_DB = {KICK: -6.5, CLAP: -6.0, HAT: 0.0, BASS: -13.0, SYNTH: -5.5}
MIXER_VOL = {3: 1.0, 6: 1.0, 0: 0.66}  # hat, synth +5.6 dB; master -4 dB (peak was +0.3)


def n(out, m, beat, d, v):
    out.append({"midi": m, "time": round(beat, 4), "duration": round(d, 4),
                "velocity": round(v, 3)})


def parts():
    P = {k: [] for k in NAMES.values()}
    for c in range(CYCLES):
        o = c * 8
        for b in KICK_ALWAYS + (KICK_HALF if c % 2 else []):
            n(P[KICK], 60, o + b, 0.25, 0.9 if b in (0.0, 2.0) else 0.78)
        for b in CLAP_AT:
            n(P[CLAP], 60, o + b, 0.25, 0.95)
        for i in range(32):
            n(P[HAT], 60, o + i / 4, 0.1, 0.62 if i % 2 == 0 else 0.42)
        for m, b, d in BASS_LINE:
            note = {"midi": m + BASS_OFFSET, "time": round(o + b, 4),
                    "duration": round(d - 0.01, 4), "velocity": 0.9}
            if b == 6.0:
                note["porta"] = True  # slide out of the pick-up
            P[BASS].append(note)
        for m, b, d in SYNTH_STABS:
            n(P[SYNTH], m, o + b, d, 0.8)
    return P


CHECKS: list = []


def final_parts(extra=()):
    import fl_edit
    P = parts()
    fl_edit.apply_all(P, fl_edit.load("sympathy_study"), NAMES, extra)
    return P


def apply_settings(c):
    t0 = time.time()
    cmds = [("transport.stop", {}), ("patterns.jump", {"index": 1, "name": "Sympathy study"}),
            ("transport.setTempo", {"bpm": BASE_BPM})]
    cmds += [("plugins.setParamValue", {"plugin_index": SYNTH, "param_index": pi, "value": v})
             for pi, v in SYNTH_PARAMS]
    cmds += [("channels.setVolume", {"index": ch, "volume": v}) for ch, v in VOLUMES.items()]
    cmds += [("channels.volumeDb", {"index": ch, "db": db}) for ch, db in MIX_DB.items()]
    cmds += [("mixer.setTrackVolume", {"track": t, "volume": v}) for t, v in MIXER_VOL.items()]
    cmds += [("channels.setName", {"index": SYNTH, "name": "Synth (PoiZone)"}),
             ("channels.routeToMixer", {"channel_index": SYNTH, "mixer_track": 6})]
    batch(c, cmds, timeout=15)
    print(f"settings: {len(cmds)} commands ({time.time() - t0:.1f} s), "
          f"{CYCLES * 2} bars at {BASE_BPM} BPM, pattern {send(c, 'patterns.info')}")
