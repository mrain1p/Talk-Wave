"""Staged voicemail greetings: one clip per persona, rendered ahead of time.

The greeting must be in the voice of whoever is on air and must play the
moment the line answers — a caller expects a recording to start instantly,
and local TTS on this deployment is slower than realtime. So clips are
rendered once, by an explicit staging run from the panel, and cached.

The cache key is WHAT THE CLIP WAS RENDERED FROM — a hash of the greeting
text, the voice, the backend and the adapter — per the operator's own design
note: re-render only when the inputs changed, never per message. Changing the
greeting text invalidates every clip; changing one persona's voice
invalidates that persona's clip and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import wave
from pathlib import Path

log = logging.getLogger("callin.voicemail")

VOICEMAIL_DIR = Path(
    os.environ.get("VOICEMAIL_PATH",
                   Path(__file__).parent.parent.parent / "data" / "voicemail")
)

DERIVED_GREETING = (
    "You've reached {station}. {dj} is on the air right now — "
    "leave a request after the beep."
)


def _overrides_path() -> Path:
    return VOICEMAIL_DIR / "overrides.json"


def read_overrides() -> dict:
    try:
        with open(_overrides_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def set_override(persona_id: str, text: str) -> None:
    """One persona's own greeting line, written from the panel. Empty clears
    it back to the shared setting. The cache key includes the text, so an
    edit invalidates exactly that persona's clip and nothing else."""
    overrides = read_overrides()
    pid = str(persona_id)
    if str(text or "").strip():
        overrides[pid] = str(text).strip()[:400]
    else:
        overrides.pop(pid, None)
    VOICEMAIL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _overrides_path().with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=1, sort_keys=True)
    for path, mode in ((VOICEMAIL_DIR, 0o755), (tmp, 0o644)):
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    tmp.replace(_overrides_path())


def greeting_text_for(persona_id: str, cfg: dict, station_name: str,
                      dj_name: str) -> str:
    """This persona's line: their own override, else the shared setting,
    else the derived sentence."""
    own = read_overrides().get(str(persona_id))
    if own:
        return own
    return greeting_text(cfg, station_name, dj_name)


def greeting_text(cfg: dict, station_name: str, dj_name: str) -> str:
    """The words a persona's clip speaks. Blank means derived, per the
    settings invariant — an empty box is the sentence above, not silence."""
    typed = str(cfg.get("voicemail_greeting") or "").strip()
    if typed:
        return typed
    return DERIVED_GREETING.format(station=station_name or "the station",
                                   dj=dj_name or "the DJ")


def render_key(text: str, voice: str, mode: str, adapter: str) -> str:
    """What a clip was rendered from, as a cache key. Everything that changes
    the audio is in here; nothing else is, so an unrelated settings save
    cannot force a re-render."""
    raw = "\x1f".join([text, voice, mode, adapter])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _index_path() -> Path:
    return VOICEMAIL_DIR / "index.json"


def read_index() -> dict:
    try:
        with open(_index_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_index(index: dict) -> None:
    VOICEMAIL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _index_path().with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1, sort_keys=True)
    for path, mode in ((VOICEMAIL_DIR, 0o755), (tmp, 0o644)):
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    tmp.replace(_index_path())


# Spoken after a message lands. Staged with the greeting, same voice — a
# double-beep stands in when it is missing, never silence.
ACK_TEXT = "Got it — I'll pass that on."


def _safe(persona_id: str) -> str:
    return "".join(c for c in str(persona_id)
                   if c.isalnum() or c in "_-")[:64] or "persona"


def clip_path(persona_id: str) -> Path:
    return VOICEMAIL_DIR / f"{_safe(persona_id)}.wav"


def ack_path(persona_id: str) -> Path:
    return VOICEMAIL_DIR / f"{_safe(persona_id)}-ack.wav"


def ack_clip(persona_id: str) -> Path | None:
    own = ack_path(persona_id)
    if own.is_file():
        return own
    for entry in sorted(VOICEMAIL_DIR.glob("*-ack.wav")):
        return entry
    return None


def staged_clip(persona_id: str) -> Path | None:
    """The clip to play for this persona, with the design's fallback order:
    their own clip, else ANY staged clip — a wrong voice beats silence."""
    own = clip_path(persona_id)
    if own.is_file():
        return own
    for entry in sorted(VOICEMAIL_DIR.glob("*.wav")):
        if not entry.name.endswith("-ack.wav"):
            return entry
    return None


def needs_render(persona_id: str, key: str) -> bool:
    entry = read_index().get(str(persona_id)) or {}
    return entry.get("key") != key or not clip_path(persona_id).is_file()


def write_clip(persona_id: str, key: str, text: str, voice: str,
               pcm: bytes, sample_rate: int) -> Path:
    """Store one rendered greeting as a WAV the worker can stream at pickup.

    WAV rather than raw PCM so the file carries its own sample rate — the
    one adapter mistake that is completely silent (see tts_adapter) must not
    be re-committable here by a config change between staging and playback.
    """
    VOICEMAIL_DIR.mkdir(parents=True, exist_ok=True)
    target = clip_path(persona_id)
    tmp = target.with_suffix(".wav.tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm)
    for path, mode in ((VOICEMAIL_DIR, 0o755), (tmp, 0o644)):
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    tmp.replace(target)

    index = read_index()
    index[str(persona_id)] = {
        "key": key, "text": text, "voice": voice,
        "renderedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_index(index)
    return target


def drop_stale(known_persona_ids: list[str]) -> None:
    """Clips for personas the station no longer has are deleted — a roster
    change must not leave a stranger's old voice answering the phone."""
    keep = {clip_path(pid).name for pid in known_persona_ids} | {
        ack_path(pid).name for pid in known_persona_ids}
    index = read_index()
    changed = False
    for entry in list(VOICEMAIL_DIR.glob("*.wav")):
        if entry.name not in keep:
            entry.unlink(missing_ok=True)
            changed = True
    for pid in list(index):
        if clip_path(pid).name not in keep:
            index.pop(pid, None)
            changed = True
    if changed:
        _write_index(index)
