"""Real video decoding with synthetic clips; vision calls stay offline."""

import base64
import io
import subprocess

import pytest
from PIL import Image

from app.core.ingestion.falhas import FalhaPermanente
from app.core.ingestion.multimodal import processar_video


def _clip(tmp_path, *, visual_seconds=2, audio_seconds=None, frames=None):
    path = tmp_path / "synthetic.mp4"
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
        f"color=c=blue:s=96x64:r=6:d={visual_seconds}",
    ]
    if audio_seconds:
        command += ["-f", "lavfi", "-i", f"anullsrc=r=8000:cl=mono:d={audio_seconds}"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if frames:
        command += ["-frames:v", str(frames)]
    if audio_seconds:
        command += ["-c:a", "aac"]
    subprocess.run(command + ["-y", str(path)], capture_output=True, check=True, timeout=15)
    return path.read_bytes()


@pytest.fixture
def vision(monkeypatch):
    from app.core import llm_client

    calls = []

    def describe(**kwargs):
        content = kwargs["messages"][0]["content"]
        encoded = content[0]["source"]["data"]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as frame:
            assert frame.format == "JPEG"
            assert max(frame.size) <= 768
            red, green, blue = frame.convert("RGB").getpixel((10, 10))
            assert blue > red + 100 and blue > green + 100
        calls.append(kwargs)
        return "A solid blue frame."

    monkeypatch.setattr(llm_client, "chat_complete", describe)
    return calls


@pytest.mark.parametrize("clip_options", [
    {},
    {"visual_seconds": 1, "audio_seconds": 6},
    {"frames": 1},
])
def test_video_reaches_generation_as_visual_descriptions(tmp_path, vision, clip_options):
    data = _clip(tmp_path, **clip_options)
    description, video = processar_video(data, "synthetic.mp4")

    assert len(vision) == 6
    assert description.count("A solid blue frame.") == 6
    assert "audio are not covered" in description
    assert "synthetic.mp4" not in description
    assert video.to_bytes() == data


def test_invalid_video_fails_before_any_vision_call(vision):
    with pytest.raises(FalhaPermanente, match="could not be decoded"):
        processar_video(b"not a video", "broken.mp4")
    assert vision == []


def test_empty_vision_response_does_not_index_the_filename(tmp_path, monkeypatch):
    from app.core import llm_client

    monkeypatch.setattr(llm_client, "chat_complete", lambda **kwargs: " ")
    with pytest.raises(RuntimeError, match="description was empty"):
        processar_video(_clip(tmp_path), "synthetic.mp4")
