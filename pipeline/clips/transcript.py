"""Transcripts: SRT/VTT sidecars (creator-supplied or published with the source) → cues and word timings.
Falls back to local faster-whisper if it is installed (optional; no paid API). Never guesses a transcript."""
import re
from pathlib import Path

from ..util import log

_TS = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})|(\d{2}):(\d{2})[,.](\d{3})")


def _secs(ts):
    m = _TS.match(ts.strip())
    if not m:
        raise ValueError(f"bad timestamp {ts!r}")
    if m.group(1):
        h, mi, s, ms = map(int, m.group(1, 2, 3, 4))
    else:
        h, (mi, s, ms) = 0, map(int, m.group(5, 6, 7))
    return h * 3600 + mi * 60 + s + ms / 1000


def parse(text):
    """SRT or WebVTT → [{"start", "end", "text"}] (tags, cue settings and speaker markup removed)."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("﻿", "").replace("\r", "")):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        i = next((k for k, l in enumerate(lines) if "-->" in l), None)
        if i is None:
            continue
        a, b = lines[i].split("-->")
        body = " ".join(lines[i + 1:])
        body = re.sub(r"<[^>]+>|\{[^}]+\}", "", body).strip()
        if body:
            cues.append({"start": _secs(a), "end": _secs(b.split()[0]), "text": re.sub(r"\s+", " ", body)})
    return cues


def words(cues):
    """Word timings spread across each cue by character length (good enough for 3-word caption groups)."""
    out = []
    for c in cues:
        toks = c["text"].split()
        total = sum(len(t) + 1 for t in toks) or 1
        t, span = c["start"], max(0.05, c["end"] - c["start"])
        for tok in toks:
            d = span * (len(tok) + 1) / total
            out.append({"start": t, "end": t + d, "word": tok})
            t += d
    return out


def load(path):
    return parse(Path(path).read_text(encoding="utf-8", errors="replace"))


def whisper(media_path, model_name="small.en"):
    """Optional local transcription (pip install faster-whisper). Returns cues, or None if unavailable.
    The model downloads once (~150 MB base.en, ~470 MB small.en) and then runs offline on the CPU."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    import subprocess
    import numpy as np
    log(f"transcribing locally with faster-whisper ({model_name})")
    # decode with FFmpeg (16 kHz mono float) rather than faster-whisper's PyAV path, which breaks on some PyAV versions
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(media_path), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le",
                          "-"], capture_output=True, check=True).stdout
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segs, _ = model.transcribe(np.frombuffer(pcm, dtype=np.float32), vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segs if s.text.strip()]
