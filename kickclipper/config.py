"""Configuración global de KickClipper.

Todos los valores pueden sobreescribirse con variables de entorno
prefijadas con KC_ (p.ej. KC_OLLAMA_MODEL=qwen3:8b) o un archivo .env.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Directorio de trabajo de la app (datos, salida, bin/ffmpeg, .env).
# Por defecto el cwd (run.sh hace cd a la raíz del proyecto); KC_HOME lo fija.
BASE_DIR = Path(os.environ.get("KC_HOME", Path.cwd())).resolve()


def _resolve_ffmpeg() -> str:
    """Prefiere el ffmpeg incluido en ./bin (compilado con libass)."""
    local = BASE_DIR / "bin" / "ffmpeg"
    return str(local) if local.exists() else "ffmpeg"


def _default_whisper_engine() -> str:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mlx"
    return "faster-whisper"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KC_", env_file=BASE_DIR / ".env")

    # Rutas
    data_dir: Path = BASE_DIR / "data"
    output_dir: Path = BASE_DIR / "output"   # contenido limpio listo para subir
    ffmpeg_bin: str = _resolve_ffmpeg()

    # Redes sociales por canal (opcional); se añaden a la descripción de cada
    # publicación. Configurable aquí o vía KC_STREAMER_SOCIALS en JSON, p.ej.:
    #   KC_STREAMER_SOCIALS='{"canal": {"Instagram": "https://...", "X": "https://..."}}'
    # Si un canal no tiene redes configuradas se usa su página de Kick.
    streamer_socials: dict[str, dict[str, str]] = {}

    # Servidor
    host: str = "127.0.0.1"
    port: int = 8420

    # Transcripción (large-v3-turbo: máxima precisión con buen rendimiento en MLX)
    whisper_engine: str = _default_whisper_engine()  # "mlx" | "faster-whisper"
    whisper_model: str = "large-v3-turbo"  # mlx: convertido automáticamente a repo HF
    faster_whisper_model: str = "large-v3-turbo"
    whisper_language: str | None = None  # None = autodetectar; fija KC_WHISPER_LANGUAGE=es si quieres

    # LLM local (Ollama)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_timeout: float = 180.0
    llm_max_candidates: int = 24  # ventanas que se mandan al LLM (las mejores por heurística)

    # Detección de highlights
    # Shorts (9:16): máximo 1 minuto, no son videos
    min_clip_seconds: float = 15.0
    max_clip_seconds: float = 60.0
    target_clip_seconds: float = 45.0
    # Videos (16:9): entre 5 y 10 minutos, no son shorts
    video_min_seconds: float = 300.0
    video_max_seconds: float = 600.0
    video_target_seconds: float = 420.0
    max_clips_per_job: int = 12      # shorts candidatos por job
    max_videos_per_job: int = 3      # videos candidatos por job

    # Render
    output_width: int = 1080     # short vertical
    output_height: int = 1920
    video_width: int = 1920      # video horizontal
    video_height: int = 1080
    video_bitrate: str = "7M"
    use_videotoolbox: bool = platform.system() == "Darwin"
    subtitle_font: str = "Arial Black"

    def clip_limits(self, fmt: str) -> tuple[float, float, float]:
        """(mínimo, máximo, objetivo) de duración en segundos para un formato."""
        if fmt == "video":
            return self.video_min_seconds, self.video_max_seconds, self.video_target_seconds
        return self.min_clip_seconds, self.max_clip_seconds, self.target_clip_seconds

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kickclipper.db"


settings = Settings()
settings.jobs_dir.mkdir(parents=True, exist_ok=True)
