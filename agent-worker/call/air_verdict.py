"""What one piece of evidence PROVES about the air, at this instant.

The seam its SPLITTING entry named, cut for real at 0.10.127 when the caller's
own lag measurement pushed air.py past its third raised ratchet. It is a real
one: everything here is a pure reading of evidence — a push entry, a poll
answer, a clock — and none of it touches the session, the room, the widget or
the come-back. The guard keeps the live half: the watch loop, the hand-over,
the microphone, the state everyone else reads.

A mixin rather than free functions so the methods move unchanged. They are
threaded through `self` in a dozen places (`duck_pad`, `handover_secs`,
`caller_lag`, `_assumed_until`, `_pending_until`, `_quiet_since`, `on_air`),
and rewriting all of that in the same commit as a timing change is how a
regression gets two candidate causes.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from .air_timing import DUCK_PAD_SECS, speaking_secs

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


class AirVerdict:
    """Reading the evidence. Mixed into OnAirGuard, which supplies the state."""

    def _log_says_busy(self, speech: tuple[float, str] | None) -> bool:
        """Whether the station's log says its DJ is still talking.

        The log records when an utterance STARTED and what was said — never
        when it finished — so the end is sized from the words themselves
        (`speaking_secs`), the same way the station's own voice serialiser
        holds its channel. FALLBACK_LINK_SECS is only for an entry with no
        words: as a fixed hold it either reopened the gate while a long segment
        was still mid-delivery or gagged the call for most of a minute over a
        one-line station ID.
        """
        if speech is None:
            return False
        since, text = speech
        # In CALLER time, like every other branch since 0.10.129. The log entry
        # is stamped at handoff and the caller hears it `caller_lag` later, so
        # the audible window is [lag, lag + words] and NOT [0, words].
        #
        # This was the half 0.10.129 missed. The push verdicts were slid and
        # the poll was not, so the poll went on opening holds the moment the
        # station's log moved — measured on a call: the hold opened at +1.7s
        # for a voice the caller would not hear until +23s, which is the
        # operator's report exactly ("most of the on-call completes before the
        # on-air commentary even airs").
        # The hand-over lead is built in here rather than at the call sites:
        # applying it at one of them was how the DJ stopped handing over out
        # loud at all.
        lag = self.caller_lag()
        words = speaking_secs(text, int(self.quiet_secs) or 30)
        return lag - self.handover_secs <= since < lag + words + self.duck_pad

    def hold_at_least_as_long_as(self, state: dict | None) -> None:
        """Floor an open hold on the station's OWN numbers for this utterance.

        Everything else here sizes a hold from an ESTIMATE — a word count off
        the log, or a forecast that ages between polls — and an estimate that
        comes up short does not merely end early, it makes the gate chatter.
        Read off the operator's call of 2026-08-16 (room f38cdab69cce):

            210.2  hold opened   callerLag=22.0  why=the station is on air
            211.2  hold closed   heldSecs=1.0    why=the estimate ran out
            216.2  hold opened   callerLag=22.0  why=the station is on air

        A ONE SECOND hold, for a voice that had started 16s earlier, runs 22.6s
        and which the caller had not begun to hear — they hear it from +215.7
        to +238. Opening and shutting the gate inside a second is worse than
        either answer on its own: the DJ gets a window it cannot use and the
        caller gets the duck twice.

        The push carries what the station actually knows — when it handed the
        clip over, how long it runs, and how far behind the listener is — so
        the caller stops hearing it at `at + buf + dur`, and no estimate should
        be allowed to close the gate before then. Only ever LENGTHENS a hold.
        """
        try:
            at = float((state or {}).get("at") or 0)
            dur = float((state or {}).get("durMs") or 0) / 1000.0
            buf = float((state or {}).get("bufSecs") or 0)
        except (TypeError, ValueError):
            return
        if at <= 0 or dur <= 0:
            return
        if buf <= 0:
            buf = self.caller_lag()
        self._assumed_until = max(self._assumed_until,
                                  at + buf + dur + self.duck_pad)

    def _stale_end(self, state: dict | None) -> bool:
        """True when a voice.end cannot possibly be the end of OUR action.

        The station's end event names the utterance that just finished. If it
        finished before we sent ours, it is the tail of what was already
        playing and proves nothing about the announcement the station has not
        aired yet. Its own method so the rule can be tested without running the
        watch loop — this closed a hold 0.2s after opening it on a live call.
        """
        if not self._ours_sent_at:
            return False
        return float((state or {}).get("at") or 0.0) <= self._ours_sent_at

    def _pushed_state(self) -> dict | None:
        """The last verified push, raw — the voice lifecycle ("v": 2 with a
        phase: queued / speaking / clear). A pre-1.8 entry still reads back
        here and is simply judged as no evidence; see _push_verdict.
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
        # THE VOICE LIFECYCLE ONLY. A pre-1.8 station's handoff-stamped
        # dj.say/dj.link entry used to get its own branch here; that whole
        # generation is gone (0.97.3, the operator's call: "assume everything
        # is up to date"). A station that still sends one now proves nothing
        # through this path and falls through to the poll, which reads the same
        # log the old branch did — so the behaviour degrades to the previous
        # generation's rather than breaking.
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
        # One pad for every branch below — see tail(). `buf` is what the
        # station CLAIMED, and it is a burst size rather than a playhead, so
        # it does not size the close here either.
        tail = getattr(self, "duck_pad", DUCK_PAD_SECS)
        # EVERY branch below works in CALLER time. The station's timestamps
        # are stamped at the encoder and the caller hears them `lag` seconds
        # later, so a window computed on the encoder's clock is simply the
        # wrong window — and the operator timed exactly that, twice:
        #
        #   0:36 hand-over, 0:48 back, and the commentary aired 0:53-1:03.
        #   1:10 hand-over, 1:18 back, and it aired 1:30-1:39.
        #
        # The hold and the broadcast never overlapped AT ALL. The duck ran
        # while the caller could still hear music and finished before they
        # heard a word of it.
        #
        # This was hidden for a while by tail() padding the close with the
        # station's 22, which covered the end by accident while still opening
        # seventeen seconds early — the operator's original "starts much too
        # early". 0.10.124 removed the pad on a measurement that turned out to
        # be reading buffer DEPTH rather than latency, which took the accident
        # away and left both ends wrong. Shifting the whole window is what
        # either end needed all along.
        lag = self.caller_lag()
        audible_from = float(d.get("airAt") or at) + lag
        if phase == "queued":
            # A queued voice while ALREADY holding means this break is not
            # over, however far out the forecast is — the bridge, driven by
            # the station's own warning rather than a blanket pad. The
            # hand-over window is about when to step away from a QUIET line;
            # it was never meant to decide whether to end a hold with more
            # speech queued. (Measured 2026-08-13: two voice.queued landed
            # mid-hold, were discarded as too distant, and the caller got a
            # return line and a second hand-over line for one break.)
            if self.on_air:
                return ("busy", text,
                        "Hold that thought — I've got to go on air for a second.")
            audible_to = audible_from + (
                dur or speaking_secs(text, int(self.quiet_secs) or 30))
            if now < audible_from - self.handover_secs:
                return None      # the call keeps flowing; they cannot hear it yet
            if now < audible_to + tail:
                return ("busy", text,
                        "Hold that thought — I've got to go on air for a second.")
            return None
        if phase == "speaking":
            # Measured start, and measured length when the station sends one.
            # Both are encoder-side, so the whole window slides by the lag.
            audible_to = audible_from + (
                dur or speaking_secs(text, int(self.quiet_secs) or 30))
            if now < audible_from - self.handover_secs:
                return None
            if now < audible_to + tail:
                return ("busy", text, "Hold on a second — I'm on the air.")
            return None
        if phase == "clear":
            # voice.end fires when the ENCODER finished. The caller is still
            # hearing it for `lag` seconds after that, so this proves nothing
            # until their copy has played out.
            if now < at + lag + tail:
                return None
            return ("clear", "", "")
        return None
