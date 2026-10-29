"""The local, free pass over every recording - what the lineup match (Claude) and Marek's review work from:

  levels.json        left / right RMS level per second (one-sided stereo, the waveform, the quiet between talks)
  transcript.json    faster-whisper segments [{start, end, text}] over the whole recording, done in chunks
  frames/<s>.jpg     a small still every frame_every_seconds (keyframes only)
  proxy.mp4          a 480p copy for the browser, only when it cannot play the recording itself

Each part is resumable: a finished part (or transcript chunk) is never done again.
"""
import glob
import json
import os
import site

from . import machine, media

_MODEL = {}


def _cuda_dlls():
    """Windows: the CUDA libraries pip put in site-packages/nvidia/*/bin (setup.cmd) are not on the DLL path."""
    if os.name != "nt":
        return
    for sp in site.getsitepackages():
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def model(cfg, cpu=False):
    """The Whisper model, loaded once per process (the first use downloads it to ~/.live-factory/whisper)."""
    name, device, compute = machine.whisper_setup(cfg)
    if cpu and device != "cpu":
        name, device, compute = ("small" if str(cfg.get("whisper_model") or "auto") == "auto" else name), "cpu", "int8"
    if device == "cuda":
        _cuda_dlls()
    key = (name, device, compute)
    if key not in _MODEL:
        from faster_whisper import WhisperModel
        try:
            _MODEL[key] = WhisperModel(name, device=device, compute_type=compute,
                                       download_root=os.path.join(machine.HOME_DIR, "whisper"))
        except Exception:
            if device != "cuda":
                raise
            _MODEL["cpu_only"] = True               # CUDA libraries missing: the CPU still works, just slower
            return model(cfg, cpu=True)
    return _MODEL[key], key


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    machine.swap_in(tmp, path)


def transcribe(src, folder, duration, cfg, on_progress=None):
    """Whisper over the recording in chunk_minutes pieces (folder/chunks/<n>.json each), then all of it as
    folder/transcript.json. Voice activity detection skips the silence, so quiet breaks cost nothing."""
    chunks = os.path.join(folder, "chunks")
    os.makedirs(chunks, exist_ok=True)
    size = int(float(cfg.get("chunk_minutes") or 30) * 60)
    count = max(1, int((duration + size - 1) // size))
    segs = []
    for n in range(count):
        done = os.path.join(chunks, "%03d.json" % n)
        if not os.path.exists(done):
            if on_progress:
                on_progress("transcribing part %d of %d" % (n + 1, count), n * 100 // count)
            wav = os.path.join(chunks, "%03d.wav" % n)
            media.audio_chunk(src, wav, n * size, size)
            part, cpu = [], _MODEL.get("cpu_only", False)
            for attempt in (1, 2):
                whisper, key = model(cfg, cpu)
                try:
                    part = _segments(whisper, wav, cfg, n * size, duration, on_progress)
                    break
                except Exception:
                    if key[1] != "cuda" or attempt == 2:
                        raise
                    _MODEL["cpu_only"] = cpu = True     # the card's libraries failed at run time: CPU from now on
            _write_json(done, {"model": "/".join(key), "segments": part})
            os.remove(wav)
        with open(done, encoding="utf-8") as f:
            segs += json.load(f)["segments"]
    _write_json(os.path.join(folder, "transcript.json"), {"segments": segs})
    return segs


def _segments(whisper, wav, cfg, offset, duration, on_progress):
    """One chunk through Whisper: [{start, end, text}] in seconds of the whole recording."""
    out, _ = whisper.transcribe(wav, language=cfg.get("whisper_language") or None, vad_filter=True,
                                beam_size=1, condition_on_previous_text=False)
    part = []
    for s in out:                                   # the work happens while the segments are read
        if s.text.strip():                          # lp / nsp: Whisper's confidence - low on muffled or noisy sound
            part.append({"start": round(offset + s.start, 2), "end": round(offset + s.end, 2), "text": s.text.strip(),
                         "lp": round(s.avg_logprob, 2), "nsp": round(s.no_speech_prob, 2)})
        if on_progress:
            at = int(offset + s.end)
            on_progress("transcribing %d:%02d of %d:%02d" % (at // 3600, at % 3600 // 60, duration // 3600,
                                                              duration % 3600 // 60),
                        int(min(99, at * 100 // max(1, duration))))
    return part


def load(folder, name):
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def speech_blocks(segments, min_gap):
    """The transcript's segments joined into blocks of speech wherever the pause between them is shorter than
    min_gap seconds: [(start, end)]."""
    out = []
    for s in segments:
        if out and s["start"] - out[-1][1] < min_gap:
            out[-1] = (out[-1][0], max(out[-1][1], s["end"]))
        else:
            out.append((s["start"], s["end"]))
    return out
