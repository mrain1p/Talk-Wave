"""What the caller actually heard, and how long they waited for it.

Two numbers this system has never had, and one it had and threw away.

**The wait.** `ThinkMeter` times the model's first token and `firstWordAt`
stamps the first audio of the whole call. Neither is the number a caller
experiences, which is the gap between them finishing a sentence and hearing a
reply — endpointing included. That last word matters: `min_endpointing_delay`
and `max_endpointing_delay` are settings an operator can turn in the panel,
and every existing instrument measures a leg that starts AFTER they have had
their effect. So the one dial whose whole job is to trade responsiveness
against interrupting people has never appeared in any measurement taken here.

**The barge-in.** `allow_interruptions` and `min_interruption_secs` are in the
panel too and nothing has ever measured them either. On the beta line that
stopped being an academic gap: `call/tee.py` will not air a DJ line the caller
talked over before 60% of it played, so turning the interruption dial now
decides how many turns silently vanish from the broadcast. Tuning it blind
puts holes in the station.

Measured as a PAIR, always, and the reason is worth writing down because the
temptation to report the flattering half is real: a line that answers faster
by cutting people off is not a better line. Converse publish their two numbers
together for the same reason and say so out loud, which is the one piece of
their methodology worth taking wholesale.

**And what was thrown away.** `PlaybackFinishedEvent` has carried
`playback_position`, `interrupted` and `synchronized_transcript` all along —
how much of a line actually reached the caller's ears, and the partial text
of it. Until beta nothing read them; on beta `call/tee.py` reads them to
decide whether a cut line airs, and throws them away afterwards. So the
transcript in the record is still what the DJ SAID, and on any call where
somebody talked over the DJ that is not what anyone heard.

That is an evaluation problem before it is a logging one. Every scenario set,
every postmortem and every "did the DJ repeat itself" judgement reads that
transcript. Where the two disagree, the grader is reading a call that did not
happen.

**Reading this against `ThinkMeter`, because the two look like they disagree
and do not.** They measure different populations on purpose. `replyGap` counts
only replies the caller actually HEARD — a turn the model failed to produce
never reaches audio, so it is absent here while still landing in ThinkMeter's
average. On the first deployed call this file ever ran (2026-08-18), a Gemini
504 pushed ThinkMeter's `typical` to 3.2s while `replyGap` p50 read 1.24s, and
the numbers were both correct: `typical` is a MEAN (`total / turns`) with a
10.9s failure in four samples, and three of those four started in under 1.5s.
Do not "fix" one to match the other.

What this measures precisely: from the session declaring the caller has
stopped, to the DJ's audio starting. Endpointing sits inside that window and
is the point. What sits OUTSIDE it is the VAD's own silence-confirmation
window — the SDK knows the true end-of-speech instant and passes it internally
as `last_speaking_time`, but `UserStateChangedEvent` does not carry it, so it
cannot be reached from here. The number is therefore a floor on what the
caller waited, never an overstatement.

The wait has TWO start paths, and the second exists because the first is
blind on a held talk bar: the bar-hold claims the user turn, the SDK pins
`user_state` while a claim is active, and the transition this module listens
for never fires — all four of the operator's real PTT calls on 2026-08-18
wrote `replyGap n=0` while tap-to-latch calls measured fine. So
`lifecycle.attach_turn_commit` also stamps `caller_stopped()` at the moment
it commits a bar release, which is the caller explicitly saying "your turn".
On surfaces where both paths fire, the later stamp merely restarts a wait a
moment before the reply — the recorded gap only ever shrinks by that sliver.

Nothing here may ever cost the turn it is measuring: every handler swallows
its own errors, exactly like `attach_think_pace`.
"""

from __future__ import annotations

import logging
import time

from livekit.agents import AgentSession

log = logging.getLogger("callin.agent")

# A gap longer than this is not a reply, it is a new topic — the caller went
# quiet, the idle ladder said something, or they simply sat there. Counting it
# as "how long a reply took" would put a minute-long pause in the same average
# as a 900ms one and make the whole measurement useless.
MAX_REPLY_GAP_SECS = 30.0

# Below this, nothing intelligible reached the caller and there is no cut-off
# worth recording. Same call `call/tee.py` makes with MIN_CLIP_SECS, for the
# same reason: an interrupted playback of 0.02s is not a sentence somebody
# talked over, it is a synthesis that was cleared before it started. The first
# deployed call wrote two such entries and zero barge-ins, which reads as "the
# caller cut the DJ off twice" and is simply not what happened.
MIN_CUT_SECS = 0.25


def _percentile(values: list[float], pct: float) -> float:
    """p50/p90 without pulling in statistics for two call sites.

    Nearest-rank, which on the handful of turns a call actually contains is
    the honest answer — interpolating between two samples invents a number
    that no caller waited.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, int(round(pct / 100.0 * len(ordered))))
    return round(ordered[min(rank, len(ordered)) - 1], 2)


class HeardMeter:
    """One call's worth of what the caller experienced.

    Built per call and read once at the end, the way ThinkMeter is.
    """

    def __init__(self) -> None:
        # Caller stopped talking at this moment and is now waiting. 0 = not
        # waiting on us.
        self._waiting_since = 0.0
        # The caller started talking over the DJ at this moment. 0 = not
        # barging.
        self._barge_since = 0.0
        self._agent_speaking = False
        self.replies: list[float] = []
        self.barge_ins: list[float] = []
        # Lines the caller talked over: what the DJ said, and how much of it
        # actually reached them.
        self.cut_off: list[dict] = []

    # -- the wait ----------------------------------------------------------
    def caller_stopped(self) -> None:
        self._waiting_since = time.monotonic()

    def caller_started(self) -> None:
        # They are talking, so they are not waiting. Also the moment a
        # barge-in begins, if the DJ is mid-sentence.
        self._waiting_since = 0.0
        if self._agent_speaking:
            self._barge_since = time.monotonic()

    def dj_speaking(self) -> None:
        self._agent_speaking = True
        if not self._waiting_since:
            return
        gap = time.monotonic() - self._waiting_since
        self._waiting_since = 0.0
        if 0 <= gap <= MAX_REPLY_GAP_SECS:
            self.replies.append(gap)

    def dj_quiet(self) -> None:
        self._agent_speaking = False

    def held_for_air(self) -> None:
        """Drop the wait in progress: this reply was late on purpose."""
        self._waiting_since = 0.0

    # -- the barge-in ------------------------------------------------------
    def playback_finished(self, played: float, interrupted: bool,
                          heard_text: str = "", said_text: str = "") -> None:
        self._agent_speaking = False
        if not interrupted:
            self._barge_since = 0.0
            return
        if self._barge_since:
            # Time to silence: from the caller's first sound over the top of
            # the DJ to the DJ's audio actually stopping.
            self.barge_ins.append(round(time.monotonic() - self._barge_since, 3))
            self._barge_since = 0.0
        if float(played or 0) < MIN_CUT_SECS:
            return          # cleared before it started — see MIN_CUT_SECS
        entry: dict = {"playedSecs": round(float(played or 0), 2)}
        if heard_text:
            entry["heard"] = heard_text[:400]
        if said_text and said_text != heard_text:
            entry["said"] = said_text[:400]
        self.cut_off.append(entry)

    # -- the verdict -------------------------------------------------------
    def summary(self) -> dict:
        """The pair, plus what it cost. Empty dict when nothing was measured,
        so a call that never got going writes nothing rather than zeroes."""
        if not (self.replies or self.barge_ins or self.cut_off):
            return {}
        out: dict = {}
        if self.replies:
            out["replyGap"] = {
                "n": len(self.replies),
                "p50": _percentile(self.replies, 50),
                "p90": _percentile(self.replies, 90),
                "worst": round(max(self.replies), 2),
            }
        # Reported even at zero WHENEVER a reply was measured — "nobody
        # interrupted" is a result, and a latency number published on its own
        # is the half-truth this module exists to prevent.
        if self.replies or self.barge_ins:
            out["bargeIn"] = {
                "n": len(self.barge_ins),
                "p50": _percentile(self.barge_ins, 50),
                "worst": round(max(self.barge_ins), 3) if self.barge_ins else 0.0,
            }
        if self.cut_off:
            out["cutOff"] = self.cut_off[:20]
        return out


def attach_heard(session: AgentSession, meter: HeardMeter, air=None) -> None:
    """Watch the two state machines and the audio output.

    `air` is the on-air guard, and it is why a hold does not poison the
    numbers: while the broadcast has the microphone the call DJ is waiting on
    purpose, sometimes for tens of seconds, and counting that as "how long a
    reply took" would make the ducking look like latency. The wait is dropped
    rather than clamped — a held turn is not a slow turn, it is a different
    thing, and averaging it in would hide both.
    """

    def _on_user_state(ev) -> None:
        try:
            state = str(getattr(ev, "new_state", "") or "")
            if state == "speaking":
                meter.caller_started()
            elif state == "listening":
                meter.caller_stopped()
        except Exception:                                       # noqa: BLE001
            pass

    def _on_agent_state(ev) -> None:
        try:
            state = str(getattr(ev, "new_state", "") or "")
            if state == "speaking":
                if air is not None and getattr(air, "on_air", False):
                    # Held for the broadcast — see the docstring.
                    meter.held_for_air()
                meter.dj_speaking()
            else:
                meter.dj_quiet()
        except Exception:                                       # noqa: BLE001
            pass

    def _on_playback(ev) -> None:
        try:
            meter.playback_finished(
                played=float(getattr(ev, "playback_position", 0) or 0),
                interrupted=bool(getattr(ev, "interrupted", False)),
                heard_text=str(getattr(ev, "synchronized_transcript", "") or ""),
            )
        except Exception:                                       # noqa: BLE001
            pass

    session.on("user_state_changed", _on_user_state)
    session.on("agent_state_changed", _on_agent_state)

    # The OUTERMOST audio output, which on an on-air call is call/tee.py's
    # DJTee rather than the room sink. Attached by the caller after the tee is
    # installed for exactly that reason — a listener on the object the tee
    # replaced would be watching a chain nobody plays through any more.
    out = getattr(getattr(session, "output", None), "audio", None)
    if out is None:
        log.debug("no audio output to watch — the heard meter stays empty")
        return
    try:
        out.on("playback_finished", _on_playback)
    except Exception as e:                                      # noqa: BLE001
        # An SDK that renames the event must not take the call with it.
        log.debug("could not watch playback: %s", e)
