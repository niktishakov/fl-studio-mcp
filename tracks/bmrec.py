"""Record N seconds from the "Background Music" input with PortAudio
(sounddevice) at a fixed 48 kHz. ffmpeg's avfoundation capture lost ~10% of
the samples (6 s -> 5.39 s): the audio came out sped up and pitched up
(Nik heard "faster and stuttering"; the 808 measured a tone high).
Runs in ~/.venvs/audio. Usage: bmrec.py SECONDS OUT.wav"""
import sys

import sounddevice as sd
import soundfile as sf

dur, out = float(sys.argv[1]), sys.argv[2]
dev = [i for i, d in enumerate(sd.query_devices())
       if d["name"] == "Background Music" and d["max_input_channels"] > 0][0]
x = sd.rec(int(dur * 48000), samplerate=48000, channels=2, device=dev, dtype="float32")
sd.wait()
sf.write(out, x, 48000)
