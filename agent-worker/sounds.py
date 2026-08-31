"""Bundled call sounds, as files on disk.

Until now there was nowhere in the repo to put an audio file. Uploads worked
and synthesis worked, so the product ran fine with zero assets — but shipping
a *default* ring meant writing JavaScript, because the only non-uploaded
sounds were oscillator code in the widget. That made a sound pack a code
change, which is the wrong shape for something an operator should be able to
drop in.

    assets/sounds/<pack>/<kind>.<ext>       bundled with the image
    data/sounds/<file>                      operator uploads, unchanged

Resolution order, per sound: **uploaded or configured URL -> bundled asset ->
synthesized in the browser.** The synthesis stays. That the product works with
no audio files at all is worth protecting, and it is also the reason nothing
here needs to exist: an empty `assets/sounds/` behaves exactly as before.

Adding a pack is a folder. Drop `assets/sounds/vintage/ring.mp3` and "Vintage"
appears in the Sound set dropdown, using the bundled file where one exists and
falling back to the synthesized tones for any sound the folder doesn't cover —
so a pack can be one file or all five.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("callin.sounds")

# Same layout as the widget: repo-root/assets in a checkout, /assets in the
# image, because the Dockerfile copies from the repo root. The override exists
# so a deployment can bind-mount packs without rebuilding, and so tests can
# point somewhere disposable.
ASSETS_DIR = Path(
    os.environ.get("SOUND_ASSETS_PATH", Path(__file__).parent.parent / "assets" / "sounds")
)

# The five moments a call makes a noise. Kept here rather than in settings so
# the pack loader and the settings schema cannot drift.
KINDS = ("ring", "pickup", "hold", "hangup", "failed")

# Browser-playable, in preference order when a folder has several encodings of
# the same sound. mp3 first: it is the one every browser plays.
EXTENSIONS = (".mp3", ".m4a", ".aac", ".ogg", ".wav", ".webm")

# The packs the widget can synthesize with no files present. A folder of the
# same name doesn't create a new pack — it supplies files for this one, sound
# by sound, and anything it leaves out stays synthesized.
SYNTHESIZED = {
    "classic": "Exchange — telephone network tones",
    "phone": "Handset — a real phone in a room",
    "arcade": "Arcade — 8-bit cabinet bleeps",
    "space": "Starship — a hailing console",
}

URL_PREFIX = "/pack-sounds"


def _label_for(folder: Path) -> str:
    """A pack's display name: `label.txt` if the folder says, else its name."""
    try:
        text = (folder / "label.txt").read_text(encoding="utf-8").strip()
        if text:
            return text[:60]
    except OSError:
        pass
    return folder.name.replace("-", " ").replace("_", " ").title()


def _pack_dirs() -> list[Path]:
    try:
        return sorted(p for p in ASSETS_DIR.iterdir() if p.is_dir())
    except OSError:
        return []


def packs() -> list[tuple[str, str]]:
    """(id, label) for the Sound set dropdown — built-ins, then any folders.

    The built-ins keep their curated labels even when a folder supplies files
    for them, because "Exchange" is still what the operator chose.
    """
    out = [(pid, label) for pid, label in SYNTHESIZED.items()]
    # The library folder is the SHELF's home (loose clips, below), not a
    # pack — without this it leaked into the dropdown as a "Library" set
    # whose every sound silently fell back to Exchange.
    known = set(SYNTHESIZED) | {LIBRARY_DIR_NAME}
    for folder in _pack_dirs():
        if folder.name not in known:
            out.append((folder.name, _label_for(folder)))
    return out


def file_for(pack: str, kind: str) -> Path | None:
    """The bundled file for one sound of one pack, if the folder has one."""
    if kind not in KINDS or not pack:
        return None
    folder = ASSETS_DIR / pack
    # Never let a settings value walk out of the assets directory.
    try:
        if folder.resolve().parent != ASSETS_DIR.resolve():
            return None
    except OSError:
        return None
    for ext in EXTENSIONS:
        candidate = folder / f"{kind}{ext}"
        if candidate.is_file():
            return candidate
    return None


# --- the sound library ------------------------------------------------------
# Loose bundled sounds, distinct from packs: a pack answers "what does RING
# sound like", the library is a shelf of clips an operator assigns to any
# slot. assets/sounds/library/*.wav, described by catalog.json beside them.
# WAV only, by policy: every bundled sound must be playable by the SERVER
# (the voicemail beep path), and WAV is the one format both ends read.
LIBRARY_DIR_NAME = "library"


def library_dir() -> Path:
    return ASSETS_DIR / LIBRARY_DIR_NAME


def library() -> list[dict]:
    """Every bundled clip: name, label, category, seconds. Sorted by name so
    the shelf is stable across restarts."""
    import json
    import wave

    folder = library_dir()
    if not folder.is_dir():
        return []
    try:
        catalog = json.loads((folder / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        catalog = {}
    out = []
    for f in sorted(folder.glob("*.wav")):
        meta = catalog.get(f.name) or {}
        secs = None
        try:
            with wave.open(str(f), "rb") as w:
                secs = round(w.getnframes() / float(w.getframerate() or 1), 1)
        except (OSError, wave.Error):
            pass
        out.append({
            "name": f.name,
            "label": str(meta.get("label") or f.stem.replace("-", " ")),
            "category": str(meta.get("category") or "misc"),
            # Which shipped set the clip belongs to, if any — "Dial-up",
            # "Modern"… Blank is a loose clip; the shelf filters on both.
            "pack": str(meta.get("pack") or ""),
            # The slot this clip was MADE for, when it was made for one —
            # a busy signal is a can't-connect whatever the operator does
            # with it. The shelf shows it where the used-for chips go.
            "suggests": str(meta.get("suggests") or ""),
            "secs": secs,
            "url": f"/sound-lib/{f.name}",
        })
    return out


def asset_url(pack: str, kind: str) -> str:
    """What the browser should fetch, or "" to let it synthesize."""
    found = file_for(pack, kind)
    return f"{URL_PREFIX}/{pack}/{found.name}" if found else ""


def assets_for(pack: str) -> dict[str, str]:
    """Every bundled sound this pack provides. Missing ones are absent, not
    blank, so the panel can show what a pack actually covers."""
    return {k: url for k in KINDS if (url := asset_url(pack, k))}
