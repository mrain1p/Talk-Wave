"""How a clip of ours physically reaches the station's voice channel.

Extracted from voicemail/air.py when the live-call relay became the second
consumer. The mechanism is the one proven on the live station 2026-08-17
(RID 409, heard by the operator): serve the clip at an unguessable URL on
:8100, then `voice_queue.push <url>` over the mixer's telnet — Liquidsoap
fetches the audio itself, the same way it fetches every music track from
Navidrome, so nothing is ever written to the station's disk. The voice
channel is the only one that carries speech at level (the sfx bed buries it
~7 dB under the music — measured, twice), and the queue is FIFO with the
duck held across back-to-back items, which is what makes both a studio
send's intro→clip→close and a live call's turn-after-turn chain work with
no timing arithmetic.

The settings keys keep their historical `vm_` prefix: they predate the live
relay, they are deployed, and a rename would silently disconnect every
station that already configured them. Both pathways read the same two keys
because the answer really is shared — where the mixer's telnet lives, and
what base URL the mixer can fetch us at.

If subwave#1424 (one-shot upload-and-air over the admin API) lands, the
telnet-and-URL shape retires and this module becomes an HTTP client — and
only this module. That is the point of it being one file.
"""

from __future__ import annotations

import logging
import os
import socket

log = logging.getLogger("callin.voicemail")

# Where the mixer's telnet lives. The station's own controller default
# (liquidsoap-control.ts): host `broadcast`, port 1234 — resolvable only from
# the station's docker network, which is the one deployment step this
# transport asks for (the container doing the push joins that network).
DEFAULT_MIXER = "broadcast:1234"

_TELNET_TIMEOUT = 4.0


def mixer_address(cfg: dict) -> tuple[str, int]:
    raw = str(cfg.get("vm_mixer_telnet") or "").strip() or DEFAULT_MIXER
    host, _, port = raw.partition(":")
    try:
        return host or "broadcast", int(port or 1234)
    except ValueError:
        return host or "broadcast", 1234


def mixer_reachable(cfg: dict) -> bool:
    """A plain TCP connect — cheap enough to run per send, honest enough to
    pick the backend with. Unreachable is normal (no shared network) and
    means the fallback, not an error. Note the answer is per-PROCESS: the
    web container and the worker sit on their own networks, so one being
    able to reach the mixer says nothing about the other."""
    host, port = mixer_address(cfg)
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


def telnet_push(cfg: dict, uri: str) -> str | None:
    """``voice_queue.push <uri>`` — returns the RID the mixer minted, or None.

    The response format is the one measured on the live mixer: the RID on its
    own line, then END. (409 in the first probe was the request id, not an
    HTTP status — worth saying because everyone reads it as one.)
    """
    host, port = mixer_address(cfg)
    try:
        with socket.create_connection((host, port),
                                      timeout=_TELNET_TIMEOUT) as sock:
            sock.settimeout(_TELNET_TIMEOUT)
            sock.sendall(f"voice_queue.push {uri}\nquit\n".encode())
            raw = b""
            while b"END" not in raw and len(raw) < 4096:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                raw += chunk
    except OSError as e:
        log.warning("mixer telnet push failed: %s", e)
        return None
    for line in raw.decode(errors="replace").splitlines():
        line = line.strip()
        if line.isdigit():
            return line
    log.warning("mixer answered the push without a RID: %r", raw[:120])
    return None


def air_base_url(cfg: dict) -> str:
    """Where the mixer fetches clips from: the operator's setting, else the
    published token-server port on the host IP the deployment already knows
    (HOST_IP is load-bearing for LiveKit, so it is reliably set). The mixer
    reaches host-published ports today — that is how it gets its music."""
    explicit = str(cfg.get("vm_air_base_url") or "").strip().rstrip("/")
    if explicit:
        return explicit
    host = os.environ.get("HOST_IP", "").strip()
    port = os.environ.get("TOKEN_PORT", "8100").strip() or "8100"
    return f"http://{host}:{port}" if host else ""
