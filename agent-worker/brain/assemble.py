"""Joins the briefing to the conduct.

What's left here is only what needs BOTH halves: who the DJ is, which show
it's hosting, and the operator's house style — the identity header that the
facts and the rules hang off.
"""

from __future__ import annotations

from station import StationClient

from brain import conduct
from brain.briefing import (
    CARD_BUDGET,
    clip,
    demojibake,
    latest_programme_intro,
    station_context,
)


async def _station_switches(speak_clock, track_floor, talk_between_tracks,
                            pause_talk=None, show_id=""):
    """The four station settings the prompt mirrors, filled in where the
    caller did not pass them.

    The call path passes all four — its StationConfig has /settings cached,
    so they are free there. This covers the preview and chat paths, which
    build their own snapshot, with the single authed read they already make.
    Each falls back to the station's own default: the clock speaks, there is
    no length floor, talk is not held to the gaps, no show pauses for talk.
    """
    if speak_clock is not None and track_floor is not None             and talk_between_tracks is not None and pause_talk is not None:
        return speak_clock, track_floor, talk_between_tracks, pause_talk

    from station_config import StationConfig

    sc = StationConfig()
    try:
        if speak_clock is None:
            speak_clock = await sc.speak_clock()
        if track_floor is None:
            track_floor = await sc.track_floor()
        if talk_between_tracks is None:
            talk_between_tracks = await sc.talk_between_tracks_only()
        if pause_talk is None:
            pause_talk = await sc.pause_talk_seconds(str(show_id or ""))
    except Exception:                                          # noqa: BLE001
        pass
    finally:
        await sc.aclose()
    return (True if speak_clock is None else speak_clock,
            0 if track_floor is None else track_floor,
            False if talk_between_tracks is None else talk_between_tracks,
            0 if pause_talk is None else pause_talk)


async def _persona_mirrors(persona: dict, music_lean: str | None,
                           also_speaks: list[str] | None):
    """The admin-only persona fields the prompt mirrors — Musical Leanings
    (#1678) and the languages a multilingual DJ mixes in (#1740) — filled in
    where the caller did not pass them. The call path passes both off its
    cached /settings read; the preview and chat paths make ONE read here for
    both, not one each."""
    if music_lean is not None and also_speaks is not None:
        return music_lean, also_speaks
    from station_config import StationConfig

    pid = str(persona.get("id") or "")
    sc = StationConfig()
    try:
        if music_lean is None:
            music_lean = await sc.music_lean(pid)
        if also_speaks is None:
            also_speaks = await sc.also_speaks(pid)
    except Exception:                                         # noqa: BLE001
        pass
    finally:
        await sc.aclose()
    return music_lean or "", also_speaks or []


def _language_block(dj_language: str, also_speaks: list[str]) -> str:
    """Which language the DJ works in, and — on a multilingual persona
    (SUB/WAVE #1740) — which others it mixes in. Without the second half a DJ
    the booth runs bilingual answered the phone in one language only.

    Empty when the station names neither: empty means English there, and
    asserting "you speak English" at every DJ on every call would be a
    sentence bought on every turn to say what the prompt is already written
    in."""
    mix = [clip(demojibake(m), 40) for m in also_speaks if m][:8]
    if not (dj_language or mix):
        return ""
    primary = dj_language or "English"
    if not mix:
        return (f"\nYou work in {primary} — that is the language you open in and "
                "come back to. Match a caller who brings another one.\n")
    head = ", ".join(mix[:-1])
    others = f"{head} and {mix[-1]}" if head else mix[0]
    either = f"{head} or {mix[-1]}" if head else mix[0]
    # "Answer them in that language ... not a greeting and then English": the
    # softer "a caller who picks one gets it back" was measured on the deployed
    # brain (2026-11-27, Gemini flash-lite, a Spanish caller) and got a
    # Spanish greeting followed by English on half the replies; this got the
    # whole reply in Spanish on four of four.
    return (f"\nYou work in {primary} and also speak {others}, as you do on air. "
            f"Open in {primary}. The moment a caller speaks {either}, answer them "
            "in that language for the rest of the call - the whole reply, not a "
            f"greeting and then {primary}. A caller in any other language is "
            "matched too. Artist names and song titles stay exactly as written, "
            "never translated.\n")


def _show_blurb(show: dict) -> str:
    """The show's listener-facing blurb (SUB/WAVE 1.34, #2358) as a prompt
    line, or "": what the schedule and the On Now card tell listeners the show
    is. `topic` is the DJ's brief, written to the DJ; this is the station's own
    answer to a caller asking "what's this show about?". Only when set and not
    the brief again."""
    blurb = clip(demojibake(str(show.get("description") or "").strip()), 300)
    if not blurb or blurb == str(show.get("topic") or "").strip():
        return ""
    # "In your own words": asked only to "use this", the deployed brain read
    # the blurb out word for word on three replies in four (2026-11-27); this
    # wording, one in four, and every reply still on the show.
    return ("\nHow the station describes the show to listeners - what to get "
            "across when a caller asks what it is, in your own words rather "
            f"than read out:\n  {blurb}\n")


async def _lean_block(persona: dict, music_lean: str | None) -> str:
    """The station's Musical Leanings field (#1678) as one prompt block, or "".

    What this DJ reaches for when picking, mirrored so the call DJ and the
    booth lean the same way when a caller leaves the choice to them. Empty
    says nothing — most personas leave it blank, and a sentence about having
    no leanings would ride every turn. Resolved by _persona_mirrors.
    """
    lean = clip(demojibake(str(music_lean or "").strip()), CARD_BUDGET)
    if not lean:
        return ""
    return (f"\nYour musical leanings, as the station has them: {lean}\n"
            "Reach for these when a caller leaves the pick to you; a clear ask "
            "for something else wins.\n")


def _live_queue_read(mode: str) -> bool:
    """Will this line be handed subwave_station_state?

    The briefing's queue line points at it for "what's next NOW" (review,
    2026-11-15), and a sentence that names a tool the line has not got is how
    a DJ reaches, is refused, and narrates a look it never took. A call gets
    it over MCP — an always-on read in the registry. The text line has no
    MCP: its twin (call/tools/reads.py) is built only with station admin
    credentials, so that is the condition here. Asked of the platform layer
    because the brain may not import the registry.
    """
    if mode != "chat":
        return True
    from station_config import has_admin

    return has_admin()


async def _narrow_skills(snap: dict, persona: dict, show: dict) -> dict:
    """Narrow `snap["skills"]` to what this DJ may run; return what the
    station's own gates withheld (name -> why), read BEFORE the narrowing."""
    from station_config import StationConfig, runnable_skills, withheld_skills

    withheld = withheld_skills(snap["skills"])
    sc = StationConfig()
    try:
        assigned = await sc.persona_skills(str(persona.get("id") or ""))
    except Exception:                                         # noqa: BLE001
        assigned = None
    finally:
        await sc.aclose()
    snap["skills"] = runnable_skills(
        snap["skills"], assigned, bool((show or {}).get("guests")))
    return withheld


def _report_gates(gates: dict | None, snap: dict, withheld: dict) -> None:
    """Hand a caller-supplied `gates` dict the segments this prompt PRINTED.

    The text line builds its prompt here and its run-skill tool somewhere
    else, and gave the tool no list at all — so `subwave_run_skill` answered
    "You have no segments to run on this show" on every ask while the
    briefing above it named the segments the DJ could run (review,
    2026-11-15). The keys are `build_on_air_tools`' own keyword names, so the
    caller passes the dict straight through and the prompt and the tool read
    one narrowed list: there is no second read for them to disagree across.
    """
    if gates is None:
        return
    from station_config import _skill_name

    names = (_skill_name(s) for s in (snap.get("skills") or [])
             if isinstance(s, dict))
    gates["skills"] = [n for n in names if n]
    gates["switched_off"] = dict(withheld)


async def build_system_prompt(
    station: StationClient, persona: dict, snapshot: dict | None = None,
    cfg: dict | None = None, mode: str = "call",
    speak_clock: bool | None = None,
    track_floor: int | None = None,
    talk_between_tracks: bool | None = None,
    pause_talk: int | None = None,
    music_lean: str | None = None,
    gates: dict | None = None,
    also_speaks: list[str] | None = None,
) -> str:
    """`cfg` must be the settings ALREADY RESOLVED for this caller's tier.

    Loading them here is the fallback for the operator-facing previews, and it
    resolves at the admin tier — the fullest set the settings allow. A live
    call must pass its own: the permissions are tier strings now, `"off"` is
    truthy, and a prompt built from the raw values would promise the DJ every
    capability the operator had switched off. The call passes them; this
    signature exists so that forgetting is a change to this line rather than
    something nobody notices until a caller is told the DJ can run segments.
    """
    import settings as settings_store

    if cfg is None:
        cfg = settings_store.permissions_for(settings_store.load(), "admin")

    # A pre-fetched snapshot avoids repeating the station reads the caller is
    # already waiting on. Falls back to fetching if none was supplied.
    snap = snapshot or await station.snapshot(with_skills=bool(cfg.get("allow_skills")))
    # Resolved before the skills are narrowed: a co-hosted segment is only
    # runnable when somebody else is in the booth (#1534). No I/O - it works
    # off payloads the snapshot already carries.
    show = await station.active_show(snap["now_playing"], snap.get("schedule"))
    withheld: dict = {}
    if snapshot is None and snap.get("skills"):
        # Only when we fetched it ourselves: the call path narrows the
        # catalogue to the on-air DJ before handing it over (session.prepare),
        # and doing it twice would cost a second /settings read. This covers the
        # chat line and the operator previews, which build their own snapshot —
        # a preview that lists segments the DJ cannot run is exactly the
        # disagreement the narrowing exists to remove.
        withheld = await _narrow_skills(snap, persona, show)
    _report_gates(gates, snap, withheld)

    # The clock mirror (djSpeakClock, SUB/WAVE 1.8). The call path passes it
    # in — its StationConfig caches /settings, so the read is free there;
    # this fallback covers the preview and chat paths with one authed read.
    speak_clock, track_floor, talk_between_tracks, pause_talk =         await _station_switches(speak_clock, track_floor, talk_between_tracks,
                                pause_talk, show.get("id", ""))

    facts = await station_context(station, cfg, snap, show,
                                  speak_clock=speak_clock,
                                  persona_id=str(persona.get("id") or ""),
                                  track_floor=track_floor,
                                  talk_between_tracks=talk_between_tracks,
                                  pause_talk=pause_talk,
                                  live_queue_read=_live_queue_read(mode))

    # NAME_BUDGET: identity strings ride the opening line of the prompt on
    # every turn, and while soul/topic are clipped to CARD_BUDGET their short
    # siblings — the DJ name, the station name, the show name — were
    # interpolated raw (security sitting, 2026-08-28). A real name is a
    # handful of words; the cap only bites a corrupt or hostile /dj or
    # /schedule value, the same junk-guard briefing._fld applies to the
    # track fields.
    NAME_BUDGET = 120
    name = clip(str(persona.get("name", "the DJ")), NAME_BUDGET)
    # The station's own name, not ours. This line used to say "the SUB/WAVE
    # radio station" for everybody, so a DJ on Yosemite FM told callers they
    # were live on SUB/WAVE — the software's name, which no listener has ever
    # heard of. GET /dj has carried the real one all along.
    # /dj first; then the station name carried on the persona (recovered from
    # the last-known-good record when the live read timed out — otherwise a
    # station-wide timeout put a caller through to "SUB/WAVE" instead of the
    # real station name); the generic name only when nothing else is known.
    station_name = clip(demojibake(
        str((snap.get("dj") or {}).get("station")
            or persona.get("station") or "").strip()
    ), NAME_BUDGET) or "the SUB/WAVE radio station"
    dj_card = clip(persona.get("soul", ""), CARD_BUDGET)
    # The station's own answer to "what language does this DJ work in", mirrored
    # rather than guessed — free text there, empty meaning English. Stated as
    # its own line because the alternative is the model inferring it from the
    # briefing, and the briefing is full of whatever the station happens to be
    # playing. See station.persona_from for the call that went out in the wrong
    # language.
    # Capped like every sibling identity field — name, station and show all
    # take the same cap. dj_language is uncapped station free text that rides
    # the system prompt on every turn, so a corrupt or hostile /dj value
    # balloons time-to-first-token — the exact junk it was the only one of the
    # group to slip (top-down review, 2026-08-28).
    dj_language = clip(demojibake(str(persona.get("language") or "").strip()),
                       NAME_BUDGET)
    music_lean, also_speaks = await _persona_mirrors(persona, music_lean,
                                                     also_speaks)
    language_block = _language_block(dj_language, also_speaks)
    lean_block = await _lean_block(persona, music_lean)
    show_name = clip(demojibake(show.get("name", "")), NAME_BUDGET)
    show_card = clip(show.get("topic", ""), CARD_BUDGET)

    show_block = ""
    if show_name or show_card:
        show_block = f"\n# The show you're hosting: {show_name}\n{show_card}\n"
    show_block += _show_blurb(show)

    # The Show Card is the standing format; this is what tonight's episode is
    # actually about. It only exists on programme shows, and it was being
    # thrown away by the schedule lookup in `active_show` — so the DJ knew the
    # show it hosts every week and nothing about the one it was hosting.
    episode = clip(show.get("episodeAngle", ""), 600)
    if episode:
        show_block += f"\nTonight's episode in particular:\n  {episode}\n"

    # The programme intro is pinned independently of the Show Card. It used to
    # hang off the show block, so a station that couldn't resolve the active
    # show dropped the DJ's own framing of the night entirely — the one piece
    # of show context that's always available, because the DJ said it.
    intro = latest_programme_intro(snap["session"])
    if intro:
        # Background, not material. Observed on real calls: the DJ treated it
        # as a topic and opened call after call by re-announcing the show and
        # the handover from the last DJ.
        show_block += (
            "\nHow you opened the show tonight — background only, so your world "
            "stays consistent. Do NOT recap it, re-announce the show, or bring up "
            "taking over from another DJ. The caller tuned in already:\n  "
            + intro + "\n"
        )

    # House style sits on top of the persona, not in place of it — small
    # steers about how to answer and how to close, without rewriting who the
    # DJ is. Left blank, the persona alone decides.
    style_bits = []
    if str(cfg.get("style_conversation") or "").strip():
        style_bits.append("How to run the call: " + cfg["style_conversation"].strip())
    if str(cfg.get("style_answering") or "").strip():
        style_bits.append("How to handle answers: " + cfg["style_answering"].strip())
    if str(cfg.get("style_signoff") or "").strip():
        style_bits.append("How to wrap up a call: " + cfg["style_signoff"].strip())
    style_block = (
        "\n# House style\n"
        "These are notes from the station, not a change of character — keep your\n"
        "own voice and apply them lightly.\n" + "\n".join(style_bits) + "\n"
        if style_bits else ""
    )

    # Open Lines: the subject the DJ put to the audience, if one stands right
    # now. Additive by contract — `block` returns "" whenever the feature is
    # off, no line is open, or the line belongs to a different DJ or show, and
    # the prompt is then byte-identical to a build without this feature.
    # TestOpenLinesIsAdditive holds that, because it is the promise this
    # feature was allowed to exist on.
    from openlines import prompt as open_lines

    open_block = open_lines.block(cfg, persona, show_name, mode=mode)

    # Two media, one brain: the facts and identity are shared; the opening
    # line and the conduct are the medium's. `mode="chat"` is the typed line
    # (brain/conduct_chat) — same facts, different physics.
    if mode == "chat":
        from brain import conduct_chat

        opening = (f"You are {name}, a DJ on {station_name}, and a listener "
                   "has opened the station's text line to the booth. You are "
                   "live on air; this conversation is typed.")
        the_rules = conduct_chat.rules(cfg)
    else:
        opening = (f"You are {name}, a DJ on {station_name}, and a listener has "
                   "just called in to the booth. You are live, on a phone call, "
                   "talking with them out loud.")
        the_rules = conduct.rules(cfg)

    return f"""{opening}

# Who you are
{dj_card}
{language_block}{lean_block}{show_block}{style_block}
# What's happening on the station right now
{facts}
{open_block}
{the_rules}"""
