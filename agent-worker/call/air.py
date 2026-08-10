"""Keeping the call DJ and the on-air DJ off each other's toes.

The two are the same person. Left alone they talk over each other — the caller
hears two of the same voice, and so does everyone listening to the station.
"""

from __future__ import annotations

import asyncio
import logging
import time

from livekit.agents import Agent, AgentSession

from station import StationClient

from .background import spawn

log = logging.getLogger("callin.agent")


def speaking_secs(spoken: str, fallback: int) -> int:
    """How long the on-air DJ will be talking, sized from the words themselves.

    A fixed hold was the wrong shape: an announcement is a sentence and a
    segment can run a minute or more, so one number either reopens the gate
    mid-delivery or gags the DJ long after the air is clear — both were heard
    on real calls. The station's own voice serialiser holds its channel the
    same way (word count at ~140wpm, padded), so count the words: about 2.4 a
    second, plus a beat either side.
    """
    words = len(str(spoken or "").split())
    if not words:
        return fallback
    return max(12, min(180, int(words / 2.4) + 4))


class OnAirGuard:
    """Shared "is the broadcast actually talking right now" state for one call.

    This is the single place that decides whether the air is busy; the reply
    gate, the on-air tools and the widget's status chip all read it, so they
    cannot disagree with each other.

    "Busy" means ACTIVELY SPEAKING — not thinking, not queued. It's derived
    from when the station last logged on-air speech, held for as long as those
    words take to say (`speaking_secs`), because the station tells us when a
    link STARTED and what it says, not when it finished. `on_air_quiet_secs`
    is the fallback hold for an entry with no words.
    """

    POLL_SECS = 4.0     # a station read per call every 4s, not per turn
    MAX_HOLD = 45.0     # never leave a caller in silence longer than this

    def __init__(self, station: StationClient, cfg: dict, room=None) -> None:
        self.station = station
        self.room = room
        self.enabled = bool(cfg.get("avoid_on_air_overlap"))
        self.quiet_secs = float(cfg.get("on_air_quiet_secs") or 0)
        self.on_air = False
        self._clear = asyncio.Event()
        self._clear.set()
        # When WE put something on air we know it is about to make sound, and
        # we know before the station's log does. Waiting for the poll to notice
        # left a window in which the DJ carried on talking over its own
        # announcement — observed on a real call, right after it had said it
        # was going off to air something.
        self._assumed_until = 0.0
        # An action the station accepted but had not CONFIRMED when the tool
        # returned. The fixed window above is anchored to the tool's return,
        # and on a slow station the delivery lands after it — the Ash call of
        # 2026-08-09: a 12s hold from an unconfirmed announce expired, the DJ
        # spoke, and the announcement aired over it. While this deadline is
        # live the gate stays shut until the station's log actually SHOWS the
        # delivery (then the words size the hold as normal), and a failing
        # poll means "assume busy", not clear — the same congestion that made
        # the confirmation slow was blinding the poll.
        self._pending_until = 0.0
        # Whether the caller heard a hand-over line for the current busy spell.
        # Only then is there anything to come back FROM: the gate also closes
        # for a caller who dialled in mid-link, and "I'm back" to them is a
        # line about nothing.
        self.stepped_away = False
        # The words that went out on air, for the come-back line to nod at in
        # passing rather than the DJ returning as if nothing happened.
        self.aired_text = ""

    def mark_on_air(self, seconds: float = 25.0, spoken: str = "") -> None:
        """Treat the air as busy from now, because we just made it busy.

        The poll then confirms it and extends as needed; the gate does not
        reopen until BOTH this window has passed and the station log agrees.

        `stepped_away` is set HERE and not only in the poll because this sets
        `on_air` directly — the watch loop never sees a busy edge for the DJ's
        own actions, so it never knew there was anything to come back from and
        the DJ sat silent after its own announcement finished, waiting on a
        caller it had told to hold. Heard on real calls, reported 2026-08-08.
        """
        self._assumed_until = max(self._assumed_until, time.time() + seconds)
        if spoken:
            self.aired_text = str(spoken)
        self.stepped_away = True
        if self._clear.is_set():
            self._clear.clear()
            self.on_air = True
            self._publish(True)
            log.info("our own action is going out on air — holding the call DJ back")

    PENDING_CEILING = 90.0   # an unconfirmed delivery is given this long to appear

    def mark_pending_air(self, spoken: str = "") -> None:
        """The station took our action but was too slow to confirm it, so
        nothing says when it will air. No countdown — the gate stays shut
        until the poll sees the delivery in the station's log, or the
        ceiling decides it is never coming."""
        self._pending_until = max(self._pending_until,
                                  time.time() + self.PENDING_CEILING)
        if spoken:
            self.aired_text = str(spoken)
        self.stepped_away = True
        if self._clear.is_set():
            self._clear.clear()
            self.on_air = True
            self._publish(True)
        log.info("our action was sent but not confirmed — holding until the "
                 "station's log shows it (up to %.0fs)", self.PENDING_CEILING)

    def _publish(self, on_air: bool) -> None:
        """Tell the widget, so the caller sees "DJ is on air" rather than a
        DJ that has mysteriously gone quiet."""
        if self.room is None:
            return
        try:
            spawn(
                self.room.local_participant.set_attributes(
                    {"wavetalk.onair": "1" if on_air else ""}
                )
            )
        except Exception as e:
            log.debug("on-air state publish failed (harmless): %s", e)

    async def wait_until_clear(self, timeout: float | None = None) -> float:
        """Block until the broadcast is quiet. Returns the seconds waited, so
        the caller can be told why there was a pause."""
        if not self.enabled or self._clear.is_set():
            return 0.0

        started = time.time()
        try:
            await asyncio.wait_for(self._clear.wait(), timeout or self.MAX_HOLD)
        except asyncio.TimeoutError:
            # Dead air is worse than an overlap. If the station has been
            # "speaking" for longer than any real link, assume the log is
            # stale and let the call carry on.
            log.warning("air still busy after %.0fs — letting the call continue",
                        timeout or self.MAX_HOLD)
            self._clear.set()
        return time.time() - started

    async def _come_back(self, session: AgentSession) -> None:
        """Say something on the way back from the broadcast.

        The hand-over line told the caller to hold; nothing told them the hold
        was over. So the DJ went quiet mid-conversation, came back, and then
        waited for the caller to speak first — from the caller's end that is
        indistinguishable from the line having dropped, and it is the point at
        which they hang up. Observed on the calls of 2026-08-06, where the
        silences a caller could not account for are the whole story.

        `generate_reply` rather than a canned line, because the useful version
        picks the thread back up ("right, I'm back — you were saying about the
        rock") and only the model knows what was being said. The canned line
        is the fallback: coming back saying SOMETHING beats coming back
        silently, which is the failure being fixed.
        """
        aired = (self.aired_text or "").strip()
        self.aired_text = ""
        nod = (
            f" What went out on air was: \"{aired[:200]}\" — a passing nod to "
            "it is fine, but don't read it back to them."
        ) if aired else ""
        try:
            await session.generate_reply(instructions=(
                "You just stepped away to let something go out on air, and "
                "you're back on the call now. Say so in one short line — "
                "\"alright, I'm back\" — and pick the conversation up where "
                "you left it, in your own voice. Don't apologise at length, "
                "don't recap, and don't start a new topic." + nod
            ))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("could not generate the back-from-air line: %s", e)
            try:
                session.say(
                    "Alright, I'm back — where were we?",
                    allow_interruptions=True,
                    add_to_chat_ctx=False,
                )
            except Exception:
                pass

    def _assess(self, speech: tuple[float, str] | None,
                poll_failed: bool = False) -> bool:
        """One call's answer to "is the air busy right now", from everything
        the guard knows. Split from the watch loop so the pending-delivery
        rules are testable without running the loop.

        The pending deadline resolves the moment the log shows speech — from
        then on the words size the hold like any other busy spell — and
        expires with a warning if the delivery never appears: dead air is
        still worse than an overlap, it just gets a real chance first.
        """
        now = time.time()
        log_busy = self._log_says_busy(speech)
        if self._pending_until:
            if log_busy:
                # The delivery reached the log; the normal machinery owns it.
                self._pending_until = 0.0
            elif now >= self._pending_until:
                log.warning("a sent-but-unconfirmed action never appeared in "
                            "the station log — releasing the hold")
                self._pending_until = 0.0
            # A clean read showing nothing, or a failed read: still waiting,
            # same hold — the deadline below carries both.
        # A read that DIDN'T come back cannot MOVE the gate — so on a failed
        # poll, HOLD THE CURRENT STATE rather than compute one. If the air was
        # busy it stays busy (the on-air DJ is most likely still mid-link — this
        # is what stops the Dawn/Ash overlap, where "assume clear" doubled the
        # voice), and if it was clear it stays clear (so a quiet-but-slow
        # station under congestion does NOT gag every reply — the first version
        # of this fix assumed busy on any failed read and added seconds to every
        # answer while the station was struggling, which is the opposite of what
        # a slow box needs). The next clean read moves the gate normally.
        assumed_or_pending = now < self._assumed_until or now < self._pending_until
        if poll_failed and not assumed_or_pending:
            return self.on_air
        return log_busy or assumed_or_pending

    def _log_says_busy(self, speech: tuple[float, str] | None) -> bool:
        """Whether the station's log says its DJ is still talking.

        The log records when an utterance STARTED and what was said — never
        when it finished — so the end is sized from the words themselves
        (`speaking_secs`), the same way the station's own voice serialiser
        holds its channel. `on_air_quiet_secs` is only the fallback for an
        entry with no words: as a fixed hold it either reopened the gate while
        a long segment was still mid-delivery or gagged the call for most of a
        minute over a one-line station ID.
        """
        if speech is None:
            return False
        since, text = speech
        return since < speaking_secs(text, int(self.quiet_secs) or 30)

    async def watch(self, session: AgentSession) -> None:
        """Poll the station and flip the gate. Started as a task for the life
        of the call."""
        if not (self.enabled and self.quiet_secs > 0):
            return
        # The first pass runs immediately and silently: someone who dials in
        # mid-link should have the gate already closed (so their first reply
        # waits) without the greeting being cut off by a hand-over line for a
        # broadcast that was already running when they picked up the phone.
        first = True
        while True:
            poll_failed = False
            try:
                speech = await self.station.on_air_speech()
            except asyncio.CancelledError:
                return
            except Exception as e:
                # A failed read HOLDS the gate for a short window (see _assess
                # / FAIL_HOLD), reset by the next clean read — a timed-out read
                # can't prove the air is clear, and congestion is when the
                # on-air DJ is most likely mid-link. "Assume clear" here was the
                # Ash overlap AND the Dawn overlap; dead air over a routine miss
                # is bounded (MAX_HOLD) and is the smaller cost.
                log.debug("on-air check failed (holding briefly): %s", e)
                speech = None
                poll_failed = True

            busy = self._assess(speech, poll_failed)
            if busy != self.on_air:
                self.on_air = busy
                self._publish(busy)
                if busy:
                    self._clear.clear()
                    log.info("on-air DJ is speaking — holding the call DJ back")
                    if speech and speech[1]:
                        self.aired_text = speech[1]
                    if not first:
                        # Cut the call DJ off mid-sentence if need be: the whole
                        # point is that the broadcast never hears itself doubled.
                        try:
                            session.interrupt()
                            session.say(
                                "Hold on a second — let me let that go out on air first.",
                                allow_interruptions=False,
                                # Not conversation, and an extra model turn in
                                # the history is what Gemini 400s on when a
                                # tool call follows it.
                                add_to_chat_ctx=False,
                            )
                            self.stepped_away = True
                        except Exception as e:
                            log.debug("could not hand over to air cleanly: %s", e)
                else:
                    self._clear.set()
                    log.info("air is clear — the call DJ has the floor again")
                    if self.stepped_away:
                        self.stepped_away = False
                        await self._come_back(session)
            first = False
            await asyncio.sleep(self.POLL_SECS)


class CallAgent(Agent):
    """The caller's DJ, with one addition: its replies wait for quiet air.

    Holding here rather than dropping input is deliberate. The caller's words
    are already transcribed and in the context by this point — only the REPLY
    is queued, so nothing they said is lost and they never have to repeat
    themselves just because the station was mid-link.
    """

    def __init__(self, instructions: str, guard: OnAirGuard) -> None:
        super().__init__(instructions=instructions)
        self._guard = guard

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        waited = await self._guard.wait_until_clear()
        if waited >= 2:
            log.info("held the caller's reply %.0fs while the on-air DJ was talking",
                     waited)
