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

# --- run as a normal user ---------------------------------------------------
# data/ holds the API keys and the password hashes in plaintext JSON, and it is
# bind-mounted from the host. Running as root means root on those files, and on
# anything else that mount can reach.
#
# 1000 because that is the first normal user on a Linux host and what a NAS
# share is usually owned by. Override at build time if yours differs:
#   docker build --build-arg APP_UID=1026 --build-arg APP_GID=100 ...
#
# UPGRADING AN EXISTING DEPLOYMENT: files already in data/ were written by root
# and will not be readable by this user. Fix them on the HOST first — BOTH the
# owner and the modes — or the app comes up with no settings, no keys and no
# password:
#   chown -R 1000:1000 /path/to/wave-talk/data
#   chmod -R u+rwX     /path/to/wave-talk/data
# The chmod matters as much as the chown: a Synology share creates files with
# mode 000, which root walks straight through and an owner cannot. Safe to run
# while the old container is still up — root ignores the mode bits either way.
# The password store now refuses to read as "unconfigured" when it cannot be
# opened (admin_auth.unreadable), so this fails shut and says so rather than
# quietly serving an open panel.
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd -g ${APP_GID} app 2>/dev/null || true \
 && useradd -u ${APP_UID} -g ${APP_GID} -m -d /home/app app 2>/dev/null || true \
 && mkdir -p /data /home/app \
 && chown -R ${APP_UID}:${APP_GID} /data /home/app

USER ${APP_UID}:${APP_GID}
# The weights below land under $HOME. Without this they would go to /root,
# which this user cannot read — moving a 140MB download into the first call,
# which is the exact cost the build-time fetch exists to avoid.
ENV HOME=/home/app
# /app belongs to root and is read-only to us; without this Python retries a
# .pyc write on every import and silently gives up each time.
ENV PYTHONDONTWRITEBYTECODE=1

# Downloads the Silero VAD / turn-detector weights at build time so the first
# caller doesn't pay for it mid-call. (The Whisper model for local STT still
# downloads on first use — it depends on which model you select.)
RUN python main.py download-files || true

# `start` is the production worker command; token-server overrides this with
# ["python", "token_server.py"] in compose.
CMD ["python", "main.py", "start"]
