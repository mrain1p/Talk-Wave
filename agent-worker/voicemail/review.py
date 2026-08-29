"""Drafts: a caller's clip between the recording and the air.

This module is the deliberate reversal of a line capture.py still keeps for
the classic machine — "there is no audio to keep and never was." The soundbite
flow keeps audio, on purpose, because airing the caller's own voice IS the
feature — and everything else here exists to make that holding as short and as
boring as possible: a draft lives in one directory, is deleted the moment it
is sent or abandoned, and a sweep removes anything a crash left behind. The
recording card tells the caller their message may be played on air; this
module's job is to make sure "may be played" never quietly becomes "is kept".

Storage only, synchronous, stdlib — the API layer runs STT and the action
preview and writes the results back through annotate(). That split is what
makes this testable without a station, a model, or an event loop.
"""

from __future__ import annotations

from jsonstore import write_atomic

import json
import os
import secrets
import shutil
import time
from pathlib import Path

DRAFTS_DIR = Path(
    os.environ.get("VOICEMAIL_DRAFTS_PATH",
                   Path(__file__).parent.parent.parent / "data" / "voicemail"
                   / "drafts")
)

# A draft the caller walked away from is deleted by the sweep. Long enough to
# survive a phone lock or a re-read of the transcript; short enough that an
# abandoned voice is not still on disk at breakfast.
DRAFT_TTL_SECS = 15 * 60

# The same shape as deliver.MAX_MESSAGES: a robot recording all day must not
# fill the volume the settings live on. Oldest drafts fall off first.
MAX_DRAFTS = 40

# How long a minted air URL stays claimable. The mixer fetches within a second
# of the push; two minutes covers a slow adapter without leaving a standing
# public URL to a stranger's voice.
AIR_TOKEN_TTL_SECS = 120


def _sidecar(draft_id: str) -> Path:
    return DRAFTS_DIR / f"{draft_id}.json"


def audio_path(draft_id: str) -> Path:
    return DRAFTS_DIR / f"{draft_id}.wav"


def _write_sidecar(draft_id: str, data: dict) -> None:
    write_atomic(_sidecar(draft_id), data, dir_mode=0o755, indent=1)


def get(draft_id: str) -> dict | None:
    """The sidecar, or None — a bad id and an expired draft look the same to
    the caller, and should."""
    if not draft_id or "/" in draft_id or "\\" in draft_id or "." in draft_id:
        return None
    try:
        with open(_sidecar(draft_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def create(mastered_wav: Path, stats: dict, tier: str) -> dict:
    """Adopt a mastered clip as a draft. The clip is MOVED into the store, so
    there is never a second copy waiting to be forgotten.

    Adoption happens BEFORE the sweep, deliberately: the sweep deletes any
    .wav without a sidecar, and an incoming clip that happens to sit in the
    drafts dir is exactly such a file until the move and the sidecar land —
    the first run of the tests had the sweep eat the clip it was adopting.
    """
    draft_id = secrets.token_urlsafe(9)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    # shutil.move, not Path.replace: the mastered clip is born in the
    # container's /tmp and the drafts live on the /data bind mount, and a
    # bare rename cannot cross that seam — EXDEV, thrown on the very first
    # real upload (2026-08-17, "the studio is not answering") after every
    # test had passed on a machine where both paths share a device.
    shutil.move(str(mastered_wav), str(audio_path(draft_id)))
    try:
        os.chmod(audio_path(draft_id), 0o644)
    except OSError:
        pass
    data = {
        "id": draft_id,
        "at": time.time(),
        "tier": str(tier or ""),
        "stats": dict(stats or {}),
        "transcript": "",
        "action": {},
    }
    _write_sidecar(draft_id, data)
    sweep()
    return data


def annotate(draft_id: str, *, transcript: str | None = None,
             action: dict | None = None) -> dict | None:
    """The API layer's write-back: what STT heard, and what send would do.

    The action stored here is the one that will run — resolved ids, not the
    caller's words re-interpreted at send time. Preview and execution reading
    the same record is the receipts discipline; two resolutions of the same
    phrase is how a caller approves one track and airs another.
    """
    data = get(draft_id)
    if not data:
        return None
    if transcript is not None:
        data["transcript"] = str(transcript)[:2000]
    if action is not None:
        data["action"] = dict(action)
    _write_sidecar(draft_id, data)
    return data


def delete(draft_id: str) -> None:
    """Send, abandon, re-record — every exit runs through here. Deletes the
    audio even when the sidecar is already gone: a half-created draft (clip
    moved, sidecar crash) is exactly the orphan this must not leave."""
    if not draft_id or "/" in draft_id or "\\" in draft_id or "." in draft_id:
        return
    audio_path(draft_id).unlink(missing_ok=True)
    _sidecar(draft_id).unlink(missing_ok=True)


def sweep(ttl_secs: float = DRAFT_TTL_SECS) -> int:
    """Delete expired drafts and orphaned audio. Run at startup and before
    every create — a crash mid-send must not leave a voice on disk forever."""
    removed = 0
    try:
        entries = list(DRAFTS_DIR.iterdir())
    except (FileNotFoundError, OSError):
        return 0
    cutoff = time.time() - ttl_secs
    ids = set()
    for path in entries:
        if path.suffix == ".json" and not path.name.endswith(".json.tmp"):
            ids.add(path.stem)
    for draft_id in ids:
        data = get(draft_id)
        if not data or float(data.get("at") or 0) < cutoff:
            delete(draft_id)
            removed += 1
    # Audio with no sidecar is an orphan whatever its age.
    for path in entries:
        if path.suffix == ".wav" and path.stem not in ids and path.is_file():
            path.unlink(missing_ok=True)
            removed += 1
    # The cap, oldest first, after the TTL has had its say.
    live = sorted(
        (d for d in (get(i) for i in ids) if d),
        key=lambda d: float(d.get("at") or 0))
    for stale in live[:max(0, len(live) - MAX_DRAFTS)]:
        delete(str(stale.get("id")))
        removed += 1
    return removed


# --- the air URL -----------------------------------------------------------
# The mixer fetches the clip unauthenticated (it is curl on another network),
# so the URL is the credential: unguessable, short-lived, and burned by the
# first claim. Modeled on the station's own Navidrome stream tokens — the
# pattern its mixer already trusts.

def mint_air_token(draft_id: str) -> str | None:
    data = get(draft_id)
    if not data:
        return None
    token = secrets.token_urlsafe(18)
    data["airToken"] = token
    data["airTokenAt"] = time.time()
    _write_sidecar(draft_id, data)
    return token


def peek_air_token(token: str) -> Path | None:
    """Is this token valid, WITHOUT spending it. The mixer probes the URL
    with a HEAD before it downloads — and a HEAD that burned the token left
    the real GET a 404 six milliseconds later (2026-08-17, the operator's
    third silent take). Read-only: same checks as the claim, no mutation."""
    if not token:
        return None
    try:
        entries = list(DRAFTS_DIR.glob("*.json"))
    except (FileNotFoundError, OSError):
        return None
    for sidecar in entries:
        data = get(sidecar.stem)
        if not data or data.get("airToken") != token:
            continue
        if time.time() - float(data.get("airTokenAt") or 0) > AIR_TOKEN_TTL_SECS:
            return None
        path = audio_path(sidecar.stem)
        return path if path.is_file() else None
    return None


def claim_air_token(token: str) -> Path | None:
    """One fetch, then the URL is dead. Returns the audio path on the single
    valid claim, None for everything else — expired, reused, invented."""
    if not token:
        return None
    try:
        entries = list(DRAFTS_DIR.glob("*.json"))
    except (FileNotFoundError, OSError):
        return None
    for sidecar in entries:
        data = get(sidecar.stem)
        if not data or data.get("airToken") != token:
            continue
        minted = float(data.get("airTokenAt") or 0)
        data.pop("airToken", None)
        data.pop("airTokenAt", None)
        _write_sidecar(sidecar.stem, data)          # burned either way
        if time.time() - minted > AIR_TOKEN_TTL_SECS:
            return None
        path = audio_path(sidecar.stem)
        return path if path.is_file() else None
    return None
