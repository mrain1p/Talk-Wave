"""Keeping the call DJ and the on-air DJ off each other's toes.

The two are the same person. Left alone they talk over each other — the caller
hears two of the same voice, and so does everyone listening to the station.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from livekit.agents import Agent, AgentSession

from station import StationClient

from .background import spawn

log = logging.getLogger("callin.agent")


def _air_path() -> Path:
    """Twin of api/hooks._air_path — the web process writes the last verified
    dj.say/dj.link push there, this process reads it. Duplicated rather than
    imported so the worker does not pull the HTTP surface in for one path
    string; TestThePushFileHasOneAddress pins the two derivations together."""
    return Path(os.environ.get("CALLIN_HOOK_AIR_PATH")
                or Path(os.environ.get("CALLIN_HOOK_SECRET_PATH")
                        or Path(__file__).parent.parent.parent
                        / "data" / "hook-secret.json").with_name("hook-air.json"))


# The duck, at each end, in seconds. The operator's number, and the only
# padding in this file — every hold is "as long as the voice actually runs,
# plus this". It replaced a pile of separately-reasonable constants (a 2s
# handoff lag, a +1s fudge on two branches, a 12s floor under the word
# estimate, a 25s default for our own actions) which were individually small
# and stacked into holds of half a minute. One number you can turn.
DUCK_PAD_SECS = 4.5


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
    # No 12s floor any more: it meant a four-word station ID gagged the call
    # for twelve seconds and the caller sat through most of it in silence.
    # The pad is added by the caller (DUCK_PAD_SECS), once, so it is not
    # baked in here as well.
    return max(2, min(180, int(words / 2.4)))


class OnAirGuard:
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

    # One busy spell, not five. A banter break is several utterances back to
    # back — voice.end, then voice.queued again a second later — and each gap
    # used to reopen the gate, so the caller heard "right, where were we" and
    # "hold on, I'm on air" three times over one break (operator-reported
    # from a real call, 2026-08-12). Staying held across a gap this short
    # makes the whole break ONE hand-over and ONE return.
    #
    # 2s is a deliberate floor, not a round number: it has to outlast the gap
    # a mixer leaves between two utterances of one break, and every second of
    # it is added to the silence after a link that really HAS finished — the
    # same silence the operator called too long in that same report. Bridging
    # the break still wins, because the alternative is heard three times.
    SETTLE_SECS = 2.0

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
            log.info("our own action is going out on air — holding the call DJ back")

    def tail(self) -> float:
        """How long to keep holding after the voice itself has finished.

        The duck's close. Normally DUCK_PAD_SECS; a station that reports a
        genuinely long stream buffer gets that instead, because the caller
        really is that far behind the live edge and releasing sooner would
        put the DJ back over the top of what they are still hearing.
        """
        return max(self.duck_pad, self.stream_buffer())

    def stream_buffer(self) -> float:
        """How far behind the live edge the caller is, as last measured."""
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
                    {"talkwave.onair": "1" if on_air else ""}
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
        # lag_secs rides the tail: the entry is stamped at handoff, the sound
        # starts lag_secs later, so the words finish lag_secs later too.
        return since < self.lag_secs + speaking_secs(text, int(self.quiet_secs) or 30)

    def _pushed_state(self) -> dict | None:
        """The last verified push, raw. Two generations live in the file:
        legacy dj.say/dj.link entries (handoff-stamped, no "v") and the 1.8
        voice lifecycle ("v": 2 with a phase — queued / speaking / clear).
        """
        try:
            d = json.loads(_air_path().read_text())
            if float(d.get("at") or 0) <= 0:
                return None
            return d
        except Exception:                                     # noqa: BLE001
            return None

    # What one push entry proves about the air, at `now`.
    #   ("busy", text, line)  — hold; `line` is the hand-over sentence when
    #                            this edge deserves one of its own
    #   ("clear", "", "")     — POSITIVELY quiet (voice.end): outranks the
    #                            poll's word-sized estimate for the same
    #                            utterance, which has no idea it ended early
    #   None                  — this entry proves nothing right now (too old,
    #                            or a forecast still outside the hand-over
    #                            window; the poll's verdict stands)
    def _push_verdict(self, d: dict, now: float) -> tuple[str, str, str] | None:
        if not isinstance(d, dict):
            return None
        text = str(d.get("text") or "")
        if int(d.get("v") or 0) < 2:
            # Legacy handoff-stamped entry: busy while the words (plus the
            # handoff lag) are still airing — exactly the old behaviour.
            since = now - float(d.get("at") or 0)
            if self._log_says_busy((since, text)):
                return ("busy", text,
                        "Hold on a second — let me let that go out on air first.")
            return None
        phase = str(d.get("phase") or "")
        at = float(d.get("at") or 0)
        dur = max(0.0, float(d.get("durMs") or 0) / 1000.0)
        # THE fix for "it always comes back mid-sentence". Every voice.*
        # timestamp is stamped at the ENCODER; the caller is listening to the
        # stream, which is this many seconds behind it. So the station's
        # "I've stopped talking" is the caller's "he is still talking", every
        # time, by the same amount — which is why the overlap was constant
        # rather than occasional. The station measures the offset and sends it
        # (streamBufferSeconds, its #1114); we just never read it.
        #
        # Falls back to the handoff lag when the station is too old to send
        # one: better a two-second tail than none.
        buf = d.get("bufSecs")
        # Remembered for mark_on_air: our own actions get no push until the
        # station airs them, so without this they would size their hold from
        # the encoder's clock and come back before the caller had heard a word.
        try:
            if float(d.get("bufSecs") or 0) > 0:
                self._last_buf = float(d["bufSecs"])
        except (TypeError, ValueError):
            pass
        try:
            buf = float(buf)
        except (TypeError, ValueError):
            buf = None
        if buf is None or buf <= 0:
            buf = 0.0
        # One pad for every branch below — see DUCK_PAD_SECS.
        tail = max(getattr(self, "duck_pad", DUCK_PAD_SECS), buf)
        if phase == "queued":
            lead = float(d.get("airAt") or at) - now
            if lead > self.handover_secs:
                return None          # the call keeps flowing until it's close
            # Inside the window: hold from here until the voice has landed
            # and played out (voice.start/end refine this the moment they
            # arrive; this bound only matters if they never do).
            landed = float(d.get("airAt") or at) + (
                dur or speaking_secs(text, int(self.quiet_secs) or 30))
            if now < landed + tail:
                return ("busy", text,
                        "Hold that thought — I've got to go on air for a second.")
            return None
        if phase == "speaking":
            # Measured start; the clip length is measured too when present.
            # Both are encoder-side, so the whole window slides by the buffer.
            held_for = dur or speaking_secs(text, int(self.quiet_secs) or 30)
            if now - at < held_for + tail:
                return ("busy", text,
                        "Hold on a second — I'm on the air.")
            return None
        if phase == "clear":
            # voice.end fires when the encoder finished, not when the caller
            # did. Until the buffer has drained they are still hearing it, so
            # this entry does not prove quiet yet — say nothing and let the
            # poll's estimate carry the tail.
            if now - at < tail:
                return None
            return ("clear", "", "")
        return None

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
                    log.info("on-air DJ is speaking — holding the call DJ back")
                    if aired_now:
                        self.aired_text = aired_now
                    # One hand-over per forecast spell: the queued warning
                    # and the start it forecasts are the same busy edge.
                    vid = str((state or {}).get("voiceId") or "")
                    if not first and (not vid or vid != self._announced_id):
                        if vid:
                            self._announced_id = vid
                        # Cut the call DJ off mid-sentence if need be: the whole
                        # point is that the broadcast never hears itself doubled.
                        try:
                            session.interrupt()
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
                    if self.stepped_away:
                        self.stepped_away = False
                        await self._come_back(session)
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
