"""The operator's own shelf of subjects, each aimed at whichever DJs suit it.

A store rather than a setting, for the same reason the voicemail greetings are
one: each entry carries per-persona assignment, and a settings field is a flat
string. Lives beside the other state in data/, written atomically, read fresh.

Selection is least-recently-used among the ones this DJ may use, not a rotating
index. An index cannot survive entries being added or deleted in the middle of
a list, and "why has it not used the third one yet" is a question nobody should
have to answer.
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


# What a fresh install finds on the shelf. Three, not thirty: enough to press
# the button on day one and hear what the feature actually sounds like, few
# enough that they read as examples to replace rather than a library to curate.
#
# All three are anchored in something that really exists — a record, a habit, a
# genuine opinion — because that is what survives somebody engaging seriously.
# Invented specifics ("that argument with the execs upstairs") crack the moment
# a listener asks which exec.
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
    items = [
        {"id": secrets.token_hex(6), "text": text, "personas": [],
         "used": 0, "last_used": "", "added": _now(), "starter": True}
        for text in STARTERS
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
    return [i for i in (items or []) if isinstance(i, dict)]


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


def add(text: str, personas: list | None = None) -> dict:
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
    }
    items = read()
    items.append(item)
    _write(items)
    return item


def update(premise_id: str, text: str | None = None,
           personas: list | None = None) -> dict:
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
    """The ones this DJ may use — its own, plus the unassigned."""
    pid = str(persona_id or "")
    out = []
    for item in read():
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
    """The least recently used subject this DJ may put up, and mark it used.

    Never-used entries come first (blank `last_used` sorts before any date), so
    a freshly added premise is the next one out — which is what an operator who
    just typed it expects.
    """
    mine = for_persona(persona_id)
    if not mine:
        return {}
    chosen = min(mine, key=lambda i: (str(i.get("last_used") or ""),
                                      int(i.get("used") or 0)))
    items = read()
    for item in items:
        if str(item.get("id")) == str(chosen.get("id")):
            item["used"] = int(item.get("used") or 0) + 1
            item["last_used"] = _now()
            _write(items)
            return dict(item)
    return dict(chosen)
