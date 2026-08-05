# Built from the REPO ROOT context (docker build -f agent-worker/Dockerfile .)
# so the widget ships inside the image — a pulled image needs no source
# checkout. token_server's default WIDGET_DIR resolves to /web-widget.
FROM python:3.12-slim

WORKDIR /app

COPY agent-worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent-worker/ .
COPY web-widget/ /web-widget/
# Bundled sound packs. Resolves to /assets/sounds, matching sounds.ASSETS_DIR
# (../assets/sounds from /app). Copied even when empty so a pack added later
# is a rebuild, not a Dockerfile change.
COPY assets/ /assets/

# Downloads the Silero VAD / turn-detector weights at build time so the first
# caller doesn't pay for it mid-call. (The Whisper model for local STT still
# downloads on first use — it depends on which model you select.)
RUN python main.py download-files || true

# `start` is the production worker command; token-server overrides this with
# ["python", "token_server.py"] in compose.
CMD ["python", "main.py", "start"]
