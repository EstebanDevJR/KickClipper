# ⚡ KickClipper

> 🇪🇸 [Documentación en español](README.es.md)

**100% local** tool that turns Kick VODs into ready-to-upload content — vertical
**shorts** (9:16, with karaoke subtitles) for YouTube Shorts / TikTok / Reels, or
horizontal **videos** (16:9, 5–10 min) for YouTube — with automatic highlight
detection powered by AI running entirely on your machine.

Works with **any kick.com channel**, in any language. No cloud APIs, no keys,
no uploads: download, transcription, scoring and rendering all happen locally.

> ⚖️ **Use responsibly.** Only process content you have the rights to, or
> explicit permission from the creator. Most platforms require permission to
> re-upload someone else's stream.

## Features

- 🎯 **Automatic highlight detection** — audio excitement analysis (RMS +
  spectral flux) plus lexical signals, then a local LLM (Ollama) scores each
  candidate for viral potential.
- ✂️ **Two output formats per job** — generate shorts (15 s – 1 min), long-form
  videos (5 – 10 min), or both at once.
- 📝 **Word-level karaoke subtitles** on shorts, styled for vertical content
  (videos render clean by default; subtitles can be toggled per clip).
- 🤖 **Publish-ready metadata** — the LLM writes a hook title, description and
  hashtags in the language of the stream; each export ships with a `.txt`
  ready to paste.
- 🖥️ **Local web studio** — real-time pipeline progress (SSE), candidate
  review with thumbnails, trim start/end, re-render, preview and download.
- 🍎 **Hardware encoding** on macOS (VideoToolbox) and GPU transcription on
  Apple Silicon (MLX); CPU fallback (`faster-whisper`) everywhere else.

## How it works

```
VOD URL ─▶ ① Download (yt-dlp) ─▶ ② Transcribe (local Whisper)
        ─▶ ③ Hype analysis (audio energy + lexical signals)
        ─▶ ④ Viral scoring (local LLM via Ollama)
        ─▶ ⑤ Render: 9:16 shorts (≤1 min, karaoke subtitles)
                     and/or 16:9 videos (5–10 min)
```

1. **Download** — `yt-dlp` fetches the VOD (or just the time range you pick).
2. **Transcribe** — Whisper with word timestamps; language auto-detected.
3. **Hype analysis** — a per-second excitement curve flags screams, laughter
   and big reactions.
4. **AI selection** — the best candidate windows are scored by a local LLM,
   which also writes the title, description and hashtags. Shorts and videos
   are searched separately, each with its own duration range.
5. **Render** — shorts at 1080×1920 with blurred background or center crop;
   videos at 1920×1080. Finished files land in `output/<channel>/<shorts|videos>/`
   next to a `.txt` with everything you need to publish.

## Requirements

- Python ≥ 3.12 and [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) running with a model (`ollama pull qwen3:8b`)
- ffmpeg **with libass** (for subtitles). If yours doesn't have it (current
  Homebrew builds don't), run:

  ```bash
  ./scripts/get_ffmpeg.sh   # downloads a static binary to ./bin/ffmpeg
  ```

## Quick start

```bash
git clone https://github.com/<you>/kickclipper.git
cd kickclipper
./scripts/get_ffmpeg.sh   # if your ffmpeg lacks libass
./run.sh                  # starts the server and opens http://127.0.0.1:8420
```

`./run.sh stop` stops the server, `./run.sh restart` restarts it (useful after
updating the code), and `KC_NO_OPEN=1` skips opening the browser.

Then, in the web UI:

1. Paste a VOD URL: `https://kick.com/<channel>/videos/<uuid>`.
2. Pick what to generate: **shorts 9:16**, **videos 16:9**, or both.
3. Optionally limit the time range (`01:00:00-01:30:00`).
4. Hit **CLIPEAR** and watch the pipeline run.

The first transcription downloads the Whisper model (~1.5 GB for
`large-v3-turbo`); after that everything is offline except the VOD download.

## Output

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

## Configuration

Environment variables with the `KC_` prefix, or a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `KC_OLLAMA_MODEL` | `qwen3:8b` | LLM used to score highlights |
| `KC_WHISPER_ENGINE` | auto | `mlx` (Apple Silicon) or `faster-whisper` |
| `KC_WHISPER_MODEL` | `large-v3-turbo` | Whisper model (MLX engine) |
| `KC_FASTER_WHISPER_MODEL` | `large-v3-turbo` | Whisper model (CPU engine) |
| `KC_WHISPER_LANGUAGE` | auto-detect | Force a transcription language (e.g. `es`, `en`) |
| `KC_MIN_CLIP_SECONDS` | `15` | Minimum short duration |
| `KC_MAX_CLIP_SECONDS` | `60` | Maximum short duration (1 min) |
| `KC_VIDEO_MIN_SECONDS` | `300` | Minimum 16:9 video duration (5 min) |
| `KC_VIDEO_MAX_SECONDS` | `600` | Maximum 16:9 video duration (10 min) |
| `KC_MAX_CLIPS_PER_JOB` | `12` | Max short candidates per VOD |
| `KC_MAX_VIDEOS_PER_JOB` | `3` | Max video candidates per VOD |
| `KC_LLM_MAX_CANDIDATES` | `24` | Candidate windows sent to the LLM |
| `KC_STREAMER_SOCIALS` | `{}` | Per-channel social links (JSON) added to descriptions |
| `KC_PORT` | `8420` | Web server port |

Example of `KC_STREAMER_SOCIALS` (channels without an entry just get their
Kick page linked):

```bash
KC_STREAMER_SOCIALS='{"somechannel": {"Kick": "https://kick.com/somechannel", "Instagram": "https://instagram.com/...", "X": "https://x.com/..."}}'
```

## Project layout

```
kickclipper/
├── server.py            # FastAPI: REST API + SSE + frontend
├── config.py            # Settings (KC_* env vars)
├── db.py                # SQLite (jobs & clips)
├── events.py            # Event bus → SSE
├── streamers.py         # Kick URLs, formats, social links
├── pipeline/
│   ├── runner.py        # Pipeline orchestrator
│   ├── downloader.py    # yt-dlp + audio extraction
│   ├── transcriber.py   # Whisper (MLX / faster-whisper)
│   ├── audio_analysis.py# Excitement curve
│   ├── highlights.py    # Heuristics + LLM (Ollama)
│   ├── subtitles.py     # ASS karaoke subtitles
│   └── renderer.py      # ffmpeg 9:16 / 16:9 + VideoToolbox
└── web/                 # UI (vanilla JS, no build step)
```

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Use it only with content you have the rights or the creator's
permission to republish.
