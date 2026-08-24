"""The two numbers the duck is built from, where both halves can see them.

A leaf on purpose: air.py imports air_verdict, air_verdict needs the pad
and the word-count estimate, and a constant living in the module that
imports you is how a split turns into a circular import a week later.
"""

from __future__ import annotations


# The duck, at each end, in seconds. The operator's number, and the only
# padding in this file — every hold is "as long as the voice actually runs,
# plus this". It replaced a pile of separately-reasonable constants (a 2s
# handoff lag, a +1s fudge on two branches, a 12s floor under the word
# estimate, a 25s default for our own actions) which were individually small
# and stacked into holds of half a minute. One number you can turn.
DUCK_PAD_SECS = 4.5

# The largest listener buffer this sidecar believes, whatever a push or a
# /now-playing read claims. NOT a free dial: the greet hold (GREET_HOLD_SECS)
# must outlast a mid-link pickup's lag or every one times out over the top
# (the 2026-08-18 bug), and MAX_HOLD caps every duck — both are sized against
# this number, and a test pins the ordering. The station's own settings field
# goes to 60 (upstream #1451); raising this to follow means raising both
# ceilings with it, deliberately, not by turning this one number.
MAX_STREAM_BUFFER_SECS = 30.0


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
