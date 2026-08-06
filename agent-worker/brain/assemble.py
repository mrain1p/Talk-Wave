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
    station: StationClient, persona: dict, snapshot: dict | None = None
) -> str:
    import settings as settings_store

    cfg = settings_store.load()

    # A pre-fetched snapshot avoids repeating the station reads the caller is
    # already waiting on. Falls back to fetching if none was supplied.
    snap = snapshot or await station.snapshot(with_skills=bool(cfg.get("allow_skills")))
    show = await station.active_show(snap["now_playing"])

    facts = await station_context(station, cfg, snap, show)

    name = persona.get("name", "the DJ")
    # The station's own name, not ours. This line used to say "the SUB/WAVE
    # radio station" for everybody, so a DJ on Yosemite FM told callers they
    # were live on SUB/WAVE — the software's name, which no listener has ever
    # heard of. GET /dj has carried the real one all along.
    station_name = demojibake(
        str((snap.get("dj") or {}).get("station") or "").strip()
    ) or "the SUB/WAVE radio station"
    dj_card = clip(persona.get("soul", ""), CARD_BUDGET)
    show_name = demojibake(show.get("name", ""))
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

    return f"""You are {name}, a DJ on {station_name}, and a listener has \
just called in to the booth. You are live, on a phone call, talking with them out loud.

# Who you are
{dj_card}
{show_block}{style_block}
# What's happening on the station right now
{facts}

{conduct.rules(cfg)}"""
