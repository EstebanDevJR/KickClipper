#!/usr/bin/env bash
# Lanza KickClipper.
#
#   ./run.sh           # arranca el servidor y abre la UI en el navegador
#   ./run.sh stop      # detiene el servidor
#   ./run.sh restart   # lo detiene y lo vuelve a arrancar
#
# UV_NO_EDITABLE=1 es necesario: uv marca el .venv con el flag "hidden" de
# macOS y CPython 3.12+ ignora los .pth de instalaciones editables ocultas,
# lo que rompería el arranque con "No module named 'kickclipper'".
set -euo pipefail
cd "$(dirname "$0")"
export UV_NO_EDITABLE=1

PORT="${KC_PORT:-8420}"

CMD="${1:-}"
if [[ "$CMD" == "stop" || "$CMD" == "restart" ]]; then
  shift
  PIDS="$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$PIDS" ]]; then
    kill $PIDS 2>/dev/null || true
    for _ in $(seq 1 20); do
      if [[ -z "$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)" ]]; then
        break
      fi
      sleep 0.3
    done
    REMAIN="$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$REMAIN" ]]; then
      kill -9 $REMAIN 2>/dev/null || true
    fi
    echo "✓ Servidor detenido."
  else
    echo "El servidor no estaba corriendo (puerto $PORT libre)."
  fi
  if [[ "$CMD" == "stop" ]]; then
    exit 0
  fi
fi

# Abre la UI en el navegador en cuanto el servidor responda (KC_NO_OPEN=1 lo desactiva).
url="http://127.0.0.1:$PORT"

# Si ya hay un servidor corriendo, solo abre la UI.
if curl -s -o /dev/null --max-time 1 "$url"; then
  echo "KickClipper ya está corriendo en $url"
  if [[ -z "${KC_NO_OPEN:-}" ]]; then
    open "$url"
  fi
  exit 0
fi

if [[ -z "${KC_NO_OPEN:-}" ]]; then
  (
    for _ in $(seq 1 120); do
      if curl -s -o /dev/null --max-time 1 "$url"; then
        open "$url"
        exit 0
      fi
      sleep 0.5
    done
  ) &
fi

exec uv run kickclipper "$@"
