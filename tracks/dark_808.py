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
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from fl_io import (  # noqa: E402
    MARK_STEP,  # noqa: F401  (used by tracks/hooks.py)
    batch,
    get_connection,
    run_piano_roll,  # noqa: F401
    send,
    verify,
    write_changed,
    write_notes,  # noqa: F401
)

BASE_BPM = 85.0
KICK, CLAP, HAT, SNARE, BASS, KEYS, SAW, _TB, PIANO, SAW2, FLEX, RISER = range(12)  # 7 unused
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
    ("break", 8, 4, [70, 70, 70, 70, 75, 85, 95, (105, 120)], None),
    ("build", 1, 4, [(120, 140)], None),  # snare roll, tempo reaches 140
    ("drop", 16, 4, 140, 140),  # double speed, the simple 8-bar drop twice
    # outro: the drop keeps going, then dies like a stopping tape (140 -> 58);
    # the prelude theme returns and the hook's low F is the last chord
    ("outro", 8, 4, [140, 140, (140, 120), (120, 100), (100, 88), (88, 78),
                     (78, 66), (66, 58)], None),
]

# F minor / phrygian. Chords for the saw and keys, roots for the 808.
CH = {"Fm": [53, 56, 60], "Gb": [54, 58, 61], "Ebm": [51, 54, 58],
      "C": [52, 55, 60], "Db": [53, 56, 61], "Bbm": [53, 58, 61], "Ab": [51, 56, 60]}
ROOT_808 = {"Fm": 53, "Gb": 54, "Ebm": 51, "C": 48, "Db": 49, "Bbm": 46, "Ab": 44}
PROG = {"prelude": ["Fm", "Db", "Bbm", "C"] * 2, "intro": ["Fm", "Fm", "Db", "C"],
        "verse": ["Fm", "Fm", "Db", "C"] * 2 + ["Bbm", "Db", "Gb", "C"]
        + ["Fm", "Fm", "Db", "C"],
        # break ends on C (dominant): the ear wants to land - on the drop
        "break": ["Db", "Ab", "Bbm", "C"] * 2,
        "build": ["C"], "drop": ["Fm", "Fm", "Gb", "C"],
        "outro": ["Fm", "Fm", "Gb", "C", "Fm", "Db", "C", "Fm"]}

# The hook ("Fate 3 - cry", chosen by Nik): a high cry that holds, then
# collapses to the bottom. Soft clean piano (channel 8). (midi, beat, len, vel)
HOOK = [(73, 0.0, 1.5, 0.78), (72, 1.5, 0.45, 0.62), (65, 2.0, 0.45, 0.6),
        (53, 2.5, 4.0, 0.72), (41, 2.5, 4.0, 0.55)]
# Short form: just the cry Db -> C.
CLAP_PITCH = 58  # clap sample 2 semitones down: darker, dirtier
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
HOOK_808_AT = [("drop", 0, 0), ("drop", 8, 0)]  # Nik: the bass hook only in the drop
PORTA_808 = 0.35  # channel portamento time (normalized)
FLEX_PRESET = 45  # "PLK Kamala" (candidate; Gramata was too "spacey")
# FLEX dry: no "space" (Nik). Names -> value; looked up per preset.
FLEX_DRY = {"Space": 0.0, "Phaser": 0.0, "Chorus": 0.0, "Delay mix": 0.0,
            "Reverb mix": 0.0}
DRIFT = 0.012     # +-beats of timing drift in the 5/4 arp (~ +-8 ms at 95 BPM)
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


# ---------------------------------------------------------------- parts
def parts():
    P = {k: [] for k in (KICK, CLAP, HAT, SNARE, BASS, KEYS, SAW, PIANO, SAW2, FLEX, RISER)}
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
            chord = PROG[sec][bar % len(PROG[sec])]
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
            elif sec == "drop":
                kicks = [0, 0.75, 2.5] if bar % 2 == 0 else [0, 1.5, 2.75, 3.5]
            elif sec == "break":  # bars 5-8 rebuild the groove step by step
                kicks = {4: [0, 2], 5: [0, 2, 2.75], 6: [0, 1, 2, 2.75, 3.5],
                         7: [0, 1, 2, 3, 3.5]}.get(bar, [])
            elif sec == "build":
                kicks = [0]
            elif sec == "outro":  # drop groove -> half-time -> one hit -> nothing
                kicks = ([0, 0.75, 2.5] if bar % 2 == 0 else [0, 1.5, 2.75, 3.5]) if bar < 2 \
                    else [0, 2.5] if bar < 4 else [0] if bar < 6 else []
            else:
                kicks = []
            for k in kicks:
                note(P[KICK], 60, sec, bar, k, 0.25, h(0.85 if k == 0 else 0.72))
            # 808: follow the kick, hold until the next hit
            if sec in ("verse", "drop", "outro") or (sec == "break" and bar >= 4):
                hits = kicks + [bpb]
                for a, b in zip(hits, hits[1:]):
                    note(P[BASS], root, sec, bar, a, b - a, h(0.8 if a == 0 else 0.65))
            elif sec in ("intro", "break"):
                # the 808 sample decays fast: re-hit every 2 beats, no silent holes
                for a in (0, 2):
                    note(P[BASS], root, sec, bar, a, 2, h(0.62 if a == 0 else 0.5))
            elif sec == "build":
                note(P[BASS], root, sec, bar, 0, 3.4, 0.6)
            # ---- clap
            if sec == "verse":
                note(P[CLAP], 60, sec, bar, 2, 0.25, h(0.9))
                if blk >= 2 and bar % 2 == 1:  # ghost clap before the next bar
                    note(P[CLAP], 60, sec, bar, 3.75, 0.2, h(0.38, 0.03))
            elif sec == "break" and bar >= 4:
                for c in ((2,) if bar < 6 else (1, 3)):
                    note(P[CLAP], 60, sec, bar, c, 0.25, h(0.75 + 0.05 * (bar - 4)))
            elif sec == "drop":
                for c in (1, 3):
                    note(P[CLAP], 60, sec, bar, c, 0.25, h(0.95))
                if bar == 0:  # impact on the drop's first beat: everything at once
                    note(P[CLAP], 60, sec, bar, 0, 0.25, 1.0)
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
                step = 0.5
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
                elif last4 or (sec == "drop" and bar % 2 == 1):
                    # 16th-triplet roll on the last beat
                    for j in range(6):
                        note(P[HAT], 60, sec, bar, bpb - 1 + j / 6, 0.08, 0.35 + j * 0.06)
            elif sec == "intro" and bar >= 2:
                for b in range(4):
                    note(P[HAT], 60, sec, bar, b, 0.1, h(0.4))
            elif sec == "break":
                if bar < 4:      # emotion: soft off-beats
                    for b in range(4):
                        note(P[HAT], 60, sec, bar, b + 0.5, 0.1, h(0.38))
                elif bar == 4:   # quarters
                    for b in range(4):
                        note(P[HAT], 60, sec, bar, b, 0.1, h(0.45))
                elif bar == 5:   # 8ths
                    for i in range(8):
                        note(P[HAT], 60, sec, bar, i / 2, 0.1, h(0.55 if i % 2 == 0 else 0.4))
                else:            # 16ths, growing into the snare roll
                    for i in range(16):
                        note(P[HAT], 60, sec, bar, i / 4, 0.06,
                             0.3 + 0.015 * ((bar - 6) * 16 + i) + (0.1 if i % 4 == 0 else 0))
            elif sec == "outro" and bar < 4:  # 8ths -> quarters, fading
                for b in ([i * 0.5 for i in range(8)] if bar < 2 else range(4)):
                    note(P[HAT], 60, sec, bar, b, 0.1,
                         h((0.6 if b % 1 == 0 else 0.4) - 0.07 * bar))
            # ---- snare roll in the build: one bar, 16ths -> 32nds, crescendo
            if sec == "build":  # quieter than the drop; the last 8th is empty
                for i in range(12):
                    note(P[SNARE], 60, sec, bar, i / 4, 0.06, 0.4 + 0.025 * i)
                for j in range(4):
                    note(P[SNARE], 60, sec, bar, 3 + j / 8, 0.05, 0.7 + j * 0.04)
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
                else:        # rebuild: quieter pad under the groove
                    for k, m in enumerate([r, t, f]):
                        note(P[KEYS], m, sec, bar, 0.03 * k, 3.95, h(0.55, 0.03))
            if sec == "build":
                for k, m in enumerate(CH[chord] + [CH[chord][0] + 12]):
                    note(P[KEYS], m, sec, bar, 0.02 * k, 3.4, h(0.5))
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
                elif bar < 6:  # the theme moves up to the saw
                    for m, b, d, v in VERSE_THEME[bar - 4]:
                        note(P[SAW], m + 12, sec, bar, b, d, h(v, 0.03))
                elif bar == 6:  # rising arpeggio, growing
                    arp = tones + [x + 12 for x in tones] + [tones[0] + 24, tones[1] + 24]
                    for i in range(8):
                        note(P[SAW], arp[i] + 12, sec, bar, i * 0.5, 0.45, 0.55 + 0.04 * i)
                else:           # chromatic climb over the dominant
                    for i, m in enumerate(range(72, 80)):
                        note(P[SAW], m + 12, sec, bar, i * 0.5, 0.5, 0.6 + 0.05 * i)
            if sec == "break" and bar == 6:  # noise riser: one long note, filter opens
                t0 = at("break", 6, 0)  # ends exactly on the drop's first beat
                P[RISER].append({"midi": 60, "time": round(t0, 4),
                                 "duration": round(TL["drop"][0] - t0 - 0.01, 4),
                                 "velocity": 0.7})
            elif sec == "verse" and blk == 2:
                # C: the prelude's "end of the world" theme returns on the saw
                for m, b, d, v in VERSE_THEME[bar % 4]:
                    drift = DRIFT * (2 * (hash_unit(m, bar, b) - 0.5))
                    note(P[SAW], m + 12, sec, bar, b + drift, d, h(v, 0.04))
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
        P[CLAP].append({**n, "midi": CLAP_PITCH, "time": round(n["time"] + flam, 4),
                        "velocity": round(n["velocity"] * 0.6, 3)})
        P[SNARE].append({**n, "midi": 60, "velocity": round(n["velocity"] * 0.4, 3)})
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
    P[SAW2] = [n for n in P[SAW] if d0 - EPS <= n["time"] < d1 - EPS]
    P[SAW] = [n for n in P[SAW] if not d0 - EPS <= n["time"] < d1 - EPS]
    return P


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
CLAP_REC = [("FType", 2 / 7), ("FCut", 0.6), ("FRes", 0.55)]
CLAP_EQ = [{"band": 0, "gain": 0.3}, {"band": 1, "gain": 0.85, "bw": 0.2},
           {"band": 2, "gain": 0.6}]
KEYS_PARAMS = [(14, 0.55), (2, 0.8), (4, 0.75), (11, 0.6), (12, 0.5),
               # set earlier by the lofi script - now here so a rebuild is complete
               (5, 0.25), (7, 0.25), (13, 0.45)]
VOLUMES = {KICK: 0.72, CLAP: 0.55, HAT: 0.36, SNARE: 0.45, BASS: 0.72, KEYS: 0.62, SAW: 0.45,
           PIANO: 0.8, SAW2: 0.45, FLEX: 0.7, RISER: 0.6}  # PIANO = "Hook Keys"
# Riser (PoiZone #3): pure noise, closed filter opened by a slow filter envelope
# (attack ~ the riser's length), soft fade-in; no delay/chorus.
RISER_PARAMS = [(9, 1.0), (18, 0.08), (19, 0.5), (10, 1.0), (11, 0.6), (13, 1.0),
                (22, 0.6), (25, 0.3), (29, 0.4), (15, 0.75), (31, 0.0), (39, 0.0)]
VOLUMES[HAT] = 0.7  # hats were not audible (Nik): measured +1.4 dB over the mix at 0.5
# Hats: brighter on their mixer track (Insert 3): less low, +high shelf band
HAT_EQ = [{"band": 0, "gain": 0.35}, {"band": 2, "gain": 0.78}]
HAT_TRACK_VOL = 1.0  # Insert 3 at +5.6 dB
# Hook piano: second FL Keys, clean and soft (see tracks/hooks.py)
PIANO_PARAMS = [(14, 0.0), (2, 0.42), (4, 0.6), (5, 0.5), (1, 0.75),
                (7, 0.3), (11, 0.45), (12, 0.5), (0, 0.0)]
NAMES = {"kick": KICK, "clap": CLAP, "hat": HAT, "snare": SNARE,
         "bass": BASS, "keys": KEYS, "saw": SAW, "piano": PIANO, "saw2": SAW2,
         "flex": FLEX, "riser": RISER}


def main():
    args = sys.argv[1:]
    force = "--force" in args
    want = [NAMES[a] for a in args if a in NAMES] or list(NAMES.values())
    c = get_connection()
    c.ensure_connected()
    t0 = time.time()
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
             ("channels.mute", {"index": 7, "muted": True})]  # placeholder channel 7
    cmds += [("channels.setName", {"index": PIANO, "name": "Hook Keys"}),
             ("mixer.setTrackName", {"track": 8, "name": "Hook Keys"}),
             ("mixer.setTrackVolume", {"track": 8, "volume": 0.8}),
             ("channels.routeToMixer", {"channel_index": PIANO, "mixer_track": 8})]
    cmds += [("channels.setRec", {"channel": CLAP, "param": prm, "value": v})
             for prm, v in CLAP_REC]
    cmds += [("mixer.setEq", {"track": 2, **eq}) for eq in CLAP_EQ]
    cmds += [("mixer.setEq", {"track": 3, **eq}) for eq in HAT_EQ]
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
             ("channels.mute", {"index": PIANO, "muted": True}),  # "muted"! "mute" toggles
             ("channels.setRec", {"channel": BASS, "param": "PortaTime", "value": PORTA_808})]
    cmds += [("channels.setVolume", {"index": ch, "volume": v}) for ch, v in VOLUMES.items()]
    cmds += [("plugins.setParamValue", {"plugin_index": RISER, "param_index": pi, "value": v})
             for pi, v in RISER_PARAMS]
    cmds += [("channels.setName", {"index": RISER, "name": "Riser"}),
             ("channels.routeToMixer", {"channel_index": RISER, "mixer_track": 11}),
             ("mixer.setTrackName", {"track": 11, "name": "Riser"})]
    cmds += [("channels.setName", {"index": FLEX, "name": "Odd Synth (FLEX)"}),
             ("channels.routeToMixer", {"channel_index": FLEX, "mixer_track": 10}),
             ("mixer.setTrackName", {"track": 10, "name": "Odd Synth"})]
    batch(c, cmds, timeout=15)
    # FLEX was tried for the 5/4 (too "spacey" / not it) - back to PoiZone,
    # FLEX stays on the rack, empty and muted (presets: fl_io.goto_preset)
    send(c, "channels.mute", {"index": FLEX, "muted": True})
    print(f"settings: {len(cmds)} commands in one batch ({time.time() - t0:.1f} s) | "
          f"length {TOTAL * 60 / BASE_BPM:.1f} s")
    P = parts()
    written = write_changed(c, "dark_808", 1, P, want, force=force)
    print("written:", written or "nothing changed", f"| total {time.time() - t0:.1f} s")
    if "--verify" in args:  # read the channels back from FL, compare note by note
        res = verify(c, P, want)
        print("verify:", "ALL IDENTICAL" if all(v[0] for v in res.values()) else res)
    print("pattern", send(c, "patterns.info"))


if __name__ == "__main__":
    main()
