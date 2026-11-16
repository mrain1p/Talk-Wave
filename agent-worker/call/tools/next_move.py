"""What a tool's RESULT may tell the DJ to do next.

Split out of `discovery.py` at 2026-09-17, the day the drill caught what
these are for. A results string is not a report — it is an INSTRUCTION, and
it is the one the model reads last, after the whole prompt. Every one of them
here used to be written into the tool that returned it, naming whichever tool
came next whatever the operator's settings said:

    GATES=shipped TIER=open. The caller asked for something dreamy. The sound
    search answered with two records and the sentence "queue the exact one
    they pick with subwave_queue_track" — on a line where `allow_exact_queue`
    is off, as it is by default. The DJ offered the records, the caller chose,
    the DJ called the tool, the surface refused a tool it had never been
    given. And then the DJ told the caller it had landed.

The prompt's action rules have ridden their switches since 0.98.51
(`brain.tool_rules`, `TestActionRulesRideTheirSwitches`). These did not, and
they arrive after the prompt, so they won. That is the mimed action `OFF_LIST`
exists to prevent, coming through the one channel nothing was watching.

So: nothing in here names a tool without asking `registry.on_the_surface`
first, and each one has a real answer for the line that has nothing — "say
you can't" is an instruction too, and a better one than pointing at a closed
door. `TestAResultNeverNamesAToolThisLineHasNot` sweeps the built surface at
five permission shapes and fails on any string that names a tool the line was
not given, so the next one of these is caught the day it is written.
"""

from __future__ import annotations

from .registry import on_the_surface


def queue_this_row(cfg: dict) -> str:
    """How a chosen row gets into the queue on this line.

    One copy, four results lists. It was four copies, and all four named the
    exact queue on lines that have never had it.
    """
    if on_the_surface(cfg, "subwave_queue_track"):
        return ("Queue the exact one they pick with subwave_queue_track, "
                "using the id shown beside it. If they left the choice to "
                "you, don't read the list back — pick ONE, queue it, and say "
                "what you went with.")
    if on_the_surface(cfg, "subwave_request_song"):
        return ("This line cannot queue a row directly: put a request in "
                "with subwave_request_song, naming the title AND the artist. "
                "It re-matches the words, so tell the caller what the receipt "
                "came back with rather than the row you offered. If they left "
                "the choice to you, don't read the list back — pick ONE, "
                "request it, and say what you went with.")
    return ("Nothing on this line can put a record in the queue. Offer one or "
            "two by name as what the station HAS — never as something you are "
            "about to play — and do not offer to line one up.")


def a_lookup_is_not_a_request(cfg: dict) -> str:
    """What a NAME search's hits end with: nothing has been queued.

    Drill on the deployed 1.0.1, 2026-11-16, the line as it ships to an
    anonymous caller. "Play Dreams by Fleetwood Mac for me": the DJ searched,
    got `1 result(s): "Dreams" by Fleetwood Mac`, and said "that's in the
    queue for you" — in three rounds of three, with no request made. The
    guard won one back in the same turn; one was only requested when the
    caller asked whether it had gone in; and in one it never was, while the
    DJ answered "it did, just confirmed it". The result ended at the listing,
    and a found record reads as a finished job to the model that reads it
    last.

    So the listing says what it was, and names the tool that does the rest —
    the one this line actually has. The condition is the caller's ask, not the
    hit: "have you got anything by them?" is still a question to answer, not
    a record to queue.
    """
    if on_the_surface(cfg, "subwave_queue_track"):
        return ("This was a LOOKUP — NOTHING is queued yet. If the caller "
                "asked you to PLAY one of these, queue it NOW, in this same "
                "turn, with subwave_queue_track and the id shown; do not say "
                "it is lined up until that tool has answered. If they only "
                "asked what you have, just tell them.")
    if on_the_surface(cfg, "subwave_request_song"):
        return ("This was a LOOKUP — NOTHING is queued or requested yet. If "
                "the caller asked you to PLAY one of these, put the request "
                "in NOW, in this same turn, with subwave_request_song (the "
                "title AND the artist); do not say it is lined up until that "
                "tool has answered. If they only asked what you have, just "
                "tell them.")
    return ("This was a LOOKUP, and nothing on this line can put a record in "
            "the queue: say what the station HAS, never that it is lined up "
            "or coming.")


def another_way_to_look(cfg: dict) -> str:
    """The other door, when the sound search cannot answer at all.

    "Do this now" pointed at a tool the line hasn't got is worse than no
    instruction: the model reaches, is refused, and has already committed to
    the caller that it was looking.
    """
    if on_the_surface(cfg, "subwave_request_song"):
        return ("DO THIS NOW, in this same turn: call subwave_request_song "
                "with the caller's own words and let the station's picker "
                "handle it. Do not answer the caller until you have — a "
                "sentence about looking, with no second tool call behind it, "
                "leaves them with nothing.")
    if on_the_surface(cfg, "subwave_browse_library"):
        return ("DO THIS NOW, in this same turn: try subwave_browse_library "
                "with their mood, genre or era — it reads the station's tags "
                "rather than the audio, so it answers when this cannot. Do "
                "not answer the caller until you have.")
    return ("There is no other way to look by feel on this line. Say plainly "
            "that you can't go hunting by mood tonight and ask them to name "
            "something instead — do not invent a record, and do not turn it "
            "into a story about the station being stubborn.")


def where_an_id_comes_from(cfg: dict) -> str:
    """Which search turns a title into an id, when the model passed a title.

    Station ids never contain whitespace, so a spaced string in an id slot is
    a title every time — and the tool that sends the model to fetch a real
    one has to be a tool the model has.
    """
    if on_the_surface(cfg, "subwave_search_library"):
        return ("Search for it with subwave_search_library, take the id off "
                "the row you want, and call this again with that.")
    return ("This line has no name search: describe the record to "
            "subwave_search_by_sound, take the id off the row you want, and "
            "call this again with that.")
