"""Free neural voiceover with word-level timings (Microsoft Edge TTS)."""
import asyncio
import random
import re
import time

from . import control
from .util import run, media_duration, log

TTS_PAUSES = (1, 2, 15, 45, 90)  # seconds after each failed try: NoAudioReceived comes in bursts (9-11 Nov on GitHub)
TTS_TIMEOUT = 90  # seconds per scene; edge-tts's receive_timeout doesn't cover a stalled websocket (hung a run 13 h)


async def _tts(text, voice, rate, pitch, mp3_path):
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    words = []
    with open(mp3_path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append({"start": start, "end": start + chunk["duration"] / 1e7, "word": chunk["text"]})
    return words


def _tokens(text):
    return [re.sub(r"[^\w]", "", w.lower()) for w in text.split() if any(ch.isalnum() for ch in w)]


def complete(text, words, min_share=0.5):
    """Did the voice say the whole scene? A websocket that drops mid-stream still returns the first words, and the
    captions (built from these timings) would silently lose the rest too. The stream's last word must be one of
    the scene's last three; word counts are only a loose floor, because Edge TTS merges number phrases into one
    boundary ("2.5 million years": 0.77 of the written words on a complete real scene, 1 Nov)."""
    mine = _tokens(text)
    last = (_tokens(words[-1]["word"]) or [""])[-1] if words else ""
    return bool(words) and len(words) >= min_share * len(mine) and (not mine or last in mine[-3:])


def synthesize(scenes, cfg, workdir):
    """Creates one wav per scene, then voice.wav. Returns scenes with timings."""
    lang = cfg["channel"].get("language", "en")
    voice = cfg["voice"][lang]
    rate, pitch = cfg["voice"].get("rate", "+0%"), cfg["voice"].get("pitch", "+0Hz")

    timeline, t = [], 0.0
    wavs = []
    for i, sc in enumerate(scenes):
        mp3 = workdir / f"vo_{i:02d}.mp3"
        wav = workdir / f"vo_{i:02d}.wav"
        for attempt, pause in enumerate(TTS_PAUSES):
            try:
                control.count("tts_calls")
                words = asyncio.run(asyncio.wait_for(_tts(sc["text"], voice, rate, pitch, mp3), TTS_TIMEOUT))
                if not words:
                    raise RuntimeError("no word timings returned (captions would be empty)")
                if not complete(sc["text"], words):
                    raise RuntimeError(f"stream ended early: {len(words)} of ~{len(_tokens(sc['text']))} words spoken")
                break
            except Exception as e:
                log(f"TTS retry {attempt + 1}: {type(e).__name__}: {e}")
                time.sleep(pause + random.random())
        else:
            raise RuntimeError(f"Edge TTS failed {len(TTS_PAUSES)} times")
        # trim trailing silence, add a tiny breath gap
        run(["ffmpeg", "-y", "-i", str(mp3), "-af",
             "areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,apad=pad_dur=0.12",
             "-ar", "44100", "-ac", "1", str(wav)])
        dur = media_duration(wav)
        timeline.append({**sc, "start": t, "duration": dur,
                         "words": [{**w, "start": w["start"] + t, "end": min(w["end"], dur) + t} for w in words]})
        t += dur
        wavs.append(wav)

    concat_list = workdir / "vo_list.txt"
    concat_list.write_text("".join(f"file '{w.name}'\n" for w in wavs))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list.name, "-c", "copy", "voice.wav"], cwd=workdir)
    log(f"voiceover {t:.1f}s across {len(scenes)} scenes")
    return timeline, workdir / "voice.wav"
