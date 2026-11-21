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
KICK, CLAP, HAT, SNARE, BASS, SYNTH, LEAD, NOISE = range(8)
NAMES = {"kick": KICK, "clap": CLAP, "hat": HAT, "snare": SNARE, "bass": BASS,
         "synth": SYNTH, "lead": LEAD, "noise": NOISE}
CYCLES = 8  # 8 x 2 bars = 16 bars
TL = {"loop": [float(i) for i in range(CYCLES * 8 + 1)]}  # beats; for listen.where

KICK_ALWAYS = [1.75, 2.0, 2.5, 2.75]       # beats inside the 2-bar cycle (0-based)
KICK_HALF = [0.0, 1.5, 4.5, 6.5]           # every other cycle
CLAP_AT = [1, 3, 5, 7]
# FLEX 808: midi 53 sounds F1 (measured on this kit) -> standard midi + 24
BASS_LINE = [(27, 0.0, 5.75), (31, 5.75, 0.25), (31, 6.0, 2.0)]  # (std midi, beat, len)
BASS_OFFSET = 24
# re-done on cleaner stems (htdemucs_ft on the whole track, 2026-11-21).
# SYNTH_STABS (only the hits repeating in 2+ cycles) was wrong - Nik: "the
# synth melody is not right": the full contour (pyin) is a steady 16th pulse
# on Eb (6 beats) then G (2 beats), see parts(). Kept for reference:
SYNTH_STABS = [(51, 0, .25), (51, .75, .25), (51, 1.5, .25), (63, 1.5, .25),
               (51, 1.75, .25), (51, 4, .5), (63, 4, .5), (55, 6, .5)]
# Nik: "the rhythm is not right" (the 16th pulse was wrong too). The synth's
# note starts (onset detection, same in both cycles) are long held notes
# re-hit on 16th-triplet positions: (beat in the cycle, root midi)
SYNTH_HITS = [(0.0, 51), (6.0, 55)]  # held like the bass: Eb for 6 beats, G for 2
# Nik: the sound is "a wave" in the bass and the synth stems: measured, the
# brightness swings ±460 Hz and the bass loudness ±6 dB once every 2 beats
# (1.1-1.27 Hz). The "triplet re-hits" were the peaks of that wave. -> held
# notes + a filter LFO (PoiZone 1.09 Hz = 2 beats at 135, retriggered by each
# note) and FLEX's Wobble on the 808.
SYNTH_LFO = [(18, 0.5), (26, 0.85), (27, 1.0), (28, 0.5195), (68, 0.5), (24, 0.9)]
BASS_WOBBLE = 0.6
# the vocal chant (almost one note, Bb3) on a voice-like synth lead
LEAD_LINE = [(58, 0, .25), (63, .25, .5), (58, 1, .25), (58, 1.25, .5), (58, 2, .25),
             (58, 2.25, .5), (58, 5.25, .5), (55, 6.5, .5), (58, 7, .5), (65, 7.5, .5)]
LEAD_PRESET = "LED Voxy Ethnic"
# a quiet bright noise bed (the hiss under the original's synth layer)
NOISE_PARAMS = [(9, 1.0), (18, 0.85), (19, 0.3), (24, 1.0)]  # noise, cutoff, res, sustain
BASS_DRIVE = 0.5  # FLEX 808 Drive: the original 808 is distorted (octave harmonics)
# PoiZone as FL adds it has the arpeggiator on and transposes -24: off / 0
SYNTH_PARAMS = [(17, 0.5), (69, 0.0), (47, 0.0), (11, 0.0)]
# The synth stab built from a measurement of the original (Nik: none of three
# presets was close): 2nd harmonic 6 dB over the fundamental -> saw + saw an
# octave up, louder; noisy (flatness 0.38) -> 30% noise; bright (centroid
# 4.3 kHz) -> filter open, no filter envelope; ~250 ms -> amp decay 293 ms.
# The fresh PoiZone's closed filter (34%, 2.5 s envelope), delay 56% and
# chorus made it a low hum.
SYNTH_PATCH = [(0, 0.0), (2, 0.0), (4, 0.0), (6, 0.75), (8, 0.62), (9, 0.3),
               (10, 0.5), (18, 0.92), (19, 0.2), (22, 0.0), (23, 0.35), (24, 0.25),
               (25, 0.2), (29, 0.3), (31, 0.0), (39, 0.0), (24, 0.6)]  # sustain up: held notes
VOLUMES = {KICK: 0.78, CLAP: 0.7, HAT: 0.45, SNARE: 0.0, BASS: 0.8, SYNTH: 0.55}
# measured (fl stems 1-2): the 808 was 15-40 dB over everything and the mix
# clipped (Nik: "it crackles") -> channel volume in dB, mixer tracks up
# target as in Dark 808: kick ~ 808, clap and synth -8 dB, hats -15 dB
MIX_DB = {KICK: -6.5, CLAP: -6.0, HAT: 0.0, BASS: -9.0, SYNTH: -8.0, LEAD: -0.5,
          NOISE: -10.0}
SYNTH_EQ = [{"band": 0, "freq_hz": 150, "gain_db": 0}]  # flat (the low end came from the patch)
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
        hits = SYNTH_HITS + [(8.0, None)]
        for (b, root), (b2, _) in zip(hits, hits[1:]):  # held until the next hit
            for m in (root, root + 12):
                n(P[SYNTH], m, o + b, b2 - b - 0.03, 0.85)
        for m, b, d in LEAD_LINE:
            n(P[LEAD], m, o + b, d - 0.02, 0.85)
        n(P[NOISE], 72, o, 7.9, 0.45)
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
    from fl_io import goto_preset
    names = send(c, "plugins.preset", {"channel": LEAD, "list": True}, timeout=15)["presets"]
    print("lead preset:", goto_preset(c, LEAD, names.index(LEAD_PRESET)))
    import json
    from pathlib import Path
    fresh = json.loads((Path(__file__).resolve().parent / "plugins" / "PoiZone_fresh.json")
                       .read_text())
    cmds += [("plugins.setParamValue", {"plugin_index": SYNTH, "param_index": prm["index"],
                                        "value": prm["value"]}) for prm in fresh]
    cmds += [("plugins.setParamValue", {"plugin_index": ch, "param_index": pi, "value": v})
             for ch in (SYNTH, NOISE) for pi, v in SYNTH_PARAMS]
    cmds += [("plugins.setParamValue", {"plugin_index": SYNTH, "param_index": pi, "value": v})
             for pi, v in SYNTH_PATCH + SYNTH_LFO]
    cmds += [("plugins.setParamValue",
              {"plugin_index": BASS, "param_index": 13, "value": BASS_WOBBLE})]
    cmds += [("plugins.setParamValue", {"plugin_index": LEAD, "param_index": pi, "value": v})
             for pi, v in ((69, 0.0), (47, 0.0), (31, 0.0), (39, 0.0))]  # no arp/gate/fx
    cmds += [("plugins.setParamValue", {"plugin_index": NOISE, "param_index": pi, "value": v})
             for pi, v in NOISE_PARAMS]
    cmds += [("plugins.setParamValue",
              {"plugin_index": BASS, "param_index": 11, "value": BASS_DRIVE})]
    cmds += [("channels.setName", {"index": LEAD, "name": "Lead (vocal chant)"}),
             ("channels.setName", {"index": NOISE, "name": "Noise bed"}),
             ("channels.routeToMixer", {"channel_index": LEAD, "mixer_track": 7}),
             ("channels.routeToMixer", {"channel_index": NOISE, "mixer_track": 8})]
    cmds += [("channels.setVolume", {"index": ch, "volume": v}) for ch, v in VOLUMES.items()]
    cmds += [("channels.volumeDb", {"index": ch, "db": db}) for ch, db in MIX_DB.items()]
    cmds += [("mixer.setTrackVolume", {"track": t, "volume": v}) for t, v in MIXER_VOL.items()]
    cmds += [("mixer.setEq", {"track": 6, **eq}) for eq in SYNTH_EQ]
    cmds += [("channels.setName", {"index": SYNTH, "name": "Synth (PoiZone)"}),
             ("channels.routeToMixer", {"channel_index": SYNTH, "mixer_track": 6})]
    batch(c, cmds, timeout=15)
    print(f"settings: {len(cmds)} commands ({time.time() - t0:.1f} s), "
          f"{CYCLES * 2} bars at {BASE_BPM} BPM, pattern {send(c, 'patterns.info')}")
