"""Client for the SUB/WAVE controller REST API — the station's read AND write
surface, one class per external service.

Two halves cohabit here:

- READS (best-effort) assemble the pre-call system prompt: now-playing, the
  schedule, the DJ persona, listener counts, the library. A failed read degrades
  the prompt rather than the call — see `degraded()` and `_read_stats`.
- WRITES / ACTIONS (admin-gated) are the wrappers behind the DJ's station-
  changing tools — queue a track, take a request, like/unlike, block, run a
  segment, take over the schedule. Each is the local receipt-writing tool for
  its action (architecture invariant 12: every station-changing action leaves a
  receipt the DJ cannot forge). They carry the operator's admin credential; they
  are NOT public and NOT unauthenticated.

An earlier docstring called this a "slim read-only client" whose actions "go
through MCP" over "public reads, no auth" — none of which survived the write
wrappers landing here, and the stale description misled for a while. The shape
is right (one client per external service), so the fix was the words, not a
split into read/write classes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re as _re
import time
from pathlib import Path

import httpx

import settings as settings_store
from log_setup import describe

log = logging.getLogger("callin.station")

# Consecutive failed reads across every client in this process, so the token
# server can tell the card "the station is struggling" instead of silently
# serving thin prompts. Any successful read resets it.
_read_stats = {"consecutive_failures": 0}
_DEGRADED_AFTER = 3


def degraded() -> bool:
    return _read_stats["consecutive_failures"] >= _DEGRADED_AFTER


# Lines WE handed the station to speak, so the next conversation can recognise
# them in the station's own feeds. The kind we send with them cannot do it:
# /dj/say accepts only SAY_KINDS = ['dj-speak', 'link'] and silently coerces
# anything else, so every "callin" / "voicemail" marker this codebase sends is
# stored by the station as plain 'dj-speak'. Checked against the live session
# feed 2026-08-23: no 'callin' kind exists anywhere in it, which means the
# briefing's kind-based privacy filter (brain/briefing._PRIVATE_KINDS) has
# never once fired on a real deployment — a past caller's hand-back line was
# reaching the next caller's prompt as ordinary booth chatter.
#
# Module-level on purpose: one worker process serves many calls, and the line
# that must be recognised was aired during the PREVIOUS call. A restart
# forgets the ledger, which costs one call's worth of filtering, not privacy
# of anything the station didn't already broadcast.
from collections import deque as _deque
from urllib.parse import quote as _quote


def _seg(value) -> str:
    """One id, safe to drop into a URL PATH segment.

    Every id here arrives from the station via a tool result the MODEL has
    relayed, or from a caller's own words — never ours to trust with the
    shape of a path. Unquoted, a crafted id like "../../schedule/override"
    re-targets the request at a different station endpoint under the admin
    credentials we attach, which reaches DELETE routes whose own tools the
    operator disabled — defeating the per-tool grant boundary the tier
    ladder is built on (httpx collapses the dot-segments at build time).
    request_status and unblock_track always quoted; three siblings did not
    (security sitting, 2026-08-28). One quoter now, so a new path-building
    call cannot forget again. `safe=''` leaves a slash as %2F, not a
    separator.

    Dots are ALSO encoded — the subtle half, caught by the cloud review the
    same day. `.` is unreserved per RFC 3986, so `quote('..')` returns `'..'`
    unchanged, and httpx's build-time dot-segment collapse still fires on a
    bare `..` id: `/dj/queue/..` becomes `/dj`. Only the slash-carrying input
    the first test used was actually neutralised; a plain `..` still
    traversed. Encoding dots to %2E defeats the collapse (httpx removes the
    literal `..` before decoding, never the encoded form), and a station
    decodes %2E back to `.` so a legitimate dotted id still resolves.
    """
    return _quote(str(value), safe="").replace(".", "%2E")

_AIRED_BY_US: _deque = _deque(maxlen=80)
_AIRED_TTL_SECS = 2 * 3600.0


def _norm_spoken(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def note_aired_by_us(text: str) -> None:
    t = _norm_spoken(text)
    if t:
        _AIRED_BY_US.append((time.time(), t))


def said_by_us(text: str) -> bool:
    """Did WE put this line on air recently?

    Matched on the first 160 normalised characters so a clip on either side
    (the session feed's, or the briefing's own 220-char clip) cannot hide the
    match. Two hours is the window: the caller this protects is the NEXT one,
    minutes away, not tomorrow's.
    """
    t = _norm_spoken(text)[:160]
    if not t:
        return False
    now = time.time()
    return any(now - at <= _AIRED_TTL_SECS and t == line[:160]
               for at, line in _AIRED_BY_US)


# Reads are quick or they're broken. ACTIONS are not: /dj/skill runs a whole
# segment (script, then speech) before it answers, and /dj/say re-voices a line
# through the station's own TTS. Both routinely take longer than the read
# timeout — and a timeout was being reported to the caller as "that didn't
# work" while the segment was audibly going out on air.
ACTION_TIMEOUT = 45.0

# The library reads are the third case: not an action, but not quick either.
# Measured against the operator's live station (381,023 tracks) a filtered
# /library/browse takes ~4.1s — the default read timeout is 4.5s, so it sat a
# few hundred milliseconds from tripping and duly tripped on a real call, where
# the DJ told the caller it "couldn't read the library just now". The station is
# doing real work over a big index; give it room, but far less than an ACTION,
# because a caller is waiting on the answer.
LIBRARY_TIMEOUT = 15.0

# The station's TTS boundary (its spoken-script-policy module, upstream
# #1455): under an English or UNSET persona language -- unset is the product
# default, and every persona on this deployment is unset -- any Han,
# Hiragana, Katakana or Hangul in a line handed to /dj/say is DELETED before
# the booth speaks it, while the `spoken` text echoed back to us still
# carries it. So every sender of caller-derived text needs to know what will
# actually air. Mirrored as explicit ranges because stdlib `re` has no
# \p{Script=}: the four scripts' main blocks plus the extensions upstream's
# property classes include -- halfwidth katakana, compatibility ideographs,
# the Han extensions, hangul jamo. An explicitly non-English persona keeps
# its script upstream; if the operator ever sets one, this mirror needs that
# language plumbed in.
NATIVE_SCRIPT_RE = _re.compile(
    "[\u1100-\u11ff\u3005\u3007\u3041-\u30ff\u3130-\u318f\u31f0-\u31ff"
    "\u3400-\u4dbf\u4e00-\u9fff\ua960-\ua97f\uac00-\ud7a3\ud7b0-\ud7ff"
    "\uf900-\ufaff\uff66-\uff9f\uffa0-\uffdc\U00020000-\U0002fa1f]")


def booth_spoken_text(text) -> str:
    """The line as the booth will actually speak it -- native-script
    characters removed, the way the station's own boundary removes them.
    Compare with the original: a difference means part of the line will not
    be heard on air, and any receipt built on it must say so."""
    return NATIVE_SCRIPT_RE.sub("", str(text or ""))


def _body(r: httpx.Response) -> dict:
    """A 2xx is the success signal, not the body's shape. Some station
    endpoints answer with an empty body, plain text, or a bare list — calling
    .json() blind turned those into exceptions and, one layer up, into the DJ
    telling the caller an action had failed."""
    try:
        data = r.json() if r.content else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"result": data}


def _sent_but_unconfirmed(e: Exception) -> bool:
    """True when the request reached the station but the answer didn't come
    back in time. The action has almost certainly run, so this must NOT be
    reported as a failure — the honest line is "it's gone through"."""
    return isinstance(e, httpx.TimeoutException) and not isinstance(
        e, httpx.ConnectTimeout
    )

# Last-known-good persona, shared across calls in this process.
#
# Why: GET /dj is lazily cached on the station side. Warm it answers in ~15ms,
# but the first read after a quiet spell has been measured at 19.5s — which
# would otherwise sit in front of every call as dead air while the caller
# listens to ringing. A slightly stale persona is far better than a caller
# waiting, so a slow read falls back to the last one that worked.
_persona_cache: dict = {"value": None, "at": 0.0}
_PERSONA_TTL = 300.0

# The same fallback, but on DISK — because every call is its own job process,
# so the in-process cache above is empty at the start of each one and can only
# help within a single call. When the whole station is timing out (observed
# 2026-08-10: every read ReadTimeout, so /dj, /now-playing AND /personas all
# failed), a fresh process had nothing to fall back to and answered as the
# generic "the DJ" on "SUB/WAVE" — the wrong DJ, the wrong station name, on a
# real caller. This file lets the next process reuse the last persona and
# station name that actually resolved.
_PERSONA_FILE = Path(
    os.environ.get("LAST_PERSONA_PATH",
                   Path(__file__).parent.parent / "data" / "last-persona.json")
)


def _remember_persona(persona: dict, station: str) -> None:
    try:
        _PERSONA_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PERSONA_FILE.write_text(json.dumps(
            {"persona": persona, "station": station, "at": time.time()}))
    except Exception as e:                                    # noqa: BLE001
        log.debug("could not remember the last persona (harmless): %s", e)


def _recall_persona() -> dict | None:
    """The last persona+station that resolved, if recent enough to trust."""
    try:
        d = json.loads(_PERSONA_FILE.read_text())
        if time.time() - float(d.get("at") or 0) < _PERSONA_TTL:
            return d
    except Exception:                                         # noqa: BLE001
        pass
    return None


class StationClient:
    def __init__(self, base_url: str | None = None, timeout: float = 4.5) -> None:
        # 4.5s, down from 8s (2026-08-10). A warm station answers a read in
        # ~15ms; the only slow read is a cold or overloaded one, and waiting
        # 8s for it put ~12s of ringing in front of a caller (it used to be
        # 4-5s). The persona and voice both have last-known-good DISK caches
        # now, so a read that misses this window falls to the RIGHT DJ and the
        # RIGHT voice — stale by minutes at worst — instead of making the
        # caller wait. Action writes keep their own long ACTION_TIMEOUT.
        # Which station this points at is a setting, so it can be re-homed
        # from the settings page without a restart.
        self._client = httpx.AsyncClient(
            base_url=base_url or settings_store.station_base_url(), timeout=timeout
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, retries: int = 0) -> dict:
        """Reads are best-effort — a call should still connect if the station
        is mid-restart, just with a thinner prompt."""
        for attempt in range(retries + 1):
            try:
                r = await self._client.get(path)
                r.raise_for_status()
                _read_stats["consecutive_failures"] = 0
                return r.json()
            except Exception as e:
                if attempt < retries:
                    log.info("station read %s failed (%s) — retrying",
                             path, describe(e))
                    continue
                _read_stats["consecutive_failures"] += 1
                log.warning("station read %s failed: %s", path, describe(e))
                return {}
        return {}

    async def health(self) -> dict:
        return await self._get("/health")

    async def now_playing(self) -> dict:
        return await self._get("/now-playing")

    async def live_dj(self) -> dict:
        """Who is on air right now — name, tagline, and `soul` (the DJ Card).

        The one known-slow endpoint (lazy cache on the station side). It USED
        to get one retry, on the theory that a cold first read warms the
        station cache for a fast second. But a retry doubles the worst case —
        two full timeouts, ~9s of ringing in front of the caller — and since
        both the persona and the voice now have last-known-good DISK caches, a
        read that misses the window falls to the RIGHT DJ (stale by minutes)
        far faster than a second attempt could ever return. Speed beats a
        marginally fresher persona on a line where the caller is listening to
        the phone ring. No retry: one timeout, then the cache.
        """
        return await self._get("/dj")

    async def personas(self) -> list[dict]:
        return (await self._get("/personas")).get("personas", [])

    async def schedule(self) -> dict:
        return await self._get("/schedule")

    async def state(self) -> dict:
        return await self._get("/state")

    async def session_feed(self) -> dict:
        """The live DJ session's chat history — the station serves it FOR a
        player booth feed (its own words), sfx already filtered out. Empty
        when nobody is on."""
        return await self._get("/session")

    async def themes(self) -> dict:
        """The station's theme registry and which one is on air.

        `effective` is what a listener's player is actually painted in: an
        on-air show's own themeId outranks the station-wide default, so this
        follows the programme rather than the settings page. Every entry
        carries a `tokens` map and a light/dark `mode`.
        """
        return await self._get("/themes")

    async def session(self) -> dict:
        return await self._get("/session")

    async def snapshot(self, with_skills: bool = False) -> dict:
        """Everything the prompt needs, fetched concurrently.

        Serially these cost ~1s on a good day and far more when the station's
        persona cache is cold; the caller hears all of it as ringing. The
        skill catalogue joins the same gather when segments are enabled, so
        knowing the show costs no extra ringing time.
        """
        async def _skills() -> list[dict]:
            return await self.list_skills() if with_skills else []

        dj, personas, now, state, session, schedule, skills = await asyncio.gather(
            self.live_dj(), self.personas(), self.now_playing(),
            self.state(), self.session(), self.schedule(), _skills(),
        )
        return {
            "dj": dj, "personas": personas, "now_playing": now,
            "state": state, "session": session, "schedule": schedule,
            "skills": skills,
        }

    def persona_from(self, dj: dict, personas: list[dict]) -> dict:
        """Resolve the on-air persona from an already-fetched snapshot."""
        name = dj.get("name")
        persona_id = dj.get("id") or dj.get("personaId")

        if not persona_id and name:
            for p in personas:
                if p.get("name") == name:
                    persona_id = p.get("id")
                    break

        # The station sets an on-air language per persona (free text — "Turkish",
        # "Türkçe"; empty means English). This dict is CONSTRUCTED rather than
        # passed through, so every field not named here is dropped — and
        # `language` was, which meant the prompt never carried it and the DJ had
        # nothing to tell it what language it works in.
        #
        # It inferred one instead, from whatever was in the briefing. Heard on
        # 2026-08-18: Brock, an English persona with no CJK anywhere in his own
        # description, opened a call in Mandarin and stayed there — the station
        # was rotating Mandarin-titled tracks, one show on the schedule is named
        # in Chinese, and the previous presenter (who does work in Mandarin) had
        # her on-air line quoted verbatim into his context. The caller spoke
        # English throughout.
        #
        # Preferring /dj and falling back to the roster, because /dj is the live
        # answer but times out often enough to matter (see the note above).
        language = str(dj.get("language") or "").strip()
        if not language and persona_id:
            for p in personas:
                if p.get("id") == persona_id:
                    language = str(p.get("language") or "").strip()
                    break

        resolved = {
            "id": persona_id or "default",
            "name": name or "the DJ",
            "soul": dj.get("soul", ""),
            "tagline": dj.get("tagline", ""),
            # Carried on the persona so the prompt's station-name line has a
            # fallback when /dj timed out — see brain/assemble.py.
            "station": dj.get("station", ""),
            "language": language,
        }

        if name:
            _persona_cache["value"] = resolved
            _persona_cache["at"] = time.time()
            _remember_persona(resolved, resolved["station"])
            return resolved

        # No live answer. Prefer the in-process cache (fastest, same call),
        # then the disk cache from a previous call's process — either beats
        # collapsing a real DJ into the generic default.
        if _persona_cache["value"] and (time.time() - _persona_cache["at"]) < _PERSONA_TTL:
            log.warning("station /dj was slow or empty — using this process's last persona")
            return _persona_cache["value"]
        recalled = _recall_persona()
        if recalled and recalled.get("persona"):
            log.warning("station /dj was slow or empty — using the last persona "
                        "on record (%s)", recalled["persona"].get("name"))
            p = dict(recalled["persona"])
            p.setdefault("station", recalled.get("station", ""))
            return p

        return resolved

    async def resolve_live_persona(self) -> dict:
        """Resolve the on-air persona to {id, name, soul, tagline}.

        `GET /dj` returns the live persona but not always its id, so match it
        back against `GET /personas` by name to recover the id that
        persona-voices.json is keyed on.
        """
        dj = await self.live_dj()
        personas = await self.personas() if not (dj.get("id") or dj.get("personaId")) else []
        return self.persona_from(dj, personas)

    # djLog kinds that mean the on-air DJ is actually making sound. Everything
    # else in that log (picker, queued, scheduler, mix…) is bookkeeping.
    # Checked against the station's own queue/kinds.ts VOICE_KINDS on the
    # 2026-08-31 upstream pass: the station logs "hourly-check" (never
    # "hourly", which no release has emitted), and "banter" and "handoff"
    # are speech this guard could not see — a banter exchange or a mic-pass
    # sign-off just before pickup slipped past the same-persona overlap
    # check. "hourly" stays for any older station that did say it.
    ON_AIR_SPEECH_KINDS = {
        "link", "dj-speak", "station-id", "sfx", "hourly", "hourly-check",
        "banter", "handoff", "segment", "skill",
    }

    async def on_air_speech(self, state: dict | None = None) -> tuple[float, str] | None:
        """The newest thing the on-air DJ said: (seconds since it started, the
        words themselves). Returns None if the log shows no speech.

        Used to avoid the same persona talking on air and on the call at the
        same time. The words matter as much as the clock: the log records when
        an utterance started, never when it ended, so the guard sizes the end
        from the words (call/air.speaking_secs). When `t` is stamped depends
        on the station's generation: pre-1.8 stamps at handoff to the playout
        (a couple of seconds before it is audible), 1.8+ stamps at air time
        (its #1390). The guard's HANDOFF_LAG_SECS pad covers the older shape
        and is deliberately kept for both — see call/air.py.
        """
        from datetime import datetime, timezone

        st = state if state is not None else await self.state()
        newest = None
        message = ""
        for entry in (st.get("djLog") or []):
            kind = str(entry.get("kind") or "")
            # Skill segments are logged under their own name, so treat anything
            # that isn't recognisably bookkeeping as speech.
            speechy = kind in self.ON_AIR_SPEECH_KINDS or kind.startswith(("dj", "seg"))
            if not speechy:
                continue
            raw = entry.get("t")
            if not raw:
                continue
            try:
                when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if newest is None or when > newest:
                newest = when
                message = str(entry.get("message") or "")

        if newest is None:
            return None
        since = max(0.0, (datetime.now(timezone.utc) - newest).total_seconds())
        return since, message

    async def dj_say(self, text: str, mode: str = "styled", kind: str = "callin") -> dict:
        """Hand a line to the on-air DJ. `styled` lets the station rewrite it in
        the persona's own voice before speaking it, so the call-in agent's
        phrasing doesn't have to match the broadcast voice exactly.

        `kind` is OUR intent marker, not the station's vocabulary: /dj/say
        accepts only 'dj-speak' and 'link' and silently coerces everything
        else to 'dj-speak' (heavy duck, solo moment — which is what every
        caller of this wants). It never comes back in any station feed, so
        nothing downstream may branch on it; recognising our own lines is
        `said_by_us`'s job, fed below.

        Admin-only (the endpoint 401s without credentials).
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            log.info("skipping on-air handoff — no station admin credentials")
            return {"ok": False, "error": "no station admin credentials"}

        # The station truncates silently at its SAY_TEXT_MAX (500). Cutting
        # here instead, at a sentence-ish boundary of our choosing, beats a
        # raw-mode line going to air chopped mid-word with nothing logged.
        text = str(text or "")
        if len(text) > 500:
            log.warning("on-air line over the station's 500-char cap — "
                        "cutting at %d chars", 500)
            text = text[:500]

        try:
            r = await self._client.post(
                "/dj/say",
                json={"text": text, "mode": mode, "kind": kind},
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            body = _body(r)
            # What actually aired (styled mode rewrites `text`), so the next
            # conversation's briefing can recognise this line as its own —
            # the kind marker cannot carry it, see the docstring.
            note_aired_by_us(body.get("spoken") or text)
            return {"ok": True, **body}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("on-air line slow to confirm (%s) — treating as sent", e)
                # Best known text: styled mode's rewrite is lost with the
                # response, so the instruction is what there is to match on.
                note_aired_by_us(text)
                return {"ok": True, "unconfirmed": True}
            log.warning("on-air handoff failed: %s", describe(e))
            return {"ok": False, "error": str(e)[:140]}

    async def search_library(self, q: str, offset: int = 0,
                             limit: int = 30) -> list[dict] | None:
        """Term search over the library. Admin-gated.

        Returns None when the READ ITSELF failed — a timeout, a 5xx, missing
        credentials — and a list (possibly empty) only when the station
        actually answered. The two used to collapse into one empty list, and
        the difference is a whole conversation: [] means "no such record",
        None means "I couldn't look". On 2026-08-19 two of these timed out
        mid-call and the DJ told the caller their artist wasn't in a library
        that holds over a hundred of their tracks — twice, to the same
        person, who said "bullshit" and was right.

        LIBRARY_TIMEOUT rather than the 4.5s default, same reasoning as the
        browse: the route reloads the station's library index when it has
        gone stale, which against 381k tracks is exactly the slow read that
        timeout exists for. Warm it answers in ~0.1s; the DJ talks over the
        cold case.

        offset/limit ride /dj/search's own paging (what the station's admin
        Search tab pages with). Mirrored after the station unfenced its wide
        sources (its #1339): without an offset, anything past the first page
        of a result set was unreachable from the call line at all.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return None
        try:
            r = await self._client.get(
                "/dj/search",
                params={"q": q, "offset": max(0, int(offset)),
                        "limit": max(1, int(limit))},
                auth=httpx.BasicAuth(user, password),
                timeout=LIBRARY_TIMEOUT,
            )
            r.raise_for_status()
            d = r.json()
            items = d if isinstance(d, list) else (d.get("results") or d.get("tracks") or [])
            return items if isinstance(items, list) else []
        except Exception as e:
            log.warning("library search failed: %s", describe(e))
            return None

    async def search_by_sound(self, description: str, limit: int = 12) -> list[dict]:
        """"Sounds like" search: a description, matched against how tracks
        actually sound. Admin-gated; empty list on failure.

        The station embeds the phrase through the analyzer's CLAP text tower
        and KNNs it against the stored per-track audio vectors — the same path
        its own picker's searchBySound takes. This is the one search here that
        is not a word match, and it is why the call line was previously unable
        to answer "something dreamy and cinematic" with anything but a blind
        request: /dj/search would have returned songs with "dreamy" in the
        title.

        503 is a real and expected answer, not a fault: the capability needs
        the heavy analyzer running AND tracks that have been audio-analysed
        (the station reports both under /library/coverage as
        soundSearchAvailable). It lands here as an empty list, which the tool
        turns into "this station can't do that" rather than "nothing matched"
        — telling a caller their vibe has no music in it, when really the
        feature is off, is the worse of the two lies.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return []
        try:
            r = await self._client.get(
                "/library/search-sound",
                params={"q": description, "limit": max(1, int(limit))},
                auth=httpx.BasicAuth(user, password),
                # The station warns this shares a single-threaded analyzer
                # with bulk passes, so it holds its own deadline rather than
                # the default read timeout.
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            d = r.json()
            items = d.get("results") if isinstance(d, dict) else d
            return items if isinstance(items, list) else []
        except Exception as e:
            log.warning("sound search failed: %s", describe(e))
            return []

    async def tracks_like(self, track_id: str) -> list[dict]:
        """The station's own "what mixes well after this" neighbours.

        /library/observatory/track/:id returns the track plus `mixNext` —
        library.tracksLikeThis(), scored. Admin-gated; empty list on failure,
        including the 404 for a track id the library does not hold.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password) or not track_id:
            return []
        try:
            r = await self._client.get(
                f"/library/observatory/track/{_seg(track_id)}",
                auth=httpx.BasicAuth(user, password),
                timeout=LIBRARY_TIMEOUT,
            )
            r.raise_for_status()
            d = r.json()
            items = d.get("mixNext") if isinstance(d, dict) else None
            return items if isinstance(items, list) else []
        except Exception as e:
            log.warning("neighbours for %s failed: %s", track_id, describe(e))
            return []

    async def browse_library(self, moods: str = "", energy: str = "",
                             genre: str = "", year_from=None, year_to=None,
                             vocal: str = "", limit: int = 12) -> dict:
        """Filtered browse over the tagged library — the station's own
        Library tab, as a tool. Admin-gated; empty dict on failure.

        Returns the rows AND the station's `moodVocab`, deliberately: the mood
        list is a fixed seventeen-word vocabulary, and a caller-supplied word
        outside it silently matches nothing (asking for "melancholy" returns
        0 of 381,023 tracks — the station's word is "reflective"). The tool
        hands the vocabulary back so the DJ can re-ask in the station's own
        words instead of reporting an empty library.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {}
        params: dict = {"limit": max(1, int(limit))}
        for key, value in (("moods", moods), ("energy", energy),
                           ("genre", genre), ("vocal", vocal)):
            if value:
                params[key] = value
        for key, value in (("yearFrom", year_from), ("yearTo", year_to)):
            if value:
                params[key] = value
        try:
            r = await self._client.get(
                "/library/browse", params=params,
                auth=httpx.BasicAuth(user, password),
                timeout=LIBRARY_TIMEOUT,
            )
            r.raise_for_status()
            d = r.json()
            return d if isinstance(d, dict) else {}
        except Exception as e:
            log.warning("library browse failed: %s", describe(e))
            return {}

    async def cancel_queued_track(self, track_id: str) -> dict:
        """Pull a not-yet-aired track back out of the queue. Admin-only.

        The station removes it from Liquidsoap's dj_queue over telnet FIRST
        and only splices its own entry once that confirms, so a failure here
        never half-cancels.

        Both refusals come back as **409**, told apart only by `reason` in the
        body — `already-playing` (the track has left dj_queue: on air, or being
        prepared as the next source, and skip is the only tool for it) and
        `not-queued`. Verified against the live station rather than assumed:
        this was first written to read a 404 for the second case, which the
        station never sends, so a cancel of something that was not there would
        have been reported to the caller as "too late" — plausible, and wrong.
        Both are normal answers, not errors, so both come back named.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        if not track_id:
            return {"ok": False, "error": "no track id to cancel"}
        try:
            r = await self._client.delete(
                f"/dj/queue/{_seg(track_id)}",
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            if r.status_code == 409:
                reason = str((_body(r) or {}).get("reason") or "not-queued")
                return {"ok": False, "reason": reason,
                        "error": "that one's already on the way to air"
                                 if reason == "already-playing"
                                 else "that track isn't in the queue"}
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            # No _sent_but_unconfirmed optimism here, unlike the other writes:
            # reporting an unconfirmed cancel as done is how a caller gets told
            # a track is gone and then hears it play.
            log.warning("cancel of %s failed: %s", track_id, describe(e))
            return {"ok": False, "error": str(e)[:120]}

    async def current_lyrics(self) -> dict:
        """The station's public read for the airing track's lyrics.

        Feature-detected on purpose: /lyrics/current ships with the station's
        current-track lyrics work (its #1316), and a station without it
        answers 404. `lines` is [] for instrumentals and unindexed tracks;
        `songId` is null when the on-air item is not a library track. No auth:
        the same read every listener player gets.

        **A failed read and an empty answer are different facts, and this used
        to return the same `{}` for both.** The tool above turned that `{}`
        into "an instrumental, or the station has none indexed", so a station
        with no lyrics feature at all — this operator's, measured 2026-08-20,
        404 on /lyrics/current and on every other spelling of it — had every
        one of its tracks described to callers as an instrumental. One chat
        that afternoon did it eleven times, defended it when the caller said
        the song plainly had words, and the caller was right: it was "GALA" by
        XG. The DJ was not inventing. It was relaying a receipt that said
        something the station had never claimed.

        So the failure branch is now MARKED. `unavailable` means we do not
        know anything about this track's lyrics; an absent `unavailable` with
        empty `lines` means the station answered and holds none.
        """
        try:
            r = await self._client.get("/lyrics/current")
            r.raise_for_status()
            d = r.json()
            return d if isinstance(d, dict) else {}
        except Exception as e:
            log.info("lyrics unavailable (%s)", describe(e))
            return {"unavailable": describe(e)}

    @staticmethod
    def _refusal_words(response) -> str:
        """The station's own words for a refusal, rule and all.

        A 4xx used to come back as str(HTTPStatusError) — "Client error '409
        Conflict' for url …" — which threw the body away. Since SUB/WAVE
        1.8's blocklist rules (#1332) that body NAMES what refused ("never
        play · rule", the field and values), and the DJ can only stay in
        character about a refusal it was told the reason for: "house rules
        say no death metal" beats fumbling (parked 2026-08-10, done 0.10.91).
        """
        try:
            d = response.json()
        except Exception:                                     # noqa: BLE001
            return (getattr(response, "text", "") or "").strip()[:240]
        if not isinstance(d, dict):
            return ""
        msg = str(d.get("message") or d.get("error") or d.get("detail")
                  or d.get("reason") or "").strip()
        hit = d.get("blockedBy") or d.get("blocked_by") or {}
        if isinstance(hit, dict) and hit:
            bits = []
            if hit.get("field"):
                vals = hit.get("values")
                if isinstance(vals, list) and vals:
                    bits.append(f"{hit['field']}: "
                                + ", ".join(str(v) for v in vals[:4]))
                elif hit.get("value"):
                    bits.append(f"{hit['field']}: {hit['value']}")
                else:
                    bits.append(str(hit["field"]))
            elif hit.get("label"):
                bits.append(str(hit["label"]))
            kind = "rule" if str(hit.get("kind")) == "rule" else "never-play list"
            rule = "; ".join(bits)
            named = (f"blocked by the station's {kind}"
                     + (f" ({rule})" if rule else ""))
            msg = f"{msg} — {named}" if msg else named
        return msg[:240]

    async def queue_track(self, track: dict) -> dict:
        """Push an exact track from a search result onto the queue.

        Different from submit_request in ways that matter on a call: it takes
        the id the search returned, so the station plays THAT track rather than
        resolving the words again and possibly landing on something else — and
        it isn't subject to the request endpoint's 1-per-20s gate. No DJ intro
        is generated; the caller's DJ is already talking about it.

        Admin-only.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        if not (track.get("id") and track.get("title")):
            return {"ok": False, "error": "need the track id and title from a search"}
        try:
            r = await self._client.post(
                "/dj/queue-track",
                json={k: v for k, v in track.items() if v not in (None, "")},
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except httpx.HTTPStatusError as e:
            # 409 = the blocklist (or a duplicate); the body names the rule.
            said = self._refusal_words(e.response)
            log.warning("queue-track refused (%s): %s",
                        e.response.status_code, said or describe(e))
            return {"ok": False, "error": said or str(e)[:140]}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("queue-track slow to confirm (%s) — treating as queued", e)
                return {"ok": True, "unconfirmed": True}
            log.warning("queue-track failed: %s", describe(e))
            return {"ok": False, "error": str(e)[:140]}

    async def submit_request(self, text: str, name: str = "") -> dict:
        """Public request endpoint — the same path the station's own request
        slip uses.

        Retries once on a 5xx. Observed on a real call: the caller asked for a
        song eight seconds after pickup and the station answered 503 — it
        pauses requests while nobody is listening, and the caller's own tune-in
        had not shown up in its listener count yet. Their request was lost to a
        few seconds of timing, and the DJ told them so. A single retry after a
        short wait costs nothing and covers the window.
        """
        payload: dict = {"text": text}
        if name:
            payload["name"] = name

        last: Exception | None = None
        for attempt in (0, 1):
            if attempt:
                await asyncio.sleep(4.0)
            try:
                r = await self._client.post(
                    "/request", json=payload, timeout=ACTION_TIMEOUT
                )
                r.raise_for_status()
                return _body(r)
            except httpx.HTTPStatusError as e:
                last = e
                if e.response.status_code < 500:
                    # A real refusal — retrying would just repeat it. The
                    # body names WHY (a never-play rule, requests closed,
                    # the rate gate) and the DJ can only stay in character
                    # about a reason it was told.
                    said = self._refusal_words(e.response)
                    if said:
                        log.warning("request refused (%s): %s",
                                    e.response.status_code, said)
                        return {"error": said}
                    break
                log.info("station 5xx on request (%s) — retrying once",
                         e.response.status_code)
            except Exception as e:
                last = e
                if _sent_but_unconfirmed(e):
                    log.warning("request slow to confirm (%s) — treating as submitted", e)
                    return {"unconfirmed": True}
                break

        log.warning("request submit failed: %s", last)
        return {"error": str(last)[:140]}

    async def request_status(self, request_id: str) -> dict:
        # Quoted via _seg: the id comes back from the station through a tool
        # result the model relayed, never ours to trust as a path.
        try:
            r = await self._client.get(f"/request/{_seg(request_id)}")
            r.raise_for_status()
            return r.json()
        except Exception:
            return {}

    async def list_skills(self) -> list[dict]:
        """The station's segment catalogue — kind, label and cooldown.

        Read up front rather than left to a mid-call tool round-trip: a DJ
        knows its own show. Without this the agent either guesses at segment
        names or spends a turn asking the station what it can do, and a caller
        hears the pause.

        Admin-gated; empty list when unavailable, which simply means the
        prompt doesn't mention segments.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return []
        try:
            r = await self._client.get("/dj/skills", auth=httpx.BasicAuth(user, password))
            r.raise_for_status()
            d = _body(r)
            items = d.get("skills") or d.get("result") or []
            return items if isinstance(items, list) else []
        except Exception as e:
            log.info("skill catalogue unavailable: %s", describe(e))
            return []

    async def recent_tracks(self, limit: int = 12) -> list[dict]:
        """The library's newest arrivals — /dj/recent, flattened by the
        station into the same queue-ready shape /dj/search returns.

        Admin-gated like the search it feeds; empty list when unavailable,
        which the tool reports honestly rather than inventing arrivals.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return []
        try:
            r = await self._client.get(
                "/dj/recent",
                params={"limit": max(1, int(limit))},
                auth=httpx.BasicAuth(user, password),
            )
            r.raise_for_status()
            d = _body(r)
            items = d.get("results") or d.get("result") or []
            return items if isinstance(items, list) else []
        except Exception as e:
            log.info("recent tracks unavailable: %s", describe(e))
            return []

    async def run_skill(self, name: str) -> dict:
        """Fire one of the station's own segments. Admin-only."""
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        try:
            r = await self._client.post(
                "/dj/skill",
                json={"name": name},
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            # `ok` first so a station payload carrying its own verdict wins.
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("skill %s slow to confirm (%s) — treating as running", name, e)
                return {"ok": True, "unconfirmed": True}
            log.warning("skill %s failed: %s", name, describe(e))
            return {"ok": False, "error": str(e)[:120]}

    async def skip_track(self) -> dict:
        """Force-end whatever is playing. Admin-only, and station-wide.

        The station's own API calls this an operator override and notes there
        is deliberately no listener-facing skip. Exposing it to a caller is
        therefore a decision the operator makes about their own station, which
        is why the setting is off by default and capped per call.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        try:
            r = await self._client.post(
                "/dj/skip",
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("skip slow to confirm (%s) — treating as done", e)
                return {"ok": True, "unconfirmed": True}
            log.warning("skip failed: %s", describe(e))
            return {"ok": False, "error": str(e)[:120]}

    async def dj_segment(self, kind: str) -> dict:
        """Fire one of the station's scripted beats — station id, the hour, a
        link, banter. Admin-only.

        Distinct from `run_skill`: these are programme furniture rather than
        content segments, and the station documents that an explicit press
        bypasses its own frequency and budget gates. So the only thing pacing
        them on a call is our per-call action cap.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        try:
            r = await self._client.post(
                "/dj/segment",
                json={"type": kind},
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("segment %s slow to confirm (%s) — treating as running", kind, e)
                return {"ok": True, "unconfirmed": True}
            log.warning("segment %s failed: %s", kind, describe(e))
            return {"ok": False, "error": str(e)[:120]}

    # The station's own bounds on a takeover window (OVERRIDE_MIN/MAX_MINUTES
    # in its settings). Mirrored rather than discovered because the endpoint
    # rejects an out-of-range window with a 400 — which reaches the caller as
    # "that didn't work" for a number we could have corrected ourselves.
    TAKEOVER_MIN_MINUTES = 15
    TAKEOVER_MAX_MINUTES = 720

    async def pin_show(self, show_id: str, minutes: int) -> dict:
        """Pin a show over the weekly grid for a bounded window. Admin-only.

        The station calls this a takeover: it outranks the schedule until it
        lapses, then normal programming picks up where it would have been.
        Posting again while one is live REPLACES it, which is how "give it
        another hour" works — there is no separate extend endpoint.

        The switch is not instant. The station returns as soon as the pin is
        stored and airs the handover in the background, landing at the next
        track boundary like any show change. Anything that tells a caller
        otherwise is promising something they will not hear.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        try:
            r = await self._client.post(
                "/schedule/override",
                json={"showId": show_id, "minutes": int(minutes)},
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("takeover %s slow to confirm (%s) — treating as set", show_id, e)
                return {"ok": True, "unconfirmed": True}
            log.warning("takeover %s failed: %s", show_id, describe(e))
            return {"ok": False, "error": str(e)[:120]}

    async def clear_pinned_show(self) -> dict:
        """Cancel a takeover and resume the weekly schedule. Admin-only.

        Idempotent at the station: clearing an already-clear override succeeds
        and airs nothing.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        try:
            r = await self._client.delete(
                "/schedule/override",
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("takeover cancel slow to confirm (%s) — treating as done", e)
                return {"ok": True, "unconfirmed": True}
            log.warning("takeover cancel failed: %s", describe(e))
            return {"ok": False, "error": str(e)[:120]}

    async def like_track(self, song_id: str) -> dict:
        """Add a like to the track playing right now. PUBLIC — no credentials.

        This is the SAME heart any listener taps in the app (POST /like), not
        the operator's admin curation, so it is safe on a call line: a caller
        liking the current record is exactly what a listener does. The station
        gates it on its own Likes toggle (403 if off) and rate-limits per IP.

        `song_id` is what WE think is on air, sent so a like that arrives just
        after the track changed doesn't land on the wrong song — the station
        answers 409 with the real songId, which we surface rather than lie.
        """
        try:
            r = await self._client.post(
                "/like",
                json={"songId": song_id} if song_id else {},
                timeout=ACTION_TIMEOUT,
            )
            if r.status_code == 403:
                return {"ok": False, "error": "likes are switched off on this station"}
            if r.status_code == 409:
                return {"ok": False, "error": "that track just ended"}
            if r.status_code == 429:
                return {"ok": False, "error": "too many likes just now — give it a moment"}
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("like slow to confirm (%s) — treating as recorded", e)
                return {"ok": True, "unconfirmed": True}
            log.warning("like failed: %s", describe(e))
            return {"ok": False, "error": str(e)[:120]}

    async def unlike_track(self, song_id: str) -> dict:
        """Remove the OPERATOR's heart from a song. Admin-only.

        This is the other likes system: not the public per-airing like above
        (that has no un-like), but the operator's own curation heart on a
        specific song id — DELETE /likes/song/:id/operator. So it only means
        anything to a caller signed in AS the operator, which is why it is
        gated to the admin tier and needs station credentials. Idempotent:
        un-hearting a song that was never hearted still succeeds.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        if not song_id:
            return {"ok": False, "error": "nothing is playing to un-like right now"}
        try:
            r = await self._client.delete(
                f"/likes/song/{_seg(song_id)}/operator",
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("un-like slow to confirm (%s) — treating as done", e)
                return {"ok": True, "unconfirmed": True}
            log.warning("un-like failed: %s", describe(e))
            return {"ok": False, "error": str(e)[:120]}

    async def library_genres(self, limit: int = 40) -> list[str]:
        """The genre words this library actually files under, commonest first.

        Read only when a genre browse comes back empty. A genre is free text on
        the station's side — "Hip Hop" and "Hip-Hop" are different words, and
        only one of them is in any given library — so an empty result is far
        more often the wrong spelling than an absent genre. Same reasoning as
        the mood vocabulary browse already hands back, one field along.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return []
        try:
            r = await self._client.get(
                "/library/genres", auth=httpx.BasicAuth(user, password),
                timeout=LIBRARY_TIMEOUT,
            )
            r.raise_for_status()
            rows = _body(r).get("genres") or []
            names = [str(g.get("value") or "").strip() for g in rows
                     if isinstance(g, dict) and str(g.get("value") or "").strip()]
            return names[:max(1, int(limit))]
        except Exception as e:
            log.info("library genres unavailable: %s", describe(e))
            return []

    async def liked_tracks(self, limit: int = 12) -> list[dict]:
        """What this station's listeners have actually hearted. Admin-only.

        The station's own Liked view, sourced from the likes store rather than
        the tagged index — deliberately, on its side: a liked track may never
        have been walked or tagged, because listeners heart whatever is on air,
        and a tagged-only read would hide exactly those.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return []
        try:
            r = await self._client.get(
                "/library/liked",
                params={"limit": max(1, int(limit)), "sort": "count"},
                auth=httpx.BasicAuth(user, password),
                timeout=LIBRARY_TIMEOUT,
            )
            r.raise_for_status()
            d = _body(r)
            items = d.get("rows") or d.get("results") or []
            return items if isinstance(items, list) else []
        except Exception as e:
            log.info("liked tracks unavailable: %s", describe(e))
            return []

    async def play_history(self, limit: int = 12) -> list[dict]:
        """What has actually aired, newest first. Admin-only.

        Distinct from the recent history on /state, which is the live queue's
        short memory and resets with it: this is the station's durable play
        log, one row per aired track, stamped with the source (ai/request/auto),
        the requester and the show that was on. It is the only way to answer
        "did you play it earlier?" for anything longer ago than the last few
        records — a question callers ask constantly and the DJ used to guess at.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return []
        try:
            r = await self._client.get(
                "/library/history", params={"limit": max(1, int(limit))},
                auth=httpx.BasicAuth(user, password),
                timeout=LIBRARY_TIMEOUT,
            )
            r.raise_for_status()
            rows = _body(r).get("rows") or []
            return rows if isinstance(rows, list) else []
        except Exception as e:
            log.info("play history unavailable: %s", describe(e))
            return []

    async def sound_search_available(self) -> bool | None:
        """Whether this station can answer a sound search at all.

        `None` means "couldn't tell" — the station is old, unreachable, or
        doesn't publish coverage — and the caller of this must treat that as
        "assume it can", not as a no. The distinction matters because an empty
        sound search has two completely different causes: an analyser that has
        never run (nothing this station can do about the caller's vibe) and a
        vibe with genuinely no music behind it. Telling a caller their taste
        isn't in the library, when really the feature is switched off, is the
        worse of the two lies.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return None
        try:
            r = await self._client.get(
                "/library/coverage", auth=httpx.BasicAuth(user, password),
                timeout=LIBRARY_TIMEOUT,
            )
            r.raise_for_status()
            d = _body(r)
            value = d.get("soundSearchAvailable")
            return bool(value) if isinstance(value, bool) else None
        except Exception as e:
            log.info("library coverage unavailable: %s", describe(e))
            return None

    async def block_track(self, track_id: str) -> dict:
        """Put a track on the station's never-play list. Admin-only.

        `{type: 'track', trackId}` is the station's own UI flow: it resolves
        the album/artist ids and the display snapshot itself, so nothing here
        has to know a track's shape. Blocking is not just a list entry — the
        station drops the track from the upcoming queue and rebuilds the
        fallback playlist, which is why an already-blocked track answering 409
        is a success from a caller's point of view and is reported as one.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        if not track_id:
            return {"ok": False, "error": "nothing identifiable to block"}
        try:
            r = await self._client.post(
                "/library/blocklist",
                json={"type": "track", "trackId": track_id},
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            if r.status_code == 409:
                return {"ok": True, "already": True}
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("block slow to confirm (%s) — treating as done", e)
                return {"ok": True, "unconfirmed": True}
            log.warning("block %s failed: %s", track_id, describe(e))
            return {"ok": False, "error": str(e)[:120]}

    async def unblock_track(self, track_id: str) -> dict:
        """Take a track back off the never-play list. Admin-only.

        The station reverses its own side-effects (it re-admits the track to
        selection, and #1402 will also lift a Navidrome-level exclusion), so
        there is nothing to undo here beyond the DELETE.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        if not track_id:
            return {"ok": False, "error": "nothing identifiable to unblock"}
        try:
            r = await self._client.delete(
                f"/library/blocklist/track/{_seg(track_id)}",
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            if r.status_code == 404:
                return {"ok": True, "already": True}
            r.raise_for_status()
            return {"ok": True, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("unblock slow to confirm (%s) — treating as done", e)
                return {"ok": True, "unconfirmed": True}
            log.warning("unblock %s failed: %s", track_id, describe(e))
            return {"ok": False, "error": str(e)[:120]}

    # The station's own caps on a genre lock (GENRE_LOCK_GENRES_MAX and
    # GENRE_LOCK_GENRE_MAX upstream). Mirrored for the same reason the takeover
    # window is: an over-long list comes back as a 400, which reaches the caller
    # as "that didn't work" for something we could have trimmed ourselves.
    GENRE_LOCK_MAX_GENRES = 15
    GENRE_LOCK_MAX_GENRE_LEN = 64

    async def set_genre_lock(self, genres: list[str], minutes: int) -> dict:
        """Hard-filter the station to a genre or few, for a bounded window.

        Upstream's quick control (PR #1404): it upserts one reserved show
        carrying the genre filter and pins it exactly like a takeover, so the
        window bounds are the takeover's own and re-posting replaces rather
        than stacks.

        **Not merged upstream yet.** A station that hasn't got it answers 404,
        which is reported here as a capability gap rather than a failure — the
        same posture as the lyrics read. When it lands this starts working with
        no change on this side.
        """
        from station_config import admin_credentials

        user, password = admin_credentials()
        if not (user and password):
            return {"ok": False, "error": "no station admin credentials"}
        clean: list[str] = []
        for genre in genres or []:
            word = str(genre or "").strip()[:self.GENRE_LOCK_MAX_GENRE_LEN]
            # De-duplicated case-insensitively in first-seen order, the way the
            # station's own schema does it, so "Jazz, jazz" is one genre here
            # too rather than two that the station silently folds.
            if word and word.lower() not in {c.lower() for c in clean}:
                clean.append(word)
        if not clean:
            return {"ok": False, "error": "no genre named"}
        try:
            r = await self._client.post(
                "/schedule/genre-lock",
                json={"genres": clean[:self.GENRE_LOCK_MAX_GENRES],
                      "minutes": int(minutes)},
                auth=httpx.BasicAuth(user, password),
                timeout=ACTION_TIMEOUT,
            )
            if r.status_code == 404:
                return {"ok": False, "unsupported": True,
                        "error": "this station's software has no genre lock yet"}
            if r.status_code == 409:
                return {"ok": False, "error": _body(r).get("error")
                        or "the station refused the lock"}
            r.raise_for_status()
            return {"ok": True, "genres": clean, **_body(r)}
        except Exception as e:
            if _sent_but_unconfirmed(e):
                log.warning("genre lock slow to confirm (%s) — treating as set", e)
                return {"ok": True, "unconfirmed": True, "genres": clean}
            log.warning("genre lock failed: %s", describe(e))
            return {"ok": False, "error": str(e)[:120]}

    async def active_show(self, now_playing: dict | None = None,
                          schedule: dict | None = None) -> dict:
        """The show currently on air, with its `topic` (the Show Card).

        Accepts already-fetched /now-playing and /schedule payloads so prompt
        assembly doesn't request either twice per call. The snapshot gathers
        both concurrently at the top of the call; on a congested station the
        serial re-reads this used to do timed out one after another in front of
        every caller (measured 2026-08-10) — pass the snapshot's copies in.

        The two records are merged rather than one replacing the other, and
        that is the whole point. /now-playing carries the show as it is
        actually RUNNING — resolved guest personas, this episode's angle, the
        genre/mood/energy filters picks are judged against. /schedule carries
        it as CONFIGURED — personaId, guestPersonaIds, mood. A measured swap
        against a live station traded fifteen fields for three, and it did the
        most damage on programme shows, which are the ones with an episode
        angle and guests to lose.
        """
        np = now_playing if now_playing is not None else await self.now_playing()
        active = (np.get("context") or {}).get("activeShow") or {}
        show_id = active.get("id")
        if not show_id:
            return active

        shows = schedule if schedule is not None else await self.schedule()
        for show in shows.get("shows", []):
            if show.get("id") == show_id:
                # The schedule still wins wherever it has something to say, so
                # nothing already reaching the prompt changes shape; it just
                # stops taking the live record's fields down with it.
                stated = {k: v for k, v in show.items() if v not in (None, "", [], {})}
                return {**active, **stated}
        return active
