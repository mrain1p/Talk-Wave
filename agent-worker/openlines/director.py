"""Opens a line, keeps it going, and closes it — the only thing that decides
when anything airs.

Runs in the web container, beside the other cleanup_ctx loops (the station
warmer, the listener sampler, the hush janitor). The worker never opens or
closes a line; it only reads the record, so a call in progress can never race
the operator's Close button.

Nothing here fires unless the operator turned the feature on, and the default
cadence is manual — `open_lines_every_minutes` is 0 out of the box, so the
only thing that reaches a station's listeners is a button somebody pressed.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from openlines import air, followup, quiz as quiz_mod, state
from openlines import premise as premise_mod
from openlines import schedule as schedule_mod

log = logging.getLogger("talkwave.openlines")

TICK_SECONDS = float(os.environ.get("OPEN_LINES_TICK", "60"))


def _cfg() -> dict:
    import settings as settings_store

    return settings_store.permissions_for(settings_store.load(), "admin")


def _when(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def persona_allowed(cfg: dict, persona: dict) -> bool:
    """The operator's DJ allowlist. Blank = whoever is on air.

    Matched on the id OR the name, and it has to be both. The field was typed
    by hand as names until the picker arrived; the picker writes IDS. Checking
    only names meant a list built by the picker matched nobody — every DJ on
    the operator's station refused, which the automatic cadence would have hit
    silently. Found on their panel with all 22 personas listed, which should
    mean "anyone" and meant "no one".
    """
    raw = str(cfg.get("open_lines_personas") or "").strip()
    if not raw:
        return True
    wanted = {n.strip().casefold() for n in raw.split(",") if n.strip()}
    return (str(persona.get("name") or "").strip().casefold() in wanted
            or str(persona.get("id") or "").strip().casefold() in wanted)


def listeners_ok(cfg: dict, now_playing: dict) -> tuple[bool, int | None]:
    """Nobody listening, nothing airs. Checked at open and before each
    reminder — never mid-window, because a topic that vanished when somebody
    closed a tab would strand whoever was already typing."""
    from api.stats import _listener_count

    floor = int(cfg.get("open_lines_min_listeners") or 0)
    if floor <= 0:
        return True, None
    count = _listener_count(now_playing or {})
    if count is None:
        # The station did not say — and silence used to count as "not proven
        # empty", which meant a cold station that had not reported a count
        # yet ALWAYS opened a line to nobody the moment it loaded (operator,
        # 2026-08-31). A floor above zero now means what it says: no count,
        # no open. A station that genuinely never reports listeners sets the
        # floor to 0 — the help text names that trade.
        return False, None
    return count >= floor, count


def _arrivals_since(opened_at: str) -> int:
    """How many conversations came through while the line stood.

    Read from the transcripts already on disk rather than counted live: the
    worker and the web container would otherwise both be incrementing one
    file. Deliberately not "how many were about the topic" — nothing can know
    that — so the sign-off only ever distinguishes none from some.
    """
    start = _when(opened_at)
    if not start:
        return 0
    try:
        from call import record as record_mod
    except ImportError:
        return 0
    seen = 0
    for item in record_mod.recent(60):
        began = _when(item.get("startedAt"))
        if began and began >= start:
            seen += 1
    return seen


async def _live_context(station):
    """Who is on air, what show, and the week's grid.

    The schedule comes back too because an open line must not outlast the
    programme that opened it — see openlines.schedule.
    """
    persona = await station.resolve_live_persona()
    now_playing = await station.now_playing()
    try:
        week = await station.schedule()
    except Exception:                                          # noqa: BLE001
        week = {}
    show = await station.active_show(now_playing, week or None)
    return persona, now_playing, show or {}, week or {}


async def _resolve_bit(cfg, station, persona, show, bit: str) -> dict:
    """Turn a recurring BIT into tonight's specific instance.

    One step for every format, deliberately. Logic per format would fix the
    twelve anybody thought of and forbid the thirteenth an operator writes;
    this handles any of them, and the only rule is that the booth never
    receives the NAME of a bit to work out on air.
    """
    snap = await station.snapshot(with_skills=False)
    return await quiz_mod.resolve(cfg, station, persona, snap, show, bit)


async def open_now(reason: str = "operator", cfg: dict | None = None,
                   source: str | None = None, premise: str | None = None,
                   premise_id: str | None = None,
                   minutes: int | None = None) -> dict:
    """Put a subject up and open the line.

    `source` overrides the configured one for this press only — the dashboard
    offers "made up" and "off the shelf" as two buttons rather than making an
    operator visit the settings page to change their mind about one topic.

    Returns a small verdict the panel renders as-is. Every refusal names the
    gate that stopped it: "nothing happened" is the one answer an operator
    cannot act on.
    """
    from station import StationClient

    cfg = cfg or _cfg()
    if not cfg.get("open_lines_enabled"):
        return {"ok": False, "why": "Open Lines is switched off."}

    import secrets_store

    secrets_store.apply_to_env()
    station = StationClient()
    try:
        persona, now_playing, show, week = await _live_context(station)
        show_name = str(show.get("name") or "")
        # A press runs. The DJ allowlist and the listener floor pace the
        # AUTOMATIC cadence — they are there so the station does not solicit
        # arguments at 3am through a persona that should not — but an operator
        # pressing the button has already made both of those decisions, and a
        # button that answers "no" to the person holding it is a button that
        # gets pressed twice and then distrusted. Same rule the station applies
        # to its own segments: an explicit press bypasses shouldFire.
        by_hand = reason not in {"schedule"}
        if not by_hand:
            if not persona_allowed(cfg, persona):
                who = persona.get("name") or "This DJ"
                return {"ok": False,
                        "why": f"{who} is not on the Open Lines list."}
            ok, count = listeners_ok(cfg, now_playing)
            if not ok:
                floor = cfg.get("open_lines_min_listeners")
                why = (f"Only {count} listening — the floor is {floor}."
                       if count is not None else
                       "The station hasn't reported a listener count yet — "
                       f"the floor is {floor}, so nothing opens blind.")
                return {"ok": False, "why": why}

        source = str(source or cfg.get("open_lines_source") or "dj")
        picked = {}
        asked = {}
        if premise:
            # Typed at the dashboard, for this press only. Never touches the
            # shelf: a one-off subject an operator thought of is not a library
            # entry, and silently filing it would grow a shelf nobody curated.
            text = premise_mod.clean(premise)
            source = "typed"
            if not text:
                return {"ok": False, "why": "That subject is empty."}
        elif premise_id:
            picked = premise_mod.take_by_id(str(premise_id))
            text = str(picked.get("text") or "")
            source = "shelf"
            if not text:
                return {"ok": False,
                        "why": "That subject is no longer on the shelf."}
        elif source == "quiz":
            asked = await _resolve_bit(cfg, station, persona, show, "")
            if not asked:
                return {"ok": False,
                        "why": "The DJ could not set a question it could also "
                               "mark — try again, or pick a subject instead."}
            # The premise IS the resolved question: it is what gets announced,
            # and what the block reminds the DJ it asked.
            text = asked["question"]
        elif source == "shelf":
            picked = premise_mod.take_from_shelf(str(persona.get("id") or ""))
            text = str(picked.get("text") or "")
            if not text:
                who = persona.get("name") or "this DJ"
                return {"ok": False,
                        "why": f"Nothing on the shelf for {who} — add a "
                               "subject, or let the DJ make one up."}
        elif source == "directions":
            # The operator's random mode: one targeted angle off the
            # catalogue per open, the DJ still writing the subject in
            # persona — a skill-shaped brief rather than an open one.
            from openlines import directions as directions_mod

            chosen = directions_mod.pick(cfg)
            text = await premise_mod.invent(cfg, station, persona,
                                            direction=chosen)
            if not text:
                return {"ok": False,
                        "why": "The DJ could not come up with one — check the "
                               "model is reachable."}
        else:
            text = await premise_mod.invent(cfg, station, persona)
            if not text:
                return {"ok": False,
                        "why": "The DJ could not come up with one — check the "
                               "model is reachable."}

        # A shelf entry can be a BIT — a recurring format ("Would You Rather")
        # rather than a subject — and a bit is resolved into tonight's specific
        # instance BEFORE anything airs. Handed the NAME of a game the booth
        # announces that a game exists: "the suits want me to push something
        # called a Quiz question", which tells a listener there is a bit on
        # without inviting them into it.
        if picked.get("format") and text:
            asked = await _resolve_bit(cfg, station, persona, show, text)
            if not asked:
                return {"ok": False,
                        "why": f"The DJ could not turn \"{text[:40]}\" into "
                               "tonight's version of it — try again."}
            text = asked["question"]

        # An open line must not outlast the programme that opened it: the show
        # changing already ends it, and a countdown that said otherwise was
        # promising time the DJ was never going to have.
        # The player's ribbon picks a duration per press; the panel does not
        # and falls through to the setting.
        wanted = int(minutes or cfg.get("open_lines_minutes") or 60)
        minutes, cut_by_show = schedule_mod.bounded_minutes(
            week, str(show.get("id") or ""), wanted)

        spoken, aired = await air.say(
            station, air.open_direction(text, cfg, quiz=bool(asked)))
        if not aired:
            return {"ok": False,
                    "why": "The booth would not take the line — check the "
                           "station admin credentials."}

        record = state.build(
            premise=text, spoken=spoken, persona=persona, show_name=show_name,
            minutes=minutes, source=source,
            reminder_minutes=int(cfg.get("open_lines_reminder_minutes") or 0),
            reminder_max=int(cfg.get("open_lines_reminder_max") or 0))
        # Only when the bit HAS a right answer. "Ask the DJ" and "Rate My
        # Take" have nothing to mark, and an empty answer must not put the
        # block into quiz mode.
        if asked.get("answer"):
            record["quiz_answer"] = asked["answer"]
        record["opened_by"] = reason
        record["show_id"] = str(show.get("id") or "")
        record["cut_by_show"] = cut_by_show
        if picked:
            record["premise_id"] = str(picked.get("id") or "")
        state.write(record)
        log.info("open lines: %s opened a line (%d min%s) — %s",
                 persona.get("name"), minutes,
                 ", cut to the show" if cut_by_show else "", text)
        out = {"ok": True, "premise": text, "spoken": spoken,
               "expires_at": record["expires_at"], "minutes": minutes,
               "cutByShow": cut_by_show, "source": source}
        if not spoken:
            out["why"] = ("It aired, but the station was too slow to send "
                          "back what it said — the line is open and the DJ "
                          "knows the subject, not its own wording.")
        return out
    finally:
        await station.aclose()


async def _sign_off(cfg: dict, record: dict) -> None:
    """The closing line, aired exactly once.

    The latch is CLAIMED before the station is asked to speak, not set after it
    finishes — see state.claim_signoff. Setting it afterwards left seconds of
    TTS between the check and the write, and two overlapping web containers
    (which is what a redeploy is) both aired the same closing line.
    """
    from station import StationClient

    import secrets_store

    claimed = state.claim_signoff()
    if not claimed:
        # Another director already has it. Not an error, and not worth a log
        # line: a redeploy makes this the normal case for a few seconds.
        return
    record = claimed

    secrets_store.apply_to_env()
    station = StationClient()
    try:
        took = _arrivals_since(str(record.get("opened_at")))
        spoken, _aired = await air.say(station, air.close_direction(
            str(record.get("premise") or ""), took,
            record.get("followup_lines")))
        # Re-read: the claim was written a moment ago, and the panel may have
        # touched the record since. Only the words are ours to add now.
        fresh = state.read_raw() or record
        fresh["sign_off_spoken"] = spoken
        state.write(fresh)
        log.info("open lines: closed (%s arrivals while it stood)", took)
    finally:
        await station.aclose()


async def _remind(cfg: dict, record: dict) -> None:
    from station import StationClient

    import secrets_store

    # Spend the slot BEFORE airing, for the same reason the sign-off claims its
    # latch first: two overlapping directors that both read the same due
    # reminder would both air it. The slot is spent either way — banking
    # skipped reminders through a quiet hour would spend them all at once the
    # moment somebody tunes in — so moving the write earlier costs nothing and
    # closes most of the window.
    fresh = state.read_raw()
    if not state.is_live(fresh) or not fresh.get("next_reminder_at"):
        return
    state.write(state.note_reminder(
        fresh, int(cfg.get("open_lines_reminder_minutes") or 0)))

    secrets_store.apply_to_env()
    station = StationClient()
    try:
        _persona, now_playing, _show, _week = await _live_context(station)
        ok, _count = listeners_ok(cfg, now_playing)
        if ok:
            await air.say(station, air.remind_direction(
                str(record.get("premise") or ""), cfg,
                str(record.get("spoken") or "")))
    finally:
        await station.aclose()


async def _follow_up(cfg: dict, record: dict) -> bool:
    """Report ONE finished conversation to the room. True if something aired.

    One per tick on purpose. Two contributions arriving between passes should
    reach the audience a minute apart, as two moments — not stacked into one
    breath, which is how a DJ starts sounding like it is reading a feed.
    """
    from station import StationClient

    if int(record.get("followups_sent") or 0) >= followup.MAX_PER_LINE:
        return False
    waiting = followup.candidates(
        record, record.get("opened_at"), record.get("followed_up"))
    if not waiting:
        return False

    import secrets_store

    secrets_store.apply_to_env()
    station = StationClient()
    try:
        persona, now_playing, _show, _week = await _live_context(station)
        # The DJ that opened it has to be the one reporting back. A changeover
        # mid-window ends the line anyway, but the tick that notices may be the
        # one that would otherwise have aired this.
        if str(persona.get("id") or "") != str(record.get("persona_id") or ""):
            return False

        item = waiting[0]
        # Claim the conversation before spending a model call on it, let alone
        # airing it. Two directors that both saw the same contribution would
        # otherwise both report it, and the room would hear the DJ discover one
        # person's answer twice. If the line below turns out to be worth
        # airing, note_followup upgrades this to a counted follow-up.
        state.write(state.note_seen(state.read_raw(), item.get("id")))
        premise = str(record.get("premise") or "")
        line = await followup.line_for(cfg, station, persona, premise, item)
        if not line:
            # Considered and nothing to say — most conversations while a line
            # is open are requests. Already marked seen by the claim above, so
            # the model is not asked about the same hello every sixty seconds.
            return False

        # Checked here as well as at open: reporting back to an empty room is
        # the one moment this feature can spend the DJ on nobody.
        ok, _count = listeners_ok(cfg, now_playing)
        if not ok:
            return False

        spoken, aired = await air.say(
            station, air.followup_direction(premise, line, cfg))
        fresh = state.read_raw()
        if not aired:
            # The booth refused. It stays marked seen from the claim: retrying
            # a station that is saying no, once a minute, is how one failure
            # becomes sixty.
            return False
        state.write(state.note_followup(fresh, item.get("id"), spoken))
        log.info("open lines: reported a contribution on air — %s", line)
        return True
    finally:
        await station.aclose()


async def tick(cfg: dict | None = None) -> None:
    """One pass: close what is over, remind what is due, open what is owed."""
    cfg = cfg or _cfg()
    if not cfg.get("open_lines_enabled"):
        return
    record = state.read_raw()

    if record and not state.is_live(record) and not record.get("signed_off"):
        await _sign_off(cfg, record)
        return

    # Follow-ups before reminders. Something that actually came back is worth
    # more to the room than asking again, and airing "still no takers" a
    # moment after a contribution arrived would be a lie.
    if state.is_live(record) and cfg.get("open_lines_followup"):
        if await _follow_up(cfg, record):
            return
        record = state.read_raw()

    if state.is_live(record) and record.get("next_reminder_at"):
        due = _when(record["next_reminder_at"])
        if due and datetime.now(timezone.utc) >= due:
            await _remind(cfg, record)
        return

    every = int(cfg.get("open_lines_every_minutes") or 0)
    if every <= 0 or state.is_live(record):
        return
    last = _when(record.get("closed_at") or record.get("opened_at"))
    if last and (datetime.now(timezone.utc) - last).total_seconds() < every * 60:
        return
    await open_now(reason="schedule", cfg=cfg)


async def run(app) -> None:
    """cleanup_ctx: the director's own loop.

    <= 0 disables it outright, which is what the suite sets — a test that
    builds the real app must not reach for a network, and this loop would
    otherwise poll the station the moment it starts.
    """
    if TICK_SECONDS <= 0:
        yield
        return

    async def loop() -> None:
        while True:
            try:
                await tick()
            except Exception as e:                             # noqa: BLE001
                log.debug("open lines tick failed: %s", e)
            await asyncio.sleep(TICK_SECONDS)

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:                                      # noqa: BLE001
            pass
