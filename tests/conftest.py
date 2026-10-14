"""Configuración común: datos de la app en un directorio temporal."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_HOME = tempfile.mkdtemp(prefix="kc-tests-")
os.environ["KC_HOME"] = _HOME
os.environ.setdefault("KC_OLLAMA_URL", "http://127.0.0.1:9")  # Ollama "apagado"
os.environ.setdefault("KC_CHAT_SIGNAL", "false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FFMPEG = shutil.which("ffmpeg")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_HOME, ignore_errors=True)


@pytest.fixture(scope="session")
def ffmpeg():
    if not FFMPEG:
        pytest.skip("ffmpeg no disponible")
    return FFMPEG


def make_video(path: Path, seconds: float = 4.0, size: str = "1280x720",
               overlay: Path | None = None, overlay_xy: str = "W-w-40:H-h-40",
               overlay_w: int = 240) -> Path:
    """Vídeo sintético (patrón + tono) con una imagen opcional encima (facecam)."""
    cmd = [FFMPEG, "-y", "-v", "error",
           "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if overlay is not None:
        cmd += ["-loop", "1", "-t", str(seconds), "-i", str(overlay),
                "-filter_complex", f"[2:v]scale={overlay_w}:-2[o];[0:v][o]overlay={overlay_xy}[v]",
                "-map", "[v]", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


def media_info(path: Path) -> dict:
    proc = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    import re
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", proc.stderr)
    size = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", proc.stderr)
    secs = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)) if dur else 0
    return {"duration": secs, "size": (int(size.group(1)), int(size.group(2))) if size else None,
            "audio": "Audio:" in proc.stderr}
