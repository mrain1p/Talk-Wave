"""What happens after a request has already answered.

The station resolves a request in the background, so `subwave_request_song`
routinely returns before anyone knows which record it picked. Waiting inline
for the answer is the wrong trade — the tool return is latency the caller hears
as silence — so the tool answers immediately and this module keeps asking.

Split out of music.py at 0.10.104: it is the only thing there that runs after
its tool has returned, it owns the "is the DJ free to say this yet" judgement,
and keeping it inline was pushing the module past the file ceiling for reasons
that had nothing to do with music.
"""

from __future__ import annotations

import asyncio
import logging

from station import StationClient

log = logging.getLogger("callin.agent")

# How long the station gets to say what a request matched, once the inline
# poll has already missed. Observed on a real call (2026-08-08): the station
# matched "Spiders" by Moby some time after the tool's 2s look, so the DJ told
# the caller "something is lined up" and could not name it — the caller had to
# ask, and the DJ had to go digging through station state to answer.
_LATE_MATCH_DELAYS = (3.0, 5.0, 8.0, 13.0, 21.0)

# How long the announcer waits for a quiet moment before giving up, in
# half-second beats. A caller mid-sentence, a DJ mid-reply or a broadcast
# hold must not be talked over for the sake of naming a song.
_QUIET_BEATS = 40


def _already_named(session, title: str) -> bool:
    """Did the DJ already tell the caller which track it is?

    Exactly what happened on the call that motivated all this: the caller
    asked, the DJ read the answer off station state, and a late announcement
    on top of that would read as the DJ forgetting the conversation.
    """
    needle = str(title or "").casefold()
    if not needle:
        return False
    try:
        items = list(session.history.items)[-12:]
    except Exception:
        return False
    for item in items:
        if getattr(item, "role", None) != "assistant":
            continue
        content = getattr(item, "content", None)
        text = content if isinstance(content, str) else (
            getattr(item, "text_content", "") or "")
        if needle in str(text).casefold():
            return True
    return False


async def _surface_late_match(
    station: StationClient, rid: str, get_session=None, air=None, record=None,
    delays=None, actions=None,
) -> None:
    """Keep asking the station what a request matched, and pass it on.

    Runs in the background after request_song has already answered. If the
    match lands, the DJ volunteers it at the next quiet moment; if it never
    does, the record says so — a request whose title never surfaced is the
    thing an operator reads the transcript to find.
    """
    from .music import _fmt_track, _when_it_plays

    track: dict = {}
    position = None
    for delay in (_LATE_MATCH_DELAYS if delays is None else delays):
        # The caller is waiting on THIS, so hold the 'DJ is working' flag across
        # the whole poll — otherwise the idle watcher reads the quiet as the
        # caller having left and asks "still there?" (the Zeppelin call).
        if actions is not None:
            actions.mark_working(delay + 10)
        await asyncio.sleep(delay)
        try:
            st = await station.request_status(str(rid))
        except Exception:
            continue
        t = st.get("track") or st.get("matched") or {}
        if isinstance(t, dict) and t.get("title"):
            track, position = t, st.get("queuePosition")
            break

    if not track:
        if record:
            record.problem(
                "A request went in but the station never said what it matched, "
                "so the caller was never told the track. Either the station's "
                "resolver is slow past the polling window or the request "
                "matched nothing — check the station's request queue."
            )
        return

    if record:
        record.tool("subwave_request_song",
                    f"matched after the tool returned: {_fmt_track(track)}")

    session = get_session() if get_session else None
    if session is None:
        return
    if _already_named(session, track.get("title")):
        return

    # A quiet moment: the DJ is listening, the caller is not mid-word, and
    # the broadcast does not have the microphone. If one never comes, stay
    # quiet — the match is in the history-side receipts either way.
    for _ in range(_QUIET_BEATS):
        busy = (
            str(getattr(session, "agent_state", "") or "") != "listening"
            or str(getattr(session, "user_state", "") or "") == "speaking"
            or bool(getattr(air, "on_air", False))
        )
        if not busy:
            break
        await asyncio.sleep(0.5)
    else:
        return

    try:
        # The bracketed user turn is the same trick the greeting uses: a
        # generation prompted by instructions alone, on a history ending in
        # the DJ's own turn, is exactly the shape weak models answer by
        # re-saying that turn. The note is never spoken and never counts as
        # a caller turn — see handoff.is_prime.
        await session.generate_reply(
            user_input=(
                f"[The station has just resolved the earlier request: it "
                f"matched {_fmt_track(track)}. {_when_it_plays(position)}]"
            ),
            instructions=(
                "Pass the station's pick on to the caller — one short line in "
                "your own voice, and it's queued, not playing yet. If the "
                "conversation has moved on, drop it in lightly and come back "
                "to what you were talking about."
            ),
        )
    except Exception as e:
        log.debug("late-match announcement failed (harmless): %s", e)
