#!/usr/bin/env bash
# Talk Wave one-command install:
#   curl -fsSL https://raw.githubusercontent.com/mrain1p/Talk-Wave/main/install.sh | bash
#
# Makes ./talk-wave (or the folder named as the first argument), fetches the
# stack files, generates the LiveKit keypair, detects this machine's LAN
# address, prepares data/, and starts the stack. Refuses to touch a folder
# that already holds a deployment — it is an installer, not an updater.
set -euo pipefail

RAW="https://raw.githubusercontent.com/mrain1p/Talk-Wave/main"
DIR="${1:-talk-wave}"

say() { printf '\n== %s\n' "$*"; }

command -v docker >/dev/null || { echo "docker is required — install it first"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "docker compose v2 is required"; exit 1; }

if [ -e "$DIR/.env" ] || [ -e "$DIR/livekit.yaml" ]; then
  echo "$DIR already holds a deployment — refusing to overwrite it."
  exit 1
fi

say "Fetching the stack into $DIR/"
mkdir -p "$DIR"
cd "$DIR"
curl -fsSL "$RAW/docker-compose.yaml" -o docker-compose.yaml
curl -fsSL "$RAW/Caddyfile" -o Caddyfile
curl -fsSL "$RAW/.env.example" -o .env
curl -fsSL "$RAW/livekit.example.yaml" -o livekit.yaml

say "Generating the LiveKit keypair (one secret, written to both files)"
if command -v openssl >/dev/null; then
  SECRET="$(openssl rand -base64 36 | tr -d '/+=\n')"
else
  SECRET="$(head -c 36 /dev/urandom | base64 | tr -d '/+=\n')"
fi
sed -i "s|REPLACE_WITH_A_FRESH_SECRET|$SECRET|" livekit.yaml
sed -i "s|^LIVEKIT_API_SECRET=.*|LIVEKIT_API_SECRET=$SECRET|" .env

say "Detecting this machine's LAN address"
HOST_IP="$(ip -4 route get 1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -n1)"
[ -n "$HOST_IP" ] || HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -n "$HOST_IP" ]; then
  sed -i "s|^HOST_IP=.*|HOST_IP=$HOST_IP|" .env
  echo "   HOST_IP=$HOST_IP"
else
  echo "   could not detect it — set HOST_IP in $DIR/.env before callers can connect"
fi

say "Starting the stack"
# data/ ownership is handled by the stack's own init service on first start.
docker compose up -d

say "Done — open https://${HOST_IP:-<this-machine>}:8443"
echo "   (one-time certificate screen, then: set the admin password, add an API key, press Call)"
echo "   Point it at your SUB/WAVE station in the panel: Configuration -> SUB/WAVE Station."
