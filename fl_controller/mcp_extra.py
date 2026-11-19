"""Extra FL Studio MCP commands (hot-reloaded by device_FLStudioMCP.py).

Runs inside FL Studio. The controller injects the FL API modules
(channels, general, midi, mixer, patterns, plugins, transport, ui) into this
file's namespace and re-reads it whenever it changes - no script reload needed.
Add a handler function and list it in EXTRA_HANDLERS.
"""
# ruff: noqa: F821  (FL modules are injected by the controller)

# =============================================================================

STEP_PARAMS = {"pitch": 0, "velocity": 1, "release": 2, "fine": 3,
               "pan": 4, "mod_x": 5, "mod_y": 6, "shift": 7}


def _tempo_bpm() -> float:
    t = mixer.getCurrentTempo(False)
    return t / 1000 if t > 1000 else t  # FL returns BPM*1000 here


def handle_transport_get_tempo(params: dict) -> dict:
    return {"bpm": _tempo_bpm()}


def handle_transport_set_tempo(params: dict) -> dict:
    bpm = float(params.get("bpm", 120))
    general.processRECEvent(midi.REC_Tempo, int(round(bpm * 1000)),
                            midi.REC_Control | midi.REC_UpdateControl)
    return {"bpm": _tempo_bpm()}


def handle_project_save(params: dict) -> dict:
    transport.globalTransport(midi.FPT_Save, 1)
    return {"saved": True}


def handle_patterns_info(params: dict) -> dict:
    num = patterns.patternNumber()
    return {"current": num, "name": patterns.getPatternName(num),
            "length_steps": patterns.getPatternLength(num),
            "count": patterns.patternCount()}


def _clear_steps(channel: int, length: int) -> int:
    # Overwriting can leave stacked duplicate notes (+6 dB); clear in passes
    # until every grid bit reads 0.
    for _ in range(4):
        for i in range(length):
            channels.setGridBit(channel, i, 0, True)
        if not any(channels.getGridBit(channel, i, True) for i in range(length)):
            break
    return sum(1 for i in range(length) if channels.getGridBit(channel, i, True))


def handle_channels_clear_steps(params: dict) -> dict:
    channel = params.get("channel", 0)
    length = int(params.get("length", 64))
    left = _clear_steps(channel, length)
    return {"channel_name": channels.getChannelName(channel, True), "remaining": left}


def handle_channels_set_steps(params: dict) -> dict:
    """steps: [{"pos": int, "velocity": 0-127, "pan": 0-127, "shift": ticks,
    "pitch": midi}]; shift is a delay in ticks
    inside the step (0 .. PPQ/4-1). Clears `length` steps first unless clear=False."""
    channel = params.get("channel", 0)
    steps = params.get("steps", [])
    length = int(params.get("length", max([s["pos"] for s in steps] + [63]) + 1))
    left = 0
    if params.get("clear", True):
        left = _clear_steps(channel, length)
    pat = patterns.patternNumber()
    tps = general.getRecPPQ() // 4  # ticks per step
    for s in steps:
        pos = int(s["pos"])
        channels.setGridBit(channel, pos, 1, True)
        for key, pid in STEP_PARAMS.items():
            if key in s:
                value = int(s[key])
                if key == "shift":
                    # Param 7 is the note's absolute time in ticks, not an
                    # offset: writing a small number moves the note to bar 1.
                    value = pos * tps + max(0, min(value, tps - 1))
                channels.setStepParameterByIndex(channel, pat, pos, pid, value, True)
    return {"channel_name": channels.getChannelName(channel, True),
            "steps_set": len(steps), "left_before_write": left,
            "pattern": pat}


def handle_channels_get_steps(params: dict) -> dict:
    channel = params.get("channel", 0)
    length = int(params.get("length", 64))
    out = []
    for i in range(length):
        if channels.getGridBit(channel, i, True):
            out.append({"pos": i,
                        "velocity": channels.getCurrentStepParam(channel, i, 1, True),
                        "pan": channels.getCurrentStepParam(channel, i, 4, True),
                        "pitch": channels.getCurrentStepParam(channel, i, 0, True)})
    return {"channel_name": channels.getChannelName(channel, True), "steps": out}


def handle_ui_focus_piano_roll(params: dict) -> dict:
    """Point the piano roll at a channel and focus it (for ComposeWithLLM)."""
    channel = params.get("channel", 0)
    method = params.get("method", "rack")
    if method == "rack":
        # F7 acts on the channel rack selection, so focus the rack first
        # (otherwise a focused plugin window decides which channel opens)
        ui.showWindow(midi.widChannelRack)
        ui.setFocused(midi.widChannelRack)
    channels.selectOneChannel(channel, True)
    if method in ("f7", "rack"):
        # Same as pressing F7: piano roll for the selected channel
        if ui.getVisible(midi.widPianoRoll):
            ui.hideWindow(midi.widPianoRoll)
        transport.globalTransport(midi.FPT_F7, 1)
    ui.showWindow(midi.widPianoRoll)
    ui.setFocused(midi.widPianoRoll)
    return {"channel_name": channels.getChannelName(channel, True),
            "focused": ui.getFocused(midi.widPianoRoll),
            "caption": ui.getFocusedFormCaption()}


def handle_ui_show_window(params: dict) -> dict:
    wid = int(params.get("window", 3))
    if params.get("hide"):
        ui.hideWindow(wid)
    else:
        ui.showWindow(wid)
        ui.setFocused(wid)
    return {"window": wid, "visible": ui.getVisible(wid)}


def handle_ui_escape(params: dict) -> dict:
    """Press Escape inside FL (closes the focused dialog, e.g. trial ads)."""
    before = ui.getFocusedFormCaption()
    ui.escape()
    return {"closed": before, "now_focused": ui.getFocusedFormCaption()}


def handle_ui_focused(params: dict) -> dict:
    return {"caption": ui.getFocusedFormCaption()}


# ---------------------------------------------------------------------------
# Live tempo map: FL has no scripting API for tempo automation clips, so the
# controller follows a map while playing. tempo_map.json next to this file:
# {"enabled": true, "points": [[start_step, bpm], ...]}  (16 steps = 4/4 bar)
# Only live playback follows it; an offline render keeps the project tempo.
# ---------------------------------------------------------------------------

_TEMPO = {"mtime": None, "points": [], "enabled": False, "last": None,
          "idle_calls": 0, "step": None}


def _tempo_map_path():
    return SCRIPT_DIR / "tempo_map.json"


def _load_tempo_map():
    path = _tempo_map_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _TEMPO["enabled"] = False
        return
    if mtime != _TEMPO["mtime"]:
        data = json.loads(path.read_text(encoding="utf-8"))
        _TEMPO["points"] = sorted((int(a), float(b)) for a, b in data.get("points", []))
        _TEMPO["enabled"] = bool(data.get("enabled", True))
        _TEMPO["mtime"] = mtime
        _TEMPO["last"] = None


def handle_idle(params):
    _TEMPO["idle_calls"] += 1
    _load_tempo_map()
    if not _TEMPO["enabled"] or not _TEMPO["points"] or not transport.isPlaying():
        return {}
    tps = general.getRecPPQ() // 4
    step = transport.getSongPos(midi.SONGLENGTH_ABSTICKS) // tps
    _TEMPO["step"] = step
    bpm = _TEMPO["points"][0][1]
    for start, b in _TEMPO["points"]:
        if step >= start:
            bpm = b
        else:
            break
    if bpm != _TEMPO["last"]:
        general.processRECEvent(midi.REC_Tempo, int(round(bpm * 1000)),
                                midi.REC_Control | midi.REC_UpdateControl)
        _TEMPO["last"] = bpm
    return {"step": step, "bpm": bpm}


def handle_tempo_set_map(params):
    """points: [[start_step, bpm], ...]; enabled: bool."""
    data = {"enabled": params.get("enabled", True), "points": params.get("points", [])}
    _tempo_map_path().write_text(json.dumps(data), encoding="utf-8")
    _TEMPO["mtime"] = None
    return {"points": len(data["points"]), "enabled": data["enabled"]}


def handle_tempo_get_map(params):
    _load_tempo_map()
    return {"enabled": _TEMPO["enabled"], "points": _TEMPO["points"],
            "current_bpm": _tempo_bpm(), "idle_calls": _TEMPO["idle_calls"],
            "step": _TEMPO["step"], "last_set": _TEMPO["last"]}


def handle_debug_core(params):
    import sys
    mods = {k: sorted(n for n in dir(m) if n.startswith("On"))
            for k, m in list(sys.modules.items()) if "FLStudioMCP" in k or k == "__main__"}
    return {"modules": mods}


def handle_patterns_jump(params):
    """Switch to pattern `index` (1-based); FL creates it if it is empty."""
    idx = int(params.get("index", 1))
    patterns.jumpToPattern(idx)
    if params.get("name"):
        patterns.setPatternName(idx, str(params["name"]))
    num = patterns.patternNumber()
    return {"current": num, "name": patterns.getPatternName(num),
            "length_steps": patterns.getPatternLength(num),
            "count": patterns.patternCount()}


def handle_patterns_list(params):
    return {"patterns": [{"index": i, "name": patterns.getPatternName(i),
                          "length_steps": patterns.getPatternLength(i)}
                         for i in range(1, patterns.patternCount() + 1)],
            "current": patterns.patternNumber()}


# ---------------------------------------------------------------------------
# Channel REC events (sampler filter, pitch, ...) and the mixer's built-in EQ.
# ---------------------------------------------------------------------------

def _rec_id(params):
    import device  # noqa: F401  (FL module)
    ch = int(params.get("channel", 0))
    return channels.getRecEventId(ch, True) + getattr(midi, "REC_Chan_" + params["param"])


def _rec_state(eid):
    import device
    out = {"value": device.getLinkedValue(eid)}
    try:
        out["text"] = device.getLinkedValueString(eid)
    except Exception:
        pass
    return out


def handle_channels_get_rec(params):
    """param: FCut, FRes, FType, Pitch, Vol, Pan, ... (midi.REC_Chan_*)."""
    return _rec_state(_rec_id(params))


def handle_channels_set_rec(params):
    """Set a channel REC control to a normalized value 0..1."""
    eid = _rec_id(params)
    v = max(0.0, min(1.0, float(params["value"])))
    general.processRECEvent(eid, int(round(v * midi.FromMIDI_Max)),
                            midi.REC_MIDIController)
    return _rec_state(eid)


def handle_mixer_get_eq(params):
    t = int(params.get("track", 0))
    bands = []
    for b in range(mixer.getEqBandCount()):
        bands.append({"band": b, "gain": mixer.getEqGain(t, b),
                      "gain_db": mixer.getEqGain(t, b, 1),
                      "freq": mixer.getEqFrequency(t, b),
                      "freq_hz": mixer.getEqFrequency(t, b, 1),
                      "bw": mixer.getEqBandwidth(t, b)})
    return {"track": t, "bands": bands}


def handle_mixer_set_eq(params):
    """band 0/1/2; gain, freq, bw normalized 0..1 (gain 0.5 = 0 dB)."""
    t, b = int(params.get("track", 0)), int(params.get("band", 0))
    if "freq" in params:
        mixer.setEqFrequency(t, b, float(params["freq"]))
    if "bw" in params:
        mixer.setEqBandwidth(t, b, float(params["bw"]))
    if "gain" in params:
        mixer.setEqGain(t, b, float(params["gain"]))
    return handle_mixer_get_eq({"track": t})


def handle_batch(params):
    """Run many commands in one round trip: {"commands": [{"action", "params"}]}.
    Results come back in order; one failing command does not stop the rest."""
    import sys
    dispatch = sys.modules["__main__"].dispatch_command
    out = []
    for cmd in params.get("commands", []):
        try:
            out.append(dispatch(cmd["action"], cmd.get("params", {})))
        except Exception as e:
            out.append({"error": str(e)})
    return {"results": out}


def handle_plugins_preset(params):
    """Current preset of a channel plugin; optional "step": +1/-1 to move first.
    "index": preset number to jump to (0-based) via plugins.setPreset if present."""
    i = int(params.get("index_channel", params.get("channel", 0)))
    step = int(params.get("step", 0))
    if step > 0:
        for _ in range(step):
            plugins.nextPreset(i, -1, True)
    elif step < 0:
        for _ in range(-step):
            plugins.prevPreset(i, -1, True)
    count = plugins.getPresetCount(i, -1, True)
    out = {"preset": plugins.getName(i, -1, midi.FPN_Preset, -1, True), "count": count}
    if params.get("list"):  # paramIndex k -> name of preset k; -1 -> current preset
        out["presets"] = [plugins.getName(i, -1, midi.FPN_Preset, k, True)
                          for k in range(count)]
    return out


def handle_transport_get_pos(params):
    """Exact play position: ms, seconds, abs ticks, beats (PPQ), bar hint.
    Use it to prove which part of the track is sounding (not the start request)."""
    ppq = general.getRecPPQ()
    ticks = transport.getSongPos(midi.SONGLENGTH_ABSTICKS)
    return {"ms": transport.getSongPos(midi.SONGLENGTH_MS),
            "s": transport.getSongPos(midi.SONGLENGTH_S),
            "ticks": ticks, "beats": ticks / ppq, "ppq": ppq,
            "hint": transport.getSongPosHint(),
            "playing": bool(transport.isPlaying()),
            "song_mode": transport.getLoopMode() == 1,
            "bpm": _tempo_bpm()}


EXTRA_HANDLERS = {
    "transport.getPos": handle_transport_get_pos,
    "plugins.preset": handle_plugins_preset,
    "batch": handle_batch,
    "channels.getRec": handle_channels_get_rec,
    "channels.setRec": handle_channels_set_rec,
    "mixer.getEq": handle_mixer_get_eq,
    "mixer.setEq": handle_mixer_set_eq,
    "patterns.jump": handle_patterns_jump,
    "patterns.list": handle_patterns_list,
    "debug.core": handle_debug_core,
    "__idle__": handle_idle,
    "tempo.setMap": handle_tempo_set_map,
    "tempo.getMap": handle_tempo_get_map,
    "tempo.tick": handle_idle,
    "ui.escape": handle_ui_escape,
    "ui.focused": handle_ui_focused,
    "transport.getTempo": handle_transport_get_tempo,
    "transport.setTempo": handle_transport_set_tempo,
    "project.save": handle_project_save,
    "patterns.info": handle_patterns_info,
    "channels.clearSteps": handle_channels_clear_steps,
    "channels.setSteps": handle_channels_set_steps,
    "channels.getSteps": handle_channels_get_steps,
    "ui.focusPianoRoll": handle_ui_focus_piano_roll,
    "ui.showWindow": handle_ui_show_window,
}
