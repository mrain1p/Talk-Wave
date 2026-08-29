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

from jsonstore import write_atomic

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
# When nobody is on air the machine answers as the STATION, in the operator's
# configured default voice — a named DJ who is not actually there is a small
# lie the caller can hear.
DERIVED_STATION_GREETING = (
    "You've reached {station}. Leave a request after the beep."
)
# The station-default clip's slot in the cache, beside the persona ids.
STATION_ID = "_station"


class _Blank(dict):
    """format_map that renders unknown or empty placeholders as nothing — a
    template writer's typo must not crash a greeting into the beep."""

    def __missing__(self, key):
        return ""


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
    write_atomic(_overrides_path(), overrides, dir_mode=0o700,
                 indent=1, sort_keys=True)


def greeting_text_for(persona_id: str, cfg: dict, station_name: str,
                      dj_name: str, show_name: str = "") -> str:
    """This persona's line: their own override, else the shared setting,
    else the derived sentence. Overrides take the placeholders too."""
    own = read_overrides().get(str(persona_id))
    if own:
        try:
            base = " ".join(own.format_map(_Blank(
                station=station_name or "the station",
                dj=dj_name, show=show_name)).split())
        except (ValueError, IndexError):
            base = own
    else:
        base = greeting_text(cfg, station_name, dj_name, show_name)
    return base + _open_lines_clause(persona_id, cfg, show_name)


def _open_lines_clause(persona_id: str, cfg: dict, show_name: str) -> str:
    """The machine names tonight's subject while a line stands.

    Appended to the TEXT, which means it flows through `render_key` and the
    staged clip is re-rendered once per open line rather than per caller —
    the cache keeps working, it just keys on a greeting that now mentions the
    topic. When the line closes the text reverts and the clip that was already
    there is reused. Empty in every other case, so a station that never turns
    Open Lines on renders exactly the clips it always did.
    """
    try:
        from openlines import prompt as open_lines

        return open_lines.voicemail_clause(
            cfg, {"id": persona_id}, show_name)
    except Exception:                                          # noqa: BLE001
        # A greeting is the one thing that must never fail to exist: the
        # machine picking up in silence is how a caller learns the station is
        # broken. Any trouble here simply costs the extra sentence.
        return ""


def greeting_text(cfg: dict, station_name: str, dj_name: str,
                  show_name: str = "") -> str:
    """The words a clip speaks. Blank means derived, per the settings
    invariant. A typed greeting may use {station}, {dj} and {show} — filled
    per persona at staging time, and an empty or unknown placeholder simply
    disappears. With no DJ to name, the derived line speaks as the station.
    """
    fields = _Blank(station=station_name or "the station",
                    dj=dj_name, show=show_name)
    typed = str(cfg.get("voicemail_greeting") or "").strip()
    template = typed or (DERIVED_GREETING if dj_name
                         else DERIVED_STATION_GREETING)
    try:
        out = template.format_map(fields)
    except (ValueError, IndexError):
        out = template
    return " ".join(out.split())


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
    write_atomic(_index_path(), index, dir_mode=0o700,
                 indent=1, sort_keys=True)


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
    """The clip to play, in fallback order: this persona's own, then the
    STATION default (the honest voice when nobody is on air), then any staged
    clip at all — a wrong voice beats silence."""
    for candidate in (clip_path(persona_id), clip_path(STATION_ID)):
        if candidate.is_file():
            return candidate
    # Random rather than alphabetical: with no station clip staged, "always
    # the DJ whose name sorts first" made one voice the accidental station
    # default. Operator's call.
    import random

    others = [entry for entry in VOICEMAIL_DIR.glob("*.wav")
              if not entry.name.endswith("-ack.wav")]
    return random.choice(others) if others else None


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


# One render at a time: ensure_clip below spends the operator's TTS money,
# and two studio opens racing must not both pay for the same clip.
_render_lock = None


def _lock():
    global _render_lock
    import asyncio

    if _render_lock is None:
        _render_lock = asyncio.Lock()
    return _render_lock


async def ensure_clip(persona: dict, dj: dict, cfg: dict):
    """A greeting clip for this persona, staged or rendered on demand.

    The staged clip answers first. With nothing staged the line is rendered
    HERE, through the exact machinery staging uses, and cached in the same
    index — the classic machine speaks the greeting live at every pickup,
    and the studio going silent instead was heard on the operator's own
    phone (ring, beep, no voice; 2026-08-17). The cache bounds the spend: a
    stranger opening the studio can cost at most one render per persona,
    never one per visit. Returns a Path, or None when the render failed.
    """
    pid = str((persona or {}).get("id") or "")
    if pid in ("", "default"):
        pid = STATION_ID
    clip = staged_clip(pid)
    if clip:
        return clip

    from station_config import StationConfig
    from tts_adapter import AdapterTTS, resolve_adapter

    station_name = str((dj or {}).get("station") or "")
    show_name = str((dj or {}).get("show") or (dj or {}).get("showName") or "")
    name = str((persona or {}).get("name") or "")
    sc = StationConfig(base_url=cfg.get("station_base_url"))
    try:
        if pid == STATION_ID:
            voice = str(cfg.get("tts_voice") or "")
            text = greeting_text_for(pid, cfg, station_name, "", show_name)
        else:
            voice = await sc.voice_for(pid)
            text = greeting_text_for(pid, cfg, station_name, name, show_name)
    finally:
        await sc.aclose()

    key = render_key(text, voice, str(cfg.get("tts_mode", "")),
                     str(cfg.get("tts_adapter") or ""))
    async with _lock():
        if needs_render(pid, key):
            tts = AdapterTTS(
                voice=voice,
                base_url=cfg.get("tts_base_url") or "",
                adapter_path=resolve_adapter(cfg.get("tts_adapter")),
                model=cfg.get("tts_model") or "",
                mode=str(cfg.get("tts_mode", "cloud")),
            )
            pcm = bytearray()
            try:
                async for ev in tts.synthesize(text):
                    pcm.extend(ev.frame.data.tobytes())
            except Exception:                                 # noqa: BLE001
                return None
            finally:
                await tts.aclose()
            if not pcm:
                return None
            write_clip(pid, key, text, voice, bytes(pcm), tts.sample_rate)
    return staged_clip(pid)
