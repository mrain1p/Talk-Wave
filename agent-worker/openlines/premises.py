"""The operator's own shelf of topics, each aimed at whichever DJs suit it.

A store rather than a setting, for the same reason the voicemail greetings are
one: each entry carries per-persona assignment and an enabled flag, and a
settings field is a flat string. Lives beside the other state in data/,
written atomically, read fresh.

A topic is in the draw when it is ENABLED and this DJ may use it — its own or
unassigned — and the pick among those is RANDOM. It was least-recently-used
first, which made the order a queue: with a shelf of recurring bits you could
tell what was coming, which is the opposite of what a rotating segment wants.
The use count and last-aired are still kept, they just no longer decide.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("talkwave.openlines")

PREMISES_PATH = Path(
    # parent.parent.PARENT — a directory deeper than settings.py, so the walk
    # needs the extra step to reach the mounted data/. See state.py.
    os.environ.get("OPEN_LINE_PREMISES_PATH",
                   Path(__file__).parent.parent.parent / "data"
                   / "open-line-premises.json"))

TEXT_BUDGET = 400


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# What a fresh install finds on the shelf: a few one-off subjects, and the
# recurring BITS. A bit is a format ("Would You Rather") rather than a subject;
# it is resolved into tonight's specific instance before anything airs, because
# handing the booth the name of a game got "the suits want me to push something
# called a Quiz question" on air, and a listener told there is a game has not
# been invited into one.
STARTERS = (
    "a record you skipped for years that finally won you over, and what changed",
    "whether an album still means anything, or whether everyone just plays songs now",
    "the song you would put on to make a stranger understand where you grew up",
)


def _seed() -> list[dict]:
    """Write the starters once, the first time the shelf is asked for.

    Seeded on read rather than shipped as a file so an operator who clears the
    shelf deliberately gets an EMPTY shelf — the file exists after the first
    read, so "no file" only ever means "never opened this feature".
    """
    from openlines.quiz import FORMATS

    items = [
        {"id": secrets.token_hex(6), "text": text, "personas": [],
         "used": 0, "last_used": "", "added": _now(), "starter": True,
         "format": is_format}
        for text, is_format in ([(t, False) for t in STARTERS]
                                + [(f, True) for f in FORMATS])
    ]
    _write(items)
    return items


def read() -> list[dict]:
    """Every premise on the shelf, in the order the operator put them."""
    try:
        with open(PREMISES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return _seed()
    except (OSError, ValueError) as e:
        log.warning("unreadable premise shelf, treating as empty: %s", e)
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return _mark_shipped([i for i in (items or []) if isinstance(i, dict)])


def _mark_shipped(items: list[dict]) -> list[dict]:
    """Backfill `starter` on rows this repo ships the text of.

    The panel chips a row "built in" from this flag, and the operator found
    their shelf reading "11 yours, 3 built in" when all fourteen came out of
    the box: the recurring BITS were added to `_seed` after their shelf had
    already been written, so those rows carry no flag and read as the
    operator's own work. Miscounting authorship is not cosmetic here — a
    "built in" row is one they can expect back if they clear the shelf, and a
    row of theirs is not.

    Matched on exact shipped TEXT, so it can only ever recognise a string this
    repo authored; anything the operator wrote, including an edit of a
    starter, stays theirs. Read-only — the file is rewritten on the next
    ordinary write, and a stale file simply gets marked again on the next read.
    """
    from openlines.quiz import FORMATS

    shipped = {t: False for t in STARTERS}
    shipped.update({f: True for f in FORMATS})
    for item in items:
        if item.get("starter"):
            continue
        is_format = shipped.get(str(item.get("text") or "").strip())
        if is_format is not None:
            item["starter"] = True
            item.setdefault("format", is_format)
    return items


def _write(items: list[dict]) -> None:
    PREMISES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PREMISES_PATH.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, indent=1)
        try:
            os.chmod(tmp, 0o644)
        except OSError:
            pass
        tmp.replace(PREMISES_PATH)
    except OSError as e:
        log.warning("could not write the premise shelf: %s", e)


def add(text: str, personas: list | None = None,
        is_format: bool = False) -> dict:
    text = " ".join(str(text or "").split())[:TEXT_BUDGET].strip()
    if not text:
        return {}
    item = {
        "id": secrets.token_hex(6),
        "text": text,
        # Empty = any DJ. Named = only those, which is the whole point of the
        # shelf: an argument that suits one persona is wrong in another's mouth.
        "personas": [str(p) for p in (personas or []) if str(p).strip()],
        "used": 0,
        "last_used": "",
        "added": _now(),
        # Disabled topics stay on the shelf and out of the draw: an operator
        # who is done with one for a while should not have to delete it and
        # retype it later.
        "enabled": True,
        # A BIT rather than a subject: resolved into tonight's specific
        # instance before it airs, instead of being read out as a label.
        "format": bool(is_format),
    }
    items = read()
    items.append(item)
    _write(items)
    return item


def update(premise_id: str, text: str | None = None,
           personas: list | None = None, enabled: bool | None = None) -> dict:
    items = read()
    for item in items:
        if str(item.get("id")) != str(premise_id):
            continue
        if text is not None:
            cleaned = " ".join(str(text).split())[:TEXT_BUDGET].strip()
            if cleaned:
                item["text"] = cleaned
        if personas is not None:
            item["personas"] = [str(p) for p in personas if str(p).strip()]
        if enabled is not None:
            item["enabled"] = bool(enabled)
        _write(items)
        return item
    return {}


def remove(premise_id: str) -> bool:
    items = read()
    kept = [i for i in items if str(i.get("id")) != str(premise_id)]
    if len(kept) == len(items):
        return False
    _write(kept)
    return True


def for_persona(persona_id: str) -> list[dict]:
    """The ones this DJ may use — its own, plus the unassigned. Disabled
    topics are never in the draw."""
    pid = str(persona_id or "")
    out = []
    for item in read():
        # Absent means enabled: entries written before the flag existed are
        # not silently dropped out of the draw.
        if item.get("enabled") is False:
            continue
        who = item.get("personas") or []
        if not who or pid in [str(p) for p in who]:
            out.append(item)
    return out


def take_one(premise_id: str) -> dict:
    """Mark ONE named subject used, and return it. `{}` if it has gone.

    Separate from take_next because the dashboard's dropdown is a choice, not
    a rotation — an operator who picked the third one wants the third one."""
    items = read()
    for item in items:
        if str(item.get("id")) != str(premise_id):
            continue
        item["used"] = int(item.get("used") or 0) + 1
        item["last_used"] = _now()
        _write(items)
        return dict(item)
    return {}


def take_next(persona_id: str) -> dict:
    """One of the topics this DJ may use, AT RANDOM, marked used.

    Random rather than least-recently-used (operator, 2026-08-22). LRU made
    the order a queue: with a shelf of recurring bits you could tell what was
    coming next, which is the opposite of what a rotating segment wants. The
    use count and last-aired are still kept — they are worth reading — they
    just no longer decide.
    """
    mine = for_persona(persona_id)
    if not mine:
        return {}
    chosen = secrets.choice(mine)
    items = read()
    for item in items:
        if str(item.get("id")) == str(chosen.get("id")):
            item["used"] = int(item.get("used") or 0) + 1
            item["last_used"] = _now()
            _write(items)
            return dict(item)
    return dict(chosen)
