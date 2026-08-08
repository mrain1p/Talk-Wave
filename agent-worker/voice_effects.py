"""Per-DJ voice-effect overrides — the greeting-overrides pattern, again.

The effect itself is applied in the CALLER'S browser (call.js builds the
WebAudio graph); all the server holds is which colour rides /live while a
given persona is on air. One small JSON file, persona id -> effect kind.
Absent means "the shared Voice effects setting decides", which is why an
empty pick CLEARS rather than storing an empty string — the settings
invariant, kept here too.

Intensity stays global on purpose: the dial is the operator's taste for
how loud a costume may be, not part of any one character.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _path() -> Path:
    """Read the env each call, not at import: the tests point every writable
    path at a temp dir AFTER modules are imported, and a path baked in at
    import time is how a test scribbles on real data."""
    return Path(os.environ.get(
        "VOICE_FX_PATH",
        Path(__file__).parent.parent / "data" / "voice-effects.json"))


def read() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def effect_for(persona_id: str) -> str:
    """This persona's colour, or "" for "the shared setting decides"."""
    return str(read().get(str(persona_id)) or "")


def set_effect(persona_id: str, effect: str) -> None:
    """Empty clears back to the shared setting. Which kinds are legal is the
    API layer's check, against the same CHOICES list the dropdown offers —
    this module stores, it does not adjudicate."""
    data = read()
    pid = str(persona_id)
    if str(effect or "").strip():
        data[pid] = str(effect).strip()
    else:
        data.pop(pid, None)
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    # Synology creates files with mode 000 and root never noticed; the
    # non-root container did. Same self-chmod every store here performs.
    for path, mode in ((target.parent, 0o755), (tmp, 0o644)):
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    tmp.replace(target)
