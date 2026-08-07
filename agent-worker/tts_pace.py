"""Whether the voice backend can keep up with the caller listening to it.

Time to first audio is the number everyone measures, and on the deployment
that motivated this file it was fine: ~1.5s, comfortably inside the bar. The
DJ started speaking on cue every time. What nobody measured was the pace
AFTER the first word — the same backend was generating at 1.6-2.3x realtime,
so it fell further behind with every sentence. A 45-word reply took 11.3s to
synthesise 6.8s of speech, which the caller hears as gaps, drag, and a DJ that
seems to be thinking mid-word.

Nothing errored. Nothing in the transcript looked wrong. The only evidence
that existed was the operator saying calls "felt laggy", which is not
something anyone can act on. So the pace is measured on every line and
reported once per call, into the call record, next to the config it ran under.

Kept out of `tts_adapter` because it is a different subject: that file is how
to speak through an arbitrary backend, this is whether that backend is fast
enough to be on a phone call.
"""

from __future__ import annotations

# Above 1.0 the backend is behind playback. A little headroom before calling
# it a fault: buffering absorbs a brief overrun, and on a single-GPU host the
# station's own on-air renders contend for the same card, so the occasional
# slow line is normal rather than broken.
FAULT_ABOVE = 1.15

# Below this there is not enough audio for the ratio to mean anything — one
# buffer's jitter dominates a very short line.
MIN_SECONDS = 1.0


class PaceMeter:
    """Synthesis time against playback time, accumulated over one call."""

    def __init__(self) -> None:
        self.lines = 0
        self.audio = 0.0      # seconds of speech produced
        self.wall = 0.0       # seconds spent producing it
        self.worst = 0.0      # the worst single line's ratio

    def note(self, wall: float, plays: float) -> None:
        """One synthesised line: how long it took, how long it plays for."""
        if plays < MIN_SECONDS or wall <= 0:
            return
        self.lines += 1
        self.wall += wall
        self.audio += plays
        self.worst = max(self.worst, wall / plays)

    def report(self) -> str:
        """What to write in the call record, or "" when the pace was fine."""
        if self.lines < 2 or self.audio <= 0:
            return ""
        rtf = self.wall / self.audio
        if rtf <= FAULT_ABOVE:
            return ""
        behind = self.wall - self.audio
        return (
            f"The TTS could not keep up with playback: {self.wall:.0f}s of "
            f"synthesis for {self.audio:.0f}s of speech across {self.lines} "
            f"lines ({rtf:.2f}x realtime, worst line {self.worst:.2f}x). The "
            f"caller heard roughly {behind:.0f}s of gaps, mostly in the longer "
            "replies. Time to first audio is not the problem here — the backend "
            "is generating slower than the caller hears it. Use a faster voice "
            "backend for the live leg, or lower its quality settings until this "
            "is under 1.0."
        )


def seconds_of_pcm(byte_count: int, sample_rate: int, channels: int) -> float:
    """How long raw 16-bit samples play for.

    Only valid for PCM. A compressed format would make this arithmetic produce
    a confident wrong number, which is worse than no number — so callers must
    check the adapter's encoding before reaching for this.
    """
    return byte_count / max(1, channels * 2) / max(1, sample_rate)
