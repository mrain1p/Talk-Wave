"""The open line itself — one record, on disk, shared by both containers.

This is the first Talk Wave object that outlives a single session. Everything
else re-reads live state per call and expires for free; an open line has to
survive between them, and the worker (calls) and the web container (panel,
chat, voicemail, the director loop) are separate processes that share only the
bind-mounted `data/`. So: one small JSON file, written atomically, read fresh
on every use. No in-memory cache — a cached open line in the worker would
outlive the operator pressing Close in the panel, and the panel is the one
place an operator expects to win.

One line at a time, by design. Opening a new one replaces whatever was there.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("talkwave.openlines")

# parent.parent.PARENT — this module is a directory deeper than settings.py,
# and the walk has to reach the same place. `parent.parent` from here lands on
# agent-worker/ (which is /app in the image), where there is no data dir at
# all: the deployed stack mounts it at /data, one level up. The feature could
# not write its record on a real deployment and the whole of it was dead,
# while every local test passed because they all override STATE_PATH.
# TestTheRecordLandsWhereTheOtherStateDoes pins it against settings.py now,
# which is the only comparison that could have caught this.
STATE_PATH = Path(
    os.environ.get("OPEN_LINE_PATH",
                   Path(__file__).parent.parent.parent / "data" / "open-line.json"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def write(record: dict | None) -> None:
    """Replace the record, or clear it when `None`.

    Atomic: the worker may be reading this file while the web container writes
    it, and a half-written open line reads as a crash on a live call.
    """
    path = STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if record is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("could not clear the open line: %s", e)
        return
    tmp = path.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=1, sort_keys=True)
        try:
            os.chmod(tmp, 0o644)
        except OSError:
            pass
        tmp.replace(path)
    except OSError as e:
        log.warning("could not write the open line: %s", e)


def read_raw() -> dict:
    """Whatever is on disk, expiry ignored. For the panel and the director."""
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("unreadable open line, treating as none: %s", e)
        return {}


def seconds_left(record: dict) -> float:
    expires = _parse(record.get("expires_at"))
    if not expires:
        return 0.0
    return max(0.0, (expires - _now()).total_seconds())


def is_live(record: dict, persona_id: str = "", show_name: str = "") -> bool:
    """Is this record an open line RIGHT NOW, for this DJ and this show?

    Three ways it stops being live, and they are all deliberate:

    - closed by hand from the panel;
    - past its duration;
    - the persona or the show changed underneath it. A premise opened by one
      DJ must not survive into another's show — the next persona would be
      defending an argument it never made, to someone who heard the first one
      make it. Same rule the rest of the briefing already follows by re-reading
      live state per session; this object is the one that has to check.
    """
    if not record or record.get("closed"):
        return False
    if seconds_left(record) <= 0:
        return False
    if persona_id and str(record.get("persona_id") or "") != str(persona_id):
        return False
    if show_name and str(record.get("show") or "") != str(show_name):
        return False
    return True


def current(persona_id: str = "", show_name: str = "") -> dict:
    """The live open line, or `{}`. The read every consumer should use."""
    record = read_raw()
    return record if is_live(record, persona_id, show_name) else {}


def build(premise: str, spoken: str, persona: dict, show_name: str,
          minutes: int, source: str, reminder_minutes: int,
          reminder_max: int) -> dict:
    """A fresh record. `spoken` is what actually aired — see the note in
    `air.announce` for why the direction we sent is the wrong thing to keep."""
    opened = _now()
    minutes = max(1, int(minutes or 1))
    record = {
        "premise": premise,
        "spoken": spoken,
        "opened_at": _iso(opened),
        "expires_at": _iso(opened + timedelta(minutes=minutes)),
        "persona_id": str(persona.get("id") or ""),
        "persona_name": str(persona.get("name") or ""),
        "show": show_name,
        "source": source,
        "reminders_sent": 0,
        # Follow-ups: how many have aired, and which conversations have been
        # considered (aired or not) so none is reported to the room twice.
        "followups_sent": 0,
        "followed_up": [],
        "reminder_max": max(0, int(reminder_max or 0)),
        "next_reminder_at": None,
        "closed": False,
    }
    if reminder_minutes > 0 and record["reminder_max"] > 0:
        record["next_reminder_at"] = _iso(
            opened + timedelta(minutes=int(reminder_minutes)))
    return record


def note_reminder(record: dict, reminder_minutes: int) -> dict:
    """Count a reminder that has just aired, and schedule the next one — or
    stop, when the cap is spent or the next one would fall past the close."""
    record = dict(record)
    record["reminders_sent"] = int(record.get("reminders_sent") or 0) + 1
    record["next_reminder_at"] = None
    if (reminder_minutes > 0
            and record["reminders_sent"] < int(record.get("reminder_max") or 0)):
        nxt = _now() + timedelta(minutes=int(reminder_minutes))
        expires = _parse(record.get("expires_at"))
        # A reminder in the last stretch is an invitation nobody can take up.
        if expires and nxt < expires:
            record["next_reminder_at"] = _iso(nxt)
    return record


def note_followup(record: dict, conversation_id: str) -> dict:
    """Count a follow-up that has just aired, and remember which conversation
    it was about.

    The id list is the latch. Without it a restart between ticks would report
    the same conversation to the room a second time, and the audience would
    hear the DJ discover the same contribution twice."""
    record = dict(record)
    record["followups_sent"] = int(record.get("followups_sent") or 0) + 1
    seen = [str(i) for i in (record.get("followed_up") or [])]
    if conversation_id and str(conversation_id) not in seen:
        seen.append(str(conversation_id))
    record["followed_up"] = seen
    return record


def note_seen(record: dict, conversation_id: str) -> dict:
    """Mark a conversation considered without airing anything.

    Most conversations while a line is open are requests, and a request is
    not a contribution. Marking them seen is what stops the model being asked
    about the same hello every sixty seconds for an hour."""
    record = dict(record)
    seen = [str(i) for i in (record.get("followed_up") or [])]
    if conversation_id and str(conversation_id) not in seen:
        seen.append(str(conversation_id))
    record["followed_up"] = seen
    return record


def close(reason: str = "operator") -> dict:
    """Mark the line closed and keep it on disk, so the director can air a
    sign-off exactly once and the panel can say what happened."""
    record = read_raw()
    if not record or record.get("closed"):
        return {}
    record["closed"] = True
    record["closed_at"] = _iso(_now())
    record["closed_reason"] = reason
    write(record)
    return record
