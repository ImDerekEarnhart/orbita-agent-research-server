#!/bin/sh
set -eu

home="${ORBITA_AGENT_HOME:-/data}"
mkdir -p "$home"
chown -R orbita:orbita "$home"

exec gosu orbita orbita-agent \
  --home "$home" \
  serve \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port "${PORT:-8000}"
