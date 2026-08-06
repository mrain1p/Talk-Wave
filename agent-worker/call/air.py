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


class OnAirGuard:
    """Shared "is the broadcast actually talking right now" state for one call.

    This is the single place that decides whether the air is busy; the reply
    gate, the on-air tools and the widget's status chip all read it, so they
    cannot disagree with each other.

    "Busy" means ACTIVELY SPEAKING — not thinking, not queued. It's derived
    from when the station last logged on-air speech, held for
    `on_air_quiet_secs` (roughly how long a link runs), because the station
    tells us when a link STARTED, not when it finished.
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

    def mark_on_air(self, seconds: float = 25.0) -> None:
        """Treat the air as busy from now, because we just made it busy.

        The poll then confirms it and extends as needed; the gate does not
        reopen until BOTH this window has passed and the station log agrees.
        """
        self._assumed_until = max(self._assumed_until, time.time() + seconds)
        if self._clear.is_set():
            self._clear.clear()
            self.on_air = True
            self._publish(True)
            log.info("our own action is going out on air — holding the call DJ back")

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
        try:
            await session.generate_reply(instructions=(
                "You just stepped away to let something go out on air, and "
                "you're back on the call now. Say so in one short line — "
                "\"alright, I'm back\" — and pick the conversation up where "
                "you left it, in your own voice. Don't apologise at length, "
                "don't recap, and don't start a new topic."
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
        # Whether we actually cut the caller off for this link. Only then is
        # there anything to come back FROM: the gate also closes for a caller
        # who dialled in mid-link, and telling them "I'm back" when they never
        # heard a hand-over is a line about nothing.
        stepped_away = False
        while True:
            try:
                since = await self.station.seconds_since_on_air_speech()
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.debug("on-air check failed (assuming clear): %s", e)
                since = None

            busy = (since is not None and since < self.quiet_secs) or (
                time.time() < self._assumed_until
            )
            if busy != self.on_air:
                self.on_air = busy
                self._publish(busy)
                if busy:
                    self._clear.clear()
                    log.info("on-air DJ is speaking — holding the call DJ back")
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
                            stepped_away = True
                        except Exception as e:
                            log.debug("could not hand over to air cleanly: %s", e)
                else:
                    self._clear.set()
                    log.info("air is clear — the call DJ has the floor again")
                    if stepped_away:
                        stepped_away = False
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
