"""What this booth has done to the station lately, across every call.

The day-log — decision 3 of the conversation-engine review, scoped by the
operator: station-STATE actions only, with attribution by door tier and
time, and never a word of caller content. It exists because of one real
opening line: "did you recently cancel my queue?", answered with "I haven't
touched anything since we started chatting" — per-call true and globally
evasive, because each call's memory starts at pickup and nothing durable
said what the booth had done an hour before. This file is that answer:
a caller asking about an earlier call gets a lookup, not an apology.

Scope, and the line it must never cross: entries carry WHEN, WHICH DOOR
(open / guest / admin — a tier, never an identity), WHAT KIND of action,
and the station-side label the action already wears in the receipts (a
track title, a show name). No transcript text, no caller names, no room
ids. The labels come from CallActions.note, which already truncates them
for the receipt cards; this log stores nothing the operator's own calls
list doesn't show more of.

Mechanics: one JSON file beside the other data stores, pruned on every
write to 48 hours and 400 entries — a day-log, not an archive. Every write
is wrapped so a full disk or a bad file can never cost a caller their
action: the action already happened; this is bookkeeping about it.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("callin.daylog")

#: The action kinds worth remembering across calls — the ones that CHANGE
#: what the station will do. Reads, announcements, segments and likes stay
#: out: the question this answers is "who moved the machinery", not "who
#: spoke". This MUST track the kinds the tools actually emit (CallActions.note
#: -> daylog.note): it had drifted to two dead strings ("queue", "allowed
#: again" — nothing emits either) while missing "album", "mix" and
#: "never-play lifted", so a caller who queued a whole album was told, on
#: ringing back, that nothing was queued — the exact per-call evasion this
#: module was built to kill (Casino night, 2026-08-26; caught by the
#: 2026-08-28 top-down review). test_the_daylog_kinds_match_the_tools pins it.
KINDS = frozenset((
    "request", "clear", "cancel", "skip", "album", "mix", "takeover",
    "takeover lifted", "genre lock", "genre lock lifted", "never-play",
    "never-play lifted",
    # Widened 2026-09-01 for the player's Requests tab (the operator wants
    # the whole receipt printer: "favorited, skipped, queued, dj change,
    # on air announce") — every one an action kind a tool actually emits.
    "like", "unlike", "announcement", "skill", "segment",
))

#: Kinds whose DETAIL is the caller's own words (a shoutout message is a
#: dedication naming a person). The KIND is a station fact and stays; the
#: words never reach a store that outlives the call — the same covenant
#: test_a_request_fallback_logs_no_caller_words pins for requests.
MUTE_DETAIL = frozenset(("announcement",))

MAX_ENTRIES = 400
MAX_AGE_SECS = 48 * 3600.0


def _path() -> Path:
    return Path(os.environ.get(
        "DAYLOG_PATH",
        Path(__file__).parent.parent.parent / "data" / "day-log.json"))


def _load() -> list:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:                                          # noqa: BLE001
        return []


def note(kind: str, label: str, tier: str = "") -> None:
    """One station-changing action, remembered. Silent on any failure.

    No tier, no entry: the tier is the attribution, and the builders that
    construct a CallActions without one (the panel's preview, unit tests)
    are exactly the ones whose actions must never look like a caller's.
    """
    k = str(kind or "").strip()
    if k not in KINDS or not str(tier or "").strip():
        return
    try:
        entries = _load()
        entries.append({
            "t": time.time(),
            "tier": str(tier or "open")[:8],
            "kind": k,
            "what": "" if k in MUTE_DETAIL else str(label or "")[:80],
        })
        cutoff = time.time() - MAX_AGE_SECS
        entries = [e for e in entries
                   if isinstance(e, dict)
                   and float(e.get("t") or 0) >= cutoff][-MAX_ENTRIES:]
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:                                     # noqa: BLE001
        log.debug("day-log write skipped: %s", e)


def recent(limit: int = 12) -> list:
    """Newest first, pruned to the window, each entry display-ready."""
    cutoff = time.time() - MAX_AGE_SECS
    out = []
    for e in reversed(_load()):
        if not isinstance(e, dict) or float(e.get("t") or 0) < cutoff:
            continue
        out.append(e)
        if len(out) >= max(1, int(limit)):
            break
    return out


def as_lines(limit: int = 12) -> str:
    """The log as the DJ reads it — ages, doors, kinds, labels.

    Empty string when there is nothing, so the tool above it can say what an
    empty log MEANS instead of showing a blank.
    """
    now = time.time()
    lines = []
    for e in recent(limit):
        age = max(0, now - float(e.get("t") or now))
        when = (f"{int(age // 3600)}h ago" if age >= 3600
                else f"{max(1, int(age // 60))}m ago")
        door = {"open": "a caller", "guest": "a guest-code caller",
                "admin": "the operator's line"}.get(
                    str(e.get("tier") or ""), "a caller")
        what = f" — {e['what']}" if e.get("what") else ""
        lines.append(f"{when}: {e.get('kind')}{what}  ({door})")
    return "\n".join(lines)
