"""What the DJ knows when the phone rings.

Everything here turns a station read into a line of prompt: what's playing,
what just played, what's queued, what the DJ has said on air tonight, which
segments exist, what else is on today. Facts only — how to behave with them
lives in `conduct`.

Source of the persona text: the station itself. The original build notes
assumed DJ Card / Show Card lived as markdown files on the NAS, but the
controller already publishes both —

    GET /dj         -> `soul`  == the DJ Card (voice, character, behaviour)
    GET /schedule   -> show `topic` == the Show Card (mechanics, format)

so cards are read live and always match what's actually on air. Nothing to
mount, nothing to keep in sync.

Budget discipline matters here: this is a realtime voice agent, so every
token in the system prompt is paid on time-to-first-token for every single
turn, not just at session start. Station reads are summarised, not dumped.
"""

from __future__ import annotations

import station as station_mod

CARD_BUDGET = 2000  # chars, matches the station's own DJ/Show Card convention

# Some station text (show topics especially) comes back double-encoded — an
# em dash arrives as "â€”", a middot as "Â·". Left alone, that lands in the
# prompt and the TTS reads it out as noise.
_MOJIBAKE_MARKERS = ("â€", "Â", "Ã")


def demojibake(text: str) -> str:
    if not text or not any(m in text for m in _MOJIBAKE_MARKERS):
        return text
    try:
        return text.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def clip(text: str, limit: int) -> str:
    text = demojibake((text or "").strip())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _fld(value, limit: int = 200) -> str:
    """One station-supplied field, hard-capped for the prompt.

    Every field below is interpolated into the SYSTEM PROMPT and re-sent on
    every turn — a multi-KB title (or one carrying prompt-like text) would
    balloon latency and cost and could crowd the conduct rules. So each field
    is capped. The limits sit WELL above any real value — a song title, artist
    or album is tens of characters, so the cap only ever bites a corrupt or
    hostile field of thousands. It is a guard against junk, never a trim of
    real metadata (0.10.61 gave the caps obvious headroom after the operator,
    reasonably, read "capped per field" as "loses context" — it does not).
    """
    return demojibake(str(value or "")).strip()[:limit]


def _fmt_now_playing(np: dict, speak_clock: bool = True) -> str:
    track = np.get("nowPlaying") or {}
    ctx = np.get("context") or {}
    bits = []

    if track.get("title"):
        # "When this call connected", not "now". The prompt is assembled once
        # in `prepare()` and `update_instructions` is never called, so every
        # fact here is frozen for the life of the call — while max_call_seconds
        # defaults to 300 and a track runs three to four minutes. Written as
        # "Now playing", the back half of a normal call carried a station fact
        # that had simply stopped being true, stated flatly, with nothing to
        # tell the DJ it had aged. A model trusts its own briefing over
        # spending a tool call, and this is the one line most likely to be read
        # back to a caller word for word.
        #
        # The sentence is now true for as long as the call lasts, which is the
        # smallest honest fix. Whether it also changes behaviour is UNMEASURED:
        # the drill reads the station once and cannot advance its clock, so
        # this is a correctness change, not a demonstrated improvement. The
        # bigger answer — refreshing the volatile facts on a track-change push,
        # which the on-air guard already receives — is still open.
        line = f"Playing when this call connected: \"{_fld(track['title'])}\""
        if track.get("artist"):
            line += f" by {_fld(track['artist'])}"
        # The station analyses tracks and publishes what it found. Without
        # this the DJ could name the record but had nothing to SAY about it —
        # and "what is this?" is one of the commonest things a caller asks.
        detail = []
        if track.get("album"):
            detail.append(_fld(track["album"]))
        if track.get("genre"):
            detail.append(_fld(track["genre"]))
        moods = track.get("moods") or []
        if isinstance(moods, list) and moods:
            detail.append(", ".join(_fld(m, 60) for m in moods[:3]))
        if track.get("bpm"):
            detail.append(f"{_fld(track['bpm'], 12)} bpm")
        if track.get("musicalKey"):
            detail.append(_fld(track["musicalKey"], 24))
        if detail:
            line += " — " + "; ".join(detail)
        bits.append(line + ".")
    else:
        bits.append("Nothing was playing as this call connected (between tracks).")

    clock = ctx.get("clock") or {}
    weather = ctx.get("weather") or {}
    time_ctx = ctx.get("time") or {}

    where = []
    # Mirrored from the station's djSpeakClock (SUB/WAVE 1.8): a station
    # that keeps the wall clock off air must not find the call-in DJ is the
    # one voice still announcing the hour. The daypart vibe below stays
    # either way — the station's own switch makes the same carve-out.
    # Every one of these context fields is station-supplied and rides the
    # SYSTEM PROMPT on every turn, exactly like the track fields above — so
    # each goes through _fld, the same junk-and-hostile-field cap. Missed at
    # first (the caps landed on the track siblings only); found in the
    # 2026-08-28 security sitting, where a hostile or upstream-relayed value
    # (a third-party weather condition, a mood aggregate) could otherwise
    # arrive uncapped and unrepaired. Short caps: these are a word or two.
    if speak_clock and clock.get("display"):
        where.append(_fld(clock["display"], 40))
    # `time` is a plain string ("evening") on some builds and an object with a
    # `vibe` on others — take whichever this station sends.
    if isinstance(time_ctx, dict) and time_ctx.get("vibe"):
        where.append(_fld(time_ctx["vibe"], 40))
    elif isinstance(time_ctx, str) and time_ctx:
        where.append(_fld(time_ctx, 40))
    if isinstance(weather, dict) and weather.get("condition"):
        temp = (f", {_fld(weather['temp'], 12)}{_fld(weather.get('tempUnit', ''), 4)}"
                if weather.get("temp") else "")
        where.append(f"{_fld(weather['condition'], 40)}{temp}")
    elif isinstance(weather, str) and weather:
        where.append(_fld(weather, 40))
    if where:
        bits.append("It's " + ", ".join(where) + ".")

    if ctx.get("dominantMood"):
        bits.append(f"The room tonight is {_fld(ctx['dominantMood'], 60)}.")

    # How many people are actually out there. A caller asking "is anyone even
    # listening?" is asking a real question, and the station knows the answer.
    listeners = _listener_count(np)
    if listeners is not None:
        bits.append(
            "Nobody else is tuned in right now." if listeners <= 0
            else f"{listeners} listener{'s' if listeners != 1 else ''} tuned in."
        )

    return " ".join(bits)


def _listener_count(np: dict) -> int | None:
    """How many people are tuned in, whichever shape the station sends.

    This used to insist on a bare int at `listeners`. A live station answers
    with `{"current": 0, "peak": 3}` there and `{"count": 0}` under `context`,
    so the test never passed and the line has never once reached a prompt —
    the DJ has been unable to answer "is anyone even listening?" the whole
    time, silently, because a missing fact looks exactly like a quiet station.

    "Everyone else" is the honest reading: the prompt is assembled before the
    caller's own browser tunes in (see `tune_in_on_call`), and the station's
    count lags a fresh listener by seconds either way.
    """
    for value in (np.get("listeners"), (np.get("context") or {}).get("listeners")):
        # bool is an int, and a `listeners: false` would otherwise read as 0.
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, dict):
            for key in ("current", "count"):
                if isinstance(value.get(key), int):
                    return int(value[key])
    return None


def _fmt_guests(show: dict) -> str:
    """Who else is in the booth.

    The station resolves guest personas onto the live show record, and nothing
    read them: a DJ hosting alongside someone had no idea they were there, so
    a caller who greeted the guest by name got a blank back from the one
    person who should have known.
    """
    names = [
        _fld(g.get("name"), 100)
        for g in (show.get("guests") or [])
        if isinstance(g, dict) and g.get("name")
    ]
    names = [n for n in names if n][:3]
    if not names:
        return ""
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"In the booth with you: {joined}."


# The show's musical shape, in the order it reads naturally aloud.
_SHAPE_FIELDS = (("genres", ""), ("moods", ""), ("eras", ""), ("energies", " energy"))


def _fmt_show_shape(show: dict) -> str:
    """What this show will actually accept.

    The station judges its picks against these filters, so a request outside
    them is refused after the DJ has already promised it. Knowing the shape up
    front is the difference between steering a caller somewhere that will
    play and a request that quietly never turns up.
    """
    bits = []
    for key, suffix in _SHAPE_FIELDS:
        values = [_fld(v, 60) for v in (show.get(key) or []) if str(v).strip()]
        if values:
            bits.append(", ".join(values[:4]) + suffix)
    if not bits:
        return ""
    strict = " The station holds to that strictly tonight." if show.get("filtersStrict") else ""
    return "This show plays: " + "; ".join(bits) + "." + strict


def _tracks(items: list, limit: int) -> list[str]:
    out = []
    for t in (items or [])[:limit]:
        if t.get("title"):
            artist = t.get("artist")
            out.append(f"\"{_fld(t['title'])}\""
                       + (f" by {_fld(artist)}" if artist else ""))
    return out


def _fmt_recent(state: dict, limit: int) -> str:
    if limit <= 0:
        return ""
    played = _tracks(state.get("history") or [], limit)
    return "Just played: " + ", ".join(played) + "." if played else ""


def _fmt_upcoming(state: dict, limit: int) -> str:
    """What's queued next, so the DJ can answer "what's coming up?" without
    guessing — and won't request something already on its way."""
    if limit <= 0:
        return ""
    queued = _tracks(state.get("upcoming") or state.get("queue") or [], limit)
    # Frozen at pickup like the now-playing line above, and staler than it: a
    # queue that was two deep at connect is empty ten minutes later. "Was"
    # keeps the sentence true and leaves the tools as the answer to "what's
    # coming up NOW", which is what the DJ should reach for anyway.
    return "Coming up after that: " + ", ".join(queued) + "." if queued else ""


def _fmt_stream_health(state: dict) -> str:
    """The two states in which the broadcast is NOT normal programming.

    Both ride /state, which the snapshot already fetches, and both are facts
    the DJ has invented around before: the idle pause is why a request eight
    seconds after pickup got a 503 ("requests pause while nobody is
    listening") and was narrated as a jammed queue (2026-08-13). A DJ that
    knows the real state can say it; one that doesn't gets to choose between
    silence and a story.
    """
    bits = []
    if state.get("streamIdle"):
        bits.append(
            "The station is IDLE right now — nobody was tuned in, so the "
            "programme is paused; it resumes as listeners (this caller "
            "included) connect. If something station-side answers oddly in "
            "the first moments, this is why — say so plainly rather than "
            "inventing a fault.")
    if state.get("musicStarved"):
        bits.append(
            "The station's music chain is STARVED — the emergency loop is on "
            "air, not the normal programme. Do not present what's playing as "
            "the show's own programming.")
    return " ".join(bits)


def _is_show_announcement(text: str, show_name: str, show_topic: str) -> bool:
    """The station announces programme starts into the chatter feed ("Show X
    begins — theme: ..."). The Show Card already carries that in full, so a
    chatter line repeating it would put the same text in the prompt twice."""
    t = demojibake(text.strip())
    if t.startswith('Show "') and "begins" in t[:140]:
        return True
    if show_name and show_name in t and ("theme:" in t or "begins" in t):
        return True
    probe = demojibake((show_topic or "").strip())[:80]
    return len(probe) > 30 and probe in t


_BOOKKEEPING_KINDS = {"scenario", "pick", "play", "queue", "system"}
_BOOKKEEPING_ROLES = {"event", "track"}

# Lines the station spoke ABOUT a previous call. Two reasons they must never
# reach the next caller's prompt: privacy (the last caller's business is not
# this caller's), and continuity (with them in, the DJ picks up where the
# LAST call left off and greets a stranger as though the conversation were
# still running).
#
# The kind set below has NEVER matched a live entry: we send kind "callin"
# but /dj/say accepts only 'dj-speak'/'link' and coerces everything else, so
# the station stores our lines as plain 'dj-speak' — checked against the live
# session feed 2026-08-23, which holds no 'callin' anywhere. The fixtures
# that pinned this filter invented the field, which is the same green-test
# trap as the energy float. The set stays because it documents intent and
# costs nothing; the check that actually fires live is `station.said_by_us`,
# fed by dj_say with the text of every line we aired.
_PRIVATE_KINDS = {"callin", "caller", "call"}


def _is_spoken(m: dict) -> bool:
    """Only actual DJ speech belongs in "things you said on air" — scenario
    lines, picker decisions and track-play markers are bookkeeping, and a
    track marker framed as the DJ's own words reads as nonsense."""
    kind = str(m.get("kind") or "").lower()
    role = str(m.get("role") or "").lower()
    return kind not in _BOOKKEEPING_KINDS and role not in _BOOKKEEPING_ROLES


def latest_programme_intro(session: dict) -> str:
    """The DJ's spoken intro for the current programme — its own message kind
    in the feed. Pinned into the prompt separately so it never scrolls out of
    the chatter window mid-show: it frames the whole show's fiction."""
    messages = session.get("messages") or session.get("turns") or []
    for m in reversed(messages):
        if str(m.get("kind") or "").lower() == "programme-intro":
            return clip(m.get("text") or "", 450)
    return ""


def _fmt_booth(session: dict, limit: int, show_name: str = "", show_topic: str = "") -> str:
    """The DJ's own recent on-air lines — handed over as live material to
    carry into the call, not just a repetition hazard."""
    if limit <= 0:
        return ""
    messages = session.get("messages") or session.get("turns") or []
    lines = []
    # Scan deeper than the limit so filtered bookkeeping doesn't shrink the
    # window below what was asked for.
    for m in messages[-(limit * 3):]:
        text = m.get("text") or m.get("content") or ""
        if not text or not _is_spoken(m):
            continue
        kind = str(m.get("kind") or "").lower()
        # The programme intro is pinned separately — keep it out of here.
        if kind == "programme-intro":
            continue
        # Anything the station said about an earlier CALL stays out: every
        # call starts fresh, and the last caller's business isn't this one's.
        if kind in _PRIVATE_KINDS:
            continue
        # The check that actually fires on a real station — see _PRIVATE_KINDS
        # for why the kind alone cannot: the station stores our lines as
        # 'dj-speak', indistinguishable by kind from its own announcements.
        if station_mod.said_by_us(text):
            continue
        # Pattern fallback for payloads without kind fields.
        if _is_show_announcement(text, show_name, show_topic):
            continue
        lines.append(clip(text, 220))
    lines = lines[-limit:]
    if not lines:
        return ""
    joined = "\n  ".join(lines)
    return (
        "Things YOU said on the broadcast in the last little while — the "
        "caller may well have heard them:\n  " + joined
    )


def _fmt_skills(skills: list) -> str:
    """The station's real segment catalogue, by name.

    A DJ knows its own show. Without this the agent had to spend a turn asking
    the station what segments exist — or, more often, guessed, and either
    offered something this station doesn't have or answered "what can you do?"
    with a vague list it wasn't sure of.
    """
    # Names only. The station enforces its own cooldowns and will say no if a
    # segment isn't due — which the DJ already handles honestly. Telling it the
    # intervals up front just made it ration segments itself and explain
    # timings to callers, which is the opposite of running a show.
    lines = []
    for s in (skills or [])[:12]:
        name = _fld(s.get("kind") or s.get("name"), 100)
        if not name:
            continue
        label = _fld(s.get("label"), 100)
        lines.append(name + (f" ({label})" if label and label.lower() != name else ""))
    if not lines:
        return ""
    return (
        "Segments you can run on air, by name — these and no others. Run one "
        "whenever a caller asks; the station decides if it's due:\n  "
        + "\n  ".join(lines)
    )


def _fmt_schedule(schedule: dict, active_id: str, takeover: bool = False) -> str:
    """The station's other shows, for "what's on after this?" — and, when the
    caller may switch the station, for recognising a show by name at all.

    Twelve, not the four it was: this station has eleven shows and The
    Overlook was fifth, so a caller asking Wade for it by name got told, in
    character, that no such show existed — two calls in a row (2026-08-09,
    rooms ee3ef9616834 and 7046da2b9289). A roster the DJ can't see is a
    takeover the caller has to fight for.
    """
    shows = schedule.get("shows") or []
    names = [
        _fld(s.get("name"), 120)
        for s in shows
        if s.get("id") != active_id and s.get("name")
    ][:12]
    if not names:
        return ""
    line = "Other shows on this station: " + ", ".join(names) + "."
    if takeover:
        line += (" A caller may ask to put one of these on the air for a "
                 "while — that is what subwave_takeover_show is for, and "
                 "these names are real.")
    return line


async def station_context(station, cfg: dict, snap: dict, show: dict,
                          speak_clock: bool = True) -> str:
    """Everything true about the station right now, as prompt text.

    Every read is already in the SNAPSHOT — including the schedule — so this
    adds nothing to the caller's wait. It used to re-read /schedule here, a
    second serial round-trip that on a congested station timed out for a full
    4.5-9s on top of the snapshot's, in front of every call (measured
    2026-08-10). The snapshot already gathered it concurrently; reuse it.
    """
    parts = [
        _fmt_now_playing(snap["now_playing"], speak_clock),
        # Both come off the live show record, so they cost no extra read — and
        # both are a line at most, which is what earns them a place in a prompt
        # paid for on every turn.
        _fmt_guests(show),
        _fmt_show_shape(show),
        _fmt_recent(snap["state"], int(cfg.get("context_recent_tracks", 3))),
        _fmt_upcoming(snap["state"], int(cfg.get("context_upcoming", 2))),
        # Nothing on a normal night — see _fmt_stream_health.
        _fmt_stream_health(snap["state"]),
        _fmt_booth(snap["session"], int(cfg.get("context_booth_lines", 4)),
                   demojibake(show.get("name", "")), show.get("topic", "")),
    ]
    # The roster also rides in whenever takeover is allowed, whatever the
    # context_schedule setting says: a caller who may switch the station to
    # another show is talking to a DJ who must recognise that show's name.
    # Wade refused The Overlook as caller nonsense because the permission was
    # on and this line was off (2026-08-09). The schedule comes from the
    # snapshot — no extra read — and the takeover tool reads it fresh when used.
    if cfg.get("context_schedule") or cfg.get("allow_takeover"):
        parts.append(_fmt_schedule(snap.get("schedule") or {}, show.get("id", ""),
                                   takeover=bool(cfg.get("allow_takeover"))))
    # Only when segments are actually enabled — otherwise it's a list of things
    # the DJ is about to be told it can't do.
    if cfg.get("allow_skills"):
        parts.append(_fmt_skills(snap.get("skills") or []))

    return "\n".join(filter(None, parts))
