# Contributing to KickClipper

Thanks for your interest in improving KickClipper! Issues and pull requests
are welcome.

## Development setup

```bash
git clone https://github.com/<you>/kickclipper.git
cd kickclipper
./scripts/get_ffmpeg.sh   # static ffmpeg with libass (if yours lacks it)
./run.sh                  # server + UI at http://127.0.0.1:8420
```

Requirements: Python ≥ 3.12, [uv](https://docs.astral.sh/uv/), and
[Ollama](https://ollama.com) running locally for highlight scoring.

`./run.sh restart` restarts the server after code changes. The frontend is
plain HTML/CSS/JS in `kickclipper/web/` — no build step, just reload the page.

## Guidelines

- **Keep it local.** The core promise of the project is that nothing leaves
  the user's machine except the VOD download. Don't add cloud APIs or
  telemetry.
- **Keep it dependency-light.** The UI is vanilla JS on purpose; the backend
  sticks to FastAPI + SQLite.
- **Match the existing style.** Code comments and UI copy are in Spanish;
  follow the conventions of the file you're editing.
- **Test the pipeline end to end** with a short VOD range (e.g. a 10-minute
  section) before opening a PR that touches `kickclipper/pipeline/`.

## Reporting issues

Please include:

- OS and hardware (Apple Silicon? GPU?)
- Python version and how you launched the app
- The job's error message from the UI, and relevant server logs
- The VOD URL and time range, if shareable

## Legal note

KickClipper is meant for creators clipping **their own content** or content
they have explicit permission to republish. Contributions that facilitate
bulk scraping or bypassing platform protections won't be accepted.
