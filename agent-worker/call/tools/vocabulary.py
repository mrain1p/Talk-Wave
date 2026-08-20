"""Which word the library files under, and what to say when it files another.

Split out of discovery.py at 0.98.17, along the same seam shows.py was cut on:
nothing here touches the station, the action ledger or a tool. It is name
resolution over the vocabularies a browse has to speak — the two fixed ones
(energy, vocal) and the open one (genre, 894 words deep on the operator's
station).

Every function here exists because the tool and the station disagreed about a
word and the caller paid for it. `jazz` matched 0 of 54,841 where `Jazz`
matched all of them. `Instrumental` with a capital I matched the WHOLE library
instead of the 36 instrumentals, because the station reads an unrecognised
vocal filter as no filter at all. And a caller asking for jazz on a station
that files Instrumental Jazz, Cool Jazz and Acid Jazz was told there was none.
"""

from __future__ import annotations


# Every genre this library files, for MATCHING. `library_genres` reads the
# whole list from the station and truncates client-side, so a big number here
# costs nothing extra over the 40 it used to ask for — and 40 was hiding 854
# of the operator's 894 genres.
_ALL_GENRES = 5000

# How many genre names may ever reach the model. The full list is 894 words on
# this station: prompt weight nobody can read down a phone line, and a model
# handed all of it picks at random.
_OFFER = 5

# Under this many rows, a result is thin enough that a neighbouring genre is
# worth naming beside it. Two tracks came back for jazz+instrumental while
# "Instrumental Jazz" held 740, and nothing said so.
_THIN = 3


def _fold(word: str) -> str:
    """Case and punctuation off, so "Hip-Hop", "hip hop" and "HipHop" are one
    word. Only ever used to decide whether the caller MEANT a genre the
    station holds — never to display, and never to send."""
    return "".join(c for c in str(word or "").lower() if c.isalnum())


def _same_genre(wanted: str, known: list[str]) -> str:
    """The station's own spelling of the genre the model asked for, or "".

    The station's genre filter is exact — `jazz` returns nothing while `Jazz`
    returns 54,841 tracks — and the model types the caller's lowercase words.
    Returns the station's spelling only when it is DIFFERENT from what was
    asked, so the caller-facing retry above never repeats a query that has
    already come back empty."""
    folded = _fold(wanted)
    if not folded:
        return ""
    for real in known or []:
        if _fold(real) == folded and str(real) != str(wanted):
            return str(real)
    return ""


def _related_genres(wanted: str, known: list[str],
                    prefer: list[str] | None = None) -> list[str]:
    """Genres this library files that CONTAIN the word the caller said.

    The operator's point, 2026-08-20: "if i dont have jazz and i have jazz
    instramental than that might be a viable option". It is better than that
    on their station — 894 genres are filed, 33 of them contain "jazz", and
    one of those is literally **Instrumental Jazz** with 740 tracks. The call
    that started all of this asked for instrumental jazz before 2000: through
    `genre=Jazz` + `vocal=instrumental` that is 2 tracks, and through
    `genre='Instrumental Jazz'` it is 439.

    Matched on whole words so "jazz" reaches "Acid Jazz" and "Jazz Fusion"
    without "rock" dragging in "Rockabilly". Commonest first — `known` arrives
    in the station's own frequency order, and that is the order worth
    offering.

    `prefer` is the rest of what the caller asked for, and it decides ties.
    Frequency order alone put Vocal Jazz, Cool Jazz, Contemporary Jazz,
    Jazz-Funk and Jazz Fusion in front of Instrumental Jazz — so the caller
    who asked for INSTRUMENTAL jazz was shown five shelves, none of them the
    one with their name on it. A genre that also carries a word they used
    goes first."""
    want = _fold(wanted)
    if not want:
        return []
    wanted_too = {_fold(w) for w in (prefer or []) if _fold(w)}
    out: list[str] = []
    for real in known or []:
        words = {_fold(w) for w in str(real).replace("-", " ").split()}
        if want in words and _fold(real) != want:
            out.append(str(real))
    if not wanted_too:
        return out
    # A stable partition, so the station's frequency order survives inside
    # each half — this promotes, it does not re-sort.
    hit = [g for g in out
           if wanted_too & {_fold(w) for w in str(g).replace("-", " ").split()}]
    return hit + [g for g in out if g not in hit]


def _close_genres(wanted: str, known: list[str]) -> list[str]:
    """Genres spelled nearly like the word asked for, best first.

    The last resort before admitting a miss, and the same rule the show
    matcher works to: name what you DID find rather than answering a caller's
    near-miss with nothing. Advisory only — the model offers these to the
    caller, and nothing here browses one on its own."""
    import difflib

    want = _fold(wanted)
    if not want:
        return []
    scored = []
    for real in known or []:
        ratio = difflib.SequenceMatcher(None, want, _fold(real)).ratio()
        if ratio >= 0.7:
            scored.append((ratio, str(real)))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [name for _r, name in scored[:_OFFER]]


# The two filters with a FIXED vocabulary, and the reason they are resolved
# here rather than sent as typed. Both are exact-match on the station's side,
# and they fail in opposite directions:
#
#   energy='Low'          -> 0 of 150,229. Loud, and a retry would catch it.
#   vocal='Instrumental'  -> 381,023. The WHOLE library.
#
# The second one is why this exists. The station reads
# `q.vocal === 'instrumental' || q.vocal === 'vocal' ? q.vocal : null`, so any
# other spelling becomes null — no filter at all — and the DJ offers sung
# tracks to a caller who asked for instrumentals, with nothing anywhere
# disagreeing. An empty answer can be retried; a full one cannot be noticed.
# So neither value is ever sent unless it is already one of the station's own
# words, and a value that cannot be resolved stops the call rather than
# quietly widening it.
#
# Note `mid`: the station's OWN admin page labels the chip "MID · 113054" and
# the API wants `medium`. A model repeating what the caller read off the
# screen gets nothing.
_ENERGY = {"low": "low", "lo": "low",
           "medium": "medium", "mid": "medium", "med": "medium",
           "moderate": "medium", "middle": "medium",
           "high": "high", "hi": "high"}
_VOCAL = {"vocal": "vocal", "vocals": "vocal", "withvocals": "vocal",
          "sung": "vocal", "instrumental": "instrumental",
          "instrumentals": "instrumental", "novocals": "instrumental",
          "novocal": "instrumental", "nolyrics": "instrumental"}


def _one_of(value: str, table: dict, field: str, words: str) -> tuple[str, str]:
    """(what to send, what to say instead). Exactly one is ever non-empty.

    An unresolvable value is refused rather than dropped: dropping it is the
    same silent widening the whole comment above is about — a caller who asked
    for calm would get whatever the library had, presented as calm."""
    said = str(value or "").strip()
    if not said:
        return "", ""
    fixed = table.get(_fold(said))
    if fixed:
        return fixed, ""
    return "", (f"{field}=\"{said}\" is not something this station files, and "
                f"nothing was looked up — {field} is {words}. The filter was "
                "NOT dropped and nothing was widened: call this again with one "
                "of those words, or leave it out altogether.")

# bpm and key used to be added here, for neighbour rows only — they were the
# two numbers that justify "this mixes well after that". The station merges its
# analysis columns into EVERY listing row (search, recent and browse as well as
# neighbours), so they moved into `_fmt_track` at 0.10.132 and this helper had
# nothing left to add.
