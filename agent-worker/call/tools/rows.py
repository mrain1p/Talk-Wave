"""Reading a station listing row, and saying it the way a DJ would.

Split out of `music.py` at 0.10.132, when the never-play marker and the
station's analysis columns pushed that file past the size ceiling. The seam is
the honest one: everything here turns what the station SAID into what the DJ
HEARS, and none of it calls the station, holds call state, or knows a tool
exists. `music.py`, `discovery.py` and `late_match.py` all shape the same rows,
and two of them were importing the tool module purely to borrow a formatter.
"""

from __future__ import annotations


def _query_variants(q: str) -> list[str]:
    """The station's search requires EVERY word to match, so the natural
    phrase "Let It Be by The Beatles" returns nothing — "by" appears in no
    title or artist. Try as given, then with the last " by " connector
    removed, then the left side alone. Rightmost split keeps titles that
    themselves contain "by" ("Stand by Me by Ben E. King") intact."""
    variants = [q]
    idx = q.lower().rfind(" by ")
    if idx > 0:
        variants.append(q[:idx] + " " + q[idx + 4:])
        variants.append(q[:idx])
    return variants


# Words that describe how music FEELS rather than what it's called. A caller
# saying one of these wants the station's picker, not a title match — but the
# model reaches for the search tool anyway, and "fun" dutifully returns
# "Fun, Fun, Fun" by The Beach Boys. Observed on a real call.
_VIBE_WORDS = {
    "fun", "upbeat", "happy", "sad", "chill", "chilled", "chillout", "relaxing",
    "calm", "mellow", "moody", "dark", "bright", "energetic", "energy", "hype",
    "party", "dance", "dancey", "slow", "slower", "fast", "faster", "romantic",
    "sexy", "angry", "aggressive", "soft", "loud", "quiet", "dreamy", "nostalgic",
    "uplifting", "feelgood", "feel-good", "summery", "wintry", "rainy", "sunny",
    "night", "nighttime", "morning", "driving", "workout", "study", "sleep",
    "groovy", "funky", "smooth", "heavy", "light", "epic", "emotional", "vibe",
    "vibes", "mood", "something", "anything", "good", "nice", "cool",
    # The station's own request-slip vocabulary, so the two agree on what
    # counts as a description.
    "sustained", "surprise", "random", "afternoon", "evening", "late-night",
    "latenight", "upbeat", "downbeat", "banger", "bangers", "classic",
    "classics", "oldies", "newer", "older", "similar", "this", "that",
}
# Filler that shouldn't count either way when judging a query.
_VIBE_FILLER = {"a", "an", "the", "some", "me", "for", "and", "or", "of", "to",
                "songs", "song", "music", "track", "tracks", "tune", "tunes",
                "play", "find", "get", "want", "like", "really", "very", "more"}


def looks_like_a_vibe(q: str) -> bool:
    """True when a search query describes a feeling rather than names a track.

    Deliberately conservative: it only fires when EVERY meaningful word is a
    mood word, so "Fun House by The Stooges" and "Mr. Blue Sky" are untouched.
    """
    import re as _re

    words = [w for w in _re.findall(r"[a-z'-]+", (q or "").lower())
             if w not in _VIBE_FILLER]
    if not words or len(words) > 4:
        return False
    return all(w in _VIBE_WORDS for w in words)


def _blocked_reason(t: dict) -> str:
    """Why the station will never air this row, or "" if it will.

    Every admin listing the station serves — search, recent, browse, sound
    search, neighbours — stamps `blockedBy` on each row: an entry ref
    ({kind:'entry', type, id, name}) or a rule ref ({kind:'rule', label,
    seasonal}), null when clear. The station returns blocked rows on PURPOSE
    so its operator can find one to review; a caller is not an operator, and
    the queue gate answers 409 for every one of them.
    """
    ref = t.get("blockedBy")
    if not isinstance(ref, dict):
        return ""
    if ref.get("kind") == "rule":
        label = str(ref.get("label") or ref.get("field") or "a station rule")[:60]
        return f"{label}, seasonal" if ref.get("seasonal") else label
    kind = str(ref.get("type") or "track")[:12]
    name = str(ref.get("name") or "").strip()[:60]
    return f"blocked {kind}" + (f" — {name}" if name else "")


def _drop_blocked(items: list) -> tuple[list, int]:
    """Split a station listing into what a caller may be offered, and a count.

    The DJ used to read blocked tracks out as available, take the caller's
    pick, and only then meet the station's refusal at the queue gate — having
    already promised it. Filtering here is the fix; the count is what lets the
    tools tell "the library hasn't got it" apart from "we've got it and this
    station doesn't play it", which are different sentences to a caller.
    """
    keep = [t for t in (items or []) if not _blocked_reason(t)]
    return keep, len(items or []) - len(keep)


def era_year(t: dict) -> str:
    """The year the DJ may say for this row, under the station's era rule.

    A reissue's file year is not the record's year: the station announced a
    1964 Stax single as a 2012 record until upstream #1418/#1431 taught every
    listener-facing surface it owns (its scripts, its picker, /now-playing)
    to resolve the era first. Admin rows hand this sidecar the raw evidence —
    `originalYear` when a lookup or the operator answered, `isCompilation` /
    `eraUntrusted` when the row is a reissue suspect — but never the composed
    verdict, so the same precedence is composed here: the resolved original
    year wins; a suspect row with no answer says NOTHING rather than the
    wrong decade; only a trusted row's file year is repeated. Both flags
    null/absent — a library not yet re-walked, or a pre-1.9.0 station — fall
    through to the raw year, which is exactly the old behaviour.
    """
    orig = t.get("originalYear")
    if isinstance(orig, str):
        # int() is the normaliser on purpose: it accepts what a year tag can
        # legitimately be (whitespace, fullwidth digits normalise to 2014)
        # and rejects what merely LOOKS numeric (superscript digits pass
        # isdigit() but are not a year int() can read — found in review; they
        # must fall through, never override a trusted file year or crash).
        try:
            orig = int(orig.strip())
        except ValueError:
            orig = None
    if isinstance(orig, (int, float)) and not isinstance(orig, bool) and orig > 0:
        # Capped like every other station field in this file: a corrupt
        # value must not ride the prompt at full length.
        return str(int(orig))[:12]
    # /dj/* rows carry the two raw flags; /library/browse rows also carry the
    # station's own composed `yearUntrusted`. Any one being true means the
    # file year is a reissue's — and `is True` on purpose: a null is absent
    # evidence, not a verdict, and must not suppress a trusted year.
    if (t.get("isCompilation") is True or t.get("eraUntrusted") is True
            or t.get("yearUntrusted") is True):
        return ""
    return str(t.get("year") or "")[:12].strip()


def _fmt_track(t: dict, with_id: bool = False) -> str:
    # Every one of these fields comes from the station and goes into the
    # prompt, where length is latency on every turn for the rest of the call
    # and is paid for per token. The count is capped at 8 results; nothing
    # capped the size of one, so a single malformed record — a title that is
    # really a description, a tag dump in an album field — could dwarf the
    # rest of the briefing. A track that needs more than this to name itself
    # is not one the DJ can read out anyway.
    def f(key: str, limit: int = 120) -> str:
        return str(t.get(key) or "")[:limit].strip()

    bits = f"\"{f('title') or '?'}\" by {f('artist') or '?'}"
    if f("album"):
        # The era-resolved year, not the raw file year — see era_year. On the
        # live library the difference is already real: "Action Man in Motown
        # Suit" files as 2014 with originalYear 1981, and the station's own
        # announcer now says 1981 while a raw read here said 2014 back.
        year = era_year(t)
        bits += f" ({f('album')}" + (f", {year})" if year else ")")
    # The station stores mood tags and an energy score per track and returns
    # them on every search hit. Dropping them left the DJ describing records it
    # had real information about purely from the title.
    feel = []
    moods = t.get("moods") or []
    if isinstance(moods, list) and moods:
        feel.extend(str(m)[:40] for m in moods[:3])
        # `source` is the tag's provenance, and 'propagated' means the moods
        # were INHERITED from embedding neighbours because this track's own
        # metadata was too thin to judge — upstream's words for those rows are
        # "guesses built on guesses" (their #1362: a big-beat dance track
        # propagated to [calm, night] and aired on an ambient show off the
        # back of it). 41% of a real library. The DJ was reading them out
        # with the same confidence as a per-track judgement; a two-word
        # hedge is what keeps "moody, nocturnal" from becoming a claim.
        # 'llm' / 'manual' / 'uncertain-llm' stay unhedged — upstream's own
        # correction pass leaves them alone as real per-track judgements.
        if f("source", 24).lower() == "propagated":
            feel.append("(feel tags inherited — a guess)")
    # The station files energy as a WORD — 'low' | 'medium' | 'high' — not as a
    # score. This tested it with isinstance(..., (int, float)), so it dropped
    # the field from every row the station has ever sent, and the test that was
    # supposed to defend it passed a float no station produces. Found on the
    # 2026-08-14 upstream pass by reading the station's handler rather than the
    # fixture. Numbers are still read: a hand-built row or an older station
    # should not lose the field on the way back in.
    energy = t.get("energy")
    if isinstance(energy, str) and energy.strip():
        feel.append(f"{energy.strip().lower()} energy")
    elif isinstance(energy, (int, float)) and not isinstance(energy, bool):
        feel.append("high energy" if energy >= 0.66
                    else "low energy" if energy <= 0.33 else "mid energy")
    # Analysis columns ride every search, recent and browse row (the station
    # merges its library index into all three). bpm and key are the DJ's own
    # vocabulary — "same tempo, and it's in the relative minor" is a statement
    # rather than a claim — and `instrumental` answers "something without
    # singing" without a second search.
    bpm = t.get("bpm")
    if isinstance(bpm, (int, float)) and not isinstance(bpm, bool) and bpm:
        feel.append(f"{round(float(bpm))} bpm")
    if t.get("musicalKey"):
        feel.append(str(t["musicalKey"])[:12])
    if t.get("instrumental") is True:
        feel.append("instrumental")
    if feel:
        bits += " — " + ", ".join(feel)
    # Never-play. A blocked row reaching a caller-facing list is a bug the
    # tools below fix by filtering, but the marker stays: anything that slips
    # through must not read as available, because the queue gate WILL refuse it
    # and the DJ will have promised it first.
    blocked = _blocked_reason(t)
    if blocked:
        bits += f"  [NEVER-PLAY: {blocked} — cannot be queued]"
    # The exact-queue tool needs the id the search returned. Without it in the
    # text the model has nothing to pass and silently falls back to guessing.
    if with_id and f("id", 64):
        bits += f"  [id: {f('id', 64)}]"
    return bits
