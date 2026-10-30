<img src="webapp/icons/logo.svg" width="72" alt="">

# KickClipper

> 🇬🇧 [English documentation](README.md)

Convierte VODs de Kick en contenido listo para subir: **shorts** verticales
(9:16) para YouTube Shorts / TikTok / Reels y **videos** horizontales
(16:9, de 5 a 10 min) para YouTube, con detección automática de los mejores
momentos usando IA que corre **en tu propio equipo**.

Dos formas de usarlo, las dos sin APIs en la nube ni claves:

| | 🌐 **Versión web** | 🖥️ **Versión de escritorio** |
|---|---|---|
| Dónde corre | en tu navegador (Chrome/Edge) | servidor local (Python) |
| Instalación | ninguna: abre la web | `uv` + ffmpeg + Ollama |
| Hosting | estático y gratis (Vercel) | tu máquina |
| Transcripción | Whisper en el navegador (WebGPU/WASM) | Whisper large-v3-turbo (MLX / faster-whisper) |
| IA para elegir | opcional: WebLLM, la IA de Chrome o cualquier modelo de Ollama de tu PC | cualquier modelo que tengas en Ollama (por defecto `qwen3:8b`) |
| Formatos | shorts 9:16 + videos 16:9 | shorts 9:16 + videos 16:9 |

En ambos casos el vídeo, la transcripción y los clips **nunca salen de tu
dispositivo**.

> ⚖️ **Úsalo con responsabilidad.** Procesa solo contenido sobre el que tengas
> derechos o permiso explícito del creador. La mayoría de plataformas exigen
> permiso para resubir el stream de otra persona.

## Qué hace (y por qué)

Todo está basado en cómo trabajan los clippers y en lo que premian las
plataformas; la investigación completa, con fuentes, está en
**[docs/GUIA_CLIPS_VIRALES.md](docs/GUIA_CLIPS_VIRALES.md)**.

- 🎯 **Detección de momentos como un clipper**
  - **Audio**: volumen frente a la línea base *local* del stream (un grito en
    un stream tranquilo cuenta aunque haya partes más ruidosas), cambios
    bruscos del espectro, brillo de la voz (gritos) y ritmo de risa.
  - **Chat del VOD**: velocidad de mensajes frente a su línea base y
    proporción de mensajes de risa/hype (KEKW, jajaja, W…), compensando que el
    chat reacciona unos segundos *después* del momento.
  - **Candidatos con estructura de clip**: preparación → pico → reacción,
    alineados a frases, además de ventanas deslizantes para historias y
    opiniones.
  - **Heurística**: gancho de los primeros 3 s, densidad de habla, palabras de
    reacción, final en el remate, penalización de silencios.
  - **LLM local** (opcional en la web): puntúa potencial viral, fuerza del
    gancho, si se entiende sin contexto y la calidad del remate; **elige en
    qué frase empezar y terminar**, y escribe título, **texto gancho** para la
    pantalla, descripción, hashtags y palabras clave.
  - Recorte automático de intros sin gancho ("bueno chat, a ver…") y de colas
    largas tras el remate.
- 🎥 **Encuadre vertical inteligente** (detección de caras)
  - **Facecam + gameplay** → cámara arriba, juego abajo, subtítulos en la costura.
  - **Varias personas (podcast, IRL en grupo)** → recorte sobre **quien está
    hablando** (movimiento de la boca cruzado con la voz), con corte seco al
    cambiar de hablante, duración mínima de plano y **pantalla dividida**
    cuando hablan a la vez.
  - **Una persona** → recorte 9:16 que la sigue. **Sin caras** → vídeo
    completo sobre fondo difuminado.
  - Si la detección falla (máscara, cámara sin cara visible…), **marca la
    cámara a mano** arrastrando un rectángulo sobre un fotograma.
- 📝 **Subtítulos de short viral**: 1-3 palabras en mayúsculas con "pop",
  palabra activa resaltada, palabras clave en verde, **un color por
  hablante**, todo dentro de las zonas seguras de TikTok/Shorts/Reels.
  Estilos karaoke y limpio también disponibles. Fuentes libres incluidas.
- ✂️ **Ritmo**: jump cuts que quitan pausas de voz *sin* cortar la tensión del
  gameplay (solo si el audio también está en calma) y **cold open** opcional
  (2 s del clímax al principio).
- 🔊 **Audio a −14 LUFS** (referencia de YouTube/TikTok) con limitador de picos.
- 🤖 **Listo para publicar**: título, descripción, hashtags y redes del
  canal en un `.txt` junto a cada clip.

## Compatibilidad

| | Windows 10/11 | macOS | Linux |
|---|---|---|---|
| 🌐 **Versión web** | Chrome / Edge | Chrome / Edge | Chrome / Edge |
| 🖥️ **Escritorio** | ✓ (`run.ps1`) | ✓ Apple Silicon e Intel | ✓ x64 y ARM |
| Transcripción | faster-whisper (GPU NVIDIA si hay CUDA) | MLX en Apple Silicon; faster-whisper en Intel | faster-whisper (CUDA si hay) |
| Render | GPU NVIDIA / Intel / AMD si está disponible; si no, CPU | VideoToolbox | NVENC / Quick Sync si está disponible; si no, CPU |

La versión web necesita WebCodecs (Chrome/Edge 94+); con WebGPU la IA va
mucho más rápida. En Safari y Firefox no está probada. La versión de
escritorio se prueba en CI en los tres sistemas
([`tests.yml`](.github/workflows/tests.yml)).

## 🌐 Versión web (gratis, sin servidor)

Carpeta [`webapp/`](webapp/): HTML/CSS/JS sin paso de build. Todo el trabajo
pesado ocurre en el navegador del usuario:

| Etapa | Tecnología |
|---|---|
| Leer vídeo (MP4/MKV/WebM/MOV/TS) y **HLS** (VODs de Kick) | [mediabunny](https://github.com/Vanilagy/mediabunny) + WebCodecs |
| Análisis de audio | DSP propio en JS (FFT, envolvente, sonoridad) |
| Transcripción | [transformers.js](https://github.com/huggingface/transformers.js) + Whisper con marcas por palabra (WebGPU o WASM) |
| Caras / quién habla | [MediaPipe Tasks Vision](https://ai.google.dev/edge/mediapipe) (detector + `jawOpen`) |
| IA para elegir (opcional) | [WebLLM](https://github.com/mlc-ai/web-llm) (Qwen3), la Prompt API de Chrome o tu [Ollama](https://ollama.com) |
| Render | Canvas + WebCodecs → MP4 H.264/AAC (o WebM VP9 si el navegador no codifica H.264) |

Ideas para que sea viable en un navegador:

- **Análisis con la variante más ligera**: de un VOD HLS solo se descarga la
  variante de menor bitrate para analizar el audio; la calidad completa solo
  se descarga para los tramos que se renderizan.
- **Transcribir solo donde importa**: en VODs largos, Whisper se ejecuta solo
  alrededor de los picos de audio/chat (profundidad configurable: 15, 30 o
  90 min de audio).

### VODs de Kick sin servidor: el marcador

Una web de terceros no puede leer la API de Kick (CORS). En lugar de montar un
proxy, la web genera un **marcador** (bookmarklet): lo arrastras a la barra de
marcadores, abres el VOD en kick.com y lo pulsas. El código corre en
kick.com con tu sesión, lee la URL del vídeo y abre KickClipper con el VOD
listo. La pestaña de Kick se queda como **puente**: si el navegador bloquea la
descarga directa o hace falta el **chat del VOD**, la app se lo pide por
`postMessage` (solo peticiones GET a kick.com y al host del vídeo).

También puedes arrastrar un archivo de vídeo o pegar una URL directa
(MP4/M3U8 con CORS).

### Desplegarla gratis en Vercel

Importa el repo en [Vercel](https://vercel.com/new) tal cual:
[`vercel.json`](vercel.json) publica solo `webapp/` (sin build ni Python) y
cada push a `main` la actualiza. Solo se puede alojar la versión web; la de
escritorio corre siempre en el equipo de cada usuario.

Al ser archivos estáticos sin cabeceras especiales, también sirve cualquier
otro hosting estático (Cloudflare Pages, Netlify…).

**Usar tus modelos de Ollama en la web publicada:** quien tenga
[Ollama](https://ollama.com) puede elegir cualquier modelo que tenga
instalado. Ollama debe permitir el origen de la web, p. ej. abriéndolo con
`OLLAMA_ORIGINS=https://tu-web.vercel.app`; la página muestra el valor exacto.

Para probarla en local:

```bash
python3 -m http.server -d webapp 8000   # y abre http://localhost:8000
```

Requisitos del usuario: Chrome, Edge u Opera recientes en ordenador
(WebCodecs; con WebGPU la IA va mucho más rápida). La primera vez se descarga
el modelo de transcripción (~150 MB, o ~1 GB el preciso) y, si se activa, la
IA (~1 GB); después quedan en la caché del navegador. Se puede **instalar como
app** (icono de instalar en la barra de direcciones) y abre sin conexión.

**Videos 16:9 en la web:** elige *Formato → 5–10 min (YouTube)*. Se
transcriben enteros los tramos más activos del VOD y se renderizan a
1920×1080 sin recortes. El video completo se monta en memoria antes de
descargarlo: cuenta con 1–2 GB de RAM libre por video.

**Vista previa al compartir el link:** `og:image` es relativa en
`webapp/index.html`; cuando tengas el dominio, ponla absoluta
(`https://tu-web.vercel.app/icons/og.png`) para que Discord, X o WhatsApp
muestren la imagen.

## 🖥️ Versión de escritorio

```
URL del VOD ─▶ ① Descarga (yt-dlp)  +  chat del VOD (en paralelo)
            ─▶ ② Transcripción (Whisper local)
            ─▶ ③ Hype: audio + chat
            ─▶ ④ Candidatos por picos + heurística + LLM local (Ollama)
            ─▶ ⑤ Encuadre (caras) + jump cuts + subtítulos + render ffmpeg
```

### Requisitos

- Python ≥ 3.12 y [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) corriendo con algún modelo (`ollama pull qwen3:8b`)
  — sin Ollama funciona igual, solo con señales automáticas. Puedes elegir
  cualquier modelo instalado en cada trabajo, y la app te recomienda uno
  según la RAM/VRAM de tu equipo (NVIDIA, AMD o Apple Silicon) y lo instala
  con un clic.
- ffmpeg **con libass** (para los subtítulos). Si el tuyo no lo tiene
  (el de Homebrew actual no lo incluye), ejecuta `./scripts/get_ffmpeg.sh`
  (macOS/Linux) o `scripts\get_ffmpeg.ps1` (Windows): descargan un binario
  estático a `./bin/`.

### Inicio rápido

macOS / Linux:

```bash
git clone https://github.com/<tu-usuario>/kickclipper.git
cd kickclipper
./scripts/get_ffmpeg.sh   # si tu ffmpeg no trae libass
./run.sh                  # arranca el servidor y abre http://127.0.0.1:8420
```

Windows (PowerShell):

```powershell
git clone https://github.com/<tu-usuario>/kickclipper.git
cd kickclipper
powershell -ExecutionPolicy Bypass -File .\scripts\get_ffmpeg.ps1   # ffmpeg con libass en .\bin
powershell -ExecutionPolicy Bypass -File .\run.ps1                   # http://127.0.0.1:8420
```

`stop` detiene el servidor y `restart` lo reinicia (útil tras actualizar el
código): `./run.sh stop` o `.\run.ps1 stop`. Con `KC_NO_OPEN=1` no abre el
navegador. `scripts/clean_uploads.sh` necesita bash (en Windows, Git Bash o WSL).

Después, en la interfaz web:

1. Pega la URL de un VOD: `https://kick.com/<canal>/videos/<uuid>`.
2. Elige qué generar: **shorts 9:16**, **videos 16:9**, o ambos.
3. Opcionalmente limita el rango horario (`01:00:00-01:30:00`).
4. Pulsa **CLIPEAR** y mira correr el pipeline.
5. En cada clip puedes cambiar el **encuadre** (automático, cámara + gameplay,
   foco en quien habla, seguir a la persona, difuminado, recorte), el estilo
   de **subtítulos**, el **texto gancho**, los **jump cuts** y el **cold open**,
   y volver a renderizar.

La primera transcripción descarga el modelo Whisper (~1.5 GB para
`large-v3-turbo`) y el primer render con detección de caras el modelo YuNet
(230 KB); después todo es offline salvo la descarga del VOD y su chat.

### Salida

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

### Configuración

Variables de entorno con prefijo `KC_` o archivo `.env`:

| Variable | Por defecto | Descripción |
|---|---|---|
| `KC_OLLAMA_MODEL` | `qwen3:8b` | Modelo LLM para puntuar momentos |
| `KC_WHISPER_ENGINE` | auto | `mlx` (Apple Silicon) o `faster-whisper` (usa CUDA si la hay) |
| `KC_WHISPER_MODEL` | `large-v3-turbo` | Modelo Whisper (motor MLX) |
| `KC_FASTER_WHISPER_MODEL` | `large-v3-turbo` | Modelo Whisper (motor faster-whisper) |
| `KC_WHISPER_LANGUAGE` | autodetectar | Fuerza el idioma de transcripción (p.ej. `es`, `en`) |
| `KC_MIN_CLIP_SECONDS` | `15` | Duración mínima de un short |
| `KC_MAX_CLIP_SECONDS` | `60` | Duración máxima de un short (Shorts y Reels admiten hasta 180; TikTok solo monetiza > 60) |
| `KC_TARGET_CLIP_SECONDS` | `40` | Duración preferida de un short |
| `KC_VIDEO_MIN_SECONDS` | `300` | Duración mínima de un video 16:9 (5 min) |
| `KC_VIDEO_MAX_SECONDS` | `600` | Duración máxima de un video 16:9 (10 min) |
| `KC_MAX_CLIPS_PER_JOB` | `12` | Shorts candidatos máximos por VOD |
| `KC_MAX_VIDEOS_PER_JOB` | `3` | Videos candidatos máximos por VOD |
| `KC_LLM_MAX_CANDIDATES` | `24` | Ventanas que se envían al LLM |
| `KC_CHAT_SIGNAL` | `true` | Usar la velocidad del chat del VOD como señal |
| `KC_CHAT_MAX_REQUESTS` | `400` | Tope de peticiones para leer el chat de un VOD |
| `KC_CHAT_LAG_SECONDS` | `6` | Retraso típico del chat respecto al momento |
| `KC_CAPTION_STYLE` | `viral` | `viral`, `karaoke`, `clean` o `none` |
| `KC_SUBTITLE_FONT` | (incluidas) | Fuerza otra fuente para los subtítulos |
| `KC_JUMPCUT` | `true` | Quitar pausas en los shorts |
| `KC_JUMPCUT_MIN_GAP` | `0.7` | Pausas más largas que esto (s) se acortan |
| `KC_LOUDNORM` | `true` | Normalizar el audio a −14 LUFS |
| `KC_FACE_DETECTION` | `true` | Detectar caras para elegir el encuadre |
| `KC_OUTPUT_FPS` | `30` | fps de salida |
| `KC_VIDEO_ENCODER` | `auto` | `auto` (GPU si funciona), `libx264`, `h264_nvenc`, `h264_qsv`, `h264_amf`, `h264_videotoolbox` |
| `KC_STREAMER_SOCIALS` | `{}` | Redes por canal (JSON) para las descripciones |
| `KC_PORT` | `8420` | Puerto del servidor web |

Ejemplo de `KC_STREAMER_SOCIALS` (los canales sin entrada llevan solo el
enlace a su página de Kick):

```bash
KC_STREAMER_SOCIALS='{"uncanal": {"Kick": "https://kick.com/uncanal", "Instagram": "https://instagram.com/...", "X": "https://x.com/..."}}'
```

## Estructura

```
kickclipper/                 # versión de escritorio
├── server.py                # FastAPI: API REST + SSE + frontend
├── config.py                # Configuración (env KC_*)
├── db.py                    # SQLite (jobs y clips)
├── pipeline/
│   ├── runner.py            # Orquestador del pipeline
│   ├── downloader.py        # yt-dlp + extracción de audio
│   ├── transcriber.py       # Whisper (MLX / faster-whisper) + filtro de alucinaciones
│   ├── audio_analysis.py    # Curvas de audio en streaming
│   ├── chat.py              # Velocidad del chat del VOD
│   ├── signals.py           # Líneas base, picos, suavizado
│   ├── highlights.py        # Candidatos por picos + heurística + LLM
│   ├── vision.py            # Caras, facecam, hablante activo → plan de encuadre
│   ├── timeline.py          # Jump cuts y cold open
│   ├── subtitles.py         # Subtítulos ASS (viral, karaoke, clean)
│   └── renderer.py          # Grafos ffmpeg por layout, −14 LUFS
├── assets/fonts/            # Anton y Poppins (OFL)
└── web/                     # Interfaz local (vanilla JS, sin build)

webapp/                      # versión web (estática, todo en el navegador)
├── index.html, styles.css
├── js/                      # source, audio, chat, transcribe, highlights,
│                            # vision, timeline, captions, compositor, render…
└── tests/                   # node --test + prueba e2e con Playwright

docs/GUIA_CLIPS_VIRALES.md   # investigación: plataformas, ganchos, subtítulos…
```

## Tests

```bash
uv run pytest -q                         # versión de escritorio (usa ffmpeg real)
node --test webapp/tests/*.test.mjs      # módulos de la versión web
```

La prueba de extremo a extremo de la web (Chromium + Playwright) está en
`webapp/tests/e2e.mjs`; mira la cabecera del archivo para sus variables.

## Contribuir

Issues y pull requests son bienvenidos — mira [CONTRIBUTING.md](CONTRIBUTING.md).

## Licencia

[MIT](LICENSE). Úsalo solo con contenido sobre el que tengas derechos o
permiso del creador para republicar. Las fuentes incluidas (Anton, Poppins)
tienen licencia [SIL OFL 1.1](kickclipper/assets/fonts/).
