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
        # One pad for every branch below — see tail(). `buf` is what the
        # station CLAIMED, and it is a burst size rather than a playhead, so
        # it does not size the close here either.
        tail = getattr(self, "duck_pad", DUCK_PAD_SECS)
        if phase == "queued":
            lead = float(d.get("airAt") or at) - now
            # The hand-over window is measured against when the CALLER hears
            # it, not when the encoder airs it. They are `caller_lag` apart,
            # and ducking on the encoder's clock spent that whole gap as dead
            # air: the hand-over line, then two or three seconds of nothing,
            # then the broadcast. Shrinking the window by the lag lands the
            # line where it belongs — just before they actually hear it.
            window = max(0.0, self.handover_secs - self.caller_lag())
            if lead > window and not self.on_air:
                return None          # the call keeps flowing until it's close
            # ALREADY holding and the station has queued another one? Then this
            # break is not over, however far out the forecast is, and the hold
            # continues. THE bridge for a multi-part break, and it is driven by
            # the station's own warning rather than by a blanket pad.
            #
            # Measured 2026-08-13, a real call: two voice.queued landed at
            # +12.6s while the caller was already on hold, the forecast was
            # further out than the hand-over window, so this returned None, the
            # estimate ran out 0.2s later and the line was released — then
            # re-held at +17.8s. The caller got a return line and a second
            # hand-over line for one continuous break. The hand-over window is
            # about when to START a hold from quiet; it was never meant to
            # decide whether to END one while more speech is queued.
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
