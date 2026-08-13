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
from ..air import OnAirGuard, speaking_secs

log = logging.getLogger("callin.agent")


def _match_show(shows: list[dict], wanted: str,
                personas: list[dict] | None = None) -> dict | None:
    """Find the show a caller named, without making the model look it up first.

    The station's takeover endpoint wants a showId. A caller says "put the
    late show on". Making the model fetch the schedule, hold the ids and pass
    the right one back is a turn of latency and a chance to hallucinate an id
    that 404s — so resolve it here, the same way the library wrapper resolves
    awkward phrasing rather than reporting a miss.

    Exact id, then exact name, then a unique substring. Ambiguity is NOT
    resolved by picking the first: two shows containing "night" and a caller
    who gets the other one is a station-wide change nobody asked for.

    **A DJ's NAME resolves to their show**, which is how callers actually ask
    — "change the DJ to Duke". The conduct has promised exactly that since
    0.10.93 ("a DJ's name resolves to their show") and this function could not
    do it: it read `name` and nothing else, so a real persona came back as no
    match. Observed 2026-08-13 — a caller asked for Duke Sterling three times,
    was told each time that no such DJ was on the roster, and only got there
    by naming his show (The Alibi Room) himself. Personas are matched AFTER
    shows so a show called after its host still wins on its own name.
    """
    want = str(wanted or "").strip().lower()
    if not want:
        return None
    for show in shows:
        if str(show.get("id") or "").lower() == want:
            return show
    named = [s for s in shows if str(s.get("name") or "").strip().lower() == want]
    if len(named) == 1:
        return named[0]
    partial = [s for s in shows if want in str(s.get("name") or "").lower()]
    if len(partial) == 1:
        return partial[0]

    # No show by that name — try the people. Exact first, then a unique
    # substring, so "duke" finds Duke Sterling but an ambiguous fragment
    # still refuses rather than picking one.
    by_person: list[dict] = []
    for persona in (personas or []):
        pname = str(persona.get("name") or "").strip().lower()
        if not pname:
            continue
        if pname != want and want not in pname:
            continue
        pid = str(persona.get("id") or "")
        by_person += [s for s in shows
                      if str(s.get("personaId") or "") == pid and pid]
    # Dedupe on id: two personas whose names both contain the fragment can
    # point at the same show, and that is still one unambiguous answer.
    unique = {str(s.get("id")): s for s in by_person}
    return next(iter(unique.values())) if len(unique) == 1 else None


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

    # How long the station will be talking is sized from the words it told us:
    # both /dj/say and /dj/skill return the text they are about to speak. The
    # formula (`speaking_secs`) lives in ..air so the guard's own poll sizes
    # its hold the same way — two definitions of "how long does speech take"
    # is how the tools and the reply gate end up disagreeing about whether
    # the air is busy.

    async def wait_for_clear_air() -> float:
        """Block until the on-air DJ stops. One source of truth (the guard),
        so a tool can't decide the air is clear while the reply gate thinks
        it's busy. Capped shorter than the guard's own limit — a caller
        waiting on an action they asked for needs an answer sooner."""
        if not guarded:
            return 0.0
        return await guard.wait_until_clear(timeout=20.0)

    def after_action(
        what: str, waited: float, unconfirmed: bool = False, secs: int = 25
    ) -> str:
        note = f"Waited {waited:.0f}s for the air to clear. " if waited >= 2 else ""
        # A slow confirmation is not a failure: the station took the action,
        # it just hadn't finished answering. Say it went through.
        if unconfirmed:
            note += "The station was slow to confirm, but it has gone through. "
        if not guarded:
            return (
                f"{note}{what} has been HANDED TO THE BOOTH and airs in a moment, "
                "in your own voice. It has NOT been heard yet — 'that went out "
                "already, everyone heard it' is false at this instant and the "
                "caller can check. Say it's on its way, in your own words."
            )
        if unconfirmed:
            # No honest number exists: the station accepted it but had not
            # aired it when it answered. The Ash call read the sized guess out
            # loud ("about twelve seconds") and the delivery landed after the
            # guess expired — over the DJ's next line.
            return (
                f"{note}{what} is about to go out on air, in your own voice — the "
                "station is lining it up now, so it may be a short moment. Tell "
                "the caller briefly that you're on air for a moment, then stay "
                "quiet until it's done — do not talk over yourself, and do not "
                "promise how long it takes. When it finishes, come back to them "
                "and pick the conversation up where you left it."
            )
        return (
            f"{note}{what} is going out on air now, in your own voice, and it runs "
            f"about {secs} seconds. You cannot be in two places at once: tell the "
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
            # The gate closes now, not when the station log catches up — and
            # stays closed for as long as the station will actually be talking.
            # The spoken words ride along so the come-back line can nod at them.
            spoken = result.get("spoken") or message
            secs = speaking_secs(spoken, 25)
            if result.get("unconfirmed"):
                # The station accepted it but had not aired it when it
                # answered, so a countdown from HERE measures the wrong thing
                # — the Ash call's hold expired before the delivery started.
                # Hold until the station's log shows it instead.
                guard.mark_pending_air(spoken)
            else:
                guard.mark_on_air(secs, spoken=spoken)
            return after_action(
                "Your announcement", waited, result.get("unconfirmed"), secs)

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
            # Segments run far longer than an announcement — a fixed 25s hold
            # reopened the gate mid-delivery and the DJ talked over its own
            # voice on the broadcast. 60s is the fallback when the station
            # doesn't tell us what it said.
            secs = speaking_secs(result.get("spoken"), 60)
            if result.get("unconfirmed"):
                # Same shape as the announcement: unconfirmed means the clock
                # cannot start here. Segments are the case that made the
                # station slow in the first place.
                guard.mark_pending_air(result.get("spoken") or "")
            else:
                guard.mark_on_air(secs, spoken=result.get("spoken") or "")
            return after_action(
                f"The {name} segment", waited, result.get("unconfirmed"), secs)

        tools.append(run_skill)

    # --- station-wide, opt-in ---------------------------------------------
    # Both of these reach every listener rather than just the caller, which is
    # why they are off by default. They are wrappers rather than allowlisted
    # MCP tools on purpose: only a wrapper consults the per-call action cap,
    # so served over MCP they would be unlimited.

    if cfg.get("allow_dj_segment"):
        @lk_llm.function_tool(name="subwave_dj_segment")
        async def dj_segment(type: str) -> str:
            """Fire one of the station's scripted beats on air: station-id,
            hourly, link, banter, or a programme-intro/feature/outro. A caller
            asking for a station ID or the top of the hour by name means THIS
            tool, not an announcement — an announcement is you improvising a
            line; this is the station's own produced beat, and they asked for
            the real one. Otherwise these are the programme's furniture, not a
            listener request — reach for one only when the moment genuinely
            calls for it."""
            if actions.at_limit():
                return actions.refusal()
            waited = await wait_for_clear_air()
            result = await station.dj_segment(type)
            if not result.get("ok"):
                return (
                    f"That segment didn't fire: "
                    f"{result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            actions.note("segment", type)
            secs = speaking_secs(result.get("spoken"), 30)
            guard.mark_on_air(secs, spoken=result.get("spoken") or "")
            return after_action(
                f"The {type} beat", waited, result.get("unconfirmed"), secs)

        tools.append(dj_segment)

    if cfg.get("allow_skip_track"):
        @lk_llm.function_tool(name="subwave_skip_track")
        async def skip_track() -> str:
            """Cut the track that is playing right now and move to the next
            one. This affects EVERYONE listening, not just the caller, so use
            it when they have actually asked — never to make room for
            something you are queueing."""
            if actions.at_limit():
                return actions.refusal()
            result = await station.skip_track()
            if not result.get("ok"):
                return (
                    f"That didn't skip: "
                    f"{result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            actions.note("skip", "current track")
            # No hold: skipping makes no speech of its own, so the DJ can keep
            # talking. The next track simply starts.
            return (
                "Done — that track is ended and the next one is coming in. "
                "Say so in your own words, and remember everyone listening "
                "just had it cut short."
            )

        tools.append(skip_track)

    if cfg.get("allow_takeover"):
        # The furthest-reaching thing on this line. Not because it is loud —
        # it makes no sound of its own — but because it is the only one whose
        # effect outlives the call: everything else here is over in a minute,
        # and this changes what the station IS for the next hour.
        @lk_llm.function_tool(name="subwave_takeover_show")
        async def takeover_show(show: str, minutes: int = 60) -> str:
            """Put a different show on air, ahead of the schedule, for a while.
            `show` is the show's name as the caller said it. `minutes` defaults
            to an hour — pass more ONLY if they asked for longer. This changes
            what EVERYONE hears, not just this caller, and it outlasts the
            call, so use it when they have actually asked for it."""
            if actions.at_limit():
                return actions.refusal()

            shows = (await station.schedule()).get("shows") or []
            if not shows:
                return (
                    "I can't read the station's show list, so there's nothing to "
                    "put on. Tell the caller plainly — do not guess at a name."
                )
            # The people as well as the programmes: a caller naming a DJ is
            # the commonest way this is asked, and it used to miss entirely.
            personas = await station.personas()
            picked = _match_show(shows, show, personas)
            if not picked:
                names = ", ".join(
                    str(s.get("name") or "").strip() for s in shows
                    if str(s.get("name") or "").strip())
                djs = ", ".join(
                    str(p.get("name") or "").strip() for p in personas
                    if str(p.get("name") or "").strip())
                # Both lists, because the caller may have named either — and
                # a DJ told only the show names invents a roster from them,
                # which is how a real persona got denied three times.
                return (
                    f"No show matches \"{show}\" — or more than one does. The "
                    f"station's shows are: {names}."
                    + (f" Its DJs are: {djs}." if djs else "")
                    + " Ask the caller which one they mean and try again with "
                    "that name. Do NOT tell them a name is missing from the "
                    "roster unless it is absent from BOTH lists above."
                )

            asked = int(minutes or 0) or 60
            window = max(StationClient.TAKEOVER_MIN_MINUTES,
                         min(StationClient.TAKEOVER_MAX_MINUTES, asked))
            result = await station.pin_show(picked.get("id"), window)
            if not result.get("ok"):
                return (
                    f"That takeover didn't go through: "
                    f"{result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            name = str(picked.get("name") or "that show").strip()
            actions.note("takeover", f"{name} for {window} min")
            # Said out loud because the caller will otherwise be told the show
            # has "just started" while the current record is still playing —
            # the station airs the handover at the next track boundary.
            corrected = ""
            if window != asked:
                corrected = (
                    f" They asked for {asked}; the desk only allows "
                    f"{StationClient.TAKEOVER_MIN_MINUTES}–"
                    f"{StationClient.TAKEOVER_MAX_MINUTES} minutes, so it's "
                    f"{window}. Say the real number."
                )
            return (
                f"Done — {name} is pinned for the next {window} minutes, and the "
                f"schedule picks up again after that.{corrected} It takes over at "
                "the end of the record that's playing now, not this second, so "
                "don't say it has already started. Everyone listening is about to "
                "get a different show, so say so in your own words."
            )

        tools.append(takeover_show)

        @lk_llm.function_tool(name="subwave_cancel_takeover")
        async def cancel_takeover() -> str:
            """Cancel a show takeover early and hand the schedule back. Use
            when the caller asks to undo one, or asks for normal programming
            back. Harmless if nothing is pinned."""
            if actions.at_limit():
                return actions.refusal()
            result = await station.clear_pinned_show()
            if not result.get("ok"):
                return (
                    f"That didn't cancel: "
                    f"{result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            actions.note("takeover", "cancelled — back to the schedule")
            return (
                "Done — the takeover is off and the weekly schedule is back. Like "
                "the pin itself, it lands at the end of the current record rather "
                "than this second. Say so in your own words."
            )

        tools.append(cancel_takeover)

    return tools


