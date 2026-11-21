"""Record the current FL pattern by channel groups (others muted) into WAV
files - our side of a soundlab comparison. Usage:
  uv run python tracks/record_groups.py --track sympathy_study OUTDIR START DUR name=0,1 ... mix=all
"""
from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fl_io  # noqa: E402
import listen  # noqa: E402

args = sys.argv[1:]
i = args.index("--track")
track = importlib.import_module(args[i + 1])
del args[i:i + 2]
out, start, dur, groups = Path(args[0]), float(args[1]), float(args[2]), args[3:]
out.mkdir(parents=True, exist_ok=True)
listen.track = track
c = fl_io.get_connection()
c.ensure_connected()
muted0 = {x["index"]: x["is_muted"] for x in fl_io.send(c, "channels.getAll")["channels"]}
try:
    for g in groups:
        name, chs = g.split("=")
        on = set(muted0) if chs == "all" else {int(x) for x in chs.split(",")}
        fl_io.batch(c, [("channels.mute",
                         {"index": k, "muted": (muted0[k] if chs == "all" else k not in on)})
                        for k in muted0])
        listen.play(start, dur, rec=True, quiet=True)
        shutil.copy(listen.REC, out / f"{name}.wav")
        print("recorded", name)
finally:
    fl_io.batch(c, [("channels.mute", {"index": k, "muted": m}) for k, m in muted0.items()])
