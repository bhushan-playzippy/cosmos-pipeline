"""Original background music for both channels, synthesised from scratch (no samples, no third-party audio).

Why generated: "free" stock tracks regularly carry Content ID claims that divert a Short's revenue or block it;
music we make ourselves can't be claimed and needs no attribution. Each track is ~90 s of soft bed music that
render.py mixes under the voice (ducked while the narrator speaks, fading in and out).
  space:    ambient pads + shimmering bell arpeggio + sub drone           → Universe in a Teaspoon
  stories:  documentary score: low strings, felt-piano ostinato, heartbeat → Unbelievably True

Run:  .venv/bin/python tools/make_music.py      → assets/music/space/*.m4a, assets/music/stories/*.m4a
Any .mp3/.m4a you add to those folders (e.g. YouTube Audio Library, "no attribution required") joins the rotation.
"""
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SR = 44100
LENGTH = 90.0


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def fade(n, a, r):
    """Smooth (raised-cosine) fade in over `a` and out over `r` samples."""
    e = np.ones(n)
    a, r = min(a, n // 2), min(r, n // 2)
    if a:
        e[:a] = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, a))
    if r:
        e[-r:] = 0.5 + 0.5 * np.cos(np.linspace(0, np.pi, r))
    return e


def lowpass(x, fc, order=4):
    """Gentle zero-phase low-pass in the frequency domain (no per-sample Python loop)."""
    X = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(x.shape[0], 1 / SR)
    g = 1 / np.sqrt(1 + (f / fc) ** (2 * order))
    return np.fft.irfft(X * (g[:, None] if x.ndim == 2 else g), n=x.shape[0], axis=0)


def highpass(x, fc, order=3):
    """Phone speakers reproduce little below ~150 Hz: energy down there only muddies the voice."""
    X = np.fft.rfft(x, axis=0)
    f = np.maximum(np.fft.rfftfreq(x.shape[0], 1 / SR), 1e-3)
    g = 1 / np.sqrt(1 + (fc / f) ** (2 * order))
    return np.fft.irfft(X * (g[:, None] if x.ndim == 2 else g), n=x.shape[0], axis=0)


def reverb(x, seconds=3.5, decay=0.9, seed=0):
    """Stereo hall: convolution with two decorrelated, darkened noise tails."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    ir = rng.standard_normal((n, 2)) * np.exp(-t / decay)[:, None]
    ir = lowpass(ir, 4500, 2)
    ir[: int(0.012 * SR)] = 0  # pre-delay keeps the dry sound clear
    ir /= np.sqrt((ir ** 2).sum(axis=0))
    N = 1 << int(np.ceil(np.log2(x.shape[0] + n)))
    y = np.fft.irfft(np.fft.rfft(x, N, axis=0) * np.fft.rfft(ir, N, axis=0), N, axis=0)[: x.shape[0]]
    return y


def pad_note(f, dur, bright=1.6, harmonics=8, seed=0):
    """Warm detuned pad: three slightly detuned additive saw-ish voices, each panned differently."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros((n, 2))
    for det, pan in ((0.996, 0.25), (1.0, 0.5), (1.004, 0.75)):
        v = np.zeros(n)
        for k in range(1, harmonics + 1):
            v += np.sin(2 * np.pi * f * det * k * t + rng.uniform(0, 2 * np.pi)) / k ** bright
        lfo = 1 + 0.08 * np.sin(2 * np.pi * rng.uniform(0.07, 0.15) * t + rng.uniform(0, 6))
        v *= lfo
        out[:, 0] += v * np.sqrt(1 - pan)
        out[:, 1] += v * np.sqrt(pan)
    return out / 3


def bell(f, dur=2.5, tau=1.1):
    n = int(dur * SR)
    t = np.arange(n) / SR
    v = (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2.76 * f * t) * np.exp(-t * 2.5)
         + 0.12 * np.sin(2 * np.pi * 5.4 * f * t) * np.exp(-t * 5))
    return v * np.exp(-t / tau) * fade(n, int(0.004 * SR), int(0.05 * SR))


def piano(f, dur=2.2, vel=1.0):
    """Felt-piano tone: slightly inharmonic partials, the high ones dying fastest."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    v = np.zeros(n)
    for k in range(1, 9):
        fk = k * f * np.sqrt(1 + 0.0004 * k * k)
        v += np.sin(2 * np.pi * fk * t) / k ** 1.4 * np.exp(-t * (1.2 + 0.9 * k))
    return vel * v * fade(n, int(0.006 * SR), int(0.08 * SR))


def heartbeat(dur=0.7):
    """Soft low thump (pitch drops 140 → 70 Hz, high enough to be felt on a phone): the documentary 'pulse'."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    freq = 70 + 70 * np.exp(-t * 25)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return np.sin(phase) * np.exp(-t * 7) * fade(n, int(0.003 * SR), int(0.05 * SR))


def place(track, sound, start, gain=1.0, pan=0.5):
    i = int(start * SR)
    if i >= track.shape[0]:
        return
    s = sound[: track.shape[0] - i]
    if s.ndim == 1:
        track[i:i + len(s), 0] += s * gain * np.sqrt(1 - pan)
        track[i:i + len(s), 1] += s * gain * np.sqrt(pan)
    else:
        track[i:i + len(s)] += s * gain


def pads(track, chords, bar, octave_shift=0, gain=0.22, bright=1.6, cutoff=1400):
    layer = np.zeros_like(track)
    xf = min(2.0, bar / 3)
    n_bars = int(np.ceil(LENGTH / bar)) + 1
    for b in range(n_bars):
        chord = chords[b % len(chords)]
        for j, m in enumerate(chord):
            note = pad_note(hz(m + octave_shift), bar + xf, bright=bright, seed=b * 10 + j)
            note *= fade(len(note), int(xf * SR), int(xf * SR))[:, None]
            place(layer, note, b * bar, gain / len(chord) ** 0.5)
    return track + lowpass(layer, cutoff)


def space_track(chords, bar=8.0, step=0.75, seed=1):
    rng = np.random.default_rng(seed)
    track = np.zeros((int(LENGTH * SR), 2))
    track = pads(track, chords, bar)
    # sub drone on the chord root, two octaves down
    sub = np.zeros_like(track)
    for b in range(int(np.ceil(LENGTH / bar)) + 1):
        root = min(chords[b % len(chords)]) - 12
        n = int((bar + 2) * SR)
        t = np.arange(n) / SR
        place(sub, np.sin(2 * np.pi * hz(root) * t) * fade(n, 2 * SR, 2 * SR), b * bar, 0.05)
    track += sub
    # shimmering bell arpeggio with ping-pong echo, enters after the first bar
    bells = np.zeros_like(track)
    t = bar
    while t < LENGTH - 2:
        chord = chords[int(t // bar) % len(chords)]
        if rng.random() < 0.8:
            m = rng.choice(chord) + 24
            place(bells, bell(hz(m)), t, rng.uniform(0.10, 0.16), pan=rng.uniform(0.2, 0.8))
        t += step
    d = int(step * 1.5 * SR)
    echo = np.zeros_like(bells)
    echo[d:, 1] += bells[:-d, 0] * 0.45
    echo[d:, 0] += bells[:-d, 1] * 0.45
    echo[2 * d:] += bells[:-2 * d] * 0.18
    track += lowpass(bells + echo, 6000)
    return track * 0.7 + reverb(track, 4.5, 1.4, seed) * 0.6


def story_track(chords, bpm=76, ostinato=(0, 7, 12, 7, 3, 7, 12, 7), seed=1):
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    bar = 4 * beat * 2  # two 4/4 bars per chord
    track = np.zeros((int(LENGTH * SR), 2))
    track = pads(track, chords, bar, octave_shift=0, gain=0.22, bright=1.5, cutoff=1300)  # strings
    keys = np.zeros_like(track)
    t, i = 0.0, 0
    while t < LENGTH - 2:
        chord = chords[int(t // bar) % len(chords)]
        root = min(chord)
        root = root + 12 if root < 62 else root
        third = 3 if (sorted(chord)[1] - min(chord)) % 12 == 3 else 4
        m = root + {3: third}.get(ostinato[i % len(ostinato)], ostinato[i % len(ostinato)])
        vel = 0.75 + 0.25 * (i % 4 == 0) + rng.uniform(-0.08, 0.08)
        if t >= bar / 2:  # piano enters after half a chord: the start stays calm under the hook
            place(keys, piano(hz(m), vel=vel), t, 0.13, pan=0.4 + 0.2 * (i % 2))
        t += beat / 2
        i += 1
    track += lowpass(keys, 5000)
    pulse = np.zeros_like(track)
    t = bar  # heartbeat on beat 1 of every bar, from the second chord on
    while t < LENGTH - 1:
        place(pulse, heartbeat(), t, 0.22)
        place(pulse, heartbeat(), t + 0.24, 0.12)
        t += 4 * beat
    track += pulse
    return track * 0.75 + reverb(track, 3.0, 1.0, seed) * 0.45


def master(x):
    x = highpass(x - x.mean(axis=0), 110)
    rms = np.sqrt((x ** 2).mean())
    x = x * (10 ** (-18 / 20) / rms)        # about -18 dBFS RMS: render sets the final level
    x = np.tanh(x * 1.2) / 1.2              # soft-clip the rare peak instead of distorting
    n = x.shape[0]
    return x * fade(n, int(1.5 * SR), int(3 * SR))[:, None]


def write(x, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "t.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "192k", str(path)],
                       check=True)
    print(f"wrote {path.relative_to(ROOT)}")


# MIDI chord voicings (C4 = 60)
SPACE = {
    "drift-am": [(57, 60, 64), (53, 57, 60, 64), (48, 55, 60, 64), (55, 59, 62)],          # Am  Fmaj7  C  G
    "nebula-d": [(50, 54, 57, 61), (47, 50, 54, 57), (43, 47, 50, 54), (45, 50, 52, 57)],  # Dmaj7 Bm7 Gmaj7 Asus
    "orbit-em": [(52, 55, 59), (48, 52, 55, 59), (43, 47, 50, 55), (50, 54, 57, 62)],      # Em  Cmaj7 G  D
    "deepfield-f": [(53, 57, 60, 64), (55, 59, 62, 65), (52, 55, 59, 62), (57, 60, 64)],   # Fmaj7 G Em7 Am
}
STORIES = {
    "archive-dm": ([(50, 53, 57), (46, 50, 53), (53, 57, 60), (48, 52, 55)], 76),          # Dm Bb F C
    "fog-am": ([(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)], 70),              # Am F C G
    "chronicle-cm": ([(48, 51, 55), (44, 48, 51), (51, 55, 58), (46, 50, 53)], 84),        # Cm Ab Eb Bb
    "tension-em": ([(52, 55, 59), (48, 52, 55), (45, 48, 52), (47, 51, 54)], 72),          # Em C Am B
    "triumph-g": ([(55, 59, 62), (50, 54, 57), (52, 55, 59), (48, 52, 55)], 88),           # G D Em C (heroic)
}


def main(only=None):
    for i, (name, chords) in enumerate(SPACE.items()):
        if not only or only in name:
            write(master(space_track(chords, step=(0.75, 0.6, 0.5, 0.9)[i], seed=i + 1)),
                  ROOT / "assets" / "music" / "space" / f"{name}.m4a")
    for i, (name, (chords, bpm)) in enumerate(STORIES.items()):
        if not only or only in name:
            write(master(story_track(chords, bpm, seed=i + 11)), ROOT / "assets" / "music" / "stories" / f"{name}.m4a")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
