"""One way in to the six ways of finding a record.

The DJ has six tools for looking something up and picked the wrong one often
enough that the prompt grew a table telling it which — `brain/tool_rules.py`'s
`finding_rule`, itself written after a run of calls on 2026-08-12 where "the DJ
had more than one way to look and only ever used the first one".

That table is a decision tree living in prose, read by a small fast model on
every turn, against a tool list of thirty. On 2026-08-20 the same failure
happened on a READ: seven asks about the record on air, eleven calls to
`subwave_current_lyrics`, none to `subwave_now_playing`. LiveKit's own guidance
for this framework puts reliable selection at five to ten tools and says that
past twenty "the model struggles to choose reliably."

So the table moves into code. The model stops choosing a tool and instead says
**what it heard**, which is the thing it is actually good at; the routing
becomes a function with tests. Same move as `promises.unbacked` (0.10.154) and
`call/door.py` — and `door.py`'s warning applies here too: mechanism over prose
is a hypothesis to test per rule, which is why this ships behind a setting that
defaults OFF and the six stay reachable until there are numbers.

**Three things this must not cost, in the operator's words: capability,
discretion, and the DJ's own voice.**

*Capability* — every argument of every underlying tool is reachable from here,
pagination and the explicit neighbour id included. Nothing routes to a tool
that was not built, so a switched-off capability stays switched off rather than
becoming a confusing error. And `subwave_request_song` is deliberately NOT
behind this: it is an ACTION, rate-limited, and firing one because a lookup
found nothing would be the tool taking a decision that belongs to the DJ.

*Discretion* — `prefer` names a route and overrides the routing entirely. The
model is not being demoted to a form-filler; it is being spared a choice it was
getting wrong, and it can still take that choice back whenever it has a reason
the fields cannot carry ("something like the last one but sadder" is a judgment
about a person, not a parse of their words).

*Voice* — the receipt says which shelf the records came off, so the DJ has
something real to say before it offers them. That is 0.98.17's rule and it is
the whole reason a caller hears "these are the ones people round here have
loved" instead of a list. The underlying tools' own wording passes through
untouched, including every careful sentence about what an empty answer means.
"""

from __future__ import annotations

import logging

log = logging.getLogger("callin.tools.finding")

#: Route name -> the tool that serves it. Route names are what `prefer` takes
#: and what the receipt reports, so they are the DJ's vocabulary, not ours.
ROUTES: dict[str, str] = {
    "name": "subwave_search_library",
    # The recall route (2026-08-31): "did you cancel my queue?" was answered
    # from per-call memory — a global evasion — because the dispatcher had
    # no way to reach the booth's cross-call ledger, and the prose row
    # vanished with the table when single_lookup_tool went on.
    "booth": "subwave_booth_log",
    "sound": "subwave_search_by_sound",
    "neighbours": "subwave_more_like_this",
    "browse": "subwave_browse_library",
    "favourites": "subwave_station_favourites",
    "history": "subwave_already_played",
}

#: route_for's own keyword set, for callers that must filter a find_music
#: argument dict down to the router's fields — the drill's C.5 A/B credits
#: the routed tool from the model's find_music arguments, and re-listing
#: these there would be the drift this module exists to prevent.
ROUTE_FIELDS = frozenset((
    "named_track", "artist", "mood", "genre", "energy", "vocal",
    "year_from", "year_to", "sounds_like", "like_whats_on", "like_track_id",
    "already_played", "earlier_call", "let_you_pick"))

#: What the receipt says a route means, in words the DJ can reuse out loud.
SHELF: dict[str, str] = {
    "name": "a name search",
    "sound": "how the music actually sounds",
    "neighbours": "records that sit close to what's on",
    "browse": "the shelf for that mood, genre or era",
    "favourites": "what listeners round here have loved",
    "history": "the station's play log",
    "booth": "the booth's own log of what earlier calls did",
}


def route_for(*, named_track: str = "", artist: str = "", mood: str = "",
              genre: str = "", energy: str = "", vocal: str = "",
              year_from: int = 0, year_to: int = 0, sounds_like: str = "",
              like_whats_on: bool = False, like_track_id: str = "",
              already_played: bool = False, earlier_call: bool = False,
              let_you_pick: bool = False) -> str:
    """Which shelf these words belong to, or "" when nothing fits.

    `finding_rule`'s table, in the order it states it. Kept as a free function
    with no station and no config so the routing can be tested on its own —
    the thing the prose version could never be.

    Order matters where inputs overlap. A caller who names a track AND a mood
    ("something upbeat — got any Kygo?") is naming a record: the name search is
    the specific answer and the mood is colour around it.
    """
    if earlier_call:
        # Before history on purpose: "where's the song I asked for?" is about
        # what this LINE did, and the play log cannot answer it.
        return "booth"
    if already_played:
        return "history"
    if like_track_id or like_whats_on:
        return "neighbours"
    if named_track or artist:
        return "name"
    if sounds_like:
        return "sound"
    if mood or genre or energy or vocal or year_from or year_to:
        return "browse"
    if let_you_pick:
        return "favourites"
    return ""


def _args_for(route: str, kw: dict) -> dict:
    """The underlying tool's own arguments, built from what the model heard."""
    if route == "name":
        # Artist and title in one query string is what /dj/search takes, and
        # the wrapper's own retry (it strips a "by" connector) is why passing
        # them joined is better than picking one.
        q = " ".join(p for p in (kw.get("named_track", ""),
                                 kw.get("artist", "")) if p).strip()
        return {"q": q, "page": max(1, int(kw.get("page") or 1))}
    if route == "sound":
        return {"description": kw.get("sounds_like", "")}
    if route == "neighbours":
        # "" is meaningful here: the wrapper reads it as "whatever is on".
        return {"id": kw.get("like_track_id", "") or ""}
    if route == "browse":
        return {
            "moods": kw.get("mood", ""),
            "energy": kw.get("energy", ""),
            "genre": kw.get("genre", ""),
            "year_from": int(kw.get("year_from") or 0),
            "year_to": int(kw.get("year_to") or 0),
            "vocal": kw.get("vocal", ""),
        }
    return {}


def build_finder_tools(cfg: dict, built: list, actions=None) -> list:
    """The one finder, routing to the finders in `built`.

    `built` is the already-assembled tool list, so this reuses the real
    wrappers rather than reaching past them to the station — every retry,
    every never-play filter, every careful sentence about an empty answer is
    the one that was already written and already tested.

    Returns [] when the gate is off or when fewer than two routes are actually
    available: collapsing one tool into one tool buys nothing and costs the
    caller a hop.
    """
    if not cfg.get("single_lookup_tool"):
        return []

    from livekit.agents import llm as lk_llm

    by_name = {t.info.name: t for t in built}
    available = {route: by_name[name]
                 for route, name in ROUTES.items() if name in by_name}
    if len(available) < 2:
        return []

    offered = ", ".join(sorted(available))

    @lk_llm.function_tool(name="subwave_find_music")
    async def find_music(named_track: str = "", artist: str = "",
                         mood: str = "", genre: str = "", energy: str = "",
                         vocal: str = "", year_from: int = 0, year_to: int = 0,
                         sounds_like: str = "", like_whats_on: bool = False,
                         like_track_id: str = "", already_played: bool = False,
                         earlier_call: bool = False,
                         let_you_pick: bool = False, page: int = 1,
                         prefer: str = "") -> str:
        """Find music. Tell it WHAT THE CALLER SAID and it picks how to look.

        Fill in only what they actually gave you; leave the rest empty. You do
        not choose a search here — you report a conversation.

        - named_track / artist: they NAMED something. "got any Kygo",
          "play Firestone".
        - sounds_like: they DESCRIBED a sound or feeling rather than naming
          it. "dreamy cinematic strings", "warm and fuzzy".
        - mood / genre / energy / vocal / year_from / year_to: a mood word, a
          genre, an era, vocal vs instrumental. energy is high, medium or low.
        - like_whats_on: they want more of what is playing. like_track_id: more
          like one specific record you already have the id for.
        - already_played: did this air earlier, what was that one before.
        - earlier_call: they ask what an EARLIER CALL on this line did —
          "did you cancel my queue?", "where's the song I asked for?". Your
          own memory starts at pickup; this reads the booth's ledger.
        - let_you_pick: they left the choice to you. "play me something good".
        - page: the next page of a name search you already ran.

        prefer OVERRIDES the choice when you have a reason the fields above
        cannot carry — one of: name, sound, neighbours, browse, favourites,
        history. Use it when you know better; leave it empty otherwise.
        """
        kw = {
            "named_track": named_track.strip(), "artist": artist.strip(),
            "mood": mood.strip(), "genre": genre.strip(),
            "energy": energy.strip(), "vocal": vocal.strip(),
            "year_from": year_from, "year_to": year_to,
            "sounds_like": sounds_like.strip(),
            "like_whats_on": like_whats_on,
            "like_track_id": like_track_id.strip(),
            "already_played": already_played, "earlier_call": earlier_call,
            "let_you_pick": let_you_pick,
            "page": page,
        }

        chosen = (prefer or "").strip().lower()
        if chosen and chosen not in ROUTES:
            # Named something that is not a route at all. Say what the words
            # are rather than falling through silently — a wrong `prefer` that
            # quietly became a different search is exactly the shape of bug
            # this tool exists to remove.
            return (f"'{prefer}' is not one of the ways to look. The ones this "
                    f"station has right now are: {offered}. Say what the "
                    "caller asked for in the fields instead, and let it route.")
        if not chosen:
            chosen = route_for(**{k: v for k, v in kw.items() if k != "page"})
        if not chosen:
            # Deliberately does NOT fall through to subwave_request_song. That
            # is an action, it is rate-limited, and its result cannot be seen
            # before the DJ speaks — firing one off the back of an empty
            # lookup would be this tool making a decision that is the DJ's.
            return ("Nothing to go on yet — no name, no description, no mood "
                    "or era, and they haven't left it to you. Ask them one "
                    "short question, or if they truly gave you nothing to "
                    "work with, put it in with subwave_request_song in their "
                    "own words and let the station's picker choose.")

        if chosen not in available:
            # The gate for that route is off. Name the ones that are on: a
            # flat refusal here is how a caller gets told the library hasn't
            # got something nobody ever looked for.
            return (f"The {SHELF.get(chosen, chosen)} way in isn't available "
                    f"on this line tonight. What you do have: {offered}. Try "
                    "one of those rather than telling the caller no.")

        log.info("find_music -> %s%s", chosen, " (asked for)" if prefer else "")
        result = await available[chosen](**_args_for(chosen, kw))
        # The shelf, named, so the DJ can say where these came from before it
        # offers them — 0.98.17's rule, and the reason a caller hears "these
        # are the ones people round here have loved" instead of a list.
        return f"[found via {SHELF.get(chosen, chosen)}]\n{result}"

    return [find_music]


def apply_finder_dispatch(cfg: dict, tools: list) -> list:
    """The tool list with the finder in place OF the finders, not beside them.

    Switched on, this is a MODE rather than an extra capability: the six go
    out of the model's list and the one goes in, holding them. Offering both
    would be the worst of the three arrangements — thirty-one tools, two ways
    to do the same thing, and an A/B that cannot answer anything, because
    neither arm is the arrangement anybody would ship.

    Capability is unaffected: every route and every argument stays reachable
    (`TestNoCapabilityIsLost`), and a route whose gate is off was never in
    `tools` to begin with, so it cannot come back through this door.

    `subwave_request_song` is untouched and stays exposed. It is an action,
    not a way of looking.
    """
    finder = build_finder_tools(cfg, tools, None)
    if not finder:
        return tools
    routed = set(ROUTES.values())
    return [t for t in tools if t.info.name not in routed] + finder
