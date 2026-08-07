"""A written record of what actually happened on a call.

Diagnosing a bad call meant reading `heard:` lines — the CALLER's half of the
conversation — and inferring the rest from tracebacks. The DJ's own words, the
tools it reached for, and what the station said back were nowhere. A caller
saying "he wouldn't hang up" and a log showing a TypeError in a background
task are the same event, and it took a stack trace to connect them.

So each call now writes one file: both sides of the conversation, every tool
call with its result, the config it ran under, and anything that failed. The
panel reads these back, which means an operator can review a call without a
shell — and anyone helping them can be handed one file instead of a screenshot
of a chat window.

Cheap on purpose: it's a small JSON per call, capped, and pruned to the last
`KEEP` calls. Nothing here may ever break a live call — every entry point
swallows its own errors.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("callin.agent")

CALLS_DIR = Path(
    os.environ.get("CALLS_PATH", Path(__file__).parent.parent.parent / "data" / "calls")
)

KEEP = 40             # calls kept on disk; a call is a few KB
MAX_TURNS = 400       # a runaway loop must not write an unbounded file
MAX_TEXT = 2000       # one turn, clipped


class CallRecord:
    """Builds the record as the call runs, writes it once at the end."""

    def __init__(self, room: str, persona: dict, cfg: dict, tier: str = "") -> None:
        self.room = room
        self.started = time.time()
        self.data: dict = {
            "room": room,
            "startedAt": _iso(self.started),
            "persona": {"id": persona.get("id"), "name": persona.get("name")},
            "config": {
                "llm": f"{cfg.get('llm_provider')}/{cfg.get('llm_model')}",
                "stt": f"{cfg.get('stt_provider')}/{cfg.get('stt_model')}",
                "tts": cfg.get("tts_mode"),
                # How much the caller typed to get in. Worth writing down
                # separately from the list below: the permissions are what
                # THIS caller had, and without the tier there is nothing in
                # the transcript to explain why they differ from the panel.
                "callerTier": tier or "open",
                # Resolved for this caller, not the stored settings — the
                # session collapses the tiers before anything reads them, so
                # this is the honest list of what the DJ could actually do.
                "permissions": sorted(
                    k for k in cfg
                    if k.startswith(("allow_", "offer_", "shape_")) and cfg[k]
                ),
            },
            "turns": [],
            "tools": [],
            "problems": [],
        }

    # -- during the call ---------------------------------------------------
    def turn(self, who: str, text: str) -> None:
        """who is 'caller' or 'dj'."""
        text = str(text or "").strip()
        if not text or len(self.data["turns"]) >= MAX_TURNS:
            return
        self.data["turns"].append(
            {"t": _iso(time.time()), "who": who, "text": text[:MAX_TEXT]}
        )

    def tool(self, name: str, result: str = "") -> None:
        if len(self.data["tools"]) >= MAX_TURNS:
            return
        self.data["tools"].append(
            {"t": _iso(time.time()), "name": name, "result": str(result or "")[:400]}
        )

    def problem(self, what: str) -> None:
        if len(self.data["problems"]) >= 50:
            return
        self.data["problems"].append({"t": _iso(time.time()), "what": str(what)[:500]})

    # -- at the end --------------------------------------------------------
    def finalise(self, final_turns: list) -> None:
        """Replace the live text with the session's own final transcript.

        The live capture rides `conversation_item_added`, which fires while the
        DJ is still speaking — so recorded lines came out clipped ("Take a
        breath, I've"). The timings from the live events are right and worth
        keeping; only the wording was wrong. So the text comes from the
        session's committed history and the timestamps stay as observed.

        Matched on CONTENT, not on position. Pairing the Nth live event with
        the Nth history entry assumes the two lists describe the same turns in
        the same order, and one history entry with no live event behind it
        silently shifts every later line of that speaker onto the NEXT line's
        timestamp — with the last one appended at call-end time. That is
        exactly what the opening prime did to every call: the written record
        disagreed with the live log about who said what and when, while
        reporting no problem at all. A transcript is the thing you reach for
        when a call went wrong, so it lying quietly is worse than it being
        clipped.

        The live text is a PREFIX of the committed text — that is the whole
        premise here, that events fire mid-sentence — so a prefix match pairs
        them without assuming anything about order or count. Anything that
        does not match keeps its live wording, which is the honest fallback.
        """
        if not final_turns:
            return
        by_who: dict = {}
        for who, text in final_turns:
            by_who.setdefault(who, []).append(str(text))

        used: dict = {}
        for turn in self.data["turns"]:
            who = turn["who"]
            texts = by_who.get(who, [])
            taken = used.setdefault(who, set())
            live = turn["text"].strip()
            for i, candidate in enumerate(texts):
                if i in taken:
                    continue
                if candidate.strip().startswith(live):
                    turn["text"] = candidate[:MAX_TEXT]
                    taken.add(i)
                    break

        # Anything the session knows about that we never saw an event for —
        # a closing line the events missed, typically. Appended in order, and
        # only if it was never matched above.
        for who, texts in by_who.items():
            taken = used.get(who, set())
            for i, extra in enumerate(texts):
                if i not in taken:
                    self.data["turns"].append(
                        {"t": _iso(time.time()), "who": who,
                         "text": extra[:MAX_TEXT]}
                    )

    def write(self, reason: str = "", keep: int = 0) -> None:
        self.data["endedAt"] = _iso(time.time())
        self.data["durationSecs"] = round(time.time() - self.started, 1)
        self.data["endedBecause"] = reason
        self.data["callerTurns"] = sum(
            1 for t in self.data["turns"] if t["who"] == "caller"
        )
        try:
            CALLS_DIR.mkdir(parents=True, exist_ok=True)
            # Explicit, for the same reason settings.save() is: a Synology
            # share creates both directories and files with mode 000, which
            # root ignores and a normal user cannot get past — so the
            # directory a non-root container writes transcripts into would be
            # one it could not then list. A transcript is both sides of a
            # stranger's call, so owner-only rather than world-readable.
            try:
                os.chmod(CALLS_DIR, 0o700)
            except OSError:
                pass
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started))
            path = CALLS_DIR / f"{stamp}-{self.room[-12:]}.json"
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1, ensure_ascii=False)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(path)
            _prune(keep or KEEP)
            log.info("call transcript written: %s", path.name)
        except Exception as e:
            # A missing transcript is a nuisance; a crash here would cost the
            # on-air handoff that runs after it.
            log.warning("could not write the call transcript: %s", e)


def _iso(ts: float) -> str:
    """An instant, with the offset that makes it one.

    This used to be naive container-local time. The container runs in UTC, so
    an operator four hours west read every call record four hours off — and
    nothing downstream could correct it, because a bare "2026-08-05T03:55:35"
    doesn't say which zone it's in. Now it carries +00:00 and the panel renders
    it in whatever zone the reader is actually sitting in.
    """
    return datetime.datetime.fromtimestamp(
        ts, datetime.timezone.utc
    ).isoformat(timespec="seconds")


def _prune(keep: int = KEEP) -> None:
    # `record_keep` is how long a caller's words stay on the operator's disk,
    # so it is a real setting rather than a constant. 0 would mean "delete
    # everything", which is what turning recording off is for — treat it as
    # the default instead of as an instruction.
    keep = max(1, int(keep or KEEP))
    files = sorted(CALLS_DIR.glob("*.json"))
    for old in files[:-keep]:
        old.unlink(missing_ok=True)


def clear() -> int:
    """Delete every stored call record; returns how many went.

    Unlike `_prune`, which trims to `record_keep` as each call ends, this is
    the operator saying "these are stale, get rid of them" — the transcripts
    are a caller's words, so being able to remove them on demand rather than
    waiting for enough new calls to age them out is the point.
    """
    gone = 0
    try:
        for path in CALLS_DIR.glob("*.json"):
            try:
                path.unlink()
                gone += 1
            except OSError:
                continue
    except OSError:
        pass
    return gone


def rate(room: str, rating: str) -> bool:
    """Attach the caller's own verdict to that call's record.

    Written by the TOKEN SERVER, not the worker — the two are separate
    containers sharing this directory, and the caller's thumbs arrive over
    HTTP after the worker has already finished and gone. So this merges into
    a file somebody else wrote, and must not assume it is there yet: the
    caller can click before the worker's shutdown callback has run. The
    caller retries; this just reports whether it found anything.

    Only the rating is stored. Not who, not when they clicked, not a comment
    box — the record is already a transcript of a stranger's conversation,
    and one character is all that is needed to find the bad ones.
    """
    rating = str(rating or "").strip().lower()
    if rating not in ("up", "down"):
        return False
    # The room is `callin-<12 hex>` and the filename ends in those 12
    # characters — matching on the suffix rather than reconstructing the
    # timestamp, which this side does not know.
    tail = str(room or "")[-12:]
    if len(tail) < 6:
        return False
    try:
        matches = sorted(CALLS_DIR.glob(f"*-{tail}.json"), reverse=True)
    except OSError:
        return False
    if not matches:
        return False
    path = matches[0]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["rating"] = rating
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)
        log.info("call %s rated %s by the caller", path.stem, rating)
        return True
    except (OSError, json.JSONDecodeError) as e:
        log.warning("could not store the caller's rating: %s", e)
        return False


def recent(limit: int = 20) -> list[dict]:
    """Newest first, for the panel."""
    out = []
    try:
        for path in sorted(CALLS_DIR.glob("*.json"), reverse=True)[:limit]:
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                d["id"] = path.stem
                out.append(d)
            except (OSError, json.JSONDecodeError):
                continue
    except OSError:
        pass
    return out
