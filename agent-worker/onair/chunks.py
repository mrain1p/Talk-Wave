"""Turn clips between the worker and the mixer's fetch.

The relay's chunks cross a process boundary: the WORKER writes them during
the call, and the WEB process serves them to the mixer at `/on-air/<token>`.
The two containers already share `data/` (the call records cross the same
way), so the store is a directory — and the filename IS the token, because a
sidecar per three-second clip would be bookkeeping for its own sake. The URL
is the credential, same as the studio's `/vm-air/`: unguessable, short-lived,
and gone the moment it has served its one fetch.

Burning is the caller's job, not the lookup's. The studio learned this the
hard way (three silent takes, 2026-08-17): the mixer probes with a HEAD
before it GETs, so a lookup that burns on first touch kills the real fetch
milliseconds later. `path_for` only answers; the HTTP handler discards after
the GET it actually served.

Everything in a caller's voice is deleted three ways: discarded after the
fetch, swept by TTL when a fetch never came, and cleared wholesale when the
call's relay closes. A crash mid-call must not leave a stranger's turn on
disk past the sweep horizon.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import time
from pathlib import Path

log = logging.getLogger("callin.onair")

SERVE_DIR = Path(
    os.environ.get("ONAIR_PATH",
                   Path(__file__).parent.parent.parent / "data" / "onair")
)

# How long a minted clip URL stays fetchable. The mixer pulls within a second
# of the push; three minutes covers a congested NAS without leaving a
# standing URL to a stranger's voice.
CHUNK_TTL_SECS = 180

# The store is flat and the token is the filename, so what a token may look
# like is the whole path-safety story.
_TOKEN = re.compile(r"[A-Za-z0-9_-]{12,64}")


def _ensure_dir() -> None:
    SERVE_DIR.mkdir(parents=True, exist_ok=True)
    # Owner-only, like the call records: these are a stranger's words. The
    # web process runs as the same user in the same image, so owner-only
    # still crosses the container seam.
    try:
        os.chmod(SERVE_DIR, 0o700)
    except OSError:
        pass


def adopt(wav: Path) -> str | None:
    """Move a finished clip into the store; returns the token that serves it.

    shutil.move, not rename: the clip is born in the container's /tmp and the
    store lives on the /data bind mount — the same EXDEV seam the studio's
    draft store hit on its very first real upload.
    """
    sweep()
    token = secrets.token_urlsafe(18)
    _ensure_dir()
    try:
        shutil.move(str(wav), str(SERVE_DIR / f"{token}.wav"))
        os.chmod(SERVE_DIR / f"{token}.wav", 0o600)
    except OSError as e:
        log.warning("could not adopt an on-air chunk: %s", e)
        return None
    return token


def path_for(token: str) -> Path | None:
    """The clip a valid, unexpired token names — WITHOUT burning it. The
    mixer HEADs before it GETs; discard() is the handler's job after the GET
    it served."""
    if not token or not _TOKEN.fullmatch(token):
        return None
    path = SERVE_DIR / f"{token}.wav"
    try:
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > CHUNK_TTL_SECS:
            return None
    except OSError:
        return None
    return path


def discard(token: str) -> None:
    if not token or not _TOKEN.fullmatch(token):
        return
    (SERVE_DIR / f"{token}.wav").unlink(missing_ok=True)


def sweep(ttl_secs: float = CHUNK_TTL_SECS) -> int:
    """Delete anything past its fetch window. Run before every adopt and at
    relay close — a crash mid-call must not leave a voice on disk forever."""
    removed = 0
    cutoff = time.time() - ttl_secs
    try:
        entries = list(SERVE_DIR.glob("*.wav"))
    except (FileNotFoundError, OSError):
        return 0
    for path in entries:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


# The operator's dump crosses the same process seam the clips do: the PANEL
# talks to the web process, the relay lives in the worker, and the marker
# file is the message between them. Fresh-only, because a dump pressed while
# no phone-in was live must never behead the NEXT caller's first turn.
DUMP_FRESH_SECS = 120


# The worker's last word on whether the mixer's telnet answers — FROM the
# process that pushes. The dashboard's door probe runs in the web container,
# and the two sit on their own networks, so the web's own reachability said
# nothing about the worker's: a deployment that joined only the web to the
# station's network showed the Live Call door OPEN while every phone-in
# quietly fell back private (seen 2026-08-18, the day the wiring doc first
# said "join both"). The verdict crosses the seam the way DUMP does — a
# marker in the shared store, its own mtime as the staleness rule. Fifteen
# minutes, because fixing the network means recreating the worker container,
# and its prewarm rewrites the verdict within seconds of the fix.
VERDICT_FRESH_SECS = 900


def record_mixer_verdict(ok: bool, why: str = "") -> None:
    _ensure_dir()
    try:
        (SERVE_DIR / "MIXER").write_text(
            ("ok" if ok else f"no: {why}"), encoding="utf-8")
    except OSError as e:
        log.warning("could not write the mixer verdict: %s", e)


def mixer_verdict() -> dict | None:
    """The worker's verdict, or None when it never spoke or spoke too long
    ago to trust. Never raises — a corrupt marker reads as absent."""
    path = SERVE_DIR / "MIXER"
    try:
        age = time.time() - path.stat().st_mtime
        body = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if age > VERDICT_FRESH_SECS:
        return None
    return {"ok": body.strip() == "ok",
            "why": body.partition(":")[2].strip(), "age": age}


def request_dump() -> None:
    _ensure_dir()
    try:
        (SERVE_DIR / "DUMP").write_bytes(b"")
    except OSError as e:
        log.warning("could not write the dump marker: %s", e)


def take_dump() -> bool:
    """Consume the marker; True only when it was fresh. Consumed either way —
    a stale marker is spent, not left lying around to fire later."""
    path = SERVE_DIR / "DUMP"
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    path.unlink(missing_ok=True)
    return age <= DUMP_FRESH_SECS


def clear() -> int:
    """Everything, now — the relay closing, or the operator's kill. Unlike
    sweep() this does not wait for the TTL's opinion."""
    gone = 0
    try:
        for path in SERVE_DIR.glob("*.wav"):
            try:
                path.unlink()
                gone += 1
            except OSError:
                continue
    except OSError:
        pass
    return gone
