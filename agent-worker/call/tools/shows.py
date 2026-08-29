"""Which show a caller meant, and what to say when it isn't clear.

Split out of broadcast.py at 0.98.16, along the seam that was always there:
nothing here touches the station, the action ledger or the air guard. It is
name resolution over two lists the caller may have named either of — the
shows, and the people who present them — and it is the half of a takeover
that has to be right BEFORE anything is pinned, because a takeover changes
what the station is for the next hour and nobody can hear it land.

The miss matters as much as the match. A caller who says "wade" when Wade
presents four shows, or "walt" when the roster says Wade, used to get one
answer: the whole roster twice over and "ask which one they mean". That reads
as a DJ who doesn't know its own station. `_show_miss` is the difference.
"""

from __future__ import annotations

from .rows import _squash


def _name(thing: dict) -> str:
    return str(thing.get("name") or "").strip()


def _head(show_name: str) -> str:
    """A show's own name, without the strapline the station hangs off it.

    Every show here is titled "THE OVERLOOK · After Dark" or "Up Stream ·
    Deep Cuts", and a caller says the half before the dot."""
    return str(show_name or "").split("·")[0]


def _tight(value) -> str:
    """`_squash` with the spaces closed up, so "upstream" reaches "Up Stream".

    Only for deciding that two strings are the SAME name written differently
    — never for display, and never as the basis of a near-miss guess."""
    return _squash(value).replace(" ", "")


def _person_matches(personas: list[dict], want: str) -> list[dict]:
    """Personas a caller's word picks out — exact name first, then partial."""
    exact = [p for p in personas if _squash(_name(p)) == want and _name(p)]
    if exact:
        return exact
    return [p for p in personas
            if _name(p) and want and want in _squash(_name(p))]


def _shows_of(shows: list[dict], persona: dict) -> list[dict]:
    pid = str(persona.get("id") or "")
    return [s for s in shows if pid and str(s.get("personaId") or "") == pid]


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
    who gets the other one is a station-wide change nobody asked for. What
    ambiguity gets instead is `_show_miss`, which says who and what it DID
    find rather than a flat no.

    **A DJ's NAME resolves to their show**, which is how callers actually ask
    — "change the DJ to Duke". The conduct has promised exactly that since
    0.10.93 ("a DJ's name resolves to their show") and this function could not
    do it: it read `name` and nothing else, so a real persona came back as no
    match. Observed 2026-08-13 — a caller asked for Duke Sterling three times,
    was told each time that no such DJ was on the roster, and only got there
    by naming his show (The Alibi Room) himself. Personas are matched AFTER
    shows so a show called after its host still wins on its own name.

    Comparison is on `_squash`ed text throughout — the old version lowercased
    and nothing else, so "Up Stream · Deep Cuts" was reachable by "up stream"
    and not by "upstream", and a caller who closed up one space got the same
    flat refusal as one who named a show that does not exist.
    """
    want = _squash(wanted)
    if not want:
        return None
    shows = list(shows or [])
    personas = list(personas or [])
    for show in shows:
        if _squash(show.get("id")) == want:
            return show
    # Written differently, meaning the same thing: the full title, the half
    # before the strapline, and either of those with the spaces closed up.
    for key in (lambda s: _squash(_name(s)),
                lambda s: _squash(_head(_name(s))),
                lambda s: _tight(_name(s)),
                lambda s: _tight(_head(_name(s)))):
        named = [s for s in shows if _name(s) and key(s) == want]
        if len({str(s.get("id")) for s in named}) == 1:
            return named[0]
    partial = [s for s in shows if _name(s) and want in _squash(_name(s))]
    if len({str(s.get("id")) for s in partial}) == 1:
        return partial[0]

    # No show by that name — try the people.
    by_person: list[dict] = []
    for persona in _person_matches(personas, want):
        by_person += _shows_of(shows, persona)
    # Dedupe on id: two personas whose names both contain the fragment can
    # point at the same show, and that is still one unambiguous answer.
    unique = {str(s.get("id")): s for s in by_person}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _close_to(want: str, candidates: list[str]) -> list[str]:
    """The names a caller's word most plausibly meant, best first.

    Deliberately loose. Nothing downstream ACTS on this — it only ever ends
    up in a sentence asking the caller whether that is who they meant — so a
    generous guess costs one question and a stingy one costs the call. The
    operator's ask, 2026-08-20: "not matching letter and case and then saying
    NO". "walt" scores 0.50 against "Wade" and 0.44 against the next name
    along, which is why the bar sits at 0.5 and why two are offered rather
    than one asserted.
    """
    import difflib

    scored = []
    for name in candidates:
        folded = _squash(name)
        if not folded:
            continue
        best = max(difflib.SequenceMatcher(None, want, folded).ratio(),
                   difflib.SequenceMatcher(None, want, _squash(_head(name))).ratio())
        if best >= 0.5:
            scored.append((best, name))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [name for _score, name in scored[:2]]


def _show_miss(shows: list[dict], wanted: str,
               personas: list[dict]) -> str:
    """What to say when `_match_show` came back empty.

    A miss is nearly always one of three things, and they need three
    different answers. The old text gave one — the whole roster, twice over,
    with "ask the caller which one they mean" — which reads to a caller as
    the DJ not knowing its own station.

      * The DJ is real and presents SEVERAL shows. "i want to hear wade"
        cannot resolve because Wade has four; the useful answer names them.
        The old code returned None here and the caller was told no show
        matched — about a DJ who is on the roster four times over.
      * The word is a near miss. "walt" is nobody, but Wade is one letter
        away and presents Up Stream; saying so is the difference between a
        correction and a dead end.
      * It is genuinely not here, and then the roster IS the answer.
    """
    want = _squash(wanted)
    asked = str(wanted or "").strip()
    show_names = [_name(s) for s in shows if _name(s)]
    dj_names = [_name(p) for p in personas if _name(p)]
    roster = (f" The station's shows are: {', '.join(show_names)}."
              + (f" Its DJs are: {', '.join(dj_names)}." if dj_names else ""))

    # One DJ, more than one show of their own.
    people = _person_matches(personas, want)
    if len(people) == 1:
        mine = _shows_of(shows, people[0])
        who = _name(people[0])
        if len(mine) > 1:
            listed = ", ".join(f"\"{_name(s)}\"" for s in mine)
            return (
                f"{who} is on the roster and presents {len(mine)} shows: "
                f"{listed}. That is why this didn't resolve — not because "
                f"{who} is missing. Ask the caller which of {who}'s shows they "
                "want and call this again with that show's name. Do NOT tell "
                "them there's no such DJ."
            )
        if not mine:
            return (
                f"{who} is on the roster but has no show in the schedule to "
                "put on, so there is nothing to pin. Say that plainly — the "
                f"DJ exists, the slot doesn't — and offer what else is on."
                + roster
            )

    # Several people, or none — try the spelling.
    near_djs = _close_to(want, dj_names)
    near_shows = _close_to(want, show_names)
    if near_djs or near_shows:
        bits = []
        for who in near_djs:
            person = next((p for p in personas if _name(p) == who), None)
            mine = _shows_of(shows, person) if person else []
            if len(mine) == 1:
                bits.append(f"{who}, who presents \"{_name(mine[0])}\"")
            elif mine:
                bits.append(f"{who}, who presents {len(mine)} shows "
                            f"({', '.join(_name(s) for s in mine)})")
            else:
                bits.append(who)
        bits += [f"the show \"{s}\"" for s in near_shows]
        return (
            f"Nothing here is spelled \"{asked}\". The closest is "
            + "; or ".join(bits)
            + ". Say that to the caller in your own words — name who you think "
            "they meant and what that person presents, ask if that's the one, "
            "and call this again with the show's name once they say. Do NOT "
            "tell them the name isn't on the roster full stop, and do NOT pin "
            "anything until they've confirmed."
        )

    # Genuinely absent, or an ambiguous fragment that is close to nothing.
    return (
        f"No show matches \"{asked}\" — or more than one does." + roster
        + " Ask the caller which one they mean and try again with that name. "
        "Do NOT tell them a name is missing from the roster unless it is "
        "absent from BOTH lists above."
    )
