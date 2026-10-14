# ⚡ KickClipper

> 🇪🇸 [Documentación en español](README.es.md)

Turns Kick VODs into ready-to-upload content: vertical **shorts** (9:16) for
YouTube Shorts / TikTok / Reels and horizontal **videos** (16:9, 5–10 min)
for YouTube, with automatic highlight detection powered by AI running **on
your own device**.

Two ways to use it, neither needs cloud APIs or keys:

| | 🌐 **Web version** | 🖥️ **Desktop version** |
|---|---|---|
| Runs in | your browser (Chrome/Edge) | a local server (Python) |
| Install | none: open the site | `uv` + ffmpeg + Ollama |
| Hosting | static and free (GitHub Pages) | your machine |
| Transcription | Whisper in the browser (WebGPU/WASM) | Whisper large-v3-turbo (MLX / faster-whisper) |
| AI ranking | optional: WebLLM or Chrome's built-in AI | Ollama (`qwen3:8b`) |
| Formats | 9:16 shorts | 9:16 shorts + 16:9 videos |

Either way the video, the transcript and the clips **never leave your
device**.

> ⚖️ **Use responsibly.** Only process content you have the rights to, or
> explicit permission from the creator. Most platforms require permission to
> re-upload someone else's stream.

## What it does (and why)

Everything is based on how clippers work and what the platforms reward; the
full research, with sources, is in
**[docs/GUIA_CLIPS_VIRALES.md](docs/GUIA_CLIPS_VIRALES.md)** (Spanish).

- 🎯 **Moment detection like a clipper**
  - **Audio**: loudness against the stream's *local* baseline (a scream in a
    calm stream counts even if other parts are louder), spectral changes,
    voice brightness (screams) and laughter rhythm.
  - **VOD chat**: message rate against its rolling baseline plus the share of
    laugh/hype messages (KEKW, lol, W…), compensating for chat reacting a few
    seconds *after* the moment.
  - **Clip-shaped candidates**: setup → peak → reaction, aligned to sentences,
    plus sliding windows for stories and opinions.
  - **Heuristics**: hook strength in the first 3 s, speech density, reaction
    words, ending on the payoff, dead-air penalty.
  - **Local LLM** (optional on the web): scores viral potential, hook,
    standalone context and payoff; **picks the start and end sentence**, and
    writes the title, an **on-screen hook text**, description, hashtags and
    caption keywords.
  - Automatically trims hook-less intros ("ok chat, so…") and long tails.
- 🎥 **Smart vertical framing** (face detection)
  - **Facecam + gameplay** → camera on top, game below, captions on the seam.
  - **Several people (podcast, group IRL)** → crop on **whoever is talking**
    (mouth movement matched with speech), hard cut on speaker change, minimum
    shot length and **split screen** when they talk over each other.
  - **One person** → a 9:16 crop that follows them. **No faces** → full frame
    over a blurred background.
  - If detection fails (mask, camera without a visible face…), **mark the
    camera by hand** by dragging a rectangle over a frame.
- 📝 **Viral short captions**: 1–3 uppercase words with a "pop", active word
  highlighted, keywords in green, **one color per speaker**, all inside the
  TikTok/Shorts/Reels safe zones. Karaoke and clean styles too. Free fonts
  bundled.
- ✂️ **Pacing**: jump cuts that remove speech pauses *without* cutting
  gameplay tension (only when the audio is calm too) and an optional
  **cold open** (2 s of the climax up front).
- 🔊 **Audio at −14 LUFS** (YouTube/TikTok reference) with a peak limiter.
- 🤖 **Publish-ready**: title, description, hashtags and channel links in a
  `.txt` next to every clip.

## Compatibility

| | Windows 10/11 | macOS | Linux |
|---|---|---|---|
| 🌐 **Web version** | Chrome / Edge | Chrome / Edge | Chrome / Edge |
| 🖥️ **Desktop** | ✓ (`run.ps1`) | ✓ Apple Silicon and Intel | ✓ x64 and ARM |
| Transcription | faster-whisper (NVIDIA GPU with CUDA) | MLX on Apple Silicon; faster-whisper on Intel | faster-whisper (CUDA when available) |
| Render | NVIDIA / Intel / AMD GPU when available, else CPU | VideoToolbox | NVENC / Quick Sync when available, else CPU |

The web version needs WebCodecs (Chrome/Edge 94+); WebGPU makes the AI much
faster. Safari and Firefox are untested. The desktop version is tested in CI
on all three systems ([`tests.yml`](.github/workflows/tests.yml)).

## 🌐 Web version (free, no server)

Folder [`webapp/`](webapp/): HTML/CSS/JS with no build step. All the heavy
lifting happens in the user's browser:

| Stage | Technology |
|---|---|
| Read video (MP4/MKV/WebM/MOV/TS) and **HLS** (Kick VODs) | [mediabunny](https://github.com/Vanilagy/mediabunny) + WebCodecs |
| Audio analysis | hand-written JS DSP (FFT, envelope, loudness) |
| Transcription | [transformers.js](https://github.com/huggingface/transformers.js) + word-timestamped Whisper (WebGPU or WASM) |
| Faces / who is talking | [MediaPipe Tasks Vision](https://ai.google.dev/edge/mediapipe) (detector + `jawOpen`) |
| AI ranking (optional) | [WebLLM](https://github.com/mlc-ai/web-llm) (Qwen3) or Chrome's Prompt API |
| Render | Canvas + WebCodecs → MP4 H.264/AAC (or WebM VP9 when the browser can't encode H.264) |

What makes it feasible in a browser:

- **Analyze the lightest variant**: from an HLS VOD only the lowest-bitrate
  variant is downloaded for audio analysis; full quality is fetched only for
  the ranges being rendered.
- **Transcribe only where it matters**: on long VODs Whisper runs only around
  audio/chat peaks (configurable depth: 15, 30 or 90 minutes of audio).

### Kick VODs without a server: the bookmarklet

A third-party site can't read Kick's API (CORS). Instead of running a proxy,
the site generates a **bookmarklet**: drag it to your bookmarks bar, open the
VOD on kick.com and click it. The code runs on kick.com with your session,
reads the video URL and opens KickClipper with the VOD ready. The Kick tab
stays open as a **bridge**: if the browser blocks the direct download or the
**VOD chat** is needed, the app asks it via `postMessage` (GET requests to
kick.com and the video host only).

You can also drop a video file or paste a direct URL (MP4/M3U8 with CORS).

### Deploy it for free on GitHub Pages

1. In the repository: **Settings → Pages → Source: GitHub Actions**.
2. Push to `main`: the [`pages.yml`](.github/workflows/pages.yml) workflow runs
   the tests and publishes `webapp/` at `https://<user>.github.io/<repo>/`.

Any static host works too (Cloudflare Pages, Netlify, Vercel…): no special
headers needed. To try it locally:

```bash
python3 -m http.server -d webapp 8000   # then open http://localhost:8000
```

User requirements: a recent desktop Chrome, Edge or Opera (WebCodecs; with
WebGPU the AI is much faster). The first run downloads the transcription
model (~150 MB, or ~1 GB for the accurate one) and, if enabled, the LLM
(~1 GB); after that they stay in the browser cache.

## 🖥️ Desktop version

```
VOD URL ─▶ ① Download (yt-dlp)  +  VOD chat (in parallel)
        ─▶ ② Transcribe (local Whisper)
        ─▶ ③ Hype: audio + chat
        ─▶ ④ Peak-based candidates + heuristics + local LLM (Ollama)
        ─▶ ⑤ Framing (faces) + jump cuts + captions + ffmpeg render
```

### Requirements

- Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) running with a model (`ollama pull qwen3:8b`)
  — it still works without it, using automatic signals only.
- ffmpeg **with libass** (for subtitles). If yours doesn't have it (current
  Homebrew builds don't), run `./scripts/get_ffmpeg.sh` (macOS/Linux) or
  `scripts\get_ffmpeg.ps1` (Windows): they download a static binary to `./bin/`.

### Quick start

macOS / Linux:

```bash
git clone https://github.com/<you>/kickclipper.git
cd kickclipper
./scripts/get_ffmpeg.sh   # if your ffmpeg lacks libass
./run.sh                  # starts the server and opens http://127.0.0.1:8420
```

Windows (PowerShell):

```powershell
git clone https://github.com/<you>/kickclipper.git
cd kickclipper
powershell -ExecutionPolicy Bypass -File .\scripts\get_ffmpeg.ps1   # ffmpeg with libass in .\bin
powershell -ExecutionPolicy Bypass -File .\run.ps1                   # http://127.0.0.1:8420
```

`stop` stops the server and `restart` restarts it (useful after updating the
code): `./run.sh stop` or `.\run.ps1 stop`. `KC_NO_OPEN=1` skips opening the
browser. `scripts/clean_uploads.sh` needs bash (on Windows, Git Bash or WSL).

Then, in the web UI:

1. Paste a VOD URL: `https://kick.com/<channel>/videos/<uuid>`.
2. Pick what to generate: **shorts 9:16**, **videos 16:9**, or both.
3. Optionally limit the time range (`01:00:00-01:30:00`).
4. Hit **CLIPEAR** and watch the pipeline run.
5. For each clip you can change the **framing** (auto, camera + gameplay,
   focus on the speaker, follow the person, blurred, center crop), the
   **caption** style, the **hook text**, **jump cuts** and the **cold open**,
   and re-render.

The first transcription downloads the Whisper model (~1.5 GB for
`large-v3-turbo`) and the first face-aware render the YuNet model (230 KB);
after that everything is offline except the VOD download and its chat.

### Output

Each rendered clip is exported as an upload-ready `.mp4` plus a `.txt` with
the recommended title, description, hashtags and the channel's links, sorted
by channel and type:

```
output/
└── somechannel/
    ├── shorts/that-was-insane_19ea96.mp4
    ├── shorts/that-was-insane_19ea96.txt   ← paste-ready publish text
    └── videos/the-full-comeback_8ca9c8.mp4
```

When you've uploaded everything and want the space back:

```bash
./scripts/clean_uploads.sh                    # wipes output/ (asks first)
./scripts/clean_uploads.sh somechannel shorts # just one folder
./scripts/clean_uploads.sh --jobs             # also delete ALL jobs (UI list,
                                              # clips and cache)
./scripts/clean_uploads.sh --all -y           # everything: output/ + data/
```

### Configuration

Environment variables with the `KC_` prefix, or a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `KC_OLLAMA_MODEL` | `qwen3:8b` | LLM used to score highlights |
| `KC_WHISPER_ENGINE` | auto | `mlx` (Apple Silicon) or `faster-whisper` (uses CUDA when available) |
| `KC_WHISPER_MODEL` | `large-v3-turbo` | Whisper model (MLX engine) |
| `KC_FASTER_WHISPER_MODEL` | `large-v3-turbo` | Whisper model (faster-whisper engine) |
| `KC_WHISPER_LANGUAGE` | auto-detect | Force a transcription language (e.g. `es`, `en`) |
| `KC_MIN_CLIP_SECONDS` | `15` | Minimum short duration |
| `KC_MAX_CLIP_SECONDS` | `60` | Maximum short duration (Shorts and Reels allow up to 180; TikTok only pays for > 60) |
| `KC_TARGET_CLIP_SECONDS` | `40` | Preferred short duration |
| `KC_VIDEO_MIN_SECONDS` | `300` | Minimum 16:9 video duration (5 min) |
| `KC_VIDEO_MAX_SECONDS` | `600` | Maximum 16:9 video duration (10 min) |
| `KC_MAX_CLIPS_PER_JOB` | `12` | Max short candidates per VOD |
| `KC_MAX_VIDEOS_PER_JOB` | `3` | Max video candidates per VOD |
| `KC_LLM_MAX_CANDIDATES` | `24` | Candidate windows sent to the LLM |
| `KC_CHAT_SIGNAL` | `true` | Use the VOD chat rate as a signal |
| `KC_CHAT_MAX_REQUESTS` | `400` | Request cap when reading a VOD's chat |
| `KC_CHAT_LAG_SECONDS` | `6` | Typical chat delay after the moment |
| `KC_CAPTION_STYLE` | `viral` | `viral`, `karaoke`, `clean` or `none` |
| `KC_SUBTITLE_FONT` | (bundled) | Force another caption font |
| `KC_JUMPCUT` | `true` | Remove pauses in shorts |
| `KC_JUMPCUT_MIN_GAP` | `0.7` | Pauses longer than this (s) get shortened |
| `KC_LOUDNORM` | `true` | Normalize audio to −14 LUFS |
| `KC_FACE_DETECTION` | `true` | Detect faces to choose the framing |
| `KC_OUTPUT_FPS` | `30` | Output frame rate |
| `KC_VIDEO_ENCODER` | `auto` | `auto` (GPU when it works), `libx264`, `h264_nvenc`, `h264_qsv`, `h264_amf`, `h264_videotoolbox` |
| `KC_STREAMER_SOCIALS` | `{}` | Per-channel social links (JSON) added to descriptions |
| `KC_PORT` | `8420` | Web server port |

Example of `KC_STREAMER_SOCIALS` (channels without an entry just get their
Kick page linked):

```bash
KC_STREAMER_SOCIALS='{"somechannel": {"Kick": "https://kick.com/somechannel", "Instagram": "https://instagram.com/...", "X": "https://x.com/..."}}'
```

## Project layout

```
kickclipper/                 # desktop version
├── server.py                # FastAPI: REST API + SSE + frontend
├── config.py                # Settings (KC_* env vars)
├── db.py                    # SQLite (jobs & clips)
├── pipeline/
│   ├── runner.py            # Pipeline orchestrator
│   ├── downloader.py        # yt-dlp + audio extraction
│   ├── transcriber.py       # Whisper (MLX / faster-whisper) + hallucination filter
│   ├── audio_analysis.py    # Streaming audio curves
│   ├── chat.py              # VOD chat rate
│   ├── signals.py           # Baselines, peaks, smoothing
│   ├── highlights.py        # Peak candidates + heuristics + LLM
│   ├── vision.py            # Faces, facecam, active speaker → framing plan
│   ├── timeline.py          # Jump cuts and cold open
│   ├── subtitles.py         # ASS captions (viral, karaoke, clean)
│   └── renderer.py          # ffmpeg graphs per layout, −14 LUFS
├── assets/fonts/            # Anton and Poppins (OFL)
└── web/                     # Local UI (vanilla JS, no build step)

webapp/                      # web version (static, runs in the browser)
├── index.html, styles.css
├── js/                      # source, audio, chat, transcribe, highlights,
│                            # vision, timeline, captions, compositor, render…
└── tests/                   # node --test + Playwright end-to-end test

docs/GUIA_CLIPS_VIRALES.md   # research: platforms, hooks, captions…
```

## Tests

```bash
uv run pytest -q                         # desktop version (uses real ffmpeg)
node --test webapp/tests/*.test.mjs      # web version modules
```

The web end-to-end test (Chromium + Playwright) lives in
`webapp/tests/e2e.mjs`; see the header of that file for its variables.

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Use it only with content you have the rights or the creator's
permission to republish. The bundled fonts (Anton, Poppins) are licensed under
the [SIL OFL 1.1](kickclipper/assets/fonts/).
