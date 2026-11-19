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
    channels.selectOneChannel(channel, True)
    method = params.get("method", "f7")
    if method == "f7":
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


EXTRA_HANDLERS = {
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
