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
import re
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

    def __init__(self, room: str, persona: dict, cfg: dict, tier: str = "",
                 started: float = 0.0) -> None:
        self.room = room
        # The CALL's clock, not this object's. The record is built once the
        # persona has been resolved, several seconds of ringing after the caller
        # arrived — so a record that timed itself wrote `durationSecs: 27.1`
        # next to its own problem line saying "44s on the line", from the same
        # call. Two clocks, one of which the caller never experienced.
        self.started = started or time.time()
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
    def turn(self, who: str, text: str, at: float | None = None) -> None:
        """who is 'caller' or 'dj'.

        `at` exists for the chat line, which does not build its record until
        the conversation ends and would otherwise stamp every turn with the
        moment it was written — a whole conversation sharing one timestamp,
        with the ordering and the pacing gone. A call passes nothing and gets
        the clock, as before.
        """
        text = str(text or "").strip()
        if not text or len(self.data["turns"]) >= MAX_TURNS:
            return
        self.data["turns"].append(
            {"t": _iso(time.time() if at is None else at),
             "who": who, "text": text[:MAX_TEXT]}
        )

    def first_word(self) -> None:
        """When the DJ's audio actually STARTS, once per call.

        A dj TURN commits only after the utterance finishes playing, so
        "time to first word" measured off the first turn silently included
        the ring, the pickup and the whole greeting — the panel's chart read
        12.5s on calls whose first audio landed in ~4. This is the honest
        numerator; the chart prefers it when present."""
        if "firstWordAt" not in self.data:
            self.data["firstWordAt"] = _iso(time.time())

    def tool(self, name: str, result: str = "", at: float | None = None,
             failed: bool = False) -> None:
        """One tool call, with what it answered. `at` as for turn().

        `failed` marks a call that errored or was refused. Those are the ones
        an operator is reading the transcript to find — a chat where the DJ
        talked around three rate-limited requests looked, in the record, like
        a chat where the DJ did nothing at all.
        """
        if len(self.data["tools"]) >= MAX_TURNS:
            return
        entry = {"t": _iso(time.time() if at is None else at),
                 "name": name, "result": str(result or "")[:400]}
        if failed:
            entry["failed"] = True
        self.data["tools"].append(entry)

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

        The one thing a prefix match cannot handle alone is the SDK merging two
        live events into one committed turn. A caller saying "yeah… maybe a
        mood" produces two transcripts and one history entry: the first live
        event matches "Yeah Maybe a mood" and claims it, and the second, being
        the tail rather than the head, matches nothing and survives with its own
        wording. The record then shows

            caller: Yeah Maybe a mood
            caller: Maybe a mood

        for something said once, and counts it twice — which is how the same
        call logged `caller_turns=5` and `only 4 caller turn(s)` one line apart.
        So an unmatched live turn that is contained in the committed text the
        PREVIOUS live turn was folded into is a fragment of that merge, and goes.
        A caller who genuinely repeats themselves is unaffected: two committed
        entries means the second live turn finds one of its own to match.
        """
        if not final_turns:
            return
        by_who: dict = {}
        for who, text in final_turns:
            by_who.setdefault(who, []).append(str(text))

        used: dict = {}
        # What the last matched turn of each speaker was folded into, so the
        # fragment check below has something to test against.
        folded_into: dict = {}
        fragments: list[int] = []
        for pos, turn in enumerate(self.data["turns"]):
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
                    folded_into[who] = candidate.strip()
                    break
            else:
                previous = folded_into.get(who, "")
                if live and previous and live in previous:
                    fragments.append(pos)

        for pos in reversed(fragments):
            del self.data["turns"][pos]

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

    def _note_if_the_dj_repeated_itself(self) -> None:
        """Say so, in the record, when the DJ said the same thing twice running.

        Observed on a real call (2026-08-08): the caller went quiet, the idle
        ladder fired exactly on schedule, and the model answered both the
        check-in and the goodbye by re-generating its previous line — the
        caller heard the same sentence three times and then the click. The
        mechanism was right and the words were wrong, which no log line said:
        it was only visible to a human reading the transcript. A model-side
        failure, so the problem line points at the LLM setting.

        Near-identical rather than equal — the third repeat differed by two
        words. Short lines are exempt: two "Still with me?" in a row is the
        canned fallback doing its job, not the model looping.
        """
        import difflib

        try:
            prev = ""
            for t in self.data["turns"]:
                if t["who"] != "dj":
                    prev = ""
                    continue
                text = t["text"].strip()
                if prev and len(text) >= 40 and difflib.SequenceMatcher(
                        None, prev.casefold(), text.casefold()).ratio() >= 0.92:
                    self.problem(
                        "The DJ said practically the same line twice in a row. "
                        "The model repeated itself instead of following its "
                        "instructions — seen when the idle check-in echoes the "
                        "previous turn instead of nudging. A model-side "
                        "failure: check the LLM setting against one with "
                        "proven instruction following."
                    )
                    return
                prev = text
        except Exception:
            pass  # a diagnostic must never cost the record it annotates

    def write(self, reason: str = "", keep: int = 0) -> None:
        self._note_if_the_dj_repeated_itself()
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


# What a record id may contain: the stamp and the room tail `write` builds it
# from, and nothing else. Anchored, so no separator and no dot can ride in.
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


def delete_one(record_id: str) -> bool:
    """Delete ONE stored record by its id (the filename stem `recent` hands
    the panel). True if it went.

    Clear-all was the only way to remove a transcript, which made the honest
    choice for one bad test call "throw away every conversation you have" —
    and after a run of tests that is most of the evidence you were about to
    read. The id is validated rather than trusted: it arrives from a browser,
    it is used to build a path, and `../` in it would delete something that is
    not a call record at all.
    """
    stem = str(record_id or "").strip()
    if not stem or not _SAFE_ID.fullmatch(stem):
        return False
    path = CALLS_DIR / f"{stem}.json"
    try:
        # resolve() before the comparison: a symlink inside the directory
        # would otherwise still point out of it.
        if path.resolve().parent != CALLS_DIR.resolve():
            return False
        path.unlink()
        return True
    except OSError:
        return False


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
