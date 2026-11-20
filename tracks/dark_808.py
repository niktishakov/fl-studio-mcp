"""Dark 808 - a track with tempo changes and a 5/4 section, built only via MCP.

Tempo changes are baked into note times: every section is written in its own
"musical" tempo and converted to ticks of the fixed project tempo (BASE_BPM).
So speed-ups, slow-downs and ramps are part of the project (they survive an
export), no tempo automation clip needed. All parts are piano-roll notes.

Channels (project "808 Beat"): 0 Kick, 1 Clap, 2 HiHat, 3 Snare,
4 808 Astronomic (808 bass sample), 5 FL Keys (dirty), 6 PoiZone (saw synth),
8 Hook Piano (clean FL Keys, plays the hook).

Run:  uv run python tracks/dark_808.py [part ...] [--force]
      parts: kick clap hat snare bass keys saw piano saw2 (default: all)
      Only channels whose notes changed are written (tracks/.cache); --force
      rewrites them all; --verify reads the channels back and compares.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from fl_io import (  # noqa: E402
    MARK_STEP,  # noqa: F401  (used by tracks/hooks.py)
    batch,
    get_connection,
    goto_preset,
    run_piano_roll,  # noqa: F401
    send,
    verify,
    write_changed,
    write_notes,  # noqa: F401
)

BASE_BPM = 85.0
KICK, CLAP, HAT, SNARE, BASS, KEYS, SAW, SUB, PIANO, SAW2, FLEX, RISER, DOWN = range(13)
# SUB = 3x Osc (the old placeholder channel 7): a pure sine under the 808
# SAW2 = "PoiZone #2": same notes as the saw, angrier sound, plays the drop only

# (name, bars, beats per bar, tempo at start, tempo at end)
SECTIONS = [
    # "end of the world" twice: the 2nd time pushes (72 -> 76) straight into a
    # loud, clear hook - no calming, no slowing down (Nik). Keys only, no bass.
    ("prelude", 8, 4, [72, 72, 72, 72, 74, 76, 76, 76], None),
    ("intro", 4, 4, 85, 85),
    # verse -> odd (5/4) transition: last bar speeds up 85 -> 95 (no tempo jump)
    # verse: 16 bars developing in 4-bar blocks (the 5/4 part was removed);
    # the last bar slows 85 -> 72 into the break (70)
    ("verse", 16, 4, [85] * 15 + [(85, 72)], None),
    # bridge (Nik: "too weak"): 4 bars of emotion at 70, then 4 bars that
    # rebuild the groove and speed up; a noise riser carries it into the drop
    # Nik: FL bars 36-39 "shorter, angrier, less rhythm": one stomp bar less
    ("break", 7, 4, [70, 70, 70, 70, 75, 95, (105, 120)], None),
    ("build", 1, 4, [(120, 140)], None),  # snare roll, tempo reaches 140
    # double speed; 20 bars (8 + 8 + 4, Nik: longer), then the same 20 bars
    # again (Nik: "the drop twice as long") - see DROP_BARS / repeat_drop
    ("drop", 40, 4, 140, 140),
    # outro: the drop keeps going, then dies like a stopping tape (140 -> 58);
    # the prelude theme returns and the hook's low F is the last chord
    ("outro", 8, 4, [140, 140, (140, 120), (120, 100), (100, 88), (88, 78),
                     (78, 66), (66, 58)], None),
]

# F minor / phrygian. Chords for the saw and keys, roots for the 808.
CH = {"Fm": [53, 56, 60], "Gb": [54, 58, 61], "Ebm": [51, 54, 58],
      "C": [52, 55, 60], "Db": [53, 56, 61], "Bbm": [53, 58, 61], "Ab": [51, 56, 60]}
# 808 (FLEX "808 Astronomic"): midi 53 sounds F1 = 44 Hz (measured, fl tone).
# Roots kept in F1..E2 (44-82 Hz): the old Ab/Bb/C/Db at 26-35 Hz were below
# what headphones play - the 808 sounded like a kick (review 2026-11-20).
ROOT_808 = {"Fm": 53, "Gb": 54, "Ebm": 63, "C": 60, "Db": 61, "Bbm": 58, "Ab": 56}
BASS_RANGE = (53, 64)
SUB_VEL = 0.85  # 3x Osc plays an octave higher than the 808 for the same midi
PROG = {"prelude": ["Fm", "Db", "Bbm", "C"] * 2, "intro": ["Fm", "Fm", "Db", "C"],
        "verse": ["Fm", "Fm", "Db", "C"] * 2 + ["Bbm", "Db", "Gb", "C"]
        + ["Fm", "Fm", "Db", "C"],
        # break ends on C (dominant): the ear wants to land - on the drop
        "break": ["Db", "Ab", "Bbm", "C", "Db", "Ab", "C"],
        "build": ["C"], "drop": ["Fm", "Fm", "Gb", "C"],
        "outro": ["Fm", "Fm", "Gb", "C", "Fm", "Db", "C", "Fm"]}

# The hook ("Fate 3 - cry", chosen by Nik): a high cry that holds, then
# collapses to the bottom. Soft clean piano (channel 8). (midi, beat, len, vel)
HOOK = [(73, 0.0, 1.5, 0.78), (72, 1.5, 0.45, 0.62), (65, 2.0, 0.45, 0.6),
        (53, 2.5, 4.0, 0.72), (41, 2.5, 4.0, 0.55)]
# Short form: just the cry Db -> C.
# Drums (2026-11-20, "stock FL sounds"): Nik chose ModeAudio samples by ear,
# unprocessed - they are loaded by dragging (fl_drums / CLAUDE.md), the stock
# 808 kit processing (clap -2 st, band-pass, EQ boosts) is off.
DRUM_SAMPLES = {KICK: "Kicks/Power CrunchKick 02.wv", CLAP: "Claps/Power Clap 01.wv",
                HAT: "Hi Hats/Power CHat 01.wv", SNARE: "Snares/MA Trauma Snare 01.wv"}
CLAP_PITCH = 60  # was 58 for the stock clap
PRELUDE_HOOK_VEL = 1.0  # measured: same level as prelude pass 2 (Nik)
HOOK_FRAG = [(73, 0.0, 1.0, 0.8), (72, 1.0, 1.5, 0.7)]
# Where it sounds: (section, bar, beat, channel, "full"/"frag"). The stock clean
# piano did not fit the track (Nik), so the dirty keys and the saw play it.
# Prelude: the hook closes the melody; its last (low F) note lands exactly on
# the first beat of the main part.
HOOK_AT = [("prelude", 7, 4.0 - HOOK[-1][1], KEYS, "full"),
           ("outro", 6, 4.0 - HOOK[-1][1], KEYS, "full")]  # the last word
# In the main part the hook is played by the 808 itself, gliding (Nik: the
# keys/saw hooks did not fit). It replaces the bass line in its window, only
# at key moments: end of the verse, start of the break, drop bars 1 and 9,
# and last in the outro. (midi, beat, len, vel, glide-in)
HOOK_808 = [(61, 0.0, 1.5, 0.92, False), (60, 1.5, 0.5, 0.85, True),
            (65, 2.0, 0.5, 0.85, True), (53, 2.5, 3.5, 0.95, True)]
HOOK_808_AT = [("drop", 0, 0), ("drop", 8, 0), ("drop", 16, 0)]  # only in the drop (Nik)
PORTA_808 = 0.35  # channel portamento time (normalized)
DRIFT = 0.012     # +-beats of timing drift in the 5/4 arp (~ +-8 ms at 95 BPM)
# break -> drop (Nik: the riser + 16th hats + snare roll build was "cheap"):
# build bar stutters faster and faster, beat 4 is dead silent, then the drop hits
ONE_NOTE_FROM_FL_BAR = 26  # Nik: PoiZone on one note from here to the end
ONE_NOTE = (65, 77)  # F in two octaves
KEYS_MID_GAIN = 1.2  # Nik: keys +20% in FL bars 15-24
STOMPS = {5: [0], 6: [0]}  # break bars 6-7: the saw holds one note per bar
BUILD_STUTTER = [0, 1, 2, 2.5]  # then silence from beat 3 (Nik: less rhythm)
EPS = 1e-3  # note times are rounded to 4 decimals: compare section bounds with a margin
# Drop bars 9-12: a rough riff on the hook notes (2 bars, second one falls).
DROP_RIFF = [[(73, 0, .75), (72, .75, .75), (65, 1.5, .5), (68, 2, .5), (65, 2.5, 1.5)],
             [(73, 0, .75), (72, .75, .75), (65, 1.5, .5), (63, 2, .5), (60, 2.5, 1.5)]]

# Prelude: an epic, slow proclamation of the end of the world, in octaves on
# the dirty keys. It ends on Eb; the next note is the hook's Db (intro bar 1),
# a step down - the melody flows straight into the hook. (midi, beat, len, vel)
PRELUDE = [(65, 0, 1.5, .8), (68, 1.5, .5, .7), (72, 2, 2, .85),
           (73, 4, 1.5, .88), (72, 5.5, .5, .72), (68, 6, 1, .75), (67, 7, 1, .72),
           (65, 8, 1, .78), (68, 9, 1, .82), (73, 10, 2, .9),
           (75, 12, 1.5, .95)]  # the hook follows right after (HOOK_AT)

# Verse block C: the prelude theme, one bar per line. (midi, beat, len, vel)
VERSE_THEME = [[(65, 0, 1.5, .72), (68, 1.5, .5, .62), (72, 2, 2, .78)],
               [(73, 0, 1.5, .8), (72, 1.5, .5, .65), (68, 2, 1, .68), (67, 3, 1, .64)],
               [(65, 0, 1, .7), (68, 1, 1, .74), (73, 2, 2, .82)],
               [(75, 0, 3.5, .85)]]

LEAD = [(72, 0, .5), (75, .5, .5), (77, 1, .75), (78, 1.75, .25), (77, 2, .5),
        (75, 2.5, .5), (72, 3, 1), (68, 4.5, .5), (70, 5, .5), (72, 5.5, 1.5),
        (66, 7, .5), (65, 7.5, .5)]



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
                if isinstance(t0, list):  # per-bar tempo: number or (start, end) ramp
                    tb = t0[i // bpb]
                    a, z = tb if isinstance(tb, tuple) else (tb, tb)
                    tempo = a + (z - a) * ((i % bpb) / max(1, bpb - 1))
                else:
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


def note(out, midi, sec, bar, beat, dur, vel, **extra):
    t0 = at(sec, bar, beat)
    t1 = at(sec, bar, beat + dur)
    if isinstance(vel, tuple):  # humanized: jitter derived from the note itself,
        _, v, s = vel           # so edits elsewhere don't change it (cache stays valid)
        d = hashlib.md5(f"{midi}|{t0:.4f}|{v}|{s}".encode()).digest()
        vel = v + (2 * int.from_bytes(d[:4], "big") / 2**32 - 1) * s
    out.append({"midi": midi, "time": round(t0, 4),
                "duration": round(max(0.02, t1 - t0 - 0.005), 4),
                "velocity": round(max(0.05, min(1.0, vel)), 3), **extra})


def hash_unit(*key):
    """Deterministic 0..1 from a key (no shared RNG: edits elsewhere don't shift it)."""
    d = hashlib.md5("|".join(map(str, key)).encode()).digest()
    return int.from_bytes(d[:4], "big") / 2**32


def h(v, s=0.05):
    """Humanized velocity: resolved per note in note()."""
    return ("h", v, s)


# Nik: the verse's bass line of FL bars 22-26 (block C: Bb Db Gb C) comes
# back in the middle of the drop - from the return of the full beat (FL bar
# 45), 4 drop bars, with the drop's rhythm. (drop bar index 0-based)
# Tried {11: "Bbm", 12: "Db", 13: "Gb", 14: "C"} - Nik: "came out bad".
DROP_VERSE_BASS = {}


def chord_at(sec, bar):
    if sec == "drop" and bar in DROP_VERSE_BASS:
        return DROP_VERSE_BASS[bar]
    return PROG[sec][bar % len(PROG[sec])]


DROP_BARS = 20  # one pass of the drop; it plays twice


def repeat_drop(P):
    """The drop's second half = an exact copy of the first 20 bars (every
    channel: drums, 808, sub, saw layers, pad, hooks, stops)."""
    d0, dm = TL["drop"][0], TL["drop"][DROP_BARS * 4]
    shift = dm - d0
    for ch in P:
        P[ch] += [{**n, "time": round(n["time"] + shift, 4)}
                  for n in P[ch] if d0 - EPS <= n["time"] < dm - EPS]


# ---------------------------------------------------------------- parts
def parts():
    P = {k: [] for k in (KICK, CLAP, HAT, SNARE, BASS, KEYS, SAW, SUB, PIANO, SAW2, FLEX,
                         RISER, DOWN)}
    for rep in (0, 1):  # the prelude melody twice, doubled an octave below
        for i, (m, b, d, v) in enumerate(PRELUDE):
            last = i == len(PRELUDE) - 1
            if rep == 0 and last:
                d = 2.5  # 1st time: Eb, then two linking notes lead into pass 2
                for lm, lb, ld in ((68, 14.5, 0.75), (67, 15.25, 0.75)):  # Ab, G -> F
                    for o in (0, -12):
                        note(P[KEYS], lm + o, "prelude", 0, lb, ld,
                             min(1.0, v * 1.12) * (1 if o == 0 else 0.88))
            octs, vel = (0, -12), min(1.0, v * 1.12)
            if rep == 1 and not last:  # 2nd time: denser (three octaves), NOT louder
                octs, vel = (12, 0, -12), min(1.0, v * 1.12) * 0.88
            for o in octs:
                note(P[KEYS], m + o, "prelude", 0, rep * 16 + b, d,
                     vel if o == 0 else vel * 0.88)
    for sec, bar, beat, ch, form in HOOK_AT:
        # keys hooks go to "Hook Keys" (ch 8): same dirty sound, own level -
        # the hook stands out without touching the rest of the keys
        boost = 1.1 if ch == KEYS else 1.2
        ch = PIANO if ch == KEYS else ch
        if sec in ("prelude", "outro"):  # clear (chords stop under it):
            for m, b, d, v in HOOK:  # the quieter main keys channel, not Hook Keys
                note(P[KEYS], m, sec, bar, beat + b, d, PRELUDE_HOOK_VEL)
            continue
        for m, b, d, v in (HOOK if form == "full" else HOOK_FRAG):
            note(P[ch], m, sec, bar, beat + b, d, min(1.0, v * boost))
    for sec, (bars, bpb) in INFO.items():
        for bar in range(bars):
            if sec == "drop" and bar >= DROP_BARS:  # copied by repeat_drop
                continue
            chord = chord_at(sec, bar)
            root = ROOT_808[chord]
            last4 = bar % 4 == 3
            # ---- kick + 808 pattern (beats)
            # verse blocks: A 1-4 base, B 5-8 more motion, C 9-12 new harmony
            # + melody, D 13-16 build; on the very last beat a loud snare fill
            # plays ON TOP of the groove (Nik: layered, accented - not a gap)
            blk = bar // 4 if sec == "verse" else -1
            breath = sec == "verse" and bar == INFO["verse"][0] - 1
            if sec == "verse":
                kicks = [0, 0.75, 2.5] if bar % 2 == 0 else [0, 1.75, 2.5, 3.25]
                if blk == 1 and bar % 2 == 1:
                    kicks = kicks + [3.75]
                elif blk == 2:
                    kicks = [0, 0.75, 1.5, 2.5] if bar % 2 == 0 else [0, 1.75, 2.5, 3.25]
                elif blk == 3:
                    kicks = [0, 0.75, 2.5, 3.5]
            elif sec == "drop" and bar in HALFTIME:  # half-time: heavy and sparse
                kicks = [0, 1.75] if bar % 2 == 0 else [0, 0.75, 3.5]
            elif sec == "drop":
                kicks = [0, 0.75, 2.5] if bar % 2 == 0 else [0, 1.5, 2.75, 3.5]
            elif sec == "intro" and bar == 3:  # half-time preview of the verse groove
                kicks = [0, 2, 3.5]
            elif sec == "break":  # bars 5-8 rebuild the groove step by step
                # 6-8 (FL bars 36-38): no groove, only stomps that double each bar
                kicks = STOMPS.get(bar, {4: [0, 2]}.get(bar, []))
            elif sec == "build":  # machine-gun stutter, beat 4 is dead silent
                kicks = BUILD_STUTTER[::2]
            elif sec == "outro":  # drop groove -> half-time -> one hit -> nothing
                kicks = ([0, 0.75, 2.5] if bar % 2 == 0 else [0, 1.5, 2.75, 3.5]) if bar < 2 \
                    else [0, 2.5] if bar < 4 else [0] if bar < 6 else []
            else:
                kicks = []
            for k in kicks:
                note(P[KICK], 60, sec, bar, k, 0.25, h(0.85 if k == 0 else 0.72))
            # 808: follow the kick, hold until the next hit
            if sec == "break" and bar in STOMPS:  # stomps: short 808 hits, no hold
                for a in kicks:
                    note(P[BASS], root, sec, bar, a, 0.4 if bar < 7 else 0.3,
                         1.0 if (bar, a) == (5, 0) else h(0.8))
            elif sec in ("verse", "drop", "outro") or (sec == "break" and bar >= 4):
                hits = kicks + [bpb]
                for a, b in zip(hits, hits[1:]):
                    note(P[BASS], root, sec, bar, a, b - a, h(0.8 if a == 0 else 0.65))
            elif sec in ("intro", "break"):
                # the 808 sample decays fast: re-hit every 2 beats, no silent holes
                for a in (0, 2):
                    note(P[BASS], root, sec, bar, a, 2, h(0.62 if a == 0 else 0.5))
            # build: no 808 (Nik heard the high 808 + dive as an extra bass)
            # ---- clap
            if sec == "verse":
                note(P[CLAP], 60, sec, bar, 2, 0.25, h(0.9))
                if blk >= 2 and bar % 2 == 1:  # ghost clap before the next bar
                    note(P[CLAP], 60, sec, bar, 3.75, 0.2, h(0.38, 0.03))
            elif sec == "break" and bar >= 4:
                for c in STOMPS.get(bar, (2,)):
                    note(P[CLAP], 60, sec, bar, c, 0.25,
                         1.0 if (bar, c) == (5, 0) else h(0.85))
            elif sec == "drop":  # kick first, then the clap: backbeat on 2 and 4 (Nik)
                for c in ((2,) if bar in HALFTIME else (1, 3)):  # half-time: on 3
                    note(P[CLAP], 60, sec, bar, c, 0.25, h(0.95))
                if bar == 0:  # impact on the drop's first beat: everything at once
                    # no clap on the 1: it hid the kick (Nik: "kick first, then clap")
                    note(P[SNARE], 60, sec, bar, 0, 0.2, 1.0)
                    note(P[HAT], 60, sec, bar, 0, 0.2, 0.9)
            elif sec == "outro" and bar < 2:
                for c in (1, 3):
                    note(P[CLAP], 60, sec, bar, c, 0.25, h(0.9 - 0.1 * bar))
            elif sec == "outro" and bar < 4:
                note(P[CLAP], 60, sec, bar, 2, 0.25, h(0.7 - 0.1 * (bar - 2)))
            # ---- hats
            if sec == "verse" and blk == 3:  # build: 16ths, growing over 4 bars
                for i in range(16):
                    gv = 0.32 + 0.025 * ((bar - 12) * 4 + i // 4) + (0.12 if i % 4 == 0 else 0)
                    note(P[HAT], 60, sec, bar, i / 4, 0.06, h(gv, 0.03))
            elif sec in ("verse", "drop"):
                step = 1.0 if sec == "drop" and bar in HALFTIME else 0.5
                for i in range(int(bpb / step)):
                    b = i * step
                    note(P[HAT], 60, sec, bar, b, 0.1, h(0.62 if b % 1 == 0 else 0.42))
                if blk == 1:  # B: 16th pick-ups
                    for b in (1.75, 3.75):
                        note(P[HAT], 60, sec, bar, b, 0.06, h(0.34, 0.03))
                if breath:  # the fill: 16ths into 32nds, growing to full strength
                    for j, b in enumerate((3, 3.25, 3.5, 3.625, 3.75, 3.875)):
                        note(P[SNARE], 60, sec, bar, b, 0.06, min(1.0, 0.7 + 0.06 * j))
                    for b, v in ((3, 0.85), (3.75, 1.0)):  # clap marks the fill
                        note(P[CLAP], 60, sec, bar, b, 0.25, v)
                elif last4 or (sec == "drop" and bar % 2 == 1
                               and (bar not in HALFTIME or bar == HALFTIME[-1])):
                    # 16th-triplet roll on the last beat
                    for j in range(6):
                        note(P[HAT], 60, sec, bar, bpb - 1 + j / 6, 0.08, 0.35 + j * 0.06)
            elif sec == "intro" and bar == 2:
                for b in range(4):
                    note(P[HAT], 60, sec, bar, b, 0.1, h(0.4))
            elif sec == "intro" and bar == 3:  # build into the verse: 16ths growing
                for i in range(16):
                    note(P[HAT], 60, sec, bar, i / 4, 0.06,
                         0.45 + 0.028 * i + (0.1 if i % 4 == 0 else 0))
            elif sec == "break":
                if bar < 4:      # emotion: soft off-beats
                    for b in range(4):
                        note(P[HAT], 60, sec, bar, b + 0.5, 0.1, h(0.38))
                elif bar == 4:   # quarters
                    for b in range(4):
                        note(P[HAT], 60, sec, bar, b, 0.1, h(0.45))
                # bars 6-8: no hats - only stomps (Nik: the old 16th build was cheap)
            elif sec == "outro" and bar < 4:  # 8ths -> quarters, fading
                for b in ([i * 0.5 for i in range(8)] if bar < 2 else range(4)):
                    note(P[HAT], 60, sec, bar, b, 0.1,
                         h((0.6 if b % 1 == 0 else 0.4) - 0.07 * bar))
            # ---- snare roll in the build: one bar, 16ths -> 32nds, crescendo
            if sec == "build":  # stutter with the kick; the last beat is empty
                for i, b in enumerate(BUILD_STUTTER):
                    note(P[SNARE], 60, sec, bar, b, 0.05, min(1.0, 0.6 + 0.04 * i))
            # ---- dirty keys: break and outro chords, sustained
            if sec == "prelude":  # no bass here: a fat piano fills the space
                r, t, f = CH[chord]
                voicing = [r - 12, r, t, f, r + 12, t + 12]
                if bar < 4:          # 1st pass: wide sustained chords
                    for k, m in enumerate(voicing):
                        note(P[KEYS], m, sec, bar, 0.03 * k, 3.95, h(0.9, 0.04))
                elif bar < 8:        # 2nd pass: more notes, same strength -
                    for m in voicing[:2]:  # low sustain + pulsing 8th chords
                        note(P[KEYS], m, sec, bar, 0, 3.95 if bar < 7 else 1.45, h(0.9, 0.04))
                    hook_in = 4.0 - HOOK[-1][1]  # last bar: chords stop when the hook enters
                    for q in range(8):
                        if bar == 7 and q * 0.5 >= hook_in:
                            break
                        for k, m in enumerate(voicing[2:] + [f + 12]):
                            note(P[KEYS], m, sec, bar, q * 0.5 + 0.02 * k, 0.45,
                                 h(0.67 if q % 2 else 0.75, 0.03))  # measured: same RMS as pass 1
            if sec == "verse" and blk >= 1:  # quiet dirty-keys pad from block B
                for k, m in enumerate(CH[chord]):
                    note(P[KEYS], m, sec, bar, 0.03 * k, bpb - 0.1, h(0.42, 0.03))
            if sec == "outro" and bar >= 4:
                # the prelude returns: theme (bars 5-6) over wide chords, then
                # the hook on the keys (bar 7) lands its low F on the last chord
                r, t, f = CH[chord]
                if bar < 7:  # in bar 7 the chord stops when the hook enters
                    for k, m in enumerate([r - 12, r, t, f, r + 12]):
                        note(P[KEYS], m, sec, bar, 0.03 * k,
                             3.95 if bar < 6 else 4.0 - HOOK[-1][1] - 0.05, h(0.72, 0.03))
                else:
                    # final Fm; its low F + F come from the hook (no stacked notes)
                    for k, m in enumerate([56, 60, 65, 68]):
                        note(P[KEYS], m, sec, bar, 0.04 * k, 4.0, h(0.8, 0.03))
                if bar in (4, 5):
                    for m, b, d, v in VERSE_THEME[bar - 4]:
                        for o in (0, 12):
                            note(P[KEYS], m + o, sec, bar, b, d, v * (1 if o == 0 else 0.85))
            if sec == "break":
                r, t, f = CH[chord]
                if bar < 4:  # emotion: wide chords + the prelude theme in octaves
                    for k, m in enumerate([r - 12, r, t, f, r + 12]):
                        note(P[KEYS], m, sec, bar, 0.03 * k, 3.95, h(0.72, 0.03))
                    for m, b, d, v in VERSE_THEME[bar]:
                        for o in (0, 12):
                            note(P[KEYS], m + o, sec, bar, b, d, v * (1 if o == 0 else 0.85))
                elif bar < 5:  # rebuild: quieter pad under the groove
                    for k, m in enumerate([r, t, f]):
                        note(P[KEYS], m, sec, bar, 0.03 * k, 3.95, h(0.55, 0.03))
            # ---- saw
            tones = CH[chord]
            # Intro + verse: smeared, not staccato - each chord rings into the
            # next one (small overlap) and is strummed instead of hit at once.
            if sec == "intro":
                for b, d in ((0, 2.75), (2.5, 1.75)):
                    for k, m in enumerate(tones):
                        note(P[SAW], m + 12, sec, bar, b + 0.03 * k, d, h(0.5))
            elif sec == "break":
                if bar < 4:    # low pad under the keys
                    for m in tones:
                        note(P[SAW], m, sec, bar, 0, bpb - 0.1, h(0.4 + 0.03 * bar))
                elif bar < 5:  # the theme moves up to the saw
                    for m, b, d, v in VERSE_THEME[bar - 4]:
                        note(P[SAW], m + 12, sec, bar, b, d, h(v, 0.03))
                else:  # stomps: the saw growls from hit to hit (angrier than stabs)
                    hits = STOMPS[bar] + [bpb]
                    for a, b in zip(hits, hits[1:]):
                        for m in tones + [tones[0] + 12]:  # no low note: the 808 is the bass
                            note(P[SAW], m, sec, bar, a, b - a, 1.0)  # no gap
            elif sec == "build":  # the one-note saw holds through the build into
                note(P[SAW], tones[0], sec, bar, 0, bpb, 1.0)  # the drop (Nik: seamless)
            if sec == "verse" and blk == 2:
                # C: the prelude's "end of the world" theme returns on the saw
                for m, b, d, v in VERSE_THEME[bar % 4]:
                    drift = DRIFT * (2 * (hash_unit(m, bar, b) - 0.5))
                    note(P[SAW], m + 12, sec, bar, b + drift, d, h(v, 0.04))
            elif sec == "verse" and blk == 3:
                # D (FL bars 26-29): one note, F in 2 octaves, on the angry
                # PoiZone #2 (moved to SAW2 at the end) - Nik: "one note, angrier"
                for b, d in ((0, .95), (0.75, .95), (1.5, 1.7), (3, .7), (3.5, .75)):
                    for m in (65, 77):
                        note(P[SAW], m, sec, bar, b, d, h(0.62 if b in (0, 1.5) else 0.5))
            elif sec == "verse":
                for b, d in ((0, .95), (0.75, .95), (1.5, 1.7), (3, .7), (3.5, .75)):
                    for k, m in enumerate(tones):
                        note(P[SAW], m + 12, sec, bar, b + 0.025 * k, d,
                             h(0.62 if b in (0, 1.5) else 0.5))
                    if blk >= 1 and b in (0, 1.5):  # B, D: accents get an octave on top
                        note(P[SAW], tones[0] + 24, sec, bar, b, d, h(0.5, 0.04))
            elif sec == "drop":
                # simple and rough; bars 1-8 original lead, 9-12 a riff on the
                # hook, 13-16 lead an octave up (hook fragment at the very end)
                if 8 <= bar < 12:
                    for m, b, d in DROP_RIFF[bar % 2]:
                        note(P[SAW], m, sec, bar, b, d, h(0.85 if b % 1 == 0 else 0.72))
                else:
                    octave = 12 if bar >= 12 or bar % 8 >= 4 else 0
                    for m, b, d in LEAD:
                        if (bar % 2) * 4 <= b < (bar % 2) * 4 + 4:
                            v = 0.85 if b % 1 == 0 else 0.7
                            note(P[SAW], m + octave, sec, bar, b - (bar % 2) * 4, d, h(v))
                for b in (0.5, 1.5, 2.5, 3.5):  # offbeat stabs under the lead
                    for m in tones:
                        note(P[SAW], m, sec, bar, b, 0.2, h(0.45))
            elif sec == "outro" and bar < 4:
                # continues the drop: lead's last phrase, then the hook riff
                if bar < 2:
                    for m, b, d in LEAD:
                        if (bar % 2) * 4 <= b < (bar % 2) * 4 + 4:
                            note(P[SAW], m + 12, sec, bar, b - (bar % 2) * 4, d,
                                 h(0.8 if b % 1 == 0 else 0.66))
                else:
                    for m, b, d in DROP_RIFF[bar % 2]:
                        note(P[SAW], m, sec, bar, b, d, h(0.72 - 0.08 * (bar - 2)))
    # 808 hook: clear the bass line in the hook's window, then the gliding hook
    for sec, bar, beat in HOOK_808_AT:
        w0 = at(sec, bar, beat)
        w1 = at(sec, bar, beat + HOOK_808[-1][1] + HOOK_808[-1][2])
        kept = []
        for n in P[BASS]:
            if w0 - EPS <= n["time"] < w1 - EPS:  # EPS: note times are rounded
                continue
            if n["time"] < w0 - EPS < n["time"] + n["duration"]:
                n = {**n, "duration": round(w0 - n["time"] - 0.005, 4)}
            kept.append(n)
        P[BASS] = kept
        for m, b, d, v, glide in HOOK_808:
            extra = {"porta": True} if glide else {}
            note(P[BASS], m, sec, bar, beat + b, d, v, **extra)
    P[BASS].sort(key=lambda n: n["time"])
    # Dirty clap (Nik): 2 semitones lower, a second hit 15 ms later (flam) and
    # a quiet 808 snare under every clap.
    flam = 0.015 * BASE_BPM / 60  # 15 ms in project beats
    claps = P[CLAP]
    P[CLAP] = []
    for n in claps:
        P[CLAP].append({**n, "midi": CLAP_PITCH})
        if not (TL["drop"][0] - EPS <= n["time"] < TL["drop"][16] - EPS):  # no flam
            P[CLAP].append({**n, "midi": CLAP_PITCH,               # in drop bars 1-4
                            "time": round(n["time"] + flam, 4),
                            "velocity": round(n["velocity"] * 0.6, 3)})
        P[SNARE].append({**n, "midi": 60, "velocity": round(n["velocity"] * 0.4, 3)})
    # Transitions (Nik: "too abrupt, sounds like an FL project"):
    # risers into a part, impacts on its first beat, downlifters when energy drops
    note(P[RISER], 60, "intro", 3, 0, 3.95, 0.85)            # intro -> verse
    for j in range(4):                                        # + snare fill
        note(P[SNARE], 60, "intro", 3, 3 + j / 4, 0.06, 0.6 + 0.1 * j)
    for sec, hits in (("intro", ((BASS, ROOT_808["Fm"], 0.95), (HAT, 60, 0.6))),
                      ("verse", ((SNARE, 60, 0.55), (HAT, 60, 0.7))),  # no clap on the 1 (Nik)
                      ("break", ((BASS, ROOT_808["Db"], 0.85), (HAT, 60, 0.5)))):
        for ch, m, v in hits:                                 # impacts on bar 1
            note(P[ch], m, sec, 0, 0, 0.3 if ch != BASS else 2, v)
    note(P[DOWN], 60, "break", 0, 0, 6, 0.7)                  # verse -> break
    note(P[DOWN], 60, "outro", 4, 0, 4, 0.6)                  # outro: drums leave
    # Nik: FL Keys 20% louder in FL bars 15-24 (FL's counter: 4 beats per bar)
    for n in P[KEYS]:
        if (15 - 1) * 4 - EPS <= n["time"] < 24 * 4 - EPS:
            n["velocity"] = round(min(1.0, n["velocity"] * KEYS_MID_GAIN), 3)
    # Pad (FLEX) under the piano: one chord per bar
    for sec, bars in (("prelude", range(8)), ("break", range(4)), ("outro", range(4, 8))):
        for bar in bars:
            r, t, f = CH[PROG[sec][bar % len(PROG[sec])]]
            for k, m in enumerate((r, t, f, r + 12)):
                note(P[FLEX], m, sec, bar, 0.05 * k, 3.9, h(0.5, 0.03))
    # review: "no wholeness" - the same dark pad runs quietly under the intro
    # and the verse too, so it ties prelude, verse, break and drop together
    for sec in ("intro", "verse"):
        for bar in range(INFO[sec][0]):
            r, t, f = CH[PROG[sec][bar % len(PROG[sec])]]
            for k, m in enumerate((r - 12, r, t, f)):
                note(P[FLEX], m, sec, bar, 0.04 * k, 3.9, h(PAD_GLUE_VEL, 0.03))
    # No stacked duplicates (same pitch at the same time = +6 dB on that hit):
    # merge them, keeping the stronger velocity and the longer note.
    for ch, notes in P.items():
        merged = {}
        for n in notes:
            key = (n["midi"], n["time"])
            if key in merged:
                o = merged[key]
                merged[key] = {**o, "velocity": max(o["velocity"], n["velocity"]),
                               "duration": max(o["duration"], n["duration"]),
                               **({"porta": True} if n.get("porta") or o.get("porta") else {})}
            else:
                merged[key] = n
        P[ch] = sorted(merged.values(), key=lambda n: (n["time"], n["midi"]))
    # The drop is played by the angrier PoiZone #2 - the very same notes.
    d0, d1 = TL["drop"][0], at("outro", 4, 0)  # the drop saw carries on into the outro
    v0, v1 = at("verse", 12, 0), TL["break"][0]  # verse D: the one-note part

    def angry(n):
        return d0 - EPS <= n["time"] < d1 - EPS or v0 - EPS <= n["time"] < v1 - EPS
    P[SAW2] = [n for n in P[SAW] if angry(n)]
    P[SAW] = [n for n in P[SAW] if not angry(n)]
    # Nik: from FL bar 26 to the end PoiZone holds ONE note (F), angrier sound:
    # keep every onset and length, replace the pitches with F in two octaves,
    # all on PoiZone #2
    t0 = (ONE_NOTE_FROM_FL_BAR - 1) * 4
    hits = {}
    for n in P[SAW] + P[SAW2]:
        if n["time"] >= t0 - EPS:
            k = n["time"]
            old = hits.get(k)
            hits[k] = {"time": k,
                       "duration": max(n["duration"], old["duration"] if old else 0),
                       "velocity": max(n["velocity"], old["velocity"] if old else 0)}
    keep = [n for n in P[SAW] if n["time"] < t0 - EPS]
    early2 = [n for n in P[SAW2] if n["time"] < t0 - EPS]
    P[SAW] = keep
    one = []  # strummed chords give onsets 0.025 apart: merge them, no overlaps
    for h_ in sorted(hits.values(), key=lambda n: n["time"]):
        if one and h_["time"] - one[-1]["time"] < 0.1:
            one[-1]["velocity"] = max(one[-1]["velocity"], h_["velocity"])
            continue
        one.append(dict(h_))
    for a, b in zip(one, one[1:]):
        a["duration"] = round(min(a["duration"], b["time"] - a["time"] - 0.01), 4)
    d0, d1 = TL["drop"][0], TL["outro"][0]
    for h_ in one:  # drop: the one note stands out (Nik) - full velocity, + high F
        h_["drop"] = d0 - EPS <= h_["time"] < d1 - EPS
        if h_["drop"]:
            h_["velocity"] = 1.0
    P[SAW2] = early2 + [{k: v for k, v in {**h_, "midi": m}.items() if k != "drop"}
                        for h_ in one for m in ONE_NOTE + ((89,) if h_["drop"] else ())]
    # Nik: the drop saw gets fatter step by step towards the end (same note F):
    # bars 1-6 PoiZone #2 alone, 7-12 + the first PoiZone doubling it,
    # 13-20 + low F on both
    dl = TL["drop"]
    for n in [n for n in P[SAW2] if n["midi"] == 65 and dl[0] - EPS <= n["time"] < dl[-1]]:
        dbar = max(k for k in range(len(dl)) if dl[k] <= n["time"] + EPS) // 4
        if dbar >= 6:
            for m in (65, 77):
                P[SAW].append({**n, "midi": m})
        if dbar >= 12:
            P[SAW2].append({**n, "midi": 53})
            P[SAW].append({**n, "midi": 53})
    # Nik: FL bars 36-38 have no rhythm at all - only the one-note saw;
    # the beat starts hard on the drop
    q0, q1 = at("break", 5, 0), TL["drop"][0]
    for ch in (KICK, CLAP, HAT, SNARE, BASS):
        P[ch] = [n for n in P[ch] if not q0 - EPS <= n["time"] < q1 - EPS]
    hole = q1 - 0.5 * BASE_BPM / 60  # Nik: half a second of saw silence before the drop
    P[SAW2] = [{**n, "duration": round(min(n["duration"], hole - n["time"]), 4)}
               if n["time"] < q1 - EPS else n
               for n in P[SAW2] if not hole - EPS <= n["time"] < q1 - EPS]
    develop_drop(P)
    repeat_drop(P)
    unify_transitions(P)
    # Nik: no soft outro - the track is cut off hard at the end of the drop
    # (the outro section stays in the timeline only so the code above works)
    end = TL["outro"][0]
    for ch in P:
        P[ch] = [{**n, "duration": round(min(n["duration"], end - n["time"] - 0.005), 4)}
                 for n in P[ch] if n["time"] < end - EPS]
    # ...and ends with one last blow: half a second of silence (like before the
    # drop), then everything hits once and rings out on F (Nik: "needs an ending")
    t = round(end + 0.5 * BASE_BPM / 60, 4)
    last = [(KICK, 60, 0.3), (CLAP, CLAP_PITCH, 0.3), (SNARE, 60, 0.3),
            (BASS, ROOT_808["Fm"], 4.0)]
    last += [(SAW2, m, 3.0) for m in (53, 65, 77, 89)] + [(SAW, m, 3.0) for m in (53, 65, 77)]
    for ch, m, d in last:
        P[ch].append({"midi": m, "time": t, "duration": d, "velocity": 1.0})
    # Sub: a sine under every 808 note, same pitch (3x Osc sounds an octave
    # higher for the same midi, so -12), same glides - the 808 keeps the punch
    # and the character, the sine holds the low end while the 808 decays
    P[SUB] = [{**n, "midi": n["midi"] - 12, "velocity": round(n["velocity"] * SUB_VEL, 3)}
              for n in P[BASS]]
    return P


# Drop development (review 2026-11-20: "development is weak"): the drop was
# the same 2-bar drum loop for 20 bars and the PoiZone holds one note (Nik).
# Something new every 4 bars; a short stop at the end of each 4-bar phrase;
# from bar 9 a dark pad; the 808 hook at bars 1, 9 and 17. (bar index 0-based)
PHRASE_STOPS = (3, 7, 10, 15)  # last half beat of these bars: drums and bass out
# Nik: "still weak" -> a beat switch: bars 9-11 half-time. 3 bars, not 4 (Nik:
# the beat comes back "unevenly" at FL bar 45, not on the 4-bar grid at 46)
HALFTIME = (8, 9, 10)


def develop_drop(P):
    def add(ch, m, bar, beat, d, v):
        note(P[ch], m, "drop", bar, beat, d, v)
    for bar in range(DROP_BARS):
        blk = bar // 4
        if bar in HALFTIME:   # 9-12 half-time (see the drum patterns): only the pad
            pass
        elif blk >= 1:        # 5-8: 16th hat pick-ups
            for b in (1.75, 3.75):
                add(HAT, 60, bar, b, 0.06, h(0.36, 0.03))
        if blk >= 3:          # 13-20: extra kick + 16th hats in the second half
            if bar % 2 == 0:
                add(KICK, 60, bar, 3.25, 0.25, h(0.7))
            for i in range(8, 16, 2):
                add(HAT, 60, bar, i / 4 + 0.25, 0.06, h(0.3, 0.03))
        if bar == HALFTIME[-1] + 1:  # back to full speed: hit everything on the 1
            add(SNARE, 60, bar, 0, 0.2, 1.0)
            add(HAT, 60, bar, 0, 0.2, 0.9)
        if blk >= 3:          # 13-16: ghost snares
            for b in (1.75, 2.75):
                add(SNARE, 60, bar, b, 0.06, h(0.32, 0.03))
        if blk == 4:          # 17-20: clap on every beat 4 doubled by the snare
            add(SNARE, 60, bar, 3, 0.2, h(0.7))
        if bar in (7, 15, 19):  # snare 32nd roll into the next phrase
            for j in range(8):
                add(SNARE, 60, bar, 3 + j / 8, 0.05, min(1.0, 0.45 + 0.07 * j))
        if blk >= 2:          # pad from bar 9: low dark chord, whole bar
            r, t, f = CH[chord_at("drop", bar)]
            for k, m in enumerate((r - 12, r, t, f)):
                add(FLEX, m, bar, 0.04 * k, 3.9, h(0.65, 0.03))
        # (a keys counter-line from bar 13 was tried: "childish, weak,
        # artificial" (Nik) - the 808 hook returns at bar 17 instead)
    for bar in PHRASE_STOPS:  # the stop: nothing but the saw for half a beat
        g0, g1 = at("drop", bar, 3.5), at("drop", bar + 1, 0)
        for ch in (KICK, CLAP, HAT, SNARE, BASS):
            kept = []
            for n in P[ch]:
                if g0 - EPS <= n["time"] < g1 - EPS and not (bar in (7, 15) and ch == SNARE):
                    continue
                if n["time"] < g0 - EPS < n["time"] + n["duration"]:
                    n = {**n, "duration": round(g0 - n["time"] - 0.005, 4)}
                kept.append(n)
            P[ch] = kept


# Transitions (review: "no wholeness"): every part enters the same way - the
# drums and the bass stop for the last quarter beat of the old part, then one
# common hit on the 1 (kick, 808, snare, hat). The drop already works so.
PAD_GLUE_VEL = 0.62  # -48 dB at 0.45 was inaudible; target ~10 dB under the saw
STOP_BEATS = 0.25


def unify_transitions(P):
    names = [n for n, *_ in SECTIONS]
    for sec in ("intro", "verse", "break"):
        prev = names[names.index(sec) - 1]
        t1 = TL[sec][0]
        g0 = t1 - STOP_BEATS * (TL[prev][-1] - TL[prev][-2])
        for ch in (KICK, CLAP, HAT, SNARE, BASS):
            kept = []
            for n in P[ch]:
                if g0 - EPS <= n["time"] < t1 - EPS:
                    continue
                if n["time"] < g0 - EPS < n["time"] + n["duration"]:
                    n = {**n, "duration": round(g0 - n["time"] - 0.005, 4)}
                kept.append(n)
            P[ch] = kept
        root = ROOT_808[PROG[sec][0]]
        for ch, m, d, v in ((KICK, 60, 0.25, 0.95), (SNARE, 60, 0.2, 0.9),
                            (HAT, 60, 0.2, 0.8), (BASS, root, 2.0, 0.95)):
            note(P[ch], m, sec, 0, 0, d, v)


# ---------------------------------------------------------------- sound
# PoiZone: dirty but raw, not "spacey/tech": osc A saw + osc B pulse with
# detune and a little noise. No ring mod, no delay, no chorus, narrow unison,
# darker filter (Nik: "less cosmic, not too technological").
SAW_PARAMS = [(0, 0.0), (4, 0.5), (2, 0.0039), (3, 0.0), (7, 0.12), (9, 0.06),
              (15, 0.74), (29, 0.45), (18, 0.62), (19, 0.45), (45, 0.0),
              (25, 0.22), (31, 0.0), (39, 0.0)]
# PoiZone #2 (drop): angry = thick and growling, not bright. A first version
# (hard sync + pulse + octaver + 85% filter) sounded "pixelated" (Nik), so:
# no sync, saw+saw detuned, no octaver, wide unison, darker filter with a
# slower positive envelope (growl on every note), little noise.
SAW2_PARAMS = [(0, 0.0), (4, 0.0), (2, 0.0), (3, 0.0), (7, 0.2), (9, 0.04),
               (15, 0.8), (29, 0.68), (30, 0.45), (18, 0.66), (19, 0.5), (45, 0.0),
               (10, 0.7), (12, 0.3), (25, 0.2), (31, 0.0), (39, 0.0)]
# Dirty clap: resonant band-pass on the sampler channel (FType 2/7 = band pass)
# and the mixer's built-in EQ on Insert 2: low cut, narrow +12.6 dB at 1.5 kHz.
CLAP_REC = [("FType", 0.0), ("FCut", 1.0), ("FRes", 0.0)]  # filter open (was band pass)
CLAP_EQ = [{"band": 0, "gain": 0.5}, {"band": 1, "gain": 0.5, "bw": 0.267},
           {"band": 2, "gain": 0.5}]  # flat: the new clap is used as Nik heard it
# FL Keys, "less cheap" (Nik): almost no overdrive (it flattened the sound),
# softer hammer, a bit less muffle, wide stereo, slight detune for life,
# longer release, no tremolo.
KEYS_PARAMS = [(14, 0.12), (2, 0.62), (4, 0.68), (11, 0.5), (12, 0.5),
               (5, 0.35), (7, 0.4), (13, 0.45), (9, 0.33), (1, 0.7)]
# Pad under the piano (FLEX): chords held a whole bar in the prelude, the
# bridge's first half and the outro; a little reverb, nothing "spacey".
PAD_PRESET = 39  # "PAD Wrispi" - Nik chose it from 4 (2026-11-20); was 31 "PAD Ashword"
PAD_FX = {"Space": 0.0, "Phaser": 0.0, "Chorus": 0.0, "Delay": 0.0, "Delay mix": 0.0,
          "Reverb": 1.0, "Reverb mix": 0.18}
# Sub (3x Osc): osc 1 sine two octaves down, osc 2 and 3 silent
SUB_PARAMS = [(1, 0.0), (2, 0.0), (3, 0.5), (6, 0.0), (13, 0.0)]
# 808 (FLEX): macros are bipolar (0.5 = preset as is). The 808 is a sample
# that decays ~14 dB/s whatever the sustain (measured): the sub holds the low
# end. A little drive adds harmonics that headphones can play.
BASS_PARAMS = [(3, 0.5), (4, 0.5), (11, 0.25)]
# Nik chose it from 5 presets (2026-11-20, "stock sounds"): same pitch as
# Astronomic (F1 at midi 53), longest body, most low end (-13.8 dB sub)
BASS_PRESET = "808 Behemoth"
VOLUMES = {KICK: 0.72, CLAP: 0.55, HAT: 0.36, SNARE: 0.45, BASS: 0.72, KEYS: 0.62, SAW: 0.45,
           PIANO: 0.8, SAW2: 0.45, FLEX: 0.55, RISER: 0.6, DOWN: 0.55,
           SUB: 1.0}  # PIANO = Hook Keys; SUB 1.0: +4.5 dB at 30-70 Hz in the drop
# Mix (review 2026-11-20: "bad balance, drums vs melody and between drums").
# Channel volume in dB (0 = channel maximum), set after VOLUMES. Measured with
# fl stems 39-41 (drop): the PoiZone lead was 21 dB under the 808, kick 6-8 dB
# under the 808 + sub, sub the loudest of all.
# new drum samples are 5-8 dB hotter than the stock kit: back to the balance
MIX_DB = {SAW2: -2.2, KICK: -9.2, CLAP: -10.5, HAT: -7.2, SUB: -4.0,
          SAW: -5.2,   # verse (fl stems 21-23): the PoiZone melody was 14 dB under the kick
          PIANO: -4.0}  # drop counter-line: -27 dB, a little over the PoiZone (-31)
MASTER_VOL = 0.61  # ~-5.6 dB (was 0.66 = -4.1): headroom, the louder mix peaked at +0.3 dB
# PoiZone Drop (Insert 9) had as much 70-150 Hz as the 808: low shelf down
SAW2_EQ = [{"band": 0, "freq_hz": 160, "gain_db": -9}]
SAW_EQ = SAW2_EQ  # Insert 7: same low cut for the first PoiZone
# Downlifter (PoiZone #4): noise from an open filter that closes (-100% filter
# envelope, ~3 s) while the volume fades (~2.5 s).
DOWN_PARAMS = [(9, 1.0), (18, 0.9), (19, 0.3), (10, 0.0), (11, 0.0), (12, 0.5), (13, 0.0),
               (22, 0.0), (23, 0.6), (24, 0.0), (25, 0.3), (29, 0.4), (15, 0.75),
               (31, 0.0), (39, 0.0)]
# Riser (PoiZone #3): pure noise, closed filter opened by a slow filter envelope
# (attack ~ the riser's length), soft fade-in; no delay/chorus.
RISER_PARAMS = [(9, 1.0), (18, 0.08), (19, 0.5), (10, 1.0), (11, 0.6), (13, 1.0),
                (22, 0.6), (25, 0.3), (29, 0.4), (15, 0.75), (31, 0.0), (39, 0.0)]
VOLUMES[HAT] = 0.7  # hats were not audible (Nik): measured +1.4 dB over the mix at 0.5
# Hats: brighter on their mixer track (Insert 3): less low, +high shelf band
HAT_EQ = [{"band": 0, "gain": 0.5}, {"band": 2, "gain": 0.5}]  # flat: new sample
HAT_TRACK_VOL = 0.8  # Insert 3 at 0 dB (was +5.6 for the stock hat)
# Hook piano: second FL Keys, clean and soft (see tracks/hooks.py)
PIANO_PARAMS = [(14, 0.0), (2, 0.42), (4, 0.6), (5, 0.5), (1, 0.75),
                (7, 0.3), (11, 0.45), (12, 0.5), (0, 0.0)]
NAMES = {"kick": KICK, "clap": CLAP, "hat": HAT, "snare": SNARE,
         "bass": BASS, "keys": KEYS, "saw": SAW, "piano": PIANO, "saw2": SAW2,
         "flex": FLEX, "riser": RISER, "down": DOWN, "sub": SUB}


# Rules that must hold whatever is edited (fl check runs them before writing)
def _no_hook_outside_drop(P):
    d0, d1 = TL["drop"][0], TL["outro"][0]
    bad = [n for n in P[BASS] if n.get("porta") and not d0 - EPS <= n["time"] < d1]
    return [f"808 glide (hook) outside the drop at beat {bad[0]['time']}"] if bad else []


def _one_note_saw(P):
    t0 = (ONE_NOTE_FROM_FL_BAR - 1) * 4
    bad = {n["midi"] for ch in (SAW, SAW2) for n in P[ch]
           if n["time"] >= t0 - EPS and n["midi"] not in (53, 65, 77, 89)}
    return [f"PoiZone not on F after FL bar {ONE_NOTE_FROM_FL_BAR}: {sorted(bad)}"] if bad else []


def _no_rhythm_before_drop(P):
    q0, q1 = at("break", 5, 0), TL["drop"][0]
    bad = [ch for ch in (KICK, CLAP, HAT, SNARE, BASS)
           if any(q0 - EPS <= n["time"] < q1 - EPS for n in P[ch])]
    return [f"rhythm in the beatless bars before the drop: channels {bad}"] if bad else []


def _saw_gap_before_drop(P):
    q1 = TL["drop"][0]
    hole = q1 - 0.5 * BASE_BPM / 60
    bad = [n for ch in (SAW, SAW2) for n in P[ch]
           if n["time"] < q1 - EPS and n["time"] + n["duration"] > hole + EPS]
    return ["PoiZone sounds in the 0.5 s gap before the drop"] if bad else []


def _bass_audible(P):
    lo, hi = BASS_RANGE
    bad = sorted({n["midi"] for n in P[BASS] if not n.get("porta") and not lo <= n["midi"] <= hi})
    return [f"808 notes outside F1..E2 (inaudible on headphones): {bad}"] if bad else []


CHECKS = [("808 in the audible range", _bass_audible),
          ("hook 808 only in the drop", _no_hook_outside_drop),
          ("one-note PoiZone", _one_note_saw),
          ("no rhythm FL bars 36-37", _no_rhythm_before_drop),
          ("saw gap before the drop", _saw_gap_before_drop)]


def final_parts(extra=()):
    """Generated notes + the edits in dark_808.edits.json (+ extra ops)."""
    import fl_edit
    P = parts()
    fl_edit.apply_all(P, fl_edit.load("dark_808"), NAMES, extra)
    return P


def main():
    args = sys.argv[1:]
    force = "--force" in args
    want = [NAMES[a] for a in args if a in NAMES] or list(NAMES.values())
    c = get_connection()
    c.ensure_connected()
    t0 = time.time()
    apply_settings(c)
    P = final_parts()
    written = write_changed(c, "dark_808", 1, P, want, force=force)
    print("written:", written or "nothing changed", f"| total {time.time() - t0:.1f} s")
    if "--verify" in args:  # read the channels back from FL, compare note by note
        res = verify(c, P, want)
        print("verify:", "ALL IDENTICAL" if all(v[0] for v in res.values()) else res)
    print("pattern", send(c, "patterns.info"))


def apply_settings(c):
    """Sounds, routing, levels: one batch (~0.1 s), no GUI."""
    t0 = time.time()
    names = send(c, "plugins.preset", {"channel": BASS, "list": True}, timeout=15)["presets"]
    print("808 preset:", goto_preset(c, BASS, names.index(BASS_PRESET)))  # before BASS_PARAMS
    # Every PoiZone starts from the state of a FRESH PoiZone (as FL adds it:
    # arp up, gate, transpose -24 ... - the track was built on it), then gets
    # its *_PARAMS. NOT the "Default" preset: that is a different sound and
    # broke the PoiZones after a preset audition (Nik, 2026-11-20).
    fresh = json.loads((Path(__file__).resolve().parent / "plugins" / "PoiZone_fresh.json")
                       .read_text())
    batch(c, [("plugins.setParamValue", {"plugin_index": ch, "param_index": prm["index"],
                                         "value": prm["value"]})
              for ch in (SAW, SAW2, RISER, DOWN) for prm in fresh], timeout=30)
    keys_params = send(c, "plugins.getParams", {"index": KEYS})["params"]
    cmds = [("tempo.setMap", {"points": [], "enabled": False}),
            ("transport.stop", {}),
            ("patterns.jump", {"index": 1, "name": "Dark 808"}),  # the track = pattern 1
            ("transport.setTempo", {"bpm": BASE_BPM})]
    cmds += [("plugins.setParamValue", {"plugin_index": SAW, "param_index": pi, "value": v})
             for pi, v in SAW_PARAMS]
    cmds += [("plugins.setParamValue", {"plugin_index": KEYS, "param_index": pi, "value": v})
             for pi, v in KEYS_PARAMS]
    # "Hook Keys" (ch 8): an exact copy of the dirty keys' sound, own level
    cmds += [("plugins.setParamValue",
              {"plugin_index": PIANO, "param_index": prm["index"], "value": prm["value"]})
             for prm in keys_params]
    cmds += [("channels.routeToMixer", {"channel_index": KEYS, "mixer_track": 6}),
             ("mixer.setTrackName", {"track": 6, "name": "Keys"}),
             ("channels.mute", {"index": SUB, "muted": False}),
             ("channels.setName", {"index": SUB, "name": "Sub"}),
             ("channels.routeToMixer", {"channel_index": SUB, "mixer_track": 13}),
             ("mixer.setTrackName", {"track": 13, "name": "Sub"})]
    cmds += [("plugins.setParamValue", {"plugin_index": SUB, "param_index": pi, "value": v})
             for pi, v in SUB_PARAMS]
    cmds += [("plugins.setParamValue", {"plugin_index": BASS, "param_index": pi, "value": v})
             for pi, v in BASS_PARAMS]
    cmds += [("channels.setName", {"index": PIANO, "name": "Hook Keys"}),
             ("mixer.setTrackName", {"track": 8, "name": "Hook Keys"}),
             ("mixer.setTrackVolume", {"track": 8, "volume": 0.8}),
             ("channels.routeToMixer", {"channel_index": PIANO, "mixer_track": 8})]
    cmds += [("channels.setRec", {"channel": CLAP, "param": prm, "value": v})
             for prm, v in CLAP_REC]
    cmds += [("mixer.setEq", {"track": 2, **eq}) for eq in CLAP_EQ]
    cmds += [("mixer.setEq", {"track": 3, **eq}) for eq in HAT_EQ]
    cmds += [("mixer.setEq", {"track": 9, **eq}) for eq in SAW2_EQ]
    cmds += [("mixer.setEq", {"track": 7, **eq}) for eq in SAW_EQ]
    cmds += [("mixer.setTrackVolume", {"track": 3, "volume": HAT_TRACK_VOL})]
    cmds += [("channels.routeToMixer", {"channel_index": SAW, "mixer_track": 7})]
    cmds += [("plugins.setParamValue", {"plugin_index": SAW2, "param_index": pi, "value": v})
             for pi, v in SAW2_PARAMS]
    cmds += [("channels.setName", {"index": SAW2, "name": "PoiZone Drop"}),
             ("channels.routeToMixer", {"channel_index": SAW2, "mixer_track": 9}),
             ("mixer.setTrackName", {"track": 9, "name": "Saw Drop"}),
             ("mixer.setTrackName", {"track": 7, "name": "Saw"}),
             ("mixer.setTrackVolume", {"track": 6, "volume": 0.8}),
             ("mixer.setTrackVolume", {"track": 0, "volume": 0.66}),  # master: headroom
             ("channels.mute", {"index": PIANO, "muted": False}),  # "muted"! "mute" toggles
             ("channels.setRec", {"channel": BASS, "param": "PortaTime", "value": PORTA_808})]
    cmds += [("channels.setVolume", {"index": ch, "volume": v}) for ch, v in VOLUMES.items()]
    cmds += [("channels.volumeDb", {"index": ch, "db": db}) for ch, db in MIX_DB.items()]
    cmds += [("mixer.setTrackVolume", {"track": 0, "volume": MASTER_VOL})]
    cmds += [("plugins.setParamValue", {"plugin_index": RISER, "param_index": pi, "value": v})
             for pi, v in RISER_PARAMS]
    cmds += [("plugins.setParamValue", {"plugin_index": DOWN, "param_index": pi, "value": v})
             for pi, v in DOWN_PARAMS]
    cmds += [("channels.setName", {"index": DOWN, "name": "Downlifter"}),
             ("channels.routeToMixer", {"channel_index": DOWN, "mixer_track": 12}),
             ("mixer.setTrackName", {"track": 12, "name": "Downlifter"})]
    cmds += [("channels.setName", {"index": RISER, "name": "Riser"}),
             ("channels.routeToMixer", {"channel_index": RISER, "mixer_track": 11}),
             ("mixer.setTrackName", {"track": 11, "name": "Riser"})]
    cmds += [("channels.routeToMixer", {"channel_index": FLEX, "mixer_track": 10})]
    batch(c, cmds, timeout=15)
    # FLEX = pad under the piano (it was tried for the removed 5/4 part)
    print("pad preset:", goto_preset(c, FLEX, PAD_PRESET))
    fx = {p["name"]: p["index"] for p in send(c, "plugins.getParams", {"index": FLEX})["params"]}
    batch(c, [("plugins.setParamValue", {"plugin_index": FLEX, "param_index": fx[n], "value": v})
              for n, v in PAD_FX.items() if n in fx]
          + [("channels.mute", {"index": FLEX, "muted": False}),
             ("channels.setName", {"index": FLEX, "name": "Pad (FLEX)"}),
             ("mixer.setTrackName", {"track": 10, "name": "Pad"})])
    print(f"settings: {len(cmds)} commands in one batch ({time.time() - t0:.1f} s) | "
          f"length {TOTAL * 60 / BASE_BPM:.1f} s")


if __name__ == "__main__":
    main()
