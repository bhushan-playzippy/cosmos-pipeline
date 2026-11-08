"""The quality gate on real FFmpeg-generated files with planted defects."""
import json
import subprocess

import pytest

from pipeline import qa
from tests.conftest import make_script


def render(path, *, size="1080x1920", seconds=30, black=False, silent_gap=False, rate=30):
    color = "black" if black else "0x223366"
    video = f"color=c={color}:s={size}:r={rate}:d={seconds}"
    if not black:  # moving noise so the frames aren't "black"
        video = f"testsrc2=s={size}:r={rate}:d={seconds}"
    tone = f"sine=f=220:d={seconds}"
    af = "volume=0.5"
    if silent_gap:
        af = f"volume=enable='between(t,5,9)':volume=0,volume=0.5"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", video, "-f", "lavfi", "-i", tone,
                    "-af", af + ",loudnorm=I=-14", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def failed(checks):
    return {n for n, ok, _ in checks if not ok}


@pytest.fixture(scope="module")
def good(tmp_path_factory):
    return render(tmp_path_factory.mktemp("v") / "good.mp4")


def test_good_video_passes_technical(cfg, good):
    assert failed(qa.technical(good, cfg)) == set()


def test_detects_silence_gap(cfg, tmp_path):
    assert "no long silences" in failed(qa.technical(render(tmp_path / "s.mp4", silent_gap=True), cfg))


def test_detects_black_frames(cfg, tmp_path):
    assert "no black frames" in failed(qa.technical(render(tmp_path / "b.mp4", black=True), cfg))


def test_detects_wrong_resolution(cfg, tmp_path):
    assert "resolution" in failed(qa.technical(render(tmp_path / "r.mp4", size="1920x1080"), cfg))


def test_detects_too_short(cfg, tmp_path):
    assert "duration" in failed(qa.technical(render(tmp_path / "t.mp4", seconds=8), cfg))


def test_corrupt_file_fails_without_crashing(cfg, tmp_path):
    bad = tmp_path / "corrupt.mp4"
    bad.write_bytes(b"not a video")
    assert {"video stream", "audio stream"} <= failed(qa.technical(bad, cfg))


def test_editorial_checks(cfg):
    s = make_script("Saturn")
    timeline = [{"media": "x.jpg"}] * 8
    assert failed(qa.editorial(s, timeline, cfg)) == set()
    s["scenes"][3]["text"] = s["scenes"][2]["text"]
    s["youtube"]["description"] = "no disclosure"
    s["sources"] = []
    assert {"no repeated lines", "AI disclosure in description", "sources listed"} <= failed(
        qa.editorial(s, [{"media": "x.png"}] * 8, cfg)) | {"real visuals"}
    assert "real visuals" in failed(qa.editorial(make_script("Saturn"), [{"media": "x.png"}] * 3, cfg))


def test_originality_flags_near_copy_but_not_remake(cfg):
    from pipeline import orchestrator
    prev = orchestrator.OUTPUT / "prev"
    prev.mkdir()
    (prev / "script.json").write_text(json.dumps(make_script("Mars", "beta")))
    copy = make_script("Different topic", "beta")
    for sc, p in zip(copy["scenes"], make_script("Mars", "beta")["scenes"]):
        sc["text"] = p["text"]
    assert failed(qa.originality(copy, cfg)) == {"originality vs past videos"}
    assert failed(qa.originality(make_script("Venus", "gamma"), cfg)) == set()
    assert failed(qa.originality(make_script("Mars", "beta"), cfg)) == set()  # same topic = remake, allowed


def test_scale_graphic_renders_and_validates(tmp_path):
    from PIL import Image
    from pipeline import scalegfx
    spec = {"kind": "diameter", "unit": "km", "items": [{"label": "Earth", "value": 12742},
                                                        {"label": "Sun", "value": 1391000}]}
    assert scalegfx.valid(spec)
    assert Image.open(scalegfx.render(spec, tmp_path / "c.png")).size == (1080, 1920)
    assert not scalegfx.valid({"items": [{"label": "Earth", "value": 0}, {"label": "Sun", "value": 5}]})
    assert not scalegfx.valid({"items": [{"label": "only one", "value": 5}]})
    assert not scalegfx.valid({"items": "garbage"})


def test_scale_graphics_are_not_counted_as_fallback(cfg):
    s = make_script("Saturn")
    tl = [{"media": "compare_01.png", "rights": {"source": "compare"}}] * 3 + [{"media": "a.jpg"}] * 5
    assert "real visuals" not in failed(qa.editorial(s, tl, cfg))


def test_blank_check_ignores_dark_space_but_catches_empty_frames(tmp_path):
    import shutil
    import subprocess
    import pytest
    from pipeline import qa, visuals
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    stars = visuals.starfield(tmp_path, 0)  # mostly black sky with bright stars
    run = lambda *a: subprocess.run(["ffmpeg", "-y", "-v", "error", *a], check=True)
    run("-loop", "1", "-i", str(stars), "-t", "2", "-vf", "scale=540:960", "-pix_fmt", "yuv420p", str(tmp_path / "s.mp4"))
    run("-f", "lavfi", "-i", "color=black:s=540x960:d=1.5", "-pix_fmt", "yuv420p", str(tmp_path / "b.mp4"))
    (tmp_path / "l.txt").write_text("file 's.mp4'\nfile 'b.mp4'\nfile 's.mp4'\n")
    run("-f", "concat", "-safe", "0", "-i", str(tmp_path / "l.txt"), "-c", "copy", str(tmp_path / "mix.mp4"))
    assert qa._blank_runs(tmp_path / "s.mp4") == []
    runs = qa._blank_runs(tmp_path / "mix.mp4")
    assert len(runs) == 1 and 1.2 <= runs[0][1] - runs[0][0] <= 1.8
