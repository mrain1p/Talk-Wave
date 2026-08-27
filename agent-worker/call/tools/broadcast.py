"""Actions that make the on-air DJ produce sound.

Always local wrappers, never raw MCP. Two reasons: the wrappers hold the
overlap guard, and MCP's session timeout is shorter than a segment takes to
run — which turned a segment that was audibly playing into "that didn't work"
for the caller.
"""

from __future__ import annotations

import logging

from station import StationClient, booth_spoken_text

from ..actions import CallActions
from ..air import OnAirGuard, speaking_secs
from .shows import _match_show, _show_miss

log = logging.getLogger("callin.agent")

# The reserved show the station's genre lock pins (GENRE_LOCK_SHOW_ID upstream).
# Mirrored because clearing a lock and clearing a takeover are the same DELETE:
# without knowing which show id means "lock", lifting one would cancel the
# other, and the caller who asked about a genre would have undone the
# operator's takeover.
GENRE_LOCK_SHOW_ID = "genre_lock"



def build_on_air_tools(
    cfg: dict,
    station: StationClient,
    actions: CallActions,
    guard: OnAirGuard,
    guarded: bool = True,
    skills: list[str] | None = None,
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
            sharing with listeners. Write names and titles in their
            established Latin form (Ulfuls, Jay Chou) — the booth's voice
            does not read Han, kana or Hangul: those characters are dropped
            from the aired audio, not spoken."""
            if actions.at_limit():
                return actions.refusal()
            waited = await wait_for_clear_air()
            result = await station.dj_say(message, mode=mode, kind="callin")
            if not result.get("ok"):
                # Card the refusal — see CallActions.denied: the reason on
                # screen is the reason the persona cannot rewrite.
                actions.denied("refused",
                               result.get("error") or "the station refused it")
                return (
                    f"That didn't go out: {result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked. The "
                    "caller has been shown the refusal on a card."
                )
            actions.note("announcement", message[:120])
            # The gate closes now, not when the station log catches up — and
            # stays closed for as long as the station will actually be talking.
            # The spoken words ride along so the come-back line can nod at them.
            spoken = result.get("spoken") or message
            # What the booth will actually say: its TTS boundary drops native
            # script (#1455) before speaking, so the hold is sized from the
            # SURVIVING text — a mostly-native line airs short, and a hold
            # sized from the unspoken characters would gag the DJ over dead
            # air. Keyed on the styled `spoken`, not our input: the station
            # rewrites the words in styled mode, and what matters is what
            # reached its renderer. An entirely-scrubbed line falls back to
            # the original for a conservative hold.
            aired = booth_spoken_text(spoken)
            secs = speaking_secs(aired or spoken, 25)
            if result.get("unconfirmed"):
                # The station accepted it but had not aired it when it
                # answered, so a countdown from HERE measures the wrong thing
                # — the Ash call's hold expired before the delivery started.
                # Hold until the station's log shows it instead.
                guard.mark_pending_air(spoken)
            else:
                guard.mark_on_air(secs, spoken=spoken)
            out = after_action(
                "Your announcement", waited, result.get("unconfirmed"), secs)
            if aired != spoken:
                # The station took the line, but part of it will not be heard
                # — the caller must not be told their name went out in its
                # own script when what aired was the Latin text around it.
                out += (
                    " One caution: the booth dropped the native-script "
                    "characters from that line — they were not aired, not "
                    "spoken. If a name matters, put it through again in its "
                    "Latin form."
                )
            return out

        tools.append(announce)

    if cfg.get("allow_skills"):
        # The segments this DJ may run, as prepare() narrowed them: enabled,
        # ready, and assigned to the persona on air. The station's own manual
        # trigger honours NONE of that — it is documented as an operator
        # override and runs a skill even when it is switched off — so this is
        # the only place the operator's intent survives a caller asking.
        runnable = [str(s) for s in (skills or []) if str(s or "").strip()]

        @lk_llm.function_tool(name="subwave_run_skill")
        async def run_skill(name: str) -> str:
            """Run one of the station's own segments on air by name — for
            example weather, news, dedication, shoutout, storytime."""
            if actions.at_limit():
                return actions.refusal()
            wanted = str(name or "").strip()
            if runnable and wanted not in runnable:
                # Refused HERE rather than at the station, which would have run
                # it: the station's manual trigger ignores the enabled flag on
                # purpose. Naming the real list matters — a bare "no" sends the
                # model round again with a synonym.
                return (
                    f"'{wanted}' is not a segment you can run tonight. Yours "
                    f"are: {', '.join(runnable)}. Either run one of those or "
                    "tell the caller it isn't part of this show — do NOT try "
                    "another name for the same thing."
                )
            if not runnable:
                return (
                    "You have no segments to run on this show — the station's "
                    "list came back empty or none are assigned to you tonight. "
                    "Say so plainly rather than guessing at a name."
                )
            waited = await wait_for_clear_air()
            result = await station.run_skill(name)
            if not result.get("ok"):
                # The receipt channel's refusal half: the caller sees the card
                # whatever the DJ's prose does with it.
                actions.denied("refused",
                               result.get("error") or "the station refused it")
                return (
                    f"That segment didn't run: "
                    f"{result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            if result.get("aired") is False:
                # Station 1.8's stand-down (their #1416): the skill ran, looked
                # at what it fetched, and had nothing worth saying — a 200 with
                # `aired: false` and the reason, NOT an error. Before this
                # branch, that answer counted as success: the DJ told the
                # caller a segment was coming and the guard held the floor for
                # a minute of nothing. Strict `is False` on purpose — stations
                # older than 1.8 send no `aired` field at all, and absent must
                # keep meaning "it ran" or every segment on them goes silent.
                why = str(result.get("reason") or "").strip()
                return (
                    f"The {name} segment looked at what it had and chose not "
                    f"to air anything{' — ' + why if why else ''}. "
                    "Nothing is coming. Tell the caller that plainly, in your "
                    "own voice — do not promise it will play later."
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
                # The receipt channel's refusal half: the caller sees the card
                # whatever the DJ's prose does with it.
                actions.denied("refused",
                               result.get("error") or "the station refused it")
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
                # The receipt channel's refusal half: the caller sees the card
                # whatever the DJ's prose does with it.
                actions.denied("refused",
                               result.get("error") or "the station refused it")
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
            THIS is the tool for "change the DJ", "put Wade on", "switch to
            the jazz show" — a show change is never a song request. `show` is
            the show's name as the caller said it (a DJ's name finds their
            show). `minutes` defaults to an hour — pass more ONLY if they
            asked for longer. This changes what EVERYONE hears, not just this
            caller, and it outlasts the call, so use it when they have
            actually asked for it."""
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
                # Both rosters still reach the model on a real miss — a DJ
                # told only the show names invents a roster from them, which
                # is how a real persona got denied three times. But a miss
                # that is a near miss, or a DJ with several shows, says so
                # instead: see _show_miss.
                return _show_miss(shows, show, personas)
            # Whose show it is. The station knows; the DJ was guessing, and
            # guessed wrong on air — see the note on the receipt below.
            who = ""
            pid = str(picked.get("personaId") or "")
            for person in personas:
                if str(person.get("id") or "") == pid:
                    who = str(person.get("name") or "").strip()
                    break

            asked = int(minutes or 0) or 60
            window = max(StationClient.TAKEOVER_MIN_MINUTES,
                         min(StationClient.TAKEOVER_MAX_MINUTES, asked))
            result = await station.pin_show(picked.get("id"), window)
            if not result.get("ok"):
                # The receipt channel's refusal half: the caller sees the card
                # whatever the DJ's prose does with it.
                actions.denied("refused",
                               result.get("error") or "the station refused it")
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
            # WHOSE show, in the receipt. Observed twice, 2026-08-14: the
            # caller said "change the dj to duke", the model passed a show
            # name it had picked itself (THE OVERLOOK — Cliff's), the pin
            # worked exactly as asked, and the DJ then told the caller "Duke
            # is taking over with The Overlook". Nothing in the loop could
            # catch it: the argument was a real show, so `_match_show` was
            # right to resolve it, and the receipt only ever said which SHOW
            # was pinned. The station knows who presents it, so say so here
            # and the model can check its own work against the thing it was
            # actually asked for.
            whose = f" That is {who}'s show." if who else ""
            check = (
                f" The caller asked for a DJ — if {who} is not who they named, "
                "you have pinned the wrong show: tell them plainly and put the "
                "right one on." if who else "")
            return (
                f"Done — {name} is pinned for the next {window} minutes, and the "
                f"schedule picks up again after that.{whose}{corrected} It takes "
                "over at the end of the record that's playing now, not this "
                "second, so don't say it has already started. Everyone listening "
                "is about to get a different show, so say so in your own words — "
                f"and name whose show it is, not whose you assumed.{check}"
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
                # The receipt channel's refusal half: the caller sees the card
                # whatever the DJ's prose does with it.
                actions.denied("refused",
                               result.get("error") or "the station refused it")
                return (
                    f"That didn't cancel: "
                    f"{result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            actions.note("takeover lifted", "back to the weekly schedule")
            return (
                "Done — the takeover is off and the weekly schedule is back. Like "
                "the pin itself, it lands at the end of the current record rather "
                "than this second. Say so in your own words."
            )

        tools.append(cancel_takeover)

    if cfg.get("allow_genre_lock"):
        # The station's own quick control (SUB/WAVE #1404): it pins one
        # reserved show carrying a genre filter, using the same takeover
        # machinery — so the window bounds, the "re-post to replace" contract
        # and the next-track-boundary handover are the takeover's, already
        # documented above. Kept on its own switch rather than folded into
        # takeover: pinning a SHOW puts a named DJ on air and a listener can
        # hear whose it is, while this silently narrows what the station is
        # allowed to play, which is harder to notice and harder to attribute.
        @lk_llm.function_tool(name="subwave_genre_lock")
        async def genre_lock(genres: str, minutes: int = 60) -> str:
            """Hold the station to one genre or a few, for a while — "only
            jazz for the next two hours". `genres` is a comma-separated list in
            the caller's own words. This changes what EVERYONE hears and
            outlasts the call, like a takeover, so use it only when they have
            actually asked to lock the station to a style. For a single record,
            queue that record instead."""
            if actions.at_limit():
                return actions.refusal()
            wanted = [g.strip() for g in str(genres or "").split(",") if g.strip()]
            if not wanted:
                return ("No genre in that. Ask the caller which style they "
                        "mean and try again.")
            asked = int(minutes or 0) or 60
            window = max(StationClient.TAKEOVER_MIN_MINUTES,
                         min(StationClient.TAKEOVER_MAX_MINUTES, asked))
            result = await station.set_genre_lock(wanted, window)
            if result.get("unsupported"):
                # Not a failure and not the caller's fault: this station is
                # running a release without the control. Saying "that didn't
                # work" would send the DJ round again.
                return (
                    "This station's software doesn't have a genre lock yet, so "
                    "there is nothing to switch on. Tell the caller it isn't "
                    "something you can do here — do NOT retry, and do NOT "
                    "improvise it by pinning a show instead. You can still "
                    "queue records in that style one at a time."
                )
            if not result.get("ok"):
                # The receipt channel's refusal half: the caller sees the card
                # whatever the DJ's prose does with it.
                actions.denied("refused",
                               result.get("error") or "the station refused it")
                return (
                    f"That genre lock didn't go through: "
                    f"{result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            locked = result.get("genres") or wanted
            spoken = ", ".join(str(g) for g in locked)
            actions.note("genre lock", f"{spoken} for {window} min")
            corrected = ""
            if window != asked:
                corrected = (
                    f" They asked for {asked}; the desk only allows "
                    f"{StationClient.TAKEOVER_MIN_MINUTES}–"
                    f"{StationClient.TAKEOVER_MAX_MINUTES} minutes, so it's "
                    f"{window}. Say the real number."
                )
            dropped = ""
            if len(locked) < len(wanted):
                dropped = (" Some of what they named was a repeat or over the "
                           "station's limit, so the list is shorter than they "
                           "said — read back the ones that stuck.")
            return (
                f"Done — the station is locked to {spoken} for the next {window} "
                f"minutes.{corrected}{dropped} Like a takeover it lands at the end "
                "of the record playing now, not this second, and it ends by itself. "
                "Everyone listening is affected, so say so in your own words."
            )

        tools.append(genre_lock)

        @lk_llm.function_tool(name="subwave_clear_genre_lock")
        async def clear_genre_lock() -> str:
            """Lift a genre lock early and let the station play anything again.
            Use when the caller asks to undo one, or asks for normal
            programming back."""
            if actions.at_limit():
                return actions.refusal()
            # Checked before clearing, because the lock and an ordinary show
            # takeover are the SAME pin on the station's side: clearing blind
            # would cancel a takeover the operator set, from a caller who only
            # asked about a genre.
            pinned = (await station.schedule()).get("override") or {}
            show_id = str(pinned.get("showId") or "")
            if not show_id:
                return ("Nothing is pinned — there's no genre lock to lift. "
                        "Say so rather than implying you undid something.")
            if show_id != GENRE_LOCK_SHOW_ID:
                return (
                    "What's pinned right now is a SHOW takeover, not a genre "
                    "lock, so this won't lift it. If they want the schedule "
                    "back, that is subwave_cancel_takeover — check they mean "
                    "that before undoing someone else's takeover."
                )
            result = await station.clear_pinned_show()
            if not result.get("ok"):
                # The receipt channel's refusal half: the caller sees the card
                # whatever the DJ's prose does with it.
                actions.denied("refused",
                               result.get("error") or "the station refused it")
                return (
                    f"That didn't lift: "
                    f"{result.get('error') or 'the station refused it'}. "
                    "Tell the caller plainly — do not claim it worked."
                )
            actions.note("genre lock lifted", "the station can play anything again")
            return (
                "Done — the genre lock is off and the station can play anything "
                "again. It lands at the end of the current record rather than "
                "this second. Say so in your own words."
            )

        tools.append(clear_genre_lock)

    return tools


