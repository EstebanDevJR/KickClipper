#!/usr/bin/env bash
# Elimina los videos ya generados (cuando ya se subieron y ocupan espacio).
#
# Uso:
#   ./scripts/clean_uploads.sh                  # borra todo output/
#   ./scripts/clean_uploads.sh westcol          # solo un streamer
#   ./scripts/clean_uploads.sh westcol shorts   # solo un tipo
#   ./scripts/clean_uploads.sh --jobs           # además borra TODOS los trabajos
#                                               # (lista de la UI, clips y su caché)
#   ./scripts/clean_uploads.sh --all            # output/ + todo data/ (base incluida)
#   añade -y para no pedir confirmación
set -euo pipefail
cd "$(dirname "$0")/.."

YES=0
ALL=0
JOBS=0
ARGS=()
for a in "$@"; do
  case "$a" in
    -y) YES=1 ;;
    --all) ALL=1 ;;
    --jobs) JOBS=1 ;;
    *) ARGS+=("$a") ;;
  esac
done

TARGET="output"
[[ ${#ARGS[@]} -ge 1 ]] && TARGET="output/${ARGS[0]}"
[[ ${#ARGS[@]} -ge 2 ]] && TARGET="output/${ARGS[0]}/${ARGS[1]}"

if [[ ! -d "$TARGET" && $ALL -eq 0 && $JOBS -eq 0 ]]; then
  echo "Nada que limpiar: no existe $TARGET"
  exit 0
fi

COUNT=$(find "$TARGET" -name '*.mp4' 2>/dev/null | wc -l | tr -d ' ')
SIZE=$(du -sh "$TARGET" 2>/dev/null | cut -f1 || echo "0B")
echo "Se eliminarán $COUNT videos ($SIZE) de $TARGET"
if [[ $JOBS -eq 1 ]]; then
  JSIZE=$(du -sh data/jobs 2>/dev/null | cut -f1 || echo "0B")
  echo "y también TODOS los trabajos: lista de la UI, clips y caché data/jobs ($JSIZE)"
fi
if [[ $ALL -eq 1 ]]; then
  DSIZE=$(du -sh data 2>/dev/null | cut -f1 || echo "0B")
  echo "y también todo data/ ($DSIZE: base de datos, VODs descargados, transcripciones)"
fi

if [[ $YES -ne 1 ]]; then
  read -r -p "¿Continuar? [s/N] " RESP
  [[ "$RESP" =~ ^[sS]$ ]] || { echo "Cancelado."; exit 0; }
fi

rm -rf "$TARGET"

if [[ $JOBS -eq 1 && $ALL -eq 0 ]]; then
  BASE="http://127.0.0.1:${KC_PORT:-8420}"
  if curl -s -o /dev/null --max-time 1 "$BASE/api/status"; then
    # Servidor corriendo: se borra vía API para que la UI se actualice al momento.
    curl -s "$BASE/api/jobs" \
      | python3 -c 'import json,sys; [print(j["id"]) for j in json.load(sys.stdin)]' \
      | while read -r id; do
          curl -s -X DELETE "$BASE/api/jobs/$id" > /dev/null
          echo "  ✕ job $id"
        done
  else
    # Servidor apagado: se limpia la base y la caché directamente.
    rm -rf data/jobs
    if [[ -f data/kickclipper.db ]]; then
      sqlite3 data/kickclipper.db "DELETE FROM clips; DELETE FROM jobs;"
    fi
    echo "  jobs eliminados de data/ (servidor apagado)"
  fi
fi

if [[ $ALL -eq 1 ]]; then
  rm -rf data
  echo "(si el servidor está corriendo, reinícialo para regenerar la base de datos)"
fi
echo "✓ Limpieza completa."
