"""Whether the model answers fast enough to be on a phone call.

The voice has `tts_pace`; this is the same question one leg earlier, and it was
learned the same way — from a deployment where nothing errored and the only
evidence was an operator saying calls didn't work.

A caller hears every second the model spends thinking, and the SDK's default
patience is 10s per attempt with 3 retries. For a STREAMED completion that 10s
is a READ timeout: it fires when the FIRST token hasn't arrived. So a
self-hosted model that is merely slow does not lag — it fails, four times, and
the caller gets the canned apology about twenty seconds after they stopped
speaking. A tester on ollama/qwen2.5:7b hit exactly that (2026-08-13) while the
settings panel told him "6185ms to first token — the call will lag": no turn on
that box ever completed, and the panel's own bench had under-measured it,
because a real call also carries the briefing, the persona and every tool schema
on every single turn.

Two things came out of that call and both live here: what a box is ALLOWED to
spend before a turn is abandoned, and a meter that says — in the call record,
in the operator's words — when the caller was kept waiting inside that
allowance. Slow and working is a state worth naming; it used to be invisible
right up until it became "the line's giving me trouble".
"""

from __future__ import annotations

# What a caller absorbs without hearing a pause. One number, three surfaces:
# this meter, the settings panel's model help, and the pipeline check's verdict.
# Keep them equal — an operator who reads two different targets trusts neither.
DESIRED_FIRST_TOKEN = 1.5

# Providers that run on hardware the operator owns. The distinction is not
# "local" in the network sense but "nobody else is keeping this warm for you":
# these are the ones where a big prompt, a cold model or a busy GPU turn a
# working model into a failing one.
SELF_HOSTED = ("ollama", "openai-compatible", "locca")

# Longer per attempt, and FEWER attempts to spend it in. Retrying a local model
# that is still thinking does not make it think faster — it queues the same
# generation behind the one already running, so three retries on a slow box buy
# nothing and cost the caller half a minute.
SELF_HOSTED_TIMEOUT = 30.0
SELF_HOSTED_RETRIES = 1

# The clouds keep the SDK's own defaults. A cloud model that has not produced a
# token in ten seconds is having an outage, not thinking hard, and retrying it
# is exactly the right move.
CLOUD_TIMEOUT = 10.0
CLOUD_RETRIES = 3


def attempt_budget(provider: str) -> tuple[float, int]:
    """(seconds allowed per attempt, retries after the first) for a provider."""
    if str(provider or "").lower() in SELF_HOSTED:
        return SELF_HOSTED_TIMEOUT, SELF_HOSTED_RETRIES
    return CLOUD_TIMEOUT, CLOUD_RETRIES


class ThinkMeter:
    """How long the model kept the caller waiting, over one call."""

    def __init__(self, label: str = "", budget: float = CLOUD_TIMEOUT) -> None:
        self.label = label          # provider/model, so the sentence names it
        self.budget = budget
        self.turns = 0
        self.slow = 0
        self.worst = 0.0
        self.total = 0.0
        self.abandoned = 0

    def note(self, ttft: float) -> None:
        """One reply's time to first token, in seconds."""
        if ttft <= 0:
            return
        self.turns += 1
        self.total += ttft
        self.worst = max(self.worst, ttft)
        if ttft > DESIRED_FIRST_TOKEN:
            self.slow += 1

    def gave_up(self) -> None:
        """One attempt stopped waiting for a first token that never came."""
        self.abandoned += 1

    def report(self) -> str:
        """What the call record should say, or "" when the model kept up."""
        who = f" on {self.label}" if self.label else ""
        if self.abandoned:
            return (
                f"The model did not start answering within the {self.budget:.0f}s this "
                f"box allows — {self.abandoned} time(s){who} — so the turn was thrown "
                "away and retried, and the caller heard the apology line instead of a "
                "reply. This is a model that cannot carry a call on this hardware; it "
                "is not a network fault. Try a smaller model or a cloud provider, and "
                "re-run Model + tools in the settings panel."
            )
        if self.slow:
            typical = self.total / max(1, self.turns)
            return (
                f"The model kept the caller waiting: {self.slow} of {self.turns} replies "
                f"took longer than {DESIRED_FIRST_TOKEN:.1f}s to start (worst "
                f"{self.worst:.1f}s, typical {typical:.1f}s){who}. Nothing failed and "
                "the call completed — but that pause sits in front of every single "
                "reply, and it is most of the difference between a DJ and a kiosk."
            )
        return ""
