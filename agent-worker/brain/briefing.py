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


def _fmt_now_playing(np: dict) -> str:
    track = np.get("nowPlaying") or {}
    ctx = np.get("context") or {}
    bits = []

    if track.get("title"):
        line = f"Now playing: \"{track['title']}\""
        if track.get("artist"):
            line += f" by {track['artist']}"
        # The station analyses tracks and publishes what it found. Without
        # this the DJ could name the record but had nothing to SAY about it —
        # and "what is this?" is one of the commonest things a caller asks.
        detail = []
        if track.get("album"):
            detail.append(str(track["album"]))
        if track.get("genre"):
            detail.append(str(track["genre"]))
        moods = track.get("moods") or []
        if isinstance(moods, list) and moods:
            detail.append(", ".join(str(m) for m in moods[:3]))
        if track.get("bpm"):
            detail.append(f"{track['bpm']} bpm")
        if track.get("musicalKey"):
            detail.append(str(track["musicalKey"]))
        if detail:
            line += " — " + "; ".join(detail)
        bits.append(line + ".")
    else:
        bits.append("Nothing is playing this second (between tracks).")

    clock = ctx.get("clock") or {}
    weather = ctx.get("weather") or {}
    time_ctx = ctx.get("time") or {}

    where = []
    if clock.get("display"):
        where.append(clock["display"])
    # `time` is a plain string ("evening") on some builds and an object with a
    # `vibe` on others — take whichever this station sends.
    if isinstance(time_ctx, dict) and time_ctx.get("vibe"):
        where.append(str(time_ctx["vibe"]))
    elif isinstance(time_ctx, str) and time_ctx:
        where.append(time_ctx)
    if isinstance(weather, dict) and weather.get("condition"):
        temp = f", {weather['temp']}{weather.get('tempUnit', '')}" if weather.get("temp") else ""
        where.append(f"{weather['condition']}{temp}")
    elif isinstance(weather, str) and weather:
        where.append(weather)
    if where:
        bits.append("It's " + ", ".join(where) + ".")

    if ctx.get("dominantMood"):
        bits.append(f"The room tonight is {ctx['dominantMood']}.")

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
        demojibake(str(g.get("name") or "")).strip()
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
        values = [str(v).strip() for v in (show.get(key) or []) if str(v).strip()]
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
            out.append(f"\"{t['title']}\"" + (f" by {artist}" if artist else ""))
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
    return "Coming up: " + ", ".join(queued) + "." if queued else ""


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

# Lines the station spoke ABOUT a previous call — our own back-to-air handoff
# goes out with kind "callin". Two reasons they must never reach the next
# caller's prompt: privacy (the last caller's business is not this caller's),
# and continuity (with them in, the DJ picks up where the LAST call left off
# and greets a stranger as though the conversation were still running).
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
        name = str(s.get("kind") or s.get("name") or "").strip()
        if not name:
            continue
        label = str(s.get("label") or "").strip()
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
        demojibake(s.get("name", ""))
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


async def station_context(station, cfg: dict, snap: dict, show: dict) -> str:
    """Everything true about the station right now, as prompt text.

    Every read is already in the SNAPSHOT — including the schedule — so this
    adds nothing to the caller's wait. It used to re-read /schedule here, a
    second serial round-trip that on a congested station timed out for a full
    4.5-9s on top of the snapshot's, in front of every call (measured
    2026-08-10). The snapshot already gathered it concurrently; reuse it.
    """
    parts = [
        _fmt_now_playing(snap["now_playing"]),
        # Both come off the live show record, so they cost no extra read — and
        # both are a line at most, which is what earns them a place in a prompt
        # paid for on every turn.
        _fmt_guests(show),
        _fmt_show_shape(show),
        _fmt_recent(snap["state"], int(cfg.get("context_recent_tracks", 3))),
        _fmt_upcoming(snap["state"], int(cfg.get("context_upcoming", 2))),
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
