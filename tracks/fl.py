"""fl - one command for working on a track in FL Studio without the mouse.

    fl state                         FL up? pattern, tempo, channels, windows, GUI count
    fl check                         rules (CLAUDE.md lessons) on the notes, offline
    fl map 30-45                     note onsets per channel per FL bar, offline
    fl notes saw2 36-38              the notes themselves, offline
    fl write [ch ...] [--force] [--settings] [--verify]   notes (+ sounds) into FL
    fl play bar 36 [4] [--rec]       play FL bars 36.. for 4 s (proves the position)
    fl play 34.5 6 [--rec]           same, from seconds
    fl ab "del hat bars 39-40" [--bars 37-41] [--keep]   variant A vs B by ear + numbers
    fl edit ls | add "<op>" | rm N | off N | on N         edits on top of the track
    fl stems 39-41                   each channel alone + mix: level per band (mixing)
    fl tone bass 53 [2]              one live note: fundamental Hz + decay every 0.25 s
    fl params saw2 [filter]          plugin parameters (saved to tracks/plugins/)
    fl param saw2 "Cutoff" 0.6       set one parameter by name or index
    fl save                          save the .flp (closes the trial "Buy" window)
    fl gui                           how often FL's UI was touched today
    fl rebuild                       after an FL restart: new project + instruments

Options: --track dark_808 (default). Operations: see fl_edit.py.
Run from the repo: uv run python tracks/fl.py ...
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fl_check  # noqa: E402
import fl_edit  # noqa: E402
import fl_io  # noqa: E402

DIR = Path(__file__).resolve().parent
PLUGINS = DIR / "plugins"
PROJECTS = Path.home() / "Documents/Image-Line/FL Studio/Projects"
FL_ID = "com.image-line.flstudio"


def opt(args, name, default=None):
    if name in args:
        i = args.index(name)
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return default


def flag(args, name):
    if name in args:
        args.remove(name)
        return True
    return False


def conn():
    c = fl_io.get_connection()
    c.ensure_connected()
    return c


def chan(track, name):
    if name.isdigit():
        return int(name)
    return track.NAMES[name]


# ---------------------------------------------------------------- offline
def cmd_check(track, args):
    P = track.final_parts()
    res = fl_check.run(track, P)
    verbose = flag(args, "-v")
    shown = [r for r in res if verbose or r[0] != "info"]
    for lvl, msg in shown:
        print(f"{lvl:5} {msg}")
    errors = sum(1 for lvl, _ in res if lvl == "ERROR")
    total = sum(len(v) for v in P.values())
    print(f"check: {errors} errors, {total} notes, "
          f"{len(fl_edit.load(track.__name__))} edits")
    return errors == 0


def cmd_map(track, args):
    a, _, b = (args[0] if args else "1-60").partition("-")
    print(fl_check.bar_map(track.final_parts(), track.NAMES, int(a), int(b or a)))


def cmd_notes(track, args):
    ch = chan(track, args[0])
    t0, t1 = fl_edit.bar_range(args[1] if len(args) > 1 else "1-999")
    for n in track.final_parts()[ch]:
        if t0 - 1e-3 <= n["time"] < t1:
            bar = int(n["time"] // 4) + 1
            print(f"bar {bar:3} beat {n['time'] % 4 + 1:6.3f}  midi {n['midi']:3}  "
                  f"len {n['duration']:6.3f}  vel {n['velocity']:.2f}"
                  + ("  glide" if n.get("porta") else ""))


def cmd_edit(track, args):
    name = track.__name__
    edits = fl_edit.load(name)
    sub = args[0] if args else "ls"
    if sub == "add":
        op = " ".join(args[1:])
        err = fl_edit.check_syntax(op, track.NAMES)
        if err:
            sys.exit(f"bad op: {err}")
        edits.append({"op": op, "on": True})
    elif sub in ("rm", "off", "on"):
        i = int(args[1]) - 1
        if sub == "rm":
            edits.pop(i)
        else:
            edits[i]["on"] = sub == "on"
    fl_edit.save(name, edits)
    for i, e in enumerate(edits, 1):
        print(f"{i:2}. [{'x' if e.get('on', True) else ' '}] {e['op']}")
    if not edits:
        print("no edits")


# ---------------------------------------------------------------- FL
def cmd_write(track, args):
    force, settings, ver = (flag(args, "--force"), flag(args, "--settings"),
                            flag(args, "--verify"))
    if not flag(args, "--no-check") and not cmd_check(track, ["-q"]):
        sys.exit("not written: fix the errors above (or --no-check)")
    want = [chan(track, a) for a in args] or list(track.NAMES.values())
    c = conn()
    t0 = time.time()
    fl_io.send(c, "patterns.jump", {"index": 1})
    if settings:
        track.apply_settings(c)
    P = track.final_parts()
    w = fl_io.write_changed(c, track.__name__, 1, P, want, force=force)
    print("written:", w or "nothing changed", f"({time.time() - t0:.1f} s)")
    if ver:
        res = fl_io.verify(c, P, want)
        print("verify:", "ALL IDENTICAL" if all(v[0] for v in res.values()) else res)


def play_args(track, args):
    rec = flag(args, "--rec")
    if args[0] == "bar":
        start = (float(args[1]) - 1) * 4 * 60 / track.BASE_BPM
        args = args[2:]
    else:
        start = float(args[0])
        args = args[1:]
    return start, float(args[0]) if args else 6.0, rec


def cmd_play(track, args):
    import listen
    listen.track = track  # positions are named in this track's sections
    start, dur, rec = play_args(track, args)
    listen.play(start, dur, rec)


def measure(wav: Path) -> dict:
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav), "-ac", "1",
                          "-ar", "48000", "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    if len(x) < 4800 or float(np.abs(x).max()) < 1e-6:
        return {"rms dB": "SILENCE"}  # nothing reached Background Music
    db = lambda y: 20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-9)  # noqa: E731
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / 48000)
    w = 2400  # 50 ms windows: onsets = jumps of +6 dB
    env = np.array([db(x[i:i + w]) for i in range(0, len(x) - w, w)])
    low = spec[f < 150].sum() / spec.sum()
    peak = 20 * np.log10(np.abs(x).max() + 1e-9)
    return {"rms dB": round(float(db(x)), 1), "peak dB": round(float(peak), 1),
            "centroid Hz": int((spec * f).sum() / spec.sum()),
            "low <150 Hz %": round(float(100 * low), 1),
            "hits": int((np.diff(env) > 6).sum())}


def cmd_ab(track, args):
    """Write A (current) and B (A + ops), play both, compare. A stays unless --keep."""
    keep = flag(args, "--keep")
    bars = opt(args, "--bars")
    ops = args
    for op in ops:
        err = fl_edit.check_syntax(op, track.NAMES)
        if err:
            sys.exit(f"bad op {op!r}: {err}")
    if not bars:
        first = fl_edit.Op(ops[0], track.NAMES)
        a, b = int(first.t0 // 4) + 1, int(first.t1 // 4)
        bars = f"{max(1, a - 1)}-{b + 1}"
    t0, t1 = fl_edit.bar_range(bars)
    start, dur = t0 * 60 / track.BASE_BPM, (t1 - t0) * 60 / track.BASE_BPM
    A, B = track.final_parts(), track.final_parts(extra=ops)
    errs = [m for lvl, m in fl_check.run(track, B) if lvl == "ERROR"]
    if errs:
        print("B breaks rules:", *errs, sep="\n  ")
    diff = [ch for ch in A if fl_io.fingerprint(A[ch]) != fl_io.fingerprint(B[ch])]
    if not diff:
        sys.exit("B is the same as A")
    import listen
    c = conn()
    fl_io.send(c, "patterns.jump", {"index": 1})
    name = track.__name__
    fl_io.write_changed(c, name, 1, A, diff)
    res = {}
    for label, P in (("A", A), ("B", B)):
        if label == "B":
            fl_io.write_changed(c, name, 1, P, diff)
        print(f"--- {label}: FL bars {bars} ({dur:.1f} s)")
        listen.play(start, dur, rec=True, quiet=True)
        dst = Path(f"/tmp/fl_{label}.wav")
        shutil.copy(listen.REC, dst)
        res[label] = measure(dst)
    keys = list(res["A"])
    print(f"{'':16}{'A':>9}{'B':>9}")
    for k in keys:
        print(f"{k:16}{res['A'][k]:>9}{res['B'].get(k, '-'):>9}")
    if keep:
        edits = fl_edit.load(name) + [{"op": op, "on": True} for op in ops]
        fl_edit.save(name, edits)
        print("kept B: added to edits")
    else:
        fl_io.write_changed(c, name, 1, A, diff)
        print("back to A (use --keep to keep B)")


def tone(c, ch: int, midi: int, hold: float = 2.0, tail: float = 1.0) -> dict:
    """Play one note on a channel (FL stopped, live note), record it from
    Background Music: fundamental (Hz), level and decay every 0.25 s."""
    import numpy as np
    wav = Path("/tmp/fl_tone.wav")
    rec = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "avfoundation",
                            "-i", fl_io.bm_device(), "-t", str(hold + tail + 0.4), str(wav)])
    time.sleep(0.4)
    fl_io.send(c, "channels.triggerNote", {"channel": ch, "note": midi, "velocity": 110})
    time.sleep(hold)
    fl_io.send(c, "channels.triggerNote", {"channel": ch, "note": midi, "velocity": 0})
    rec.wait()
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav), "-ac", "1",
                          "-ar", "48000", "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    if len(x) < 4800 or np.abs(x).max() < 1e-6:
        return {"error": "silence"}
    on = int(np.argmax(np.abs(x) > np.abs(x).max() * 0.1))
    seg = x[on + 2400:on + 2400 + 48000]  # 50 ms after the attack, 1 s
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 18))
    f = np.fft.rfftfreq(1 << 18, 1 / 48000)
    band = (f > 25) & (f < 600)
    f0 = float(f[band][np.argmax(spec[band])])
    w = 12000
    env = [round(float(20 * np.log10(np.sqrt(np.mean(x[i:i + w] ** 2)) + 1e-9)))
           for i in range(on, len(x) - w, w)]
    return {"f0 Hz": round(f0, 1), "peak dB": round(float(20 * np.log10(np.abs(x).max())), 1),
            "dB every 0.25 s": env}


def cmd_tone(track, args):
    c = conn()
    hold = float(args[2]) if len(args) > 2 else 2.0
    r = tone(c, chan(track, args[0]), int(args[1]), hold)
    for k, v in r.items():
        print(f"{k}: {v}")


BANDS = [("sub", 30, 70), ("bass", 70, 150), ("lowmid", 150, 400), ("mid", 400, 2000),
         ("pres", 2000, 6000), ("high", 6000, 16000)]


def bands_db(wav: Path) -> dict:
    """rms, peak and level per frequency band (dB) of a recording."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav), "-ac", "1",
                          "-ar", "48000", "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    if len(x) < 4800 or np.abs(x).max() < 1e-6:
        return {}
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / 48000)
    p = np.abs(X) ** 2
    tot = p.sum()
    out = {"rms": 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9),
           "peak": 20 * np.log10(np.abs(x).max() + 1e-9)}
    for name, lo, hi in BANDS:  # band level = rms of the signal limited to the band
        share = p[(f >= lo) & (f < hi)].sum() / tot
        out[name] = out["rms"] + 10 * np.log10(share + 1e-12)
    return {k: round(float(v), 1) for k, v in out.items()}


def cmd_stems(track, args):
    """Each channel alone over FL bars A-B (others muted), then the full mix:
    level per band. Shows who covers whom. Mute states are restored."""
    import listen
    bars = args[0] if args else "39-41"
    t0, t1 = fl_edit.bar_range(bars)
    P = track.final_parts()
    rev = {v: k for k, v in track.NAMES.items()}
    chs = [ch for ch in sorted(P) if any(t0 <= n["time"] < t1 for n in P[ch])]
    c = conn()
    allch = fl_io.send(c, "channels.getAll")["channels"]
    muted0 = {x["index"]: x["is_muted"] for x in allch}
    start, dur = t0 * 60 / track.BASE_BPM, (t1 - t0) * 60 / track.BASE_BPM
    rows = {}
    try:
        for ch in chs + ["MIX"]:
            fl_io.batch(c, [("channels.mute", {"index": i, "muted": (
                muted0[i] if ch == "MIX" else i != ch)}) for i in muted0])
            listen.play(start, dur, rec=True, quiet=True)
            rows[rev.get(ch, ch)] = bands_db(listen.REC)
    finally:
        fl_io.batch(c, [("channels.mute", {"index": i, "muted": m}) for i, m in muted0.items()])
    cols = ["rms", "peak"] + [b[0] for b in BANDS]
    print(f"FL bars {bars}" + "".join(f"{x:>8}" for x in cols))
    for name, r in rows.items():
        print(f"{name:<12}" + "".join(f"{r.get(x, '-'):>8}" for x in cols))
    out = DIR / ".cache" / f"stems_{track.__name__}_{bars}.json"
    out.write_text(json.dumps(rows, indent=1))
    print("saved", out.relative_to(DIR))


def cmd_params(track, args):
    ch = chan(track, args[0])
    flt = args[1].lower() if len(args) > 1 else ""
    c = conn()
    name = fl_io.send(c, "plugins.getName", {"index": ch}).get("name", f"ch{ch}")
    ps = fl_io.send(c, "plugins.getParams", {"index": ch, "max_params": 4096},
                    timeout=30)["params"]
    ps = [p for p in ps if p["name"].strip()]
    PLUGINS.mkdir(exist_ok=True)
    (PLUGINS / f"{name}.json").write_text(json.dumps(ps, indent=1, ensure_ascii=False))
    for p in ps:
        if flt in p["name"].lower():
            print(f"{p['index']:4}  {p['name']:<32} {p['value']:.3f}  {p['value_string']}")
    print(f"{name}: {len(ps)} params -> tracks/plugins/{name}.json")


def cmd_param(track, args):
    ch, key, val = chan(track, args[0]), args[1], float(args[2])
    c = conn()
    if key.isdigit():
        idx = int(key)
    else:
        ps = fl_io.send(c, "plugins.getParams", {"index": ch, "max_params": 4096},
                        timeout=30)["params"]
        hits = [p for p in ps if key.lower() in p["name"].lower()]
        if len(hits) != 1:
            sys.exit(f"{len(hits)} params match {key!r}: {[p['name'] for p in hits][:10]}")
        idx = hits[0]["index"]
    fl_io.send(c, "plugins.setParamValue", {"plugin_index": ch, "param_index": idx,
                                            "value": val})
    r = fl_io.send(c, "plugins.getParamValue", {"plugin_index": ch, "param_index": idx})
    print(f"param {idx} -> {r}")


# ---------------------------------------------------------------- save / state
def osa(script: str) -> str:
    return subprocess.run(["osascript", "-e", script], capture_output=True,
                          text=True).stdout.strip()


def fl_windows() -> list[str]:
    out = osa(f'tell application "System Events" to get name of every window of '
              f'(first application process whose bundle identifier is "{FL_ID}")')
    return [w.strip() for w in out.split(",") if w.strip()]


def fl_cg_windows() -> list[tuple[str, dict]]:
    """FL's windows as macOS sees them (CoreGraphics): FL's own dialogs such as
    'Welcome... trial mode' are missing from System Events but are here."""
    import Quartz
    out = []
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll,
                                               Quartz.kCGNullWindowID):
        # hidden windows stay in the list (closed Welcome/Buy): onscreen only.
        # In Stage Manager FL's windows are offscreen until FL is in front.
        if ("FL Studio" in str(w.get("kCGWindowOwnerName")) and w.get("kCGWindowName")
                and w.get("kCGWindowIsOnscreen")):
            out.append((str(w["kCGWindowName"]), dict(w["kCGWindowBounds"])))
    return out


def click(x: int, y: int) -> None:
    """A real mouse click (CGEvent) at screen point x, y; the pointer goes back."""
    import Quartz
    old = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    for t in (Quartz.kCGEventMouseMoved, Quartz.kCGEventLeftMouseDown,
              Quartz.kCGEventLeftMouseUp):
        e = Quartz.CGEventCreateMouseEvent(None, t, (x, y), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
        time.sleep(0.08)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventMouseMoved, old, Quartz.kCGMouseButtonLeft))


def drag(x0: int, y0: int, x1: int, y1: int, steps: int = 20) -> None:
    """A real mouse drag (CGEvent) from one screen point to another."""
    import Quartz
    old = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    def post(t, x, y):
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(
            None, t, (x, y), Quartz.kCGMouseButtonLeft))
    post(Quartz.kCGEventMouseMoved, x0, y0)
    time.sleep(0.1)
    post(Quartz.kCGEventLeftMouseDown, x0, y0)
    time.sleep(0.15)
    for i in range(1, steps + 1):
        k = i / steps
        post(Quartz.kCGEventLeftMouseDragged, x0 + (x1 - x0) * k, y0 + (y1 - y0) * k)
        time.sleep(0.02)
    time.sleep(0.15)
    post(Quartz.kCGEventLeftMouseUp, x1, y1)
    time.sleep(0.1)
    post(Quartz.kCGEventMouseMoved, *old)


def close_dialogs(parts=("Buy FL Studio", "Welcome to FL Studio")) -> list[str]:
    """Trial dialogs block the MIDI controller (timeouts) and Escape does not
    close them. FL forward, then each dialog's own x button (14/15 pt from its
    top-right corner; bounds read only after FL is in front - in Stage Manager
    the bounds are the thumbnail's), then focus back to the previous app."""
    # no early exit: "Welcome" is created only once FL comes to the front
    from fl_studio_mcp.utils.fl_trigger import log_gui
    prev = osa('tell application "System Events" to get name of first application '
               'process whose frontmost is true')
    osa(f'tell application id "{FL_ID}" to activate')
    time.sleep(1.5)
    closed = []
    for part in parts:  # Buy first: it sits on top of Welcome
        if not any(part in name for name, _ in fl_cg_windows()):
            continue
        for _ in range(2):
            hits = [b for name, b in fl_cg_windows() if part in name]
            if not hits:
                break
            b = hits[0]
            x, y = int(b["X"] + b["Width"] - 14), int(b["Y"] + 15)
            click(x, y)
            log_gui("close-" + part.split()[0].lower())
            time.sleep(0.5)
        if not any(part in name for name, _ in fl_cg_windows()):
            closed.append(part)
    osa('tell application "System Events" to set frontmost of '
        f'application process "{prev}" to true')
    return closed


def close_buy() -> bool:
    return bool(close_dialogs(("Buy FL Studio",)))


def menu(*path: str) -> str:
    """Click a native FL menu item: menu("File", "New from template", "Minimal", "Basic")."""
    ref = f'menu "{path[0]}" of menu bar item "{path[0]}" of menu bar 1'
    for item in path[1:-1]:
        ref = f'menu 1 of menu item "{item}" of {ref}'
    return osa(f'tell application "System Events" to tell process "OsxFL" to '
               f'click menu item "{path[-1]}" of {ref}')


# Channels 5-12 of dark_808, added in this order after the template's 0-4
# (Kick, Clap, HiHat, Snare, 808 Astronomic). 7 is a muted placeholder.
REBUILD_ADD = ["FL Keys", "PoiZone", "3x Osc", "FL Keys", "PoiZone", "FLEX",
               "PoiZone", "PoiZone", "FLEX", "PoiZone"]  # 13 spare, 14 PoiZone Verse 2


def cmd_rebuild(track, args):
    """The trial does not open saved projects (nor copies of them as
    templates): new project from the stock template with the 808 kit, add the
    instruments through FL's native Add menu, then the track script fills it.
    Native menu clicks work while FL is in the background - FL is never
    brought forward (Nik works in other apps meanwhile). Resumes if stopped
    halfway; --new starts from the template again."""
    from fl_studio_mcp.utils.fl_trigger import log_gui
    c = conn()

    def count():
        return len(fl_io.send(c, "channels.getAll")["channels"])

    n = count()
    if flag(args, "--new") or not 5 <= n <= 5 + len(REBUILD_ADD):
        menu("File", "New from template", "Minimal", "Basic with limiter")
        log_gui("menu-bg")
        time.sleep(4)
        n = count()
        if n != 5:
            sys.exit(f"template has {n} channels, expected 5 (808 kit)")
    for inst in REBUILD_ADD[n - 5:]:
        menu("Add", inst)
        log_gui("menu-bg")
        time.sleep(2)
        menu("View", "Close all plugin windows")
        time.sleep(0.4)
    chs = fl_io.send(c, "channels.getAll")["channels"]
    for ch in chs:
        print(f"  {ch['index']:2} {ch['name']}")
    if len(chs) != 5 + len(REBUILD_ADD):
        sys.exit(f"{len(chs)} channels, expected {5 + len(REBUILD_ADD)}")
    print("next: fl write --settings --force --verify")


def newest_flp() -> Path | None:
    fs = sorted(PROJECTS.glob("*/*.flp"), key=lambda p: p.stat().st_mtime)
    return fs[-1] if fs else None


def cmd_save(track, args):
    flp = Path(args[0]) if args else newest_flp()
    m0 = flp.stat().st_mtime if flp else 0
    c = conn()
    try:
        fl_io.send(c, "project.save", timeout=3)
    except Exception as e:  # the trial holds the save until "Buy" is closed
        print("save is waiting:", e)
    for _ in range(40):
        time.sleep(0.5)
        if flp and flp.stat().st_mtime != m0 and flp.stat().st_size > 0:
            print(f"saved {flp.name} {time.strftime('%H:%M:%S')} {flp.stat().st_size} bytes")
            return
        close_buy()
    sys.exit("not saved in 20 s: look at FL's windows (fl state)")


def cmd_state(track, args):
    from fl_studio_mcp.utils.fl_trigger import fl_pid
    pid = fl_pid()
    print("FL:", f"running (pid {pid})" if pid else "NOT running")
    if not pid:
        return
    print("windows:", fl_windows())
    c = conn()
    st = fl_io.send(c, "extra.status")
    print("extra commands:", len(st.get("actions", [])), "| error:", st.get("error"))
    print("pattern:", fl_io.send(c, "patterns.info"))
    pos = fl_io.send(c, "transport.getPos")
    print(f"tempo {pos['bpm']} | pos {pos['s']} s | playing {pos['playing']} "
          f"| song mode {pos['song_mode']}")
    rev = {v: k for k, v in track.NAMES.items()}
    for ch in fl_io.send(c, "channels.getAll")["channels"]:
        print(f"  {ch['index']:2} {ch['name']:<22} {rev.get(ch['index'], '-'):<6} "
              f"mixer {ch['target_fx_track']:2} {'MUTED' if ch['is_muted'] else ''}")
    cmd_gui(track, [])


def cmd_gui(track, args):
    from fl_studio_mcp.utils.fl_trigger import GUI_LOG
    today = time.strftime("%Y-%m-%d")
    lines = GUI_LOG.read_text().splitlines() if GUI_LOG.exists() else []
    cnt = Counter(ln.split()[2] for ln in lines if ln.startswith(today))
    print("GUI today:", dict(cnt) or "none",
          "| bg-key = no focus change, focus-key/close-buy = FL brought forward")


CMDS = {"check": cmd_check, "map": cmd_map, "notes": cmd_notes, "edit": cmd_edit,
        "write": cmd_write, "play": cmd_play, "ab": cmd_ab, "params": cmd_params,
        "param": cmd_param, "save": cmd_save, "rebuild": cmd_rebuild, "tone": cmd_tone,
        "stems": cmd_stems,
        "state": cmd_state, "gui": cmd_gui}


def main():
    args = sys.argv[1:]
    track = importlib.import_module(opt(args, "--track", "dark_808"))
    if not args or args[0] not in CMDS:
        print(__doc__)
        return
    r = CMDS[args[0]](track, args[1:])
    if r is False:
        sys.exit(1)


if __name__ == "__main__":
    main()
