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

    "Busy" means ACTIVELY SPEAKING — or ABOUT TO BE, once the station's
    voice.queued forecast is inside the hand-over window. The voice lifecycle
    bounds the spell exactly (voice.start … voice.end, measured); the 4s log
    poll is the fallback, and there the end is estimated from the words
    (`speaking_secs`) plus the handoff lag. FALLBACK_LINK_SECS covers an entry
    with no words at all.
    """

    POLL_SECS = 4.0     # a station read per call every 4s, not per turn
    MAX_HOLD = 45.0     # never leave a caller in silence longer than this

    # HANDOFF-STAMPED evidence only — which now means the 4s log poll alone.
    # It is stamped when the audio is handed to the mixer, a couple of seconds
    # before it is audible, so the words finish that much later too and the gap
    # rides the hold's tail (0.10.69: the tail ending early was the reported
    # bug). The voice.* lifecycle stamps at AIR time and is held on exactly,
    # with no lag. It was an operator setting until
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

    # A typical station link, for an entry with no words to size the hold from.
    FALLBACK_LINK_SECS = 30.0

    def __init__(self, station: StationClient, cfg: dict, room=None) -> None:
        self.station = station
        self.room = room
        self.enabled = bool(cfg.get("avoid_on_air_overlap"))
        # HOW LONG A LINK RUNS when the station's log says a voice happened but
        # not what was said. A constant since 0.97.3, not a setting: the
        # station sends the words and their measured duration on every voice
        # push, so this is the fallback for a fallback — and as a settable
        # number it was a second off-switch for ducking (0 disabled the whole
        # watch loop) sitting next to the real one. Two switches for one
        # feature is the sort of thing the operator has to keep in their head
        # for nothing.
        self.quiet_secs = self.FALLBACK_LINK_SECS
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
        # Last streamBufferSeconds the station reported, primed from the last
        # push the WEB process wrote down: the buffer belongs to the station's
        # config, not to this call, and without priming the FIRST thing a call
        # aired sized its hold from the 2s fallback while the station had been
        # saying 22 all along (2026-08-16; see the test of the same name).
        try:
            pushed = self._pushed_state() or {}
            self._last_buf = float(pushed.get("bufSecs") or 0)
        except Exception:                                      # noqa: BLE001
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
        # WHEN we sent it, as well as how long we are holding for. A voice.end
        # names the utterance that just finished, and an utterance that
        # finished BEFORE we sent ours cannot be ours — see the watch loop.
        self._ours_sent_at = 0.0
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
        # SHIFTED BY THE CALLER'S LAG, like every other hold — this was the one
        # that never was. A push says the station is speaking NOW; our own
        # action only starts in the caller's ear `caller_lag` from now. Room
        # 72de3b8893fe: a 3.0s shoutout took a 7.5s hold, the DJ returned with
        # "I just sent that shoutout", then a SECOND 12.0s hold on the push.
        self._assumed_until = max(
            self._assumed_until,
            time.time() + self.caller_lag() + seconds + self.tail())
        self._ours_sent_at = time.time()
        if spoken:
            self.aired_text = str(spoken)
        self.stepped_away = True
        if self._clear.is_set():
            self._clear.clear()
            self.on_air = True
            self._publish(True)
            log.info("our own action is going out on air — holding the call DJ "
                     "back for %.1fs (%.1fs lag + %.1fs of words + %.1fs tail)",
                     self.caller_lag() + seconds + self.tail(),
                     self.caller_lag(), seconds, self.tail())
            if getattr(self, "air_log", None):
                self.air_log.opened("we put something on air",
                                    until=self._assumed_until,
                                    buf=self.stream_buffer(),
                                    lag=self.caller_lag(), text=spoken)

    # A tail is the caller's lag plus enough margin that a wobble in their
    # buffer cannot put the DJ over the top of the last word.
    TAIL_MARGIN_SECS = 1.5

    # Nobody is 40 seconds behind a live stream; a number like that is a stuck
    # element or a hostile page, and believing it would hold a caller silent
    # for most of a minute.
    MAX_CALLER_LAG = 20.0

    def caller_lag(self) -> float:
        """How far behind the live edge the caller hears the broadcast.

        The station's own `streamBufferSeconds` — its Icecast burst, which the
        player receives on connect and then plays through at 1x, so it stays
        that far behind for the whole connection.

        0.10.127 replaced this with a number the widget measured and 0.10.129
        put it back, because the widget was measuring the wrong quantity:
        `buffered.end - currentTime` is buffer DEPTH, not distance behind the
        live edge, and a player that started twenty seconds late and has been
        playing at 1x ever since has a shallow buffer and a large lag. It read
        2.3s where the operator's stopwatch, timing two hand-overs against the
        audio, said seventeen and twenty. The station's 22 was right.

        A browser cannot easily know its absolute position in a raw Icecast
        stream, so there is nothing better to measure with; if that changes,
        this is the one place to change.
        """
        return self.stream_buffer()

    def tail(self) -> float:
        """How long to keep holding after the voice itself has finished.

        The duck's close, and it is DUCK_PAD_SECS — the operator's own ask,
        "a consistent 4-5 second duck at the beginning and close".

        JUST the pad. It used to be max(duck_pad, stream_buffer), which
        double-counted: the lag belongs at the OPEN, where it shifts the whole
        window (see caller_lag, air_verdict, and mark_on_air, which was the
        last holdout until 0.97.12). Padding the close covered the end by
        accident while leaving the open seventeen seconds early.
        """
        return self.duck_pad

    def stream_buffer(self) -> float:
        """What the station last said its listeners are behind by.

        NOT the caller's playhead — see tail(). Recorded on the timeline and
        used for nothing else.
        """
        buf = getattr(self, "_last_buf", 0.0)
        return buf if buf > 0 else self.lag_secs

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
        self._ours_sent_at = time.time()
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
        if not self.enabled:
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
                # It used to lose to _assumed_until / _pending_until, and what
                # that bought was the caller held for the rest of a 25-second
                # estimate after the DJ had stopped talking — "held working the
                # booth way too long, I had to hang up". So the measurement
                # wins and the guesses are dropped rather than out-voted, or
                # they reassert the hold on the next tick.
                #
                # …UNLESS THE END PREDATES OUR OWN ACTION. voice.end names the
                # utterance that just finished, and one that finished before we
                # sent ours is the tail of what was ALREADY playing — it says
                # nothing about the announcement the station has not aired yet.
                # Measured on the operator's box, 2026-08-15 17:46:13: the hold
                # opened for our own 28.5s announcement, a voice.end landed
                # 0.2s later, the hold closed, and the DJ said its back-from-air
                # line and then talked over the announcement for half a minute
                # — "comes back a second before the on air voice even happens".
                if self._stale_end(state):
                    # Prove nothing, decide nothing: fall through to the one
                    # place that weighs everything, which keeps holding while
                    # our own window is open and releases when it expires.
                    busy = self._assess(aged, poll_failed)
                    aired_now = aged[1] if aged else ""
                else:
                    self._assumed_until = 0.0
                    self._pending_until = 0.0
                    self._ours_sent_at = 0.0
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
                            lag=self.caller_lag(), text=aired_now)
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
    """The caller's DJ, with two additions: its replies wait for quiet air, and
    a turn that showed the caller the door gets one word in its ear before the
    next one.

    Holding here rather than dropping input is deliberate. The caller's words
    are already transcribed and in the context by this point — only the REPLY
    is queued, so nothing they said is lost and they never have to repeat
    themselves just because the station was mid-link.

    This hook is also the SDK's own place to edit the context before the model
    answers, which is the only moment a correction can land BEFORE the words do.
    See call/door.py for why that matters and why the promise guard's shape does
    not transfer.
    """

    def __init__(self, instructions: str, guard: OnAirGuard, door=None) -> None:
        super().__init__(instructions=instructions)
        self._guard = guard
        self._door = door

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        if self._door is not None:
            said = getattr(new_message, "text_content", "") or ""
            hint = self._door.hint_for(said)
            if hint:
                # A system message at the tail, which is how the plugins deliver
                # per-turn instructions and how the harness feeds the idle
                # ladder. Not appended to the caller's own message: that text
                # reaches the written transcript, and a note from us inside
                # their line would be a record of something they never said.
                turn_ctx.add_message(role="system", content=hint)
                log.info("the last line held the door open — steering this one")
        waited = await self._guard.wait_until_clear()
        if waited >= 2:
            log.info("held the caller's reply %.0fs while the on-air DJ was talking",
                     waited)
