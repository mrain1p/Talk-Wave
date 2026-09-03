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


async def build_system_prompt(
    station: StationClient, persona: dict, snapshot: dict | None = None,
    cfg: dict | None = None, mode: str = "call",
    speak_clock: bool | None = None,
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
    if snapshot is None and snap.get("skills"):
        # Only when we fetched it ourselves: the call path narrows the
        # catalogue to the on-air DJ before handing it over (session.prepare),
        # and doing it twice would cost a second /settings read. This covers the
        # chat line and the operator previews, which build their own snapshot —
        # a preview that lists segments the DJ cannot run is exactly the
        # disagreement the narrowing exists to remove.
        from station_config import StationConfig, runnable_skills

        sc = StationConfig()
        try:
            assigned = await sc.persona_skills(str(persona.get("id") or ""))
        except Exception:                                     # noqa: BLE001
            assigned = None
        finally:
            await sc.aclose()
        snap["skills"] = runnable_skills(snap["skills"], assigned)
    show = await station.active_show(snap["now_playing"], snap.get("schedule"))

    # The clock mirror (djSpeakClock, SUB/WAVE 1.8). The call path passes it
    # in — its StationConfig caches /settings, so the read is free there;
    # this fallback covers the preview and chat paths with one authed read.
    if speak_clock is None:
        from station_config import StationConfig

        sc = StationConfig()
        try:
            speak_clock = await sc.speak_clock()
        except Exception:                                     # noqa: BLE001
            speak_clock = True
        finally:
            await sc.aclose()

    facts = await station_context(station, cfg, snap, show,
                                  speak_clock=speak_clock,
                                  persona_id=str(persona.get("id") or ""))

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
    # Only when the station named one. Empty means English there, and asserting
    # "you speak English" at every DJ on every call would be a sentence bought
    # on every turn to say what the prompt is already written in.
    language_block = (
        f"\nYou work in {dj_language} — that is the language you open in and "
        "come back to. Match a caller who brings another one.\n"
    ) if dj_language else ""
    show_name = clip(demojibake(show.get("name", "")), NAME_BUDGET)
    show_card = clip(show.get("topic", ""), CARD_BUDGET)

    show_block = ""
    if show_name or show_card:
        show_block = f"\n# The show you're hosting: {show_name}\n{show_card}\n"

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
{language_block}{show_block}{style_block}
# What's happening on the station right now
{facts}
{open_block}
{the_rules}"""
