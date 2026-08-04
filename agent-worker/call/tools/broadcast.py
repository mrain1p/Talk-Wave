"""Actions that make the on-air DJ produce sound.

Always local wrappers, never raw MCP. Two reasons: the wrappers hold the
overlap guard, and MCP's session timeout is shorter than a segment takes to
run — which turned a segment that was audibly playing into "that didn't work"
for the caller.
"""

from __future__ import annotations

import logging

from station import StationClient

from ..actions import CallActions
from ..air import OnAirGuard

log = logging.getLogger("callin.agent")


def build_on_air_tools(
    cfg: dict,
    station: StationClient,
    actions: CallActions,
    guard: OnAirGuard,
    guarded: bool = True,
) -> list:
    """On-air actions that keep the call and the broadcast from colliding.

    The call DJ and the on-air DJ are the same persona, so the two can end up
    talking at once. Rather than blocking the action (which just makes the DJ
    seem broken to the caller), these wait for the air to clear if it's busy,
    fire the action, then tell the agent to step back from the call while it
    plays — with a word to the caller either side, the way a real presenter
    would say "hold on, I'm on air".

    With the guard off the wrappers still stand in for the raw MCP tools —
    they're what keeps a slow-but-successful station action from being
    reported to the caller as a failure.
    """
    from livekit.agents import llm as lk_llm

    async def wait_for_clear_air() -> float:
        """Block until the on-air DJ stops. One source of truth (the guard),
        so a tool can't decide the air is clear while the reply gate thinks
        it's busy. Capped shorter than the guard's own limit — a caller
        waiting on an action they asked for needs an answer sooner."""
        if not guarded:
            return 0.0
        return await guard.wait_until_clear(timeout=20.0)

    def after_action(what: str, waited: float, unconfirmed: bool = False) -> str:
        note = f"Waited {waited:.0f}s for the air to clear. " if waited >= 2 else ""
        # A slow confirmation is not a failure: the station took the action,
        # it just hadn't finished answering. Say it went through.
        if unconfirmed:
            note += "The station was slow to confirm, but it has gone through. "
        if not guarded:
            return (
                f"{note}{what} is going out on air now, in your own voice. Tell "
                "the caller it's done, in your own words."
            )
        return (
            f"{note}{what} is going out on air now, in your own voice, and it runs "
            "roughly twenty seconds. You cannot be in two places at once: tell the "
            "caller briefly that you're on air for a moment, then stay quiet until "
            "it's done — do not talk over yourself. When it finishes, come back to "
            "them and pick the conversation up where you left it."
        )

    tools = []

    if cfg.get("allow_announcements"):
        @lk_llm.function_tool(name="subwave_dj_announce")
        async def announce(message: str, mode: str = "styled") -> str:
            """Put a short line on air, read by the on-air DJ in its own voice.
            Use for shoutouts, dedications, or anything from the call worth
            sharing with listeners."""
            if actions.at_limit():
                return actions.refusal()
            waited = await wait_for_clear_air()
            result = await station.dj_say(message, mode=mode, kind="callin")
            if not result.get("ok"):
                return (
                    f"That didn't go out: {result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            actions.note("announcement", message[:120])
            return after_action("Your announcement", waited, result.get("unconfirmed"))

        tools.append(announce)

    if cfg.get("allow_skills"):
        @lk_llm.function_tool(name="subwave_run_skill")
        async def run_skill(name: str) -> str:
            """Run one of the station's own segments on air by name — for
            example weather, news, dedication, shoutout, storytime."""
            if actions.at_limit():
                return actions.refusal()
            waited = await wait_for_clear_air()
            result = await station.run_skill(name)
            if not result.get("ok"):
                return (
                    f"That segment didn't run: "
                    f"{result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            actions.note("skill", name)
            return after_action(f"The {name} segment", waited, result.get("unconfirmed"))

        tools.append(run_skill)

    return tools


