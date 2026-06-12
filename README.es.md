# ⚡ KickClipper

> 🇬🇧 [English documentation](README.md)

Herramienta **100% local** que convierte VODs de Kick en contenido listo para
subir: **shorts** verticales (9:16, con subtítulos karaoke) para YouTube
Shorts / TikTok / Reels, o **videos** horizontales (16:9, de 5 a 10 min) para
YouTube — con detección automática de los mejores momentos usando IA que corre
por completo en tu máquina.

Funciona con **cualquier canal de kick.com**, en cualquier idioma. Sin APIs en
la nube, sin claves, sin subir nada: descarga, transcripción, puntuación y
render ocurren en local.

> ⚖️ **Úsalo con responsabilidad.** Procesa solo contenido sobre el que tengas
> derechos o permiso explícito del creador. La mayoría de plataformas exigen
> permiso para resubir el stream de otra persona.

## Características

- 🎯 **Detección automática de highlights** — análisis de excitación del audio
  (RMS + flujo espectral) más señales léxicas, y un LLM local (Ollama) puntúa
  cada candidato según su potencial viral.
- ✂️ **Dos formatos por trabajo** — genera shorts (15 s – 1 min), videos largos
  (5 – 10 min), o ambos a la vez.
- 📝 **Subtítulos karaoke palabra a palabra** en los shorts (los videos salen
  limpios por defecto; los subtítulos se activan por clip).
- 🤖 **Metadatos listos para publicar** — el LLM escribe título gancho,
  descripción y hashtags en el idioma del stream; cada export incluye un
  `.txt` listo para pegar.
- 🖥️ **Estudio web local** — progreso del pipeline en tiempo real (SSE),
  revisión de candidatos con miniaturas, ajuste de inicio/fin, re-render,
  preview y descarga.
- 🍎 **Codificación por hardware** en macOS (VideoToolbox) y transcripción en
  GPU en Apple Silicon (MLX); en otros sistemas, `faster-whisper` en CPU.

## Cómo funciona

```
URL del VOD ─▶ ① Descarga (yt-dlp) ─▶ ② Transcripción (Whisper local)
            ─▶ ③ Análisis de hype (energía de audio + señales léxicas)
            ─▶ ④ Puntuación viral (LLM local vía Ollama)
            ─▶ ⑤ Render: shorts 9:16 (≤1 min, subtítulos karaoke)
                         y/o videos 16:9 (5-10 min)
```

1. **Descarga**: `yt-dlp` baja el VOD (o solo el rango horario que elijas).
2. **Transcripción**: Whisper con timestamps por palabra; idioma autodetectado.
3. **Análisis de hype**: curva de excitación por segundo que detecta gritos,
   risas y reacciones.
4. **Selección con IA**: las mejores ventanas candidatas se puntúan con un LLM
   local que además genera título, descripción y hashtags. Shorts y videos se
   buscan por separado, cada uno con su rango de duración.
5. **Render**: shorts en 1080×1920 con fondo difuminado o recorte central;
   videos en 1920×1080. Los archivos finales quedan en
   `output/<canal>/<shorts|videos>/` junto a un `.txt` con todo lo necesario
   para publicar.

## Requisitos

- Python ≥ 3.12 y [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) corriendo con algún modelo (`ollama pull qwen3:8b`)
- ffmpeg **con libass** (para los subtítulos). Si el tuyo no lo tiene
  (el de Homebrew actual no lo incluye), ejecuta:

  ```bash
  ./scripts/get_ffmpeg.sh   # descarga un binario estático a ./bin/ffmpeg
  ```

## Inicio rápido

```bash
git clone https://github.com/<tu-usuario>/kickclipper.git
cd kickclipper
./scripts/get_ffmpeg.sh   # si tu ffmpeg no trae libass
./run.sh                  # arranca el servidor y abre http://127.0.0.1:8420
```

`./run.sh stop` detiene el servidor, `./run.sh restart` lo reinicia (útil tras
actualizar el código) y con `KC_NO_OPEN=1` no abre el navegador.

Después, en la interfaz web:

1. Pega la URL de un VOD: `https://kick.com/<canal>/videos/<uuid>`.
2. Elige qué generar: **shorts 9:16**, **videos 16:9**, o ambos.
3. Opcionalmente limita el rango horario (`01:00:00-01:30:00`).
4. Pulsa **CLIPEAR** y mira correr el pipeline.

La primera transcripción descarga el modelo Whisper (~1.5 GB para
`large-v3-turbo`); después todo es offline salvo la descarga del VOD.

## Salida

Cada clip renderizado se exporta como un `.mp4` listo para subir más un `.txt`
con el título recomendado, la descripción, los hashtags y los enlaces del
canal, en carpetas separadas por canal y tipo:

```
output/
└── uncanal/
    ├── shorts/eso-fue-una-locura_19ea96.mp4
    ├── shorts/eso-fue-una-locura_19ea96.txt   ← texto para publicar
    └── videos/la-remontada-completa_8ca9c8.mp4
```

Cuando ya subiste los videos y quieres recuperar espacio:

```bash
./scripts/clean_uploads.sh                # borra todo output/ (pide confirmación)
./scripts/clean_uploads.sh uncanal shorts # solo una carpeta
./scripts/clean_uploads.sh --jobs         # también TODOS los jobs (lista de la
                                          # UI, clips y caché)
./scripts/clean_uploads.sh --all -y       # todo: output/ + data/ completo
```

## Configuración

Variables de entorno con prefijo `KC_` o archivo `.env`:

| Variable | Por defecto | Descripción |
|---|---|---|
| `KC_OLLAMA_MODEL` | `qwen3:8b` | Modelo LLM para puntuar momentos |
| `KC_WHISPER_ENGINE` | auto | `mlx` (Apple Silicon) o `faster-whisper` |
| `KC_WHISPER_MODEL` | `large-v3-turbo` | Modelo Whisper (motor MLX) |
| `KC_FASTER_WHISPER_MODEL` | `large-v3-turbo` | Modelo Whisper (motor CPU) |
| `KC_WHISPER_LANGUAGE` | autodetectar | Fuerza el idioma de transcripción (p.ej. `es`, `en`) |
| `KC_MIN_CLIP_SECONDS` | `15` | Duración mínima de un short |
| `KC_MAX_CLIP_SECONDS` | `60` | Duración máxima de un short (1 min) |
| `KC_VIDEO_MIN_SECONDS` | `300` | Duración mínima de un video 16:9 (5 min) |
| `KC_VIDEO_MAX_SECONDS` | `600` | Duración máxima de un video 16:9 (10 min) |
| `KC_MAX_CLIPS_PER_JOB` | `12` | Shorts candidatos máximos por VOD |
| `KC_MAX_VIDEOS_PER_JOB` | `3` | Videos candidatos máximos por VOD |
| `KC_LLM_MAX_CANDIDATES` | `24` | Ventanas que se envían al LLM |
| `KC_STREAMER_SOCIALS` | `{}` | Redes por canal (JSON) para las descripciones |
| `KC_PORT` | `8420` | Puerto del servidor web |

Ejemplo de `KC_STREAMER_SOCIALS` (los canales sin entrada llevan solo el
enlace a su página de Kick):

```bash
KC_STREAMER_SOCIALS='{"uncanal": {"Kick": "https://kick.com/uncanal", "Instagram": "https://instagram.com/...", "X": "https://x.com/..."}}'
```

## Estructura

```
kickclipper/
├── server.py            # FastAPI: API REST + SSE + frontend
├── config.py            # Configuración (env KC_*)
├── db.py                # SQLite (jobs y clips)
├── events.py            # Bus de eventos → SSE
├── streamers.py         # URLs de Kick, formatos, redes sociales
├── pipeline/
│   ├── runner.py        # Orquestador del pipeline
│   ├── downloader.py    # yt-dlp + extracción de audio
│   ├── transcriber.py   # Whisper (MLX / faster-whisper)
│   ├── audio_analysis.py# Curva de excitación
│   ├── highlights.py    # Heurística + LLM (Ollama)
│   ├── subtitles.py     # Subtítulos ASS karaoke
│   └── renderer.py      # ffmpeg 9:16 / 16:9 + VideoToolbox
└── web/                 # Interfaz (vanilla JS, sin build)
```

## Contribuir

Issues y pull requests son bienvenidos — mira [CONTRIBUTING.md](CONTRIBUTING.md).

## Licencia

[MIT](LICENSE). Úsalo solo con contenido sobre el que tengas derechos o
permiso del creador para republicar.
