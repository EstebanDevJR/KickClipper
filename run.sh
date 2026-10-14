#!/usr/bin/env bash
# Lanza KickClipper (macOS, Linux y Windows con Git Bash / WSL).
# En Windows sin bash, usa run.ps1.
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
url="http://127.0.0.1:$PORT"

# PIDs escuchando en el puerto (lsof, ss o fuser, según lo que haya)
pids_on_port() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser "$PORT/tcp" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true
  elif command -v netstat >/dev/null 2>&1; then
    # Git Bash en Windows: netstat -ano → PID de Windows
    netstat -ano 2>/dev/null | grep -E "[:.]$PORT[[:space:]].*LISTEN" | awk '{print $NF}' | sort -u || true
  fi
}

stop_server() {
  local pids
  pids="$(pids_on_port)"
  if [[ -z "$pids" ]]; then
    echo "El servidor no estaba corriendo (puerto $PORT libre)."
    return
  fi
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) for p in $pids; do taskkill //PID "$p" //F >/dev/null 2>&1 || true; done ;;
    *)
      kill $pids 2>/dev/null || true
      for _ in $(seq 1 20); do [[ -z "$(pids_on_port)" ]] && break; sleep 0.3; done
      [[ -n "$(pids_on_port)" ]] && kill -9 $(pids_on_port) 2>/dev/null || true
      ;;
  esac
  echo "✓ Servidor detenido."
}

# Abre una URL con el navegador por defecto de cada sistema
open_url() {
  case "$(uname -s)" in
    Darwin) open "$1" ;;
    MINGW*|MSYS*|CYGWIN*) cmd.exe //c start "" "$1" >/dev/null 2>&1 || true ;;
    *)
      if grep -qi microsoft /proc/version 2>/dev/null && command -v wslview >/dev/null 2>&1; then
        wslview "$1"
      elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$1" >/dev/null 2>&1 &
      else
        echo "Abre $1 en tu navegador"
      fi
      ;;
  esac
}

CMD="${1:-}"
if [[ "$CMD" == "stop" || "$CMD" == "restart" ]]; then
  shift
  stop_server
  [[ "$CMD" == "stop" ]] && exit 0
fi

# Si ya hay un servidor corriendo, solo abre la UI.
if curl -s -o /dev/null --max-time 1 "$url"; then
  echo "KickClipper ya está corriendo en $url"
  [[ -z "${KC_NO_OPEN:-}" ]] && open_url "$url"
  exit 0
fi

# Abre la UI en cuanto el servidor responda (KC_NO_OPEN=1 lo desactiva).
if [[ -z "${KC_NO_OPEN:-}" ]]; then
  (
    for _ in $(seq 1 120); do
      if curl -s -o /dev/null --max-time 1 "$url"; then
        open_url "$url"
        exit 0
      fi
      sleep 0.5
    done
  ) &
fi

exec uv run kickclipper "$@"
