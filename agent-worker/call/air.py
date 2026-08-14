"""Keeping the call DJ and the on-air DJ off each other's toes.

The two are the same person. Left alone they talk over each other — the caller
hears two of the same voice, and so does everyone listening to the station.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from livekit.agents import Agent, AgentSession

from station import StationClient

from . import comeback
from .air_timing import DUCK_PAD_SECS, speaking_secs
from .air_verdict import AirVerdict, _air_path  # noqa: F401
from .background import spawn

log = logging.getLogger("callin.agent")


class OnAirGuard(AirVerdict):
    """Shared "is the broadcast actually talking right now" state for one call.

    This is the single place that decides whether the air is busy; the reply
    gate, the on-air tools and the widget's status chip all read it, so they
    cannot disagree with each other.

    "Busy" means ACTIVELY SPEAKING — or, on a station that warns us
    (voice.queued, SUB/WAVE 1.8), ABOUT TO BE, once the forecast is inside
    the hand-over window. A 1.8 station bounds the busy spell exactly
    (voice.start … voice.end, measured); an older one only says when a link
    STARTED and what it says, so the end is estimated from the words
    (`speaking_secs`) plus the handoff lag. `on_air_quiet_secs` is the
    fallback hold for an entry with no words either way.
    """

    POLL_SECS = 4.0     # a station read per call every 4s, not per turn
    MAX_HOLD = 45.0     # never leave a caller in silence longer than this

    # HANDOFF-STAMPED evidence only — the 4s log poll, and pre-1.8 pushes.
    # Those signals are stamped when the audio is handed to the mixer, a
    # couple of seconds before it is audible, so the words finish that much
    # later too and the gap rides the hold's tail (0.10.69: the tail ending
    # early was the reported bug). A station on SUB/WAVE 1.8 stamps at AIR
    # time and sends the voice.* lifecycle; that evidence carries "v": 2 and
    # is held on exactly, with no lag. It was an operator setting until
    # 0.10.97 and should not have been: nobody can measure their mixer's
    # handoff gap from the panel, the two stations that matter both want ~2s,
    # and it sat in the middle of the ducking list looking like a dial worth
    # turning.
    HANDOFF_LAG_SECS = 2.0

    # A banter break is several utterances back to back — voice.end, then
    # voice.queued again a second later — and each gap used to reopen the
    # gate, so the caller heard "right, where were we" and "hold on, I'm on
    # air" three times over one break (operator-reported, 2026-08-12).
    #
    # That was fixed with a blanket 2s pad on the end of EVERY hold, which is
    # the wrong shape: it taxed every link that had genuinely finished in
    # order to bridge the ones that had not, and it only ever bridged gaps
    # shorter than itself. Since 0.10.125 the come-back is a cancellable task
    # instead (call/comeback.py) — the loop keeps watching while the DJ is
    # returning, and a fresh voice cancels the return mid-sentence and leaves
    # the hold up. No second hand-over line, because the caller was never told
    # the hold was over. That bridges a gap of ANY length and costs a finished
    # link nothing.
    #
    # Kept at 0 rather than deleted: _settle is still the one place that can
    # extend a busy spell, and a station without the voice lifecycle may yet
    # need a floor here.
    SETTLE_SECS = 0.0

    def __init__(self, station: StationClient, cfg: dict, room=None) -> None:
        self.station = station
        self.room = room
        self.enabled = bool(cfg.get("avoid_on_air_overlap"))
        self.quiet_secs = float(cfg.get("on_air_quiet_secs") or 0)
        # See HANDOFF_LAG_SECS: a constant since 0.10.97, not a setting.
        self.lag_secs = self.HANDOFF_LAG_SECS
        # First tick of quiet inside a busy spell, or 0 when the air is busy.
        # The settle window is measured from here — see SETTLE_SECS.
        self._quiet_since = 0.0
        # How close to the FORECAST air instant (voice.queued, 1.8+) the DJ
        # hands over. The station can warn many seconds ahead — the whole
        # point of the warning is that the call keeps flowing through the
        # queue wait and steps away just before the voice lands, not that it
        # gags for the entire lead (the operator's ask, 0.10.89).
        self.handover_secs = max(0.0, float(cfg.get("on_air_handover_secs") or 0))
        # Last streamBufferSeconds the station reported on a voice push. 0
        # until one arrives; stream_buffer() falls back to the handoff lag.
        self._last_buf = 0.0
        # The duck's close, per guard rather than as a bare module constant,
        # for the same reason SETTLE_SECS and lag_secs are reachable: a test
        # about the come-back LINE has to be able to compress every real
        # second in the way, and this one is 4.5 of them.
        self.duck_pad = DUCK_PAD_SECS
        # The voice.queued spell the caller has already been handed over
        # for, so one forecast never says the line twice.
        self._announced_id = ""
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
        # The in-flight return, so a fresh voice can cancel it. See
        # SETTLE_SECS for what this replaced.
        self._comeback = None
        # How far behind the live edge THIS caller's player actually is,
        # measured by the widget and pushed over talkwave.lag. 0 until one
        # arrives — see caller_lag().
        self._caller_lag = 0.0
        # This call's ducking timeline — see call/air_log.py. Attached by the
        # session; None on a guard nobody is recording.
        self.air_log = None

    def mark_on_air(self, seconds: float = 8.0, spoken: str = "") -> None:
        """Treat the air as busy from now, because we just made it busy.

        The poll then confirms it and extends as needed; the gate does not
        reopen until BOTH this window has passed and the station log agrees.

        `stepped_away` is set HERE and not only in the poll because this sets
        `on_air` directly — the watch loop never sees a busy edge for the DJ's
        own actions, so it never knew there was anything to come back from and
        the DJ sat silent after its own announcement finished, waiting on a
        caller it had told to hold. Heard on real calls, reported 2026-08-08.
        """
        # However long the words run, plus ONE pad — the same duck close every
        # other branch uses. It used to be `seconds + lag + buffer`, three
        # separately-reasonable numbers stacked on top of a `seconds` that
        # already had a 12s floor in it, which is how a one-line shoutout
        # became half a minute of held caller.
        #
        # This is a CEILING, not a promise: a voice.end from the station drops
        # it on the spot (see the watch loop), so the normal case is that the
        # air really is measured and this never runs out.
        self._assumed_until = max(self._assumed_until,
                                  time.time() + seconds + self.tail())
        if spoken:
            self.aired_text = str(spoken)
        self.stepped_away = True
        if self._clear.is_set():
            self._clear.clear()
            self.on_air = True
            self._publish(True)
            log.info("our own action is going out on air — holding the call DJ "
                     "back for %.1fs (%.1fs of words + %.1fs tail)",
                     seconds + self.tail(), seconds, self.tail())
            if getattr(self, "air_log", None):
                self.air_log.opened("we put something on air",
                                    until=self._assumed_until,
                                    buf=self.stream_buffer(), text=spoken)

    # A tail is the caller's lag plus enough margin that a wobble in their
    # buffer cannot put the DJ over the top of the last word.
    TAIL_MARGIN_SECS = 1.5

    # Nobody is 40 seconds behind a live stream; a number like that is a stuck
    # element or a hostile page, and believing it would hold a caller silent
    # for most of a minute.
    MAX_CALLER_LAG = 20.0

    def note_caller_lag(self, secs: float) -> None:
        """The widget's own measurement of how far behind the live edge it is.

        `buffered.end - currentTime` on its own `<audio>` element, which is
        exactly the distance between what the station has sent and what this
        person is hearing. It is the only honest source for this: the station
        can only report its BURST SIZE, which on the operator's box is 22
        seconds while the browser plays 2.3 behind (measured 2026-08-13), and
        a caller on a phone, a car head unit or a slower connection is a
        different number again from the same station.
        """
        try:
            v = float(secs)
        except (TypeError, ValueError):
            return
        if 0.0 <= v <= self.MAX_CALLER_LAG:
            self._caller_lag = v

    def caller_lag(self) -> float:
        """How far behind the broadcast this caller is, as THEY measured it.

        0 when nobody has said — a caller listening on a car radio never loads
        the widget's player, and inventing a number for them is how the 22
        went wrong. Every user of this falls back to a constant instead.
        """
        # getattr, like every other optional here: the suite drives these
        # methods on bare guards built with object.__new__ to keep a timing
        # test from needing a room, a station and a session.
        return getattr(self, "_caller_lag", 0.0)

    def tail(self) -> float:
        """How long to keep holding after the voice itself has finished.

        The duck's close, and it is DUCK_PAD_SECS — the operator's own ask,
        "a consistent 4-5 second duck at the beginning and close".

        It used to be max(duck_pad, stream_buffer), on the reading that a
        station reporting a long buffer means the caller is that far behind.
        The premise was wrong, and only measuring showed it: streamBufferSecs
        is 22 here and Icecast really does burst 22 seconds on connect, but
        that is the burst SIZE, not the playhead. The widget tunes a caller in
        with a plain `<audio>` element, and that element sat a steady 2.3
        seconds behind the newest buffered byte for a full run — Chrome
        discards nearly all of the burst. So the tail was padding by 22 for a
        2.3s lag: about seventeen seconds of silence after the DJ had already
        finished, on every hold where a push had been seen. Read off a record:
        a 37.8s voice sizing a ~60s hold.

        stream_buffer() stays because the TIMELINE still records what the
        station claimed — a station that one day reports a real playhead
        offset should be believed, and that row is where it would show up.
        """
        # Now that the caller's own lag is measurable, the pad is a FLOOR
        # rather than the whole answer: a caller further behind than 3 seconds
        # gets a tail sized to them, which is the case the constant could
        # never cover and the case where the DJ really does come back over the
        # top of the last word.
        return max(self.duck_pad, self.caller_lag() + self.TAIL_MARGIN_SECS)

    def stream_buffer(self) -> float:
        """What the station last said its listeners are behind by.

        NOT the caller's playhead — see tail(). Recorded on the timeline and
        used for nothing else.
        """
        return self._last_buf if self._last_buf > 0 else self.lag_secs

    # An unconfirmed delivery is given this long to appear in the station's
    # log. It used to be 90s, which was tolerable when a stuck hold only meant
    # the DJ stayed quiet — from 0.10.107 it also mutes the CALLER, and a real
    # call sat muted until it was abandoned. A hold nobody can end is worse
    # than an overlap, so this is now the shortest window that still covers a
    # slow station: past it the gate reopens and the DJ can talk again.
    PENDING_CEILING = 15.0

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
                    # "0", not "". An empty attribute value is a DELETE in
                    # LiveKit, so clearing the flag removed the key — and the
                    # widget only reacted when the key was PRESENT. The card
                    # therefore never came out of "Working the booth": proven
                    # on a real call 2026-08-13, where the guard logged "air
                    # is clear" and the caller's card stayed on air for the
                    # remaining 80 seconds. The flag now always exists.
                    {"talkwave.onair": "1" if on_air else "0"}
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

    def _cancel_comeback(self) -> bool:
        """Stop an in-flight return. True if there was one.

        True means this busy edge is the NEXT PART of a break the caller is
        already holding through, not a new one — so the hand-over line is
        skipped. Saying it twice is the thing being fixed.
        """
        task, self._comeback = self._comeback, None
        if task is None or task.done():
            return False
        task.cancel()
        if getattr(self, "air_log", None):
            self.air_log.note("the return was cancelled — same break")
        return True

    def _settle(self, busy: bool, now: float) -> bool:
        """Ride out the gaps INSIDE a busy spell — see SETTLE_SECS.

        A banter break arrives as several utterances a second or two apart,
        and returning the caller into each gap cost a come-back line and then
        another hand-over line, three times over one break. While the settle
        window runs the gate stays shut and nothing is said either way; a
        fresh voice clears the clock, so the next gap gets a full window of
        its own rather than a stale one.

        Only ever EXTENDS a busy spell: it cannot invent one, so a caller who
        dialled into quiet air is unaffected.
        """
        if busy:
            self._quiet_since = 0.0
            return True
        if self.on_air:
            if not self._quiet_since:
                self._quiet_since = now
            if now - self._quiet_since < self.SETTLE_SECS:
                return True
        return False

    PUSH_TICK = 1.0     # the push file is local and cheap — read it every second

    async def watch(self, session: AgentSession) -> None:
        """Watch the push file every second and poll the station every
        POLL_SECS, and flip the gate. Started as a task for the life of the
        call."""
        if not (self.enabled and self.quiet_secs > 0):
            return
        # The first pass runs immediately and silently: someone who dials in
        # mid-link should have the gate already closed (so their first reply
        # waits) without the greeting being cut off by a hand-over line for a
        # broadcast that was already running when they picked up the phone.
        first = True
        tick = 0
        poll_every = max(1, int(self.POLL_SECS / self.PUSH_TICK))
        speech: tuple[float, str] | None = None
        speech_read_at = 0.0
        poll_failed = False
        while True:
            if tick % poll_every == 0:
                try:
                    speech = await self.station.on_air_speech()
                    speech_read_at = time.time()
                    poll_failed = False
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    # A failed read HOLDS the gate for a short window (see
                    # _assess / FAIL_HOLD), reset by the next clean read — a
                    # timed-out read can't prove the air is clear, and
                    # congestion is when the on-air DJ is most likely
                    # mid-link. "Assume clear" here was the Ash overlap AND
                    # the Dawn overlap; dead air over a routine miss is
                    # bounded (MAX_HOLD) and is the smaller cost.
                    log.debug("on-air check failed (holding briefly): %s", e)
                    speech = None
                    speech_read_at = 0.0
                    poll_failed = True

            # The cached poll answer ages between polls — its "seconds since"
            # was true when read, so advance it rather than replaying it.
            now = time.time()
            aged = speech
            if speech is not None and speech_read_at:
                aged = (speech[0] + (now - speech_read_at), speech[1])
            state = self._pushed_state()
            verdict = self._push_verdict(state, now) if state else None
            hand_line = "Hold on a second — let me let that go out on air first."
            aired_now = ""
            if verdict and verdict[0] == "busy":
                # Push evidence outranks the poll: same speech, earlier and
                # (on a 1.8 station) exactly bounded. The synthetic zero-age
                # tuple keeps _assess's pending-delivery resolution working.
                aired_now = verdict[1]
                hand_line = verdict[2] or hand_line
                busy = self._assess((0.0, verdict[1] or "x"), False)
            elif verdict and verdict[0] == "clear":
                # voice.end is a MEASURED stop, and it now beats OUR OWN
                # GUESS as well as the poll's.
                #
                # It used to lose to _assumed_until / _pending_until, on the
                # reasoning that an end event cannot be proved to close the
                # action we just sent. True, and beside the point: the station
                # is telling us the air is quiet NOW, and quiet is the only
                # thing this gate is about. What it bought instead was the
                # caller sitting held for the remainder of a 25-second
                # estimate after the DJ had already stopped talking —
                # "held working the booth way too long, I had to hang up".
                #
                # So the measurement wins and the guesses are dropped, not
                # merely out-voted: leaving them set would have them reassert
                # the hold on the very next tick.
                self._assumed_until = 0.0
                self._pending_until = 0.0
                busy = False
            else:
                busy = self._assess(aged, poll_failed)
                aired_now = aged[1] if aged else ""

            busy = self._settle(busy, now)
            if busy != self.on_air:
                self.on_air = busy
                self._publish(busy)
                if busy:
                    self._clear.clear()
                    # Coming back? Then this is the next part of one break, not
                    # a new one. Cancel the return and stay held — silently.
                    resumed = self._cancel_comeback()
                    log.info("on-air DJ is speaking — holding the call DJ back"
                             "%s", " (mid-return: same break)" if resumed else "")
                    if getattr(self, "air_log", None):
                        self.air_log.station(state or {})
                        self.air_log.replay((state or {}).get("recent") or [])
                        self.air_log.opened(
                            "the station is on air", buf=self.stream_buffer(),
                            text=aired_now)
                    if aired_now:
                        self.aired_text = aired_now
                    # One hand-over per forecast spell: the queued warning
                    # and the start it forecasts are the same busy edge.
                    vid = str((state or {}).get("voiceId") or "")
                    if not first and not resumed and (
                            not vid or vid != self._announced_id):
                        if vid:
                            self._announced_id = vid
                        # Cut the call DJ off mid-sentence if need be: the whole
                        # point is that the broadcast never hears itself doubled.
                        try:
                            session.interrupt()
                            if getattr(self, "air_log", None):
                                self.air_log.said("hand-over line")
                            session.say(
                                hand_line,
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
                    if getattr(self, "air_log", None):
                        self.air_log.replay((state or {}).get("recent") or [])
                        self.air_log.closed(
                            "voice.end" if verdict and verdict[0] == "clear"
                            else "the estimate ran out")
                    if self.stepped_away:
                        self.stepped_away = False
                        # NOT awaited: the loop has to keep watching while the
                        # DJ returns, or it cannot notice the next utterance of
                        # a banter break — which is what the 2s pad was for.
                        if getattr(self, "air_log", None):
                            self.air_log.said("back-from-air line")
                        self._comeback = asyncio.create_task(
                            comeback.come_back(self, session))
            first = False
            tick += 1
            await asyncio.sleep(self.PUSH_TICK)


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
